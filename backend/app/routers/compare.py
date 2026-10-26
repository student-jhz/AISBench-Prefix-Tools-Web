"""结果对比分析"""

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..core import compare_core as cc
from ..schemas import (
    CompareComputeRequest,
    CompareExportRequest,
    CompareLoadRequest,
)
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/compare", tags=["compare"])


def _resolve_source(state: AppState, source) -> tuple:
    """解析对比数据源为 (path, label); 校验目录边界"""
    if source.type == "upload":
        if not source.upload_id:
            raise HTTPException(400, "缺少上传文件ID")
        uploads = Path(state.uploads_dir).resolve()
        path = (uploads / source.upload_id).resolve()
        if not str(path).startswith(str(uploads)) or not path.is_file():
            raise HTTPException(400, f"上传文件不存在: {source.upload_id}")
        return str(path), source.upload_id

    # 结果历史
    if not source.run_id:
        raise HTTPException(400, "缺少 run_id")
    results = Path(state.results_dir).resolve()
    run_dir = (results / source.run_id).resolve()
    if not str(run_dir).startswith(str(results)) or not run_dir.is_dir():
        raise HTTPException(400, f"结果目录不存在: {source.run_id}")

    # 指定文件或首个CSV
    if source.file:
        target = (run_dir / source.file).resolve()
        if not str(target).startswith(str(run_dir)) or not target.is_file():
            raise HTTPException(400, f"结果文件不存在: {source.file}")
        return str(target), f"{source.run_id}/{source.file}"

    csvs = sorted(run_dir.rglob("*.csv"))
    if not csvs:
        raise HTTPException(400, f"结果目录中没有CSV文件: {source.run_id}")
    rel = csvs[0].relative_to(run_dir)
    return str(csvs[0]), f"{source.run_id}/{rel}"


def _load_pair(state: AppState, req: CompareLoadRequest):
    path_a, label_a = _resolve_source(state, req.a)
    path_b, label_b = _resolve_source(state, req.b)

    rows_a, err = cc.load_result_csv(path_a)
    if err:
        raise HTTPException(400, f"CSV A 加载失败: {err}")
    rows_b, err = cc.load_result_csv(path_b)
    if err:
        raise HTTPException(400, f"CSV B 加载失败: {err}")

    return rows_a, rows_b, (path_a, label_a), (path_b, label_b)


def _match_summary(match, index):
    key_a = cc._case_key(match["row_a"])
    key_b = cc._case_key(match["row_b"])
    return {
        "index": index,
        "label": cc.match_label(match),
        "exact": match["exact"],
        "input_a": key_a[0], "output_a": key_a[1],
        "input_b": key_b[0], "output_b": key_b[1],
        "total_req_a": match["row_a"].get("total_req", ""),
        "total_req_b": match["row_b"].get("total_req", ""),
        "max_cc_a": match["row_a"].get("max_cc", ""),
        "max_cc_b": match["row_b"].get("max_cc", ""),
    }


@router.post("/load")
def load(req: CompareLoadRequest, state: AppState = Depends(get_state)):
    """加载两份CSV并匹配用例"""
    rows_a, rows_b, (path_a, label_a), (path_b, label_b) = _load_pair(state, req)

    matches, unmatched_a, unmatched_b = cc.match_case_rows(rows_a, rows_b)
    return {
        "a": {"label": label_a, "rows": len(rows_a)},
        "b": {"label": label_b, "rows": len(rows_b)},
        "matches": [_match_summary(m, i) for i, m in enumerate(matches)],
        "unmatched_a": unmatched_a,
        "unmatched_b": unmatched_b,
        "metric_fields": [
            {"field": f, "name": n, "unit": u, "decimals": d, "category": c}
            for f, n, u, d, c in cc.METRIC_FIELDS
        ],
        "default_metrics": cc.DEFAULT_METRICS,
    }


def _select_matches(matches, match_indices):
    if match_indices is None:
        return matches
    indices = set(match_indices)
    return [m for i, m in enumerate(matches) if i in indices]


@router.post("/compute")
def compute(req: CompareComputeRequest, state: AppState = Depends(get_state)):
    """计算选中匹配对在选中指标上的对比表"""
    rows_a, rows_b, (_pa, label_a), (_pb, label_b) = _load_pair(state, req)

    metrics = [m for m in req.metrics if m in cc.METRIC_FIELD_MAP]
    if not metrics:
        metrics = list(cc.DEFAULT_METRICS)

    matches, unmatched_a, unmatched_b = cc.match_case_rows(rows_a, rows_b)
    selected = _select_matches(matches, req.match_indices)

    tables = cc.build_comparison_tables(selected, metrics)
    return {
        "a": {"label": label_a},
        "b": {"label": label_b},
        "match_count": len(selected),
        "unmatched_a": unmatched_a,
        "unmatched_b": unmatched_b,
        "tables": tables,
    }


@router.post("/export")
def export(req: CompareExportRequest, state: AppState = Depends(get_state)):
    """导出对比结果CSV"""
    rows_a, rows_b, (_pa, label_a), (_pb, label_b) = _load_pair(state, req)

    metrics = [m for m in req.metrics if m in cc.METRIC_FIELD_MAP]
    if not metrics:
        metrics = list(cc.DEFAULT_METRICS)

    matches, _, _ = cc.match_case_rows(rows_a, rows_b)
    selected = _select_matches(matches, req.match_indices)
    tables = cc.build_comparison_tables(selected, metrics)
    content = cc.export_comparison_csv(tables, label_a, label_b)

    filename = f"compare_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    # 加 BOM 便于 Excel 打开中文
    return Response(
        content="\ufeff" + content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

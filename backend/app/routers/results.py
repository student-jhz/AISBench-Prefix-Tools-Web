"""测试结果历史管理"""

import io
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from ..core.compare_core import load_result_csv
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/results", tags=["results"])


def _run_dir(state: AppState, run_id: str) -> Path:
    """校验并返回 run 目录"""
    results = Path(state.results_dir).resolve()
    path = (results / run_id).resolve()
    if not str(path).startswith(str(results)) or not path.is_dir():
        raise HTTPException(404, "结果目录不存在")
    return path


def _list_files(run_dir: Path) -> list:
    files = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            files.append({
                "name": str(path.relative_to(run_dir)).replace("\\", "/"),
                "size": path.stat().st_size,
            })
    return files


@router.get("")
def list_runs(state: AppState = Depends(get_state)):
    """列出所有测试结果run"""
    runs = []
    results = Path(state.results_dir)
    for run_dir in sorted(results.iterdir(), key=lambda p: p.stat().st_mtime,
                          reverse=True):
        if not run_dir.is_dir():
            continue
        files = _list_files(run_dir)
        csv_files = [f["name"] for f in files if f["name"].endswith(".csv")]
        log_files = [f["name"] for f in files if f["name"].endswith(".log")]
        mtime = run_dir.stat().st_mtime
        runs.append({
            "run_id": run_dir.name,
            "created_at": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            "file_count": len(files),
            "csv_files": csv_files,
            "log_files": log_files,
        })
    return {"runs": runs}


@router.get("/{run_id}")
def run_detail(run_id: str, state: AppState = Depends(get_state)):
    """run 详情: 文件列表 + 结果CSV解析结果(优先工具生成的 results_*.csv)"""
    run_dir = _run_dir(state, run_id)
    files = _list_files(run_dir)

    rows = []
    csv_file = ""
    # 优先本工具解析生成的 results_*.csv(字段与 CSV_FIELDS 一致),
    # 跳过 test_logs_*/aisbench_result.csv 等基准工具原始输出(列名不同)。
    candidates = sorted(run_dir.glob("results_*.csv"))
    candidates += [p for p in sorted(run_dir.rglob("*.csv"))
                   if p not in candidates]
    for path in candidates:
        csv_file = str(path.relative_to(run_dir))
        parsed, err = load_result_csv(str(path))
        if err is None:
            rows = parsed
            break

    return {
        "run_id": run_id,
        "files": files,
        "csv_file": csv_file,
        "rows": rows,
    }


@router.get("/{run_id}/download")
def download_file(run_id: str, file: str, state: AppState = Depends(get_state)):
    """下载run内单个文件"""
    run_dir = _run_dir(state, run_id)
    target = (run_dir / file).resolve()
    if not str(target).startswith(str(run_dir)) or not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(target, filename=target.name)


@router.get("/{run_id}/download-zip")
def download_zip(run_id: str, state: AppState = Depends(get_state)):
    """打包下载整个run"""
    run_dir = _run_dir(state, run_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in run_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(run_dir))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})


@router.delete("/{run_id}")
def delete_run(run_id: str, state: AppState = Depends(get_state)):
    """删除run目录"""
    run_dir = _run_dir(state, run_id)
    shutil.rmtree(run_dir)
    return {"ok": True}

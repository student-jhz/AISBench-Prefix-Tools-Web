"""结果对比分析核心 - 加载两个测试结果CSV, 按输入规模匹配测试用例并对比指标

迁移自桌面版 result_compare.py 的非 GUI 部分:
1. 数据源: 上传CSV 或历史结果目录中的 results_*.csv
2. 匹配: 先按(输入长度,输出长度)精确匹配, 剩余按输入长度回退匹配
3. 输出: 差异数据(A/B/Δ/Δ%) + 图表数据 + 导出CSV
"""

import csv
import io
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .log_parser import CSV_FIELDS


# 可对比指标: (字段名, 显示名, 单位, 小数位, 分类)
METRIC_FIELDS = [
    ("TTFT_avg", "TTFT平均", "ms", 2, "TTFT"),
    ("TTFT_min", "TTFT最小", "ms", 2, "TTFT"),
    ("TTFT_max", "TTFT最大", "ms", 2, "TTFT"),
    ("TTFT_P90", "TTFT P90", "ms", 2, "TTFT"),
    ("TPOT_avg", "TPOT平均", "ms", 2, "TPOT"),
    ("TPOT_min", "TPOT最小", "ms", 2, "TPOT"),
    ("TPOT_max", "TPOT最大", "ms", 2, "TPOT"),
    ("TPOT_SLO_P90", "TPOT SLO P90", "ms", 2, "TPOT"),
    ("E2EL_avg", "E2E延迟平均", "ms", 2, "E2E延迟"),
    ("E2EL_min", "E2E延迟最小", "ms", 2, "E2E延迟"),
    ("E2EL_max", "E2E延迟最大", "ms", 2, "E2E延迟"),
    ("E2EL_P90", "E2E延迟P90", "ms", 2, "E2E延迟"),
    ("E2E_throughput", "E2E吞吐", "tok/s", 2, "吞吐"),
    ("output_throughput", "输出吞吐", "tok/s", 2, "吞吐"),
    ("input_token_throughput", "输入吞吐", "tok/s", 2, "吞吐"),
    ("prefill_token_throughput", "预填充吞吐", "tok/s", 2, "吞吐"),
    ("qps", "QPS", "req/s", 2, "吞吐"),
    ("qpm", "QPM", "req/min", 2, "吞吐"),
    ("hbm_hit_rate", "HBM命中率", "%", 1, "命中率"),
    ("external_hit_rate", "外部命中率", "%", 1, "命中率"),
    ("E2E_time", "E2E时间", "s", 2, "其他"),
]

DEFAULT_METRICS = ["TTFT_avg", "TPOT_avg", "output_throughput", "qps",
                   "hbm_hit_rate", "external_hit_rate"]

METRIC_FIELD_MAP = {m[0]: m for m in METRIC_FIELDS}


def _to_float(value) -> Optional[float]:
    """安全转float, 失败返回None"""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _case_key(row: Dict) -> Tuple[Optional[int], Optional[int]]:
    """用例标识: (input_len, output_len)"""
    input_len = _to_float(row.get("input_len"))
    output_len = _to_float(row.get("output_len"))
    return (int(input_len) if input_len is not None else None,
            int(output_len) if output_len is not None else None)


def _sort_key(key: Tuple[Optional[int], Optional[int]]):
    return (key[0] if key[0] is not None else 0,
            key[1] if key[1] is not None else 0)


def format_len(value) -> str:
    """8192 -> 8K, 1048576 -> 1M"""
    num = _to_float(value)
    if num is None:
        return "-"
    num = int(num)
    if num >= 1048576 and num % 1048576 == 0:
        return f"{num // 1048576}M"
    if num >= 1024 and num % 1024 == 0:
        return f"{num // 1024}K"
    return str(num)


def format_value(value, decimals: int = 2) -> str:
    num = _to_float(value)
    if num is None:
        return "-"
    return f"{num:,.{decimals}f}"


def load_result_csv(file_path: str) -> Tuple[List[Dict], Optional[str]]:
    """加载并校验结果CSV: 表头字段必须与工具生成的CSV_FIELDS完全一致

    返回 (rows, error); 成功时error为None
    """
    path = Path(file_path)
    if not path.is_file():
        return [], "文件不存在"

    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
            missing = [field for field in CSV_FIELDS if field not in header]
            extra = [field for field in header if field not in CSV_FIELDS]
            if missing or extra:
                reasons = []
                if missing:
                    reasons.append(f"缺少字段: {', '.join(missing)}")
                if extra:
                    reasons.append(f"多余字段: {', '.join(extra)}")
                return [], "不是工具生成的结果CSV格式 (" + "; ".join(reasons) + ")"
            rows = []
            for raw in reader:
                row = {k.strip(): v for k, v in raw.items() if k and k.strip()}
                if row:
                    rows.append(row)
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"读取失败: {exc}"

    if not rows:
        return [], "CSV中没有数据行"
    return rows, None


def match_case_rows(rows_a: List[Dict], rows_b: List[Dict]):
    """匹配两组结果行

    第一轮按(input_len, output_len)精确匹配;
    未匹配的行回退按input_len匹配(标注为输入匹配)。
    返回 (matches, unmatched_a, unmatched_b),
    match = {'row_a':..., 'row_b':..., 'exact': bool}
    """
    matches = []
    used_b = [False] * len(rows_b)
    matched_a = [False] * len(rows_a)

    for i, row_a in enumerate(rows_a):
        key_a = _case_key(row_a)
        for j, row_b in enumerate(rows_b):
            if not used_b[j] and _case_key(row_b) == key_a:
                matches.append({"row_a": row_a, "row_b": row_b, "exact": True})
                used_b[j] = True
                matched_a[i] = True
                break

    for i, row_a in enumerate(rows_a):
        if matched_a[i]:
            continue
        input_a = _case_key(row_a)[0]
        for j, row_b in enumerate(rows_b):
            if not used_b[j] and _case_key(row_b)[0] == input_a:
                matches.append({"row_a": row_a, "row_b": row_b, "exact": False})
                used_b[j] = True
                matched_a[i] = True
                break

    matches.sort(key=lambda m: _sort_key(_case_key(m["row_a"])))
    unmatched_a = sum(1 for flag in matched_a if not flag)
    unmatched_b = sum(1 for flag in used_b if not flag)
    return matches, unmatched_a, unmatched_b


def compute_comparison(match: Dict, field: str) -> Dict:
    """计算单个匹配对单个指标的对比值 (A=基准, B=对比)"""
    value_a = _to_float(match["row_a"].get(field))
    value_b = _to_float(match["row_b"].get(field))
    diff = pct = None
    if value_a is not None and value_b is not None:
        diff = value_b - value_a
        if value_a != 0:
            pct = (value_b - value_a) / abs(value_a) * 100
    return {"a": value_a, "b": value_b, "diff": diff, "pct": pct}


def match_label(match: Dict) -> str:
    """匹配对的展示标签: 输入8K·输出512(精确)"""
    key_a = _case_key(match["row_a"])
    key_b = _case_key(match["row_b"])
    label = f"{format_len(key_a[0])}→{format_len(key_a[1])}"
    if not match["exact"] and key_b[1] != key_a[1]:
        label += f"↔{format_len(key_b[0])}→{format_len(key_b[1])}"
    return label


def build_comparison_tables(matches: List[Dict], metric_fields: List[str]) -> Dict:
    """构建各指标的对比表数据

    返回 {metric_field: {"field", "name", "unit", "decimals", "rows": [...]}}
    rows: [{label, exact, a, b, diff, pct, a_str, b_str, diff_str, pct_str}]
    """
    tables = {}
    for field in metric_fields:
        meta = METRIC_FIELD_MAP.get(field)
        if not meta:
            continue
        _f, name, unit, decimals, _cat = meta
        rows = []
        for match in matches:
            comp = compute_comparison(match, field)
            rows.append({
                "label": match_label(match),
                "exact": match["exact"],
                "a": comp["a"], "b": comp["b"],
                "diff": comp["diff"], "pct": comp["pct"],
                "a_str": format_value(comp["a"], decimals),
                "b_str": format_value(comp["b"], decimals),
                "diff_str": format_value(comp["diff"], decimals),
                "pct_str": format_value(comp["pct"], decimals),
            })
        tables[field] = {
            "field": field, "name": name, "unit": unit,
            "decimals": decimals, "rows": rows,
        }
    return tables


def export_comparison_csv(tables: Dict, source_a: str, source_b: str) -> str:
    """导出对比结果CSV(与桌面版导出格式一致)"""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["AISBench 结果对比报告"])
    writer.writerow(["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow(["基准A", source_a])
    writer.writerow(["对比B", source_b])
    writer.writerow([])

    for field, table in tables.items():
        writer.writerow([f"指标: {table['name']} ({table['field']}, {table['unit']})"])
        writer.writerow(["用例", "A值", "B值", "Δ(B-A)", "Δ%"])
        for row in table["rows"]:
            writer.writerow([
                row["label"] + ("" if row["exact"] else " (输入匹配)"),
                row["a_str"], row["b_str"], row["diff_str"], row["pct_str"],
            ])
        writer.writerow([])

    return output.getvalue()


def nice_ceiling(value: float) -> float:
    """向上取整到 1/2/5*10^k 的美观刻度"""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10 ** exponent)
    if fraction <= 1:
        nice = 1.0
    elif fraction <= 2:
        nice = 2.0
    elif fraction <= 5:
        nice = 5.0
    else:
        nice = 10.0
    return nice * (10 ** exponent)

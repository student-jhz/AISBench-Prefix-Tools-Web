"""结果对比核心逻辑测试"""

import csv
import io

from app.core.compare_core import (
    load_result_csv, match_case_rows, compute_comparison, build_comparison_tables,
    export_comparison_csv, format_len, format_value, METRIC_FIELDS, DEFAULT_METRICS,
)
from app.core.log_parser import CSV_FIELDS


def make_csv(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def write_csv(path, rows):
    path.write_text(make_csv(rows), encoding='utf-8')


def make_row(input_len, output_len, **kw):
    row = {f: '' for f in CSV_FIELDS}
    row.update({'input_len': input_len, 'output_len': output_len,
                'total_req': 100, 'max_cc': 10, 'cc': 10})
    row.update(kw)
    return row


class TestLoadCSV:
    def test_load_valid(self, tmp_path):
        p = tmp_path / "a.csv"
        write_csv(p, [make_row(8192, 512, TTFT_avg='100.5')])
        rows, err = load_result_csv(str(p))
        assert err is None
        assert len(rows) == 1
        assert rows[0]['TTFT_avg'] == '100.5'

    def test_load_missing_file(self, tmp_path):
        rows, err = load_result_csv(str(tmp_path / "nope.csv"))
        assert rows == []
        assert err == "文件不存在"

    def test_load_wrong_header(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("a,b\n1,2\n", encoding='utf-8')
        rows, err = load_result_csv(str(p))
        assert rows == []
        assert "不是工具生成的结果CSV格式" in err

    def test_load_empty_rows(self, tmp_path):
        p = tmp_path / "empty.csv"
        write_csv(p, [])
        rows, err = load_result_csv(str(p))
        assert rows == []
        assert err == "CSV中没有数据行"


class TestMatching:
    def test_exact_match(self):
        a = [make_row(8192, 512), make_row(16384, 1024)]
        b = [make_row(16384, 1024), make_row(8192, 512)]
        matches, ua, ub = match_case_rows(a, b)
        assert len(matches) == 2
        assert ua == 0 and ub == 0
        assert all(m['exact'] for m in matches)

    def test_fallback_input_match(self):
        a = [make_row(8192, 512)]
        b = [make_row(8192, 1024)]
        matches, ua, ub = match_case_rows(a, b)
        assert len(matches) == 1
        assert matches[0]['exact'] is False
        assert ua == 0 and ub == 0

    def test_unmatched(self):
        a = [make_row(8192, 512), make_row(32768, 512)]
        b = [make_row(8192, 512)]
        matches, ua, ub = match_case_rows(a, b)
        assert len(matches) == 1
        assert ua == 1 and ub == 0

    def test_sorted_by_input(self):
        a = [make_row(32768, 512), make_row(8192, 512), make_row(16384, 512)]
        b = list(a)
        matches, _, _ = match_case_rows(a, b)
        inputs = [int(m['row_a']['input_len']) for m in matches]
        assert inputs == sorted(inputs)


class TestComparison:
    def test_compute(self):
        match = {'row_a': {'qps': '10'}, 'row_b': {'qps': '12.5'}}
        comp = compute_comparison(match, 'qps')
        assert comp['a'] == 10
        assert comp['b'] == 12.5
        assert comp['diff'] == 2.5
        assert comp['pct'] == 25.0

    def test_compute_missing(self):
        match = {'row_a': {'qps': ''}, 'row_b': {'qps': '5'}}
        comp = compute_comparison(match, 'qps')
        assert comp['a'] is None
        assert comp['diff'] is None
        assert comp['pct'] is None

    def test_compute_zero_base(self):
        match = {'row_a': {'qps': '0'}, 'row_b': {'qps': '5'}}
        comp = compute_comparison(match, 'qps')
        assert comp['diff'] == 5
        assert comp['pct'] is None

    def test_build_tables(self):
        a = [make_row(8192, 512, TTFT_avg='100')]
        b = [make_row(8192, 512, TTFT_avg='110')]
        matches, _, _ = match_case_rows(a, b)
        tables = build_comparison_tables(matches, ['TTFT_avg'])
        assert 'TTFT_avg' in tables
        row = tables['TTFT_avg']['rows'][0]
        assert row['a'] == 100.0
        assert row['b'] == 110.0
        assert row['pct'] == 10.0
        assert row['pct_str'] == '10.00'


class TestExport:
    def test_export_csv(self):
        a = [make_row(8192, 512, TTFT_avg='100')]
        b = [make_row(8192, 512, TTFT_avg='110')]
        matches, _, _ = match_case_rows(a, b)
        tables = build_comparison_tables(matches, ['TTFT_avg'])
        content = export_comparison_csv(tables, "a.csv", "b.csv")
        assert "AISBench 结果对比报告" in content
        assert "指标: TTFT平均 (TTFT_avg, ms)" in content
        assert "10.00" in content  # Δ%


class TestFormat:
    def test_format_len(self):
        assert format_len(8192) == "8K"
        assert format_len(1048576) == "1M"
        assert format_len(500) == "500"
        assert format_len(None) == "-"

    def test_format_value(self):
        assert format_value(1234.567) == "1,234.57"
        assert format_value(None) == "-"
        assert format_value("abc") == "-"


def test_metric_fields_complete():
    assert len(METRIC_FIELDS) == 21
    assert set(DEFAULT_METRICS) <= {m[0] for m in METRIC_FIELDS}

"""LogParser 日志解析测试"""

import csv

from app.core.log_parser import (
    LogParser, parse_single_log, parse_log_directory, result_to_csv_row, CSV_FIELDS,
)


SAMPLE_LOG = """2026-10-20 10:00:00 INFO input token length: 8192
2026-10-20 10:00:00 INFO output token length: 512
2026-10-20 10:00:00 INFO number of dataset: 108
2026-10-20 10:00:00 INFO concurrency: 91
2026-10-20 10:00:01 INFO [开始] 全量数据集测试
2026-10-20 10:05:00 INFO {'current_time': '2026-10-20 10:05:00', 'input_len': 8192, 'output_len': 1, 'result_len': 1}
2026-10-20 10:10:00 INFO {'current_time': '2026-10-20 10:10:00', 'input_len': 8192, 'output_len': 512, 'result_len': 512, 'E2E_time': 12.5, 'qpm': 432.1}
Performance Results of task prefix_cache:
TTFT (ms):  total  200.3 ms 120.5 ms 350.8 ms
TPOT (ms):  total  25.6 ms 15.2 ms 40.1 ms
E2EL (ms):  total  9000.5 ms 8000.1 ms 9500.2 ms
Common Metrics:
  Prefill Token Throughput: 12345.6 token/s
  Input Token Throughput: 12345.6 token/s
  Output Token Throughput: 789.0 token/s
  Total Token Throughput: 13134.6 token/s
  Request Throughput: 1.5 req/s
  Concurrency   91
2026-10-20 10:10:01 INFO 全量数据集测试完成，结果保存在aisbench_result.csv
Prefix cache hit summary:
POD_NAME    hbm_hit_rate  hit_num/total_num  external_hit_rate  hit_num/total_num
ALL_PODS    85.32%  1000/1200  92.15%  1100/1200
"""


class TestLogParser:
    def test_basic_params(self):
        parser = LogParser("dummy.log", content=SAMPLE_LOG)
        params = parser.parse_basic_params()
        assert params == {
            'input_len': 8192.0,
            'output_len': 512.0,
            'total_req': 108.0,
            'max_cc': 91.0,
        }

    def test_second_test_json(self):
        parser = LogParser("dummy.log", content=SAMPLE_LOG)
        result = parser.parse_second_test_json()
        assert result is not None
        assert result['output_len'] == 512  # 取第二次(output_len>1)的结果
        assert result['E2E_time'] == 12.5

    def test_perf_table(self):
        parser = LogParser("dummy.log", content=SAMPLE_LOG)
        perf = parser.parse_second_test_perf_table()
        assert perf['TTFT_avg'] == 200.3
        assert perf['TTFT_min'] == 120.5
        assert perf['TTFT_max'] == 350.8
        assert perf['TPOT_avg'] == 25.6
        assert perf['E2EL_avg'] == 9000.5
        assert perf['prefill_token_throughput'] == 12345.6
        assert perf['output_throughput'] == 789.0
        assert perf['E2E_throughput'] == 13134.6
        assert perf['qps'] == 1.5
        assert perf['cc'] == 91

    def test_hit_rates(self):
        parser = LogParser("dummy.log", content=SAMPLE_LOG)
        hbm, external = parser.parse_hit_rates_second_test()
        assert hbm == 85.32
        assert external == 92.15

    def test_parse_single_log(self):
        result = parse_single_log("dummy.log", content=SAMPLE_LOG)
        assert result is not None
        assert result['input_len'] == 8192.0
        assert result['qps'] == 1.5
        assert result['hbm_hit_rate'] == 85.32

    def test_parse_invalid_log(self):
        assert parse_single_log("dummy.log", content="no results") is None


class TestCSVPipeline:
    def test_result_to_csv_row_fields(self):
        row = result_to_csv_row({'input_len': 8192, 'qps': 1.5})
        assert set(row.keys()) == set(CSV_FIELDS)
        assert row['input_len'] == 8192
        assert row['qps'] == 1.5
        assert row['TTFT_avg'] == ''

    def test_parse_log_directory(self, tmp_path):
        (tmp_path / "test_1_8192_512.log").write_text(SAMPLE_LOG, encoding='utf-8')
        (tmp_path / "test_2_16384_1024.log").write_text(SAMPLE_LOG, encoding='utf-8')
        (tmp_path / "other.log").write_text("irrelevant", encoding='utf-8')
        csv_path = tmp_path / "results.csv"

        messages = []
        results = parse_log_directory(
            str(tmp_path), str(csv_path),
            progress_callback=lambda msg, ok, r: messages.append(msg))

        assert len(results) == 2
        assert csv_path.exists()
        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]['input_len'] == '8192'
        assert float(rows[0]['hbm_hit_rate']) == 85.32

    def test_parse_empty_directory(self, tmp_path):
        csv_path = tmp_path / "results.csv"
        results = parse_log_directory(str(tmp_path), str(csv_path))
        assert results == []
        assert not csv_path.exists()

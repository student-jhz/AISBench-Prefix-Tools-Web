"""TestDesigner 核心计算逻辑测试"""

import pytest

from app.core import test_designer as td


def make_designer(kv=198469, dp=4, max_len=32768, rate=0.9, req_rate=0):
    d = td.TestDesigner()
    d.set_kv_cache_info(kv, dp, max_len)
    d.set_repeat_rate(rate)
    d.set_request_rate(req_rate)
    return d


class TestKVParams:
    def test_total_kv(self):
        d = make_designer(kv=1000, dp=4)
        assert d.total_kv_cache == 4000

    def test_repeat_rate_clamped(self):
        d = make_designer()
        d.set_repeat_rate(1.5)
        assert d.repeat_rate == 1.0
        d.set_repeat_rate(-0.1)
        assert d.repeat_rate == 0.0

    def test_request_rate_integer_normalized(self):
        d = make_designer()
        d.set_request_rate(2.0)
        assert d.request_rate == 2
        assert isinstance(d.request_rate, int)
        d.set_request_rate(0.3)
        assert d.request_rate == 0.3

    def test_valid_preset_inputs_filtered(self):
        d = make_designer(max_len=32768)
        assert d.get_valid_preset_inputs() == [8192, 16384]


class TestGenerateCases:
    def test_generate_cross_product(self):
        d = make_designer(kv=198469, dp=4, max_len=131072)
        skipped = d.set_input_lengths([8192, 16384, 262144])
        d.set_output_lengths([512, 1024])
        assert skipped == [262144]
        d.generate_test_cases()
        assert len(d.test_cases) == 4  # 2 inputs x 2 outputs

    def test_case_math(self):
        # total_kv = 198469*4 = 793876
        d = make_designer(kv=198469, dp=4, max_len=131072, rate=0.9)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        case = d.test_cases[0]
        total_kv = 198469 * 4
        assert case.concurrency_max == int(total_kv / (8192 + 512))
        assert case.concurrency_recommended == case.concurrency_max
        assert case.data_num_min == int(total_kv / 8192 / 0.9) + 1
        assert case.data_num_recommended == case.data_num_min * 2
        assert case.repeat_rate == 0.9
        assert case.dp == 4

    def test_no_valid_input_falls_back(self):
        d = make_designer(max_len=100)  # 所有预设都超限
        skipped = d.set_input_lengths([8192, 16384])
        assert skipped == [8192, 16384]
        assert d.input_lengths == [16384, 32768]  # 兜底默认


class TestAdjust:
    def test_adjust_data_num(self):
        d = make_designer(max_len=131072)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        before = d.test_cases[0].data_num_recommended
        d.adjust_data_num(0.5)
        assert d.test_cases[0].data_num_recommended == max(1, int(before * 0.5))

    def test_adjust_concurrency(self):
        d = make_designer(max_len=131072)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        before = d.test_cases[0].concurrency_recommended
        d.adjust_concurrency(2)
        assert d.test_cases[0].concurrency_recommended == before * 2


class TestCustomCase:
    def test_add_custom_case(self):
        d = make_designer(kv=198469, dp=4, max_len=131072)
        d.add_custom_case(4096, 256, data_num=100, concurrency=5)
        case = d.test_cases[-1]
        assert case.is_custom
        assert case.data_num_recommended == 100
        assert case.concurrency_recommended == 5
        total_kv = 198469 * 4
        assert case.data_num_min == int(total_kv / 4096 / 0.9) + 1

    def test_add_custom_case_exceeds_context(self):
        d = make_designer(max_len=32768)
        with pytest.raises(ValueError, match="超过模型最大上下文"):
            d.add_custom_case(32768, 512)

    def test_duplicate_and_delete(self):
        d = make_designer(max_len=131072)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        assert d.duplicate_case(0)
        assert len(d.test_cases) == 2
        assert d.test_cases[1].is_custom
        assert d.delete_case(0)
        assert len(d.test_cases) == 1
        assert not d.delete_case(0)  # 至少保留一个


class TestCommandsAndScript:
    def test_generate_commands_burst(self):
        d = make_designer(kv=198469, dp=4, max_len=131072, req_rate=0)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        d.test_cases[0].data_num_recommended = 100
        d.test_cases[0].concurrency_recommended = 20
        cmd = d.generate_commands()[0]
        assert "python3 aisbench_test.py --prefix_test" in cmd
        assert "--input_len 8192" in cmd
        assert "--output_len 512" in cmd
        assert "--data_num 100" in cmd
        assert "--prefix_num 100" in cmd
        assert "--concurrency 20" in cmd
        assert "--repeat_rate 90%" in cmd
        assert "--dp 4" in cmd
        assert "--seed " in cmd
        assert "--request_rate" not in cmd  # Burst 模式

    def test_generate_commands_with_rate(self):
        d = make_designer(max_len=131072, req_rate=0.3)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        cmd = d.generate_commands()[0]
        assert "--request_rate 0.3" in cmd

    def test_shell_script_structure(self):
        d = make_designer(kv=198469, dp=4, max_len=131072)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        script, log_dir = d.generate_shell_script()
        assert log_dir.startswith("test_logs_")
        assert script.startswith("#!/bin/bash")
        assert "set -o pipefail" in script
        assert "MODEL_PATH_VAL" in script  # 预检查
        assert "FAIL_COUNT" in script
        assert "exit $FAIL_COUNT" in script
        assert "test_1_8192_512.log" in script


class TestPayload:
    def test_roundtrip(self):
        d = make_designer(kv=198469, dp=4, max_len=131072, req_rate=0.3)
        d.set_input_lengths([8192, 16384])
        d.set_output_lengths([512])
        d.generate_test_cases()
        d.add_custom_case(4096, 256)

        payload = d.to_payload()
        d2 = td.TestDesigner.from_payload(payload)
        assert d2.total_kv_cache == d.total_kv_cache
        assert d2.test_cases == d.test_cases

    def test_calc_details(self):
        d = make_designer(kv=198469, dp=4, max_len=131072)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        details = d.calc_details()
        assert "用例1" in details
        assert "并发数(公式)" in details
        assert "最小请求数" in details
        assert "KV使用率" in details

    def test_validate_case_lengths(self):
        d = make_designer(max_len=32768)
        d.set_input_lengths([8192])
        d.set_output_lengths([512])
        d.generate_test_cases()
        assert d.validate_case_lengths() is None
        d.test_cases[0].input_len = 40000
        err = d.validate_case_lengths()
        assert err is not None and "超过模型最大上下文" in err


def test_testcase_from_dict_defaults():
    c = td.TestCase.from_dict({
        "input_len": 8192, "output_len": 512,
        "data_num_recommended": 10, "data_num_min": 5,
        "concurrency_recommended": 3, "concurrency_max": 3,
    })
    assert c.repeat_rate == 0.9
    assert c.dp == 1
    assert c.is_custom is False

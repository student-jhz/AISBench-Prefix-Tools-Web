"""测试用例设计(无状态计算)"""

from fastapi import APIRouter, HTTPException

from ..core import kv_log_tools
from ..core.test_designer import TestDesigner
from ..schemas import (
    DesignerCasesRequest,
    DesignerGenerateRequest,
    DesignerScriptRequest,
)

router = APIRouter(prefix="/designer", tags=["designer"])


def _designer_response(d: TestDesigner, skipped_inputs=None) -> dict:
    cases = []
    for i, case in enumerate(d.test_cases, 1):
        cases.append({
            "index": i,
            **case.to_dict(),
            "kv_usage": round(d.get_kv_usage(case), 2),
            "below_min": case.data_num_recommended < case.data_num_min,
        })
    resp = {
        "cases": cases,
        "summary": d.get_summary(),
        "calc_details": d.calc_details(),
    }
    if skipped_inputs is not None:
        resp["skipped_inputs"] = skipped_inputs
    return resp


@router.get("/presets")
def presets():
    return {
        "input_lengths": TestDesigner.PRESET_INPUT_LENGTHS,
        "output_lengths": TestDesigner.PRESET_OUTPUT_LENGTHS,
        "grep_command": kv_log_tools.KV_GREP_COMMAND,
    }


@router.post("/generate")
def generate(req: DesignerGenerateRequest):
    """根据KV参数与长度选择生成测试用例"""
    if req.single_kv_cache <= 0:
        raise HTTPException(400, "单个DP组KV cache 必须大于 0")
    if req.max_request_length <= 0:
        raise HTTPException(400, "模型最大上下文必须大于 0")
    if not (0 < req.repeat_rate <= 1):
        raise HTTPException(400, "前缀命中率应在 (0, 1] 之间")

    d = TestDesigner()
    d.set_kv_cache_info(req.single_kv_cache, req.dp, req.max_request_length)
    d.set_repeat_rate(req.repeat_rate)
    d.set_request_rate(req.request_rate)

    in_lengths = req.input_lengths
    if not in_lengths:
        valid = d.get_valid_preset_inputs()
        if not valid:
            raise HTTPException(400, "没有有效的输入长度(模型最大上下文过小)")
        in_lengths = valid[:2]

    skipped = d.set_input_lengths(in_lengths)
    d.set_output_lengths(req.output_lengths)
    d.generate_test_cases()

    resp = _designer_response(d, skipped_inputs=skipped)
    # 超长输入明确提示(与桌面版一致, 不再静默替换)
    if skipped:
        def _fmt_len(n):
            if n >= 1048576:
                return f"{n // 1048576}M"
            if n >= 1024:
                return f"{n // 1024}K"
            return str(n)
        skipped_str = ", ".join(_fmt_len(l) for l in skipped)
        kept_str = ", ".join(_fmt_len(l) for l in d.input_lengths)
        max_len = d.max_request_length
        if any(l < max_len for l in req.input_lengths):
            resp["warning"] = (f"以下选中的输入长度超过模型最大上下文 ({max_len:,})，已跳过: "
                               f"{skipped_str}。实际使用的输入长度: {kept_str}")
        else:
            resp["warning"] = (f"所选输入长度全部超过模型最大上下文 ({max_len:,}): "
                               f"{skipped_str}。已自动改用有效长度: {kept_str}")
    return resp


@router.post("/cases")
def cases(req: DesignerCasesRequest):
    """用例列表变更后的服务端校验与派生值重算

    (编辑/复制/删除/调整系数由前端操作后提交完整列表)
    """
    if req.single_kv_cache <= 0 or req.max_request_length <= 0:
        raise HTTPException(400, "请先填写有效的KV cache参数")

    d = TestDesigner()
    d.set_kv_cache_info(req.single_kv_cache, req.dp, req.max_request_length)
    d.set_repeat_rate(req.repeat_rate)
    d.set_request_rate(req.request_rate)

    for case in req.cases:
        total = case.input_len + case.output_len
        if d.max_request_length > 0 and total > d.max_request_length:
            raise HTTPException(
                400,
                f"输入+输出长度 ({case.input_len:,}+{case.output_len:,}={total:,}) "
                f"超过模型最大上下文 ({d.max_request_length:,}), "
                f"vLLM将返回Bad Request拒绝该请求")

    # 构造 designer 并重算派生字段(保留用户设定的推荐值)
    payload = {
        "single_kv_cache": req.single_kv_cache,
        "dp": req.dp,
        "max_request_length": req.max_request_length,
        "repeat_rate": req.repeat_rate,
        "request_rate": req.request_rate,
        "input_lengths": sorted({c.input_len for c in req.cases}),
        "output_lengths": sorted({c.output_len for c in req.cases}),
        "cases": [c.model_dump() for c in req.cases],
    }
    d2 = TestDesigner.from_payload(payload)

    # 重算最小请求数/最大并发(与公式一致, 防止旧值残留)
    for case in d2.test_cases:
        total = case.input_len + case.output_len
        case.concurrency_max = max(1, int(d2.total_kv_cache / total))
        case.data_num_min = int(d2.total_kv_cache / case.input_len / d2.repeat_rate) + 1

    return _designer_response(d2)


@router.post("/script")
def script(req: DesignerScriptRequest):
    """生成测试命令与 shell 脚本预览"""
    if req.single_kv_cache <= 0 or req.max_request_length <= 0:
        raise HTTPException(400, "请先填写有效的KV cache参数")

    payload = {
        "single_kv_cache": req.single_kv_cache,
        "dp": req.dp,
        "max_request_length": req.max_request_length,
        "repeat_rate": req.repeat_rate,
        "request_rate": req.request_rate,
        "input_lengths": sorted({c.input_len for c in req.cases}),
        "output_lengths": sorted({c.output_len for c in req.cases}),
        "cases": [c.model_dump() for c in req.cases],
    }
    d = TestDesigner.from_payload(payload)

    err = d.validate_case_lengths()
    if err:
        raise HTTPException(400, err)

    commands = d.generate_commands()
    script_content, log_dir_name = d.generate_shell_script(commands)
    return {
        "commands": commands,
        "script": script_content,
        "log_dir_name": log_dir_name,
        "summary": d.get_summary(),
    }

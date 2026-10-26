"""配置管理器 - 生成和写入config.py配置文件

迁移自桌面版 config_manager.py, 解耦 DockerManager:
读写操作通过 (ssh, code_host_path) 参数传入, 便于 Web 层无状态调用。
"""

import re
from typing import Dict, List, Optional, Tuple

from .ssh_manager import SSHManager


CONFIG_TEMPLATE = """# -*- coding: utf-8 -*-
# AISBench 测试配置文件 (由AISBench-Prefix-Tools自动生成)

# 数据集文件夹路径，需可访问；选aisbench_auto_tools_prefix中包含GSM8k.jsonl数据集文件路径
DATASET_PATH = "{dataset_path}"

# aisbench 工作路径，默认: "/benchmark"。可通过命令 `pip show ais-bench-benchmark | grep 'Editable project location'` 查询
WORK_PATH = "{work_path}"

# 服务配置的模型名称。见3.1.1.4.1-部署单机推理环境中vllm serve命令的 --served-model-name 参数
MODEL_NAME = "{model_name}"
# 模型权重路径, 用于读取 tokenizer，例如 /mnt/models/DeepSeek-V4-Flash-w8a8-mtp/
MODEL_PATH = "{model_path}"
# 请求目的 IP。
HOST_IP = "{host_ip}"
# 请求目的端口。
HOST_PORT = "{host_port}"
#鉴权信息，若未开启保持为空即可
API_KEY = "{api_key}"

# 如果使用稳态测试请将该字段设置为 "stable_stage"
DEFAULT_PERFORMANCE_TEST = "{default_perf}"

# aisbench输出日志保存路径
OUTPUT_DIR = "{output_dir}"

# 各节点信息，格式为 ["{{ip}}:{{port}}"]
# 用于查询vllm metrics计算各个dp域的prefix cache命中率，不配置默认为HOST_IP:HOST_PORT
# PD分离场景请填写各个节点的IP和对应dp域的port
# POD_INFO = ["141.xx.xx.11:8000","141.xx.xx.12:8000"]
POD_INFO = {pod_info}
"""

# 界面字段 -> config.py 键名
FIELD_TO_CONFIG_KEY = {
    'dataset_path': 'DATASET_PATH',
    'work_path': 'WORK_PATH',
    'model_name': 'MODEL_NAME',
    'model_path': 'MODEL_PATH',
    'host_ip': 'HOST_IP',
    'host_port': 'HOST_PORT',
    'api_key': 'API_KEY',
    'default_perf': 'DEFAULT_PERFORMANCE_TEST',
    'output_dir': 'OUTPUT_DIR',
    'pod_info': 'POD_INFO',
}

DEFAULT_CONFIG = {
    'dataset_path': '',
    'work_path': '/benchmark',
    'model_name': 'ds',
    'model_path': '/path/to/model',
    'host_ip': '127.0.0.1',
    'host_port': '8000',
    'api_key': '',
    'default_perf': 'default_perf',
    'output_dir': './outputs/default',
    'pod_info': '[]',
}


def normalize_pod_info(pod_info) -> str:
    """POD_INFO 归一化为 python 列表字面量字符串"""
    if isinstance(pod_info, list):
        pod_info = str(pod_info)
    if isinstance(pod_info, str) and not pod_info.strip().startswith('['):
        items = [s.strip() for s in pod_info.split(',') if s.strip()]
        pod_info = '[' + ', '.join(f'"{i}"' for i in items) + ']'
    return pod_info


def generate_config_content(config: Dict[str, str],
                            dataset_path_default: str = "") -> str:
    """生成config.py文件内容"""
    merged = DEFAULT_CONFIG.copy()
    merged.update({k: v for k, v in config.items() if v is not None})

    # 确保DATASET_PATH默认为代码挂载路径
    if not merged.get('dataset_path'):
        merged['dataset_path'] = dataset_path_default

    merged['pod_info'] = normalize_pod_info(merged.get('pod_info', '[]'))

    return CONFIG_TEMPLATE.format(**merged)


def validate_config(config: Dict[str, str]) -> List[str]:
    """验证配置项, 返回错误信息列表"""
    errors = []

    if not config.get('model_name'):
        errors.append("MODEL_NAME 不能为空")
    if not config.get('model_path'):
        errors.append("MODEL_PATH 不能为空")
    if not config.get('host_ip'):
        errors.append("HOST_IP 不能为空")
    if not config.get('host_port'):
        errors.append("HOST_PORT 不能为空")

    # 检查端口是否为数字
    port = config.get('host_port', '')
    if port:
        try:
            int(port)
        except ValueError:
            errors.append(f"HOST_PORT 必须为数字: {port}")

    # POD_INFO验证
    pod_info = config.get('pod_info', '')
    if isinstance(pod_info, str) and pod_info.strip():
        if pod_info.strip() not in ('[]', ''):
            cleaned = pod_info.replace('[', '').replace(']', '') \
                .replace('"', '').replace("'", '')
            for item in cleaned.split(','):
                item = item.strip()
                if item and ':' not in item:
                    errors.append(f"POD_INFO项格式应为 'ip:port': {item}")

    return errors


def parse_config_content(content: str) -> dict:
    """从config.py文本中解析键值对"""
    values = {}
    for match in re.finditer(r'^(\w+)\s*=\s*"([^"]*)"', content, re.MULTILINE):
        values[match.group(1)] = match.group(2)
    pod_match = re.search(r'^POD_INFO\s*=\s*(.+)$', content, re.MULTILINE)
    if pod_match:
        values['POD_INFO'] = pod_match.group(1).strip()
    return values


def compare_config(config: Dict[str, str], remote_content: str) -> Tuple[List[str], dict]:
    """界面配置与容器回读内容比较, 返回 (不一致项列表, 回读解析值)"""
    saved = parse_config_content(remote_content)
    mismatches = []
    for field, key in FIELD_TO_CONFIG_KEY.items():
        gui_val = str(config.get(field, '') or '')
        saved_val = str(saved.get(key, '') or '')
        if gui_val and saved_val and gui_val != saved_val:
            mismatches.append(f"{key}: 界面={gui_val}  容器={saved_val}")
    return mismatches, saved


def write_config_to_host(ssh: SSHManager, code_host_path: str,
                         content: str, config_filename: str = "config.py") -> bool:
    """将config.py写入到宿主机挂载目录(容器内可见)"""
    if not code_host_path:
        return False
    remote_path = f"{code_host_path}/{config_filename}"
    return ssh.write_file(remote_path, content)


def read_config_from_host(ssh: SSHManager, code_host_path: str,
                          config_filename: str = "config.py") -> Optional[str]:
    """读取容器挂载目录中的config.py"""
    remote_path = f"{code_host_path}/{config_filename}"
    return ssh.read_file(remote_path)

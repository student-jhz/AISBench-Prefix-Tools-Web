"""API 请求/响应模型"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------ SSH

class SSHConnectRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    password: Optional[str] = None
    key_upload_id: Optional[str] = None
    timeout: int = Field(default=10, ge=1, le=120)


# ------------------------------------------------------------ 平台 v3.0(登录 / 任务)

class PlatformLoginRequest(BaseModel):
    """登录执行机(建立该用户的 SSH 会话)"""
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    password: Optional[str] = None
    key_upload_id: Optional[str] = None
    timeout: int = Field(default=10, ge=1, le=120)


class PlatformTaskCreateRequest(BaseModel):
    """提交测试任务: 镜像 + 测试配置 + 测试用例(容器由平台自动管理, 不对外暴露)"""
    name: Optional[str] = None
    image: str = Field(min_length=1)
    config: Dict[str, str]
    designer: Dict


# ------------------------------------------------------------ 容器 / 部署(创建容器)

class ContainerSelectRequest(BaseModel):
    """选择已有容器(force: 容器正在执行测试时仍强制选择)"""
    name: str = Field(min_length=1)
    force: bool = False


class ContainerNameRequest(BaseModel):
    name: str = Field(min_length=1)

class ImageSource(BaseModel):
    """镜像来源: existing=执行机已有镜像 / upload=上传tar / remote=远程tar"""
    type: str = Field(pattern="^(existing|upload|remote)$")
    name: Optional[str] = None
    upload_id: Optional[str] = None
    remote_path: Optional[str] = None


class CodeSource(BaseModel):
    source: str = Field(pattern="^(builtin|zip)$")
    zip_upload_id: Optional[str] = None


class DeployStartRequest(BaseModel):
    image: ImageSource
    model_mount: str = Field(min_length=1)          # 映射进容器的模型(父)目录
    container_name: str = Field(min_length=1)       # 用户自定义, aisbench_ 前缀
    work_dir: str = "/tmp/AISBench_Prefix_Tools"
    code: CodeSource = Field(default_factory=lambda: CodeSource(source="builtin"))
    clean_work_dir: bool = False


class RemoteTarCheckRequest(BaseModel):
    remote_path: str = Field(min_length=1)


# ------------------------------------------------------------ 配置

class ConfigPayload(BaseModel):
    config: Dict[str, str]
    container: Optional[str] = None


# ------------------------------------------------------------ 测试用例

class DesignerParams(BaseModel):
    single_kv_cache: int = Field(ge=0)
    dp: int = Field(default=1, ge=1)
    max_request_length: int = Field(ge=0)
    repeat_rate: float = Field(default=0.9, ge=0.0, le=1.0)
    request_rate: float = Field(default=0.0, ge=0.0)


class DesignerGenerateRequest(DesignerParams):
    input_lengths: List[int] = Field(default_factory=list)
    output_lengths: List[int] = Field(default_factory=list)


class DesignerCase(BaseModel):
    input_len: int = Field(ge=1)
    output_len: int = Field(ge=1)
    data_num_recommended: int = Field(ge=1)
    data_num_min: Optional[int] = None
    concurrency_recommended: int = Field(ge=1)
    concurrency_max: Optional[int] = None
    repeat_rate: Optional[float] = None
    dp: Optional[int] = None
    is_custom: bool = False


class DesignerCasesRequest(DesignerParams):
    cases: List[DesignerCase] = Field(min_length=1)


class DesignerScriptRequest(DesignerCasesRequest):
    pass


# ------------------------------------------------------------ 执行

class ExecuteStartRequest(BaseModel):
    designer: Dict
    run_name: Optional[str] = None
    container: Optional[str] = None


# ------------------------------------------------------------ 清理

class CleanupRequest(BaseModel):
    """清理: 显式指定要删除的容器名(仅允许 aisbench 前缀), 绝不模糊删除"""
    containers: List[str] = Field(default_factory=list)
    clean_work_dir: bool = True
    work_dir: Optional[str] = None


# ------------------------------------------------------------ 日志工具

class LogtoolsConnectRequest(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    password: Optional[str] = None
    key_upload_id: Optional[str] = None
    timeout: int = Field(default=10, ge=1, le=120)


class LogtoolsGrepRequest(BaseModel):
    server: str = Field(default="session", pattern="^(session|other)$")
    mode: str = Field(pattern="^(host|container|docker_logs)$")
    path: Optional[str] = None
    container: Optional[str] = None


class LogtoolsViewRequest(LogtoolsGrepRequest):
    pass


# ------------------------------------------------------------ 结果对比

class CompareSource(BaseModel):
    type: str = Field(pattern="^(upload|result)$")
    upload_id: Optional[str] = None
    run_id: Optional[str] = None
    file: Optional[str] = None


class CompareLoadRequest(BaseModel):
    a: CompareSource
    b: CompareSource


class CompareComputeRequest(CompareLoadRequest):
    metrics: List[str] = Field(default_factory=list)
    match_indices: Optional[List[int]] = None  # None = 全部


class CompareExportRequest(CompareComputeRequest):
    pass

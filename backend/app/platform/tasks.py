"""平台任务存储 —— 测试任务是面向用户的核心对象

容器概念被完全隐藏:
- 任务记录里虽有内部执行信息(container/work_dir/token), 但 public()
  视图绝不向外暴露 —— 用户只看到 任务/状态/结果;
- 结果落在 data/results/<task_id>/, 与既有结果/对比体系天然互通。
"""

import json
import re
import secrets
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# 任务尚未终结的状态(服务重启时需要中断恢复)
ACTIVE_STATUSES = ("queued", "preparing", "running", "collecting")

STATUS_LABELS = {
    "queued": "排队中",
    "preparing": "准备执行环境",
    "running": "执行测试",
    "collecting": "收集结果",
    "done": "已完成",
    "error": "失败",
    "cancelled": "已取消",
    "interrupted": "已中断",
}

# 结果行展示字段契约(v3 前端结果表): 解析层沿用桌面版 CSV 字段名
# (TTFT_avg / total_req / E2E_throughput 等大写/业务名), 而任务详情 API
# 对外统一为前端小写驼峰契约。旧版本已持久化的原始行在 public() 视图
# 层做读时归一化, 升级后历史任务的结果表同样能正常显示。
_RESULT_FIELD_MAP = {
    "TTFT_avg": "ttft_avg",
    "TPOT_avg": "tpot_avg",
    "E2EL_avg": "e2e_avg",
    "total_req": "data_num",
    "E2E_throughput": "total_throughput",
}


def _normalize_result_rows(result: dict, designer: dict) -> list:
    """把解析层 CSV 字段行归一化为前端结果表字段"""
    rows = result.get("rows") or []
    if not isinstance(rows, list):
        return []

    # 用例设计中的重复率: 按(input_len, output_len)关联到结果行,
    # 日志本身不产出该字段, 结果表从任务设计回填。
    repeat_by_case = {}
    for case in (designer or {}).get("cases") or []:
        try:
            repeat_by_case[
                (int(case["input_len"]), int(case["output_len"]))
            ] = case.get("repeat_rate")
        except (KeyError, TypeError, ValueError):
            continue

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out = dict(row)  # 未映射字段原样透传(对比/下载等不受影响)
        for src, dst in _RESULT_FIELD_MAP.items():
            if src in row:
                out[dst] = row[src]
        # 并发: 优先取实测 cc, 缺失时回退设计并发 max_cc
        cc = row.get("cc")
        if cc in (None, ""):
            cc = row.get("max_cc")
        out["concurrency"] = cc if cc not in (None, "") else row.get("concurrency", "")
        repeat = repeat_by_case.get((row.get("input_len"), row.get("output_len")))
        if repeat is not None:
            out["repeat_rate"] = repeat
        normalized.append(out)
    return normalized


@dataclass
class PlatformTask:
    """一个测试任务(用户提交的最小工作单元)"""

    id: str
    name: str = ""
    owner: str = ""
    token: str = ""            # 创建者会话(内部, 不对外暴露)
    image: str = ""
    config: dict = field(default_factory=dict)   # 模型/服务配置
    designer: dict = field(default_factory=dict)  # 测试用例设计 payload
    status: str = "queued"
    phase: str = ""
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: str = ""
    ended_at: str = ""
    result: dict = field(default_factory=dict)
    # ---- 内部执行信息(不进入 public 视图) ----
    container: str = ""
    work_dir: str = ""

    @property
    def log_channel(self) -> str:
        return f"task@{self.id}"

    def to_record(self) -> dict:
        return {
            "id": self.id, "name": self.name, "owner": self.owner,
            "token": self.token, "image": self.image,
            "config": self.config, "designer": self.designer,
            "status": self.status, "phase": self.phase, "error": self.error,
            "created_at": self.created_at, "started_at": self.started_at,
            "ended_at": self.ended_at, "result": self.result,
            "container": self.container, "work_dir": self.work_dir,
        }

    @classmethod
    def from_record(cls, d: dict) -> "PlatformTask":
        return cls(
            id=d["id"], name=d.get("name", ""), owner=d.get("owner", ""),
            token=d.get("token", ""), image=d.get("image", ""),
            config=d.get("config") or {}, designer=d.get("designer") or {},
            status=d.get("status", "queued"), phase=d.get("phase", ""),
            error=d.get("error", ""),
            created_at=d.get("created_at", ""),
            started_at=d.get("started_at", ""),
            ended_at=d.get("ended_at", ""),
            result=d.get("result") or {},
            container=d.get("container", ""), work_dir=d.get("work_dir", ""),
        )

    def public(self, detail: bool = False, queue_position: int = 0) -> dict:
        """对外视图: 任务状态与结果 —— 不含 container/token/work_dir

        detail=True 时附带完整结果行(任务详情页结果表)。
        """
        result = self.result or {}
        out = {
            "id": self.id,
            "name": self.name or self.id,
            "owner": self.owner,
            "image": self.image,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "phase": self.phase,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "queue_position": queue_position if self.status == "queued" else 0,
            "result_summary": {
                "csv_file": result.get("csv_file", ""),
                "row_count": result.get("row_count", 0),
            },
        }
        if detail:
            result_view = dict(result)
            result_view["rows"] = _normalize_result_rows(result, self.designer)
            out["result"] = result_view
        return out


def make_task_id() -> str:
    """任务 ID: t + 时间戳 + 随机后缀, 全局唯一且可作为结果 run_id"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"t{stamp}{secrets.token_hex(3)}"


class TaskStore:
    """任务持久化: data/platform/tasks/<task_id>/task.json"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / "tasks"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 基础操作

    def create(self, **kwargs) -> PlatformTask:
        task = PlatformTask(id=make_task_id(), **kwargs)
        self.save(task)
        return task

    def save(self, task: PlatformTask):
        with self._lock:
            d = self.dir / task.id
            d.mkdir(parents=True, exist_ok=True)
            (d / "task.json").write_text(
                json.dumps(task.to_record(), ensure_ascii=False, indent=2),
                encoding="utf-8")

    def get(self, task_id: str) -> Optional[PlatformTask]:
        task_id = (task_id or "").strip()
        if not re.fullmatch(r"t[0-9a-f]+", task_id):
            return None
        f = self.dir / task_id / "task.json"
        if not f.is_file():
            return None
        try:
            return PlatformTask.from_record(
                json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            return None

    def list(self) -> List[PlatformTask]:
        """全部任务(按创建时间倒序) —— 平台共享视角: 每个用户都能看到机器上的任务"""
        tasks = []
        if self.dir.is_dir():
            for d in self.dir.iterdir():
                if not d.is_dir():
                    continue
                t = self.get(d.name)
                if t:
                    tasks.append(t)
        tasks.sort(key=lambda t: (t.created_at, t.id), reverse=True)
        return tasks

    def delete(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task:
            return False
        with self._lock:
            shutil.rmtree(self.dir / task_id, ignore_errors=True)
        return True

    # ------------------------------------------------------------ 启动恢复

    def recover_on_startup(self) -> int:
        """服务重启恢复: 上次未终结的任务标记为 interrupted(不会永久卡死)"""
        count = 0
        now = datetime.now().isoformat(timespec="seconds")
        for task in self.list():
            if task.status in ACTIVE_STATUSES:
                task.status = "interrupted"
                task.phase = ""
                task.ended_at = now
                task.error = "服务重启导致任务中断, 可重新提交"
                self.save(task)
                count += 1
        return count

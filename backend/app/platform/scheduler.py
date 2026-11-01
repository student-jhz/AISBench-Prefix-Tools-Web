"""任务调度器 —— 每个任务独立线程并行执行, 容器完全对用户不可见

执行模型(v1.0):
- 每个任务在执行时自动创建一个内部容器 aisbench_task_<id>(用后即删),
  用户全程只与"任务"交互;
- 任务并行执行, 不设最大并发数: 压测容器本身不占用 GPU/显存,
   只向推理服务发请求; 每个任务使用独立容器 aisbench_task_<id> 与独立
   工作目录 /tmp/AISBench_Platform/<task_id>, 任务之间互不干扰;
- 任务通过创建者的 SSH 会话在执行机上执行(SSH 连接多通道复用,
   取消任务时只中断该任务自己的远程命令);
- 登录时顺带清理孤儿任务容器(服务中断遗留的 aisbench_task_*)。
"""

import threading
from datetime import datetime
from pathlib import Path

from ..core import config_manager as cm
from ..core.docker_manager import DockerManager
from ..core.log_parser import parse_log_directory
from ..core.test_designer import TestDesigner
from ..logbus import LogBus
from .sessions import SessionRegistry
from .tasks import ACTIVE_STATUSES, PlatformTask, TaskStore

CODE_MOUNT_PATH = "/benchmark/ais_bench/aisbench_auto_tools_prefix-main"
TASK_CONTAINER_PREFIX = "aisbench_task_"
WORK_DIR_BASE = "/tmp/AISBench_Platform"


class TaskScheduler:
    """后台任务队列: 提交 → 排队 → 依次执行 → 结果入库"""

    def __init__(self, bus: LogBus, store: TaskStore,
                 sessions: SessionRegistry, results_dir: Path,
                 core_code_dir: Path):
        self.bus = bus
        self.store = store
        self.sessions = sessions
        self.results_dir = Path(results_dir)
        self.core_code_dir = Path(core_code_dir)
        self._lock = threading.Lock()
        self._cancel_events: dict = {}   # task_id -> Event
        # 登录后清理孤儿任务容器(上次服务中断可能遗留)
        sessions.add_login_hook(self._cleanup_orphans_async)

    # ------------------------------------------------------------ 生命周期

    def start(self):
        """启动时恢复上次未终结任务(执行线程按提交创建, 无需常驻 worker)"""
        recovered = self.store.recover_on_startup()
        if recovered:
            self._log("platform",
                      f"服务重启恢复: {recovered} 个未完成任务标记为已中断\n")

    def submit(self, task: PlatformTask):
        """新任务立即启动专属执行线程(并行执行, 不排队)"""
        self._cancel_events[task.id] = threading.Event()
        threading.Thread(target=self._run_task, args=(task,), daemon=True,
                         name=f"platform-task-{task.id}").start()

    def cancel(self, task_id: str) -> bool:
        """取消任务: 排队中直接取消; 执行中中断当前命令后收尾"""
        task = self.store.get(task_id)
        if not task or task.status not in ACTIVE_STATUSES:
            return False
        with self._lock:
            ev = self._cancel_events.get(task_id)
            if ev:
                ev.set()
        if task.status == "queued":
            task.status = "cancelled"
            task.ended_at = datetime.now().isoformat(timespec="seconds")
            task.error = "已取消"
            self.store.save(task)
            return True
        session = self.sessions.get(task.token)
        if session:
            try:
                # 只中断该任务自己的远程命令; 同连接上其他并行任务不受影响
                session.ssh.cancel_active(owner=task.id)
            except Exception:  # noqa: BLE001
                pass
        return True

    def is_active(self, task_id: str) -> bool:
        task = self.store.get(task_id)
        return bool(task and task.status in ACTIVE_STATUSES)

    # ------------------------------------------------------------ 任务线程

    def _run_task(self, task: PlatformTask):
        """单个任务的执行线程(每个任务一个, 互不阻塞)"""
        try:
            self._execute(task)
        except _TaskCancelled:
            self._transition(task, "cancelled", error="已取消")
            self._log(task.log_channel, "\n✹ 任务已取消\n")
        except Exception as e:  # noqa: BLE001 - 任何异常都终结任务
            self._transition(task, "error", error=str(e))
            self._log(task.log_channel, f"\n✗ 任务失败: {e}\n")
        finally:
            with self._lock:
                self._cancel_events.pop(task.id, None)
            self._cleanup_task_env(task)

    def _execute(self, task: PlatformTask):
        log = self.bus.channel_logger(task.log_channel)
        log(f"任务 {task.name or task.id} 开始执行\n"
            f"  提交人: {task.owner}\n  镜像: {task.image}\n\n")

        session = self.sessions.get(task.token)
        if not session or not session.ssh.connected:
            raise RuntimeError("任务创建者的登录会话已断开, 请重新登录后再提交任务")

        docker = self._docker_for(task, session)
        self._transition(task, "preparing", phase="准备执行环境",
                         started_at=datetime.now().isoformat(timespec="seconds"))

        # [1/6] 上传测试代码
        self._check_cancel(task)
        self._phase(task, "上传测试代码")
        log("[1/6] 上传测试代码...\n")
        ok, msg = docker.upload_directory(str(self.core_code_dir),
                                          docker.code_host_path, callback=log)
        if not ok:
            raise RuntimeError(f"测试代码上传失败: {msg}")

        # [2/6] 创建执行容器(内部资源, 对用户隐藏)
        self._check_cancel(task)
        self._phase(task, "创建执行环境")
        log("\n[2/6] 准备执行环境...\n")
        ok, msg = docker.create_container(
            task.image, self._model_mount(task), docker.code_host_path,
            task.container, callback=log)
        if not ok:
            raise RuntimeError(msg)

        # [3/6] 写入测试配置
        self._check_cancel(task)
        self._phase(task, "写入测试配置")
        log("\n[3/6] 写入 config.py...\n")
        errors = cm.validate_config(task.config)
        if errors:
            raise RuntimeError("配置验证失败: " + "; ".join(errors))
        content = cm.generate_config_content(task.config, docker.code_mount_path)
        if not cm.write_config_to_host(docker.ssh, docker.code_host_path, content):
            raise RuntimeError("写入 config.py 失败")
        self._check_model_path(docker, task, log)

        # [4/6] 生成并执行测试脚本
        self._transition(task, "running", phase="执行测试")
        self._check_cancel(task)
        log("\n[4/6] 生成并执行测试脚本...\n")
        designer = TestDesigner.from_payload(task.designer)
        err = designer.validate_case_lengths()
        if err:
            raise RuntimeError(err)
        if not designer.test_cases:
            raise RuntimeError("测试任务缺少测试用例")
        script_content, log_dir_name = designer.generate_shell_script()
        docker.write_script(script_content, "run_tests.sh")
        log("  开始执行测试 (这可能需要较长时间)...\n\n")
        exit_code = docker.run_script("run_tests.sh", callback=log, timeout=7200)
        self._check_cancel(task)
        if exit_code != 0:
            log(f"\n  ⚠ 部分测试失败 (exit_code={exit_code}), 继续收集日志...\n")

        # [5/6] 下载日志
        self._transition(task, "collecting", phase="收集结果")
        log("\n[5/6] 下载测试日志...\n")
        run_dir = self.results_dir / task.id
        run_dir.mkdir(parents=True, exist_ok=True)
        remote_log_dir = docker.find_latest_log_dir() \
            or f"{docker.code_host_path}/{log_dir_name}"
        local_log_dir = run_dir / log_dir_name
        local_log_dir.mkdir(parents=True, exist_ok=True)
        local_files = docker.download_logs(remote_log_dir, str(local_log_dir),
                                           callback=log)
        extra = docker.download_extra_files(
            ["aisbench_all.log", "aisbench_result.csv"],
            str(local_log_dir), callback=log)
        if not local_files:
            raise RuntimeError("未下载到任何日志文件")

        # [6/6] 解析结果
        log("\n[6/6] 解析日志生成结果...\n")
        csv_path = run_dir / f"results_{log_dir_name}.csv"
        results = parse_log_directory(
            str(local_log_dir), str(csv_path),
            progress_callback=lambda m, ok, row: log(m + "\n"))
        task.result = {
            "run_id": task.id,
            "log_dir_name": log_dir_name,
            "csv_file": csv_path.name if results else "",
            "rows": results,
            "row_count": len(results),
        }
        if not results:
            self._transition(task, "error",
                             error="测试已完成但未提取到有效结果, 请查看执行日志")
            log("  ✗ 未提取到有效结果\n")
        else:
            log(f"  ✓ 共提取 {len(results)} 条结果\n")
            self._transition(task, "done")
            log(f"\n✓ 任务完成! 结果 run_id: {task.id}\n")

    # ------------------------------------------------------------ 内部工具

    def _docker_for(self, task: PlatformTask, session) -> DockerManager:
        """构造指向该任务的 DockerManager(内部容器名/路径)"""
        task.container = f"{TASK_CONTAINER_PREFIX}{task.id[1:]}"
        task.work_dir = f"{WORK_DIR_BASE}/{task.id}"
        code_host = f"{task.work_dir}/aisbench_auto_tools_prefix-main"
        docker = DockerManager(session.ssh)
        docker.channel_owner = task.id   # 并行任务的取消隔离
        docker.container_name = task.container
        docker.image_name = task.image
        docker.code_host_path = code_host
        docker.code_mount_path = CODE_MOUNT_PATH
        docker.remote_work_base = task.work_dir
        self.store.save(task)
        return docker

    @staticmethod
    def _model_mount(task: PlatformTask) -> str:
        """模型目录: 挂载进容器同路径(MODEL_PATH 即用户配置的路径)"""
        return (task.config.get("model_path") or "").strip()

    def _check_model_path(self, docker: DockerManager, task: PlatformTask, log):
        """容器内校验 MODEL_PATH(存在且含 config.json), 失败给出明确指引"""
        check_script = (
            'import config, os, sys\n'
            'p = config.MODEL_PATH\n'
            'if not os.path.isdir(p):\n'
            '    print(f"FAIL: MODEL_PATH不存在: {p}")\n'
            '    sys.exit(1)\n'
            'if not os.path.isfile(os.path.join(p, "config.json")):\n'
            '    print(f"FAIL: {p}/config.json 不存在")\n'
            '    sys.exit(1)\n'
            'print(f"OK: MODEL_PATH={p}")\n'
        )
        host_path = f"{docker.code_host_path}/_check_model_path.py"
        if not docker.ssh.write_file(host_path, check_script):
            raise RuntimeError("写入模型路径预检查脚本失败")
        code, out, err = docker.ssh.execute(
            f"docker exec -w {docker.code_mount_path} {docker.container_name} "
            f"python3 _check_model_path.py")
        if code != 0:
            raise RuntimeError(
                f"MODEL_PATH 预检查失败: {(out or err).strip() or '执行失败'}")
        log(f"  ✓ {(out or '').strip()}\n")

    def _transition(self, task: PlatformTask, status: str, **fields):
        task.status = status
        for k, v in fields.items():
            setattr(task, k, v)
        if status in ("done", "error", "cancelled", "interrupted"):
            task.ended_at = task.ended_at or datetime.now().isoformat(timespec="seconds")
        self.store.save(task)

    def _phase(self, task: PlatformTask, phase: str):
        task.phase = phase
        self.store.save(task)

    def _check_cancel(self, task: PlatformTask):
        with self._lock:
            ev = self._cancel_events.get(task.id)
        if ev and ev.is_set():
            raise _TaskCancelled()

    def _cleanup_task_env(self, task: PlatformTask):
        """任务结束后清理内部容器与远程工作目录(用户无感知)"""
        session = self.sessions.get(task.token)
        if not session:
            return
        try:
            docker = DockerManager(session.ssh)
            if task.container:
                docker.remove_container(task.container)
            if task.work_dir:
                docker.ssh.execute(f"rm -rf {task.work_dir}")
        except Exception:  # noqa: BLE001 - 清理失败不影响任务结果
            pass

    def _cleanup_orphans_async(self, session):
        """登录时清理孤儿任务容器(仅 aisbench_task_ 前缀, 绝不动其他容器)"""
        def _worker():
            try:
                docker = DockerManager(session.ssh)
                for item in docker.list_containers(
                        prefixes=(TASK_CONTAINER_PREFIX,)):
                    task_id = "t" + item["name"][len(TASK_CONTAINER_PREFIX):]
                    if self.is_active(task_id):
                        continue
                    docker.remove_container(item["name"])
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=_worker, daemon=True,
                         name="platform-orphan-cleanup").start()

    def _log(self, channel: str, text: str):
        try:
            self.bus.append(channel, text)
        except Exception:  # noqa: BLE001
            pass


class _TaskCancelled(Exception):
    """任务被用户取消"""

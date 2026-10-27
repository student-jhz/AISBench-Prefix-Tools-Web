"""后台任务管理 - 容器创建/测试执行/清理三类长任务

v2.0: 多用户容器资源池模型
- deploy:   创建新容器(镜像可来自执行机已有镜像/tar上传/远程tar)
- execute:  按容器名分槽并发(不同容器可同时执行测试, 日志通道 exec@容器名)
- cleanup:  按显式指定的容器名列表清理, 绝不模糊删除
"""

import os
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .core.docker_manager import (
    DockerManager,
    is_managed_container_name,
    validate_container_name,
)
from .core.log_parser import parse_log_directory
from .core.test_designer import TestDesigner
from .logbus import LogBus
from .state import AppState


TASK_KINDS = ("deploy", "execute", "cleanup")


@dataclass
class TaskRun:
    """一次后台任务执行"""
    kind: str
    key: str = ""   # execute 任务: 容器名
    status: str = "running"  # running / done / error / cancelled
    phase: str = ""
    error: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    ended_at: str = ""
    result: dict = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "key": self.key,
            "running": self.status == "running",
            "status": self.status,
            "phase": self.phase,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "result": self.result,
        }


class TaskCancelled(Exception):
    """任务被取消"""


class TaskManager:
    """管理三类后台任务; execute 按容器分槽, 不同容器可并发执行"""

    def __init__(self, bus: LogBus, state: AppState):
        self.bus = bus
        self.state = state
        self._tasks: dict = {kind: None for kind in TASK_KINDS}
        # execute 任务按容器名分槽: 容器 -> TaskRun
        self._exec_tasks: dict = {}
        self._last_exec: Optional[TaskRun] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 任务控制

    def status(self, kind: str) -> dict:
        if kind == "execute":
            return self.exec_status()
        with self._lock:
            task = self._tasks.get(kind)
        if task is None:
            return {"kind": kind, "running": False, "status": "idle",
                    "phase": "", "error": "", "result": {}}
        return task.to_dict()

    def is_running(self, kind: str) -> bool:
        if kind == "execute":
            with self._lock:
                return any(t.status == "running"
                           for t in self._exec_tasks.values())
        return self.status(kind)["running"]

    def cancel(self, kind: str) -> bool:
        if kind == "execute":
            return self.cancel_execute()
        with self._lock:
            task = self._tasks.get(kind)
        if task is None or task.status != "running":
            return False
        task.cancel_event.set()
        # 主动中断正在执行的远程命令
        self.state.ssh.cancel_active()
        return True

    # ------------------------------------------------------------ 执行任务(按容器分槽)

    def exec_status(self, container: str = "") -> dict:
        """执行任务状态: 指定容器返回该容器任务, 否则返回最近一次"""
        with self._lock:
            task = (self._exec_tasks.get(container)
                    if container else self._last_exec)
            running_containers = sorted(
                name for name, t in self._exec_tasks.items()
                if t.status == "running")
        if task is None:
            return {"kind": "execute", "key": container, "running": False,
                    "status": "idle", "phase": "", "error": "", "result": {},
                    "running_containers": running_containers}
        data = task.to_dict()
        data["running_containers"] = running_containers
        return data

    def is_executing(self, container: str) -> bool:
        with self._lock:
            task = self._exec_tasks.get(container)
            return bool(task and task.status == "running")

    def cancel_execute(self, container: str = "") -> bool:
        with self._lock:
            task = (self._exec_tasks.get(container)
                    if container else self._last_exec)
        if task is None or task.status != "running":
            return False
        task.cancel_event.set()
        self.state.ssh.cancel_active()
        return True

    def _start(self, kind: str, target: Callable[[TaskRun], None]) -> TaskRun:
        with self._lock:
            existing = self._tasks.get(kind)
            if existing and existing.status == "running":
                raise RuntimeError(f"{kind} 任务正在运行中")
            task = TaskRun(kind=kind)
            self._tasks[kind] = task

        def wrapper():
            try:
                target(task)
                if task.status == "running":
                    task.status = "done"
            except TaskCancelled:
                task.status = "cancelled"
                task.error = "已取消"
            except Exception as e:  # noqa: BLE001
                task.status = "error"
                task.error = f"{e}"
                traceback.print_exc()
            finally:
                task.ended_at = datetime.now().isoformat(timespec="seconds")

        task.thread = threading.Thread(target=wrapper, daemon=True,
                                       name=f"task-{kind}")
        task.thread.start()
        return task

    def _check_cancel(self, task: TaskRun, channel: str):
        if task.cancel_event.is_set():
            self.bus.append(channel, "  ⋹ 已取消\n")
            raise TaskCancelled()

    # ------------------------------------------------------------ 部署

    def start_deploy(self, params: dict, core_code_dir: Path) -> TaskRun:
        return self._start("deploy",
                           lambda task: self._run_deploy(task, params, core_code_dir))

    def _run_deploy(self, task: TaskRun, params: dict, core_code_dir: Path):
        state = self.state
        bus = self.bus
        log = bus.channel_logger("deploy")
        ch = "deploy"

        ssh = state.require_ssh()

        model_mount = params["model_mount"].strip()
        container_name = params["container_name"].strip()
        work_dir = params["work_dir"].strip() or "/tmp/AISBench_Prefix_Tools"
        image = params.get("image") or {}
        code = params.get("code") or {}

        docker = DockerManager(ssh)
        docker.remote_work_base = work_dir
        state.docker = docker

        # ===== [1/5] 检查Docker环境 =====
        task.phase = "检查Docker环境"
        log("[1/5] 检查Docker环境...\n")
        ok, msg = docker.check_docker_installed()
        if not ok:
            raise RuntimeError(msg)
        log(f"  ✓ {msg}\n\n")
        self._check_cancel(task, ch)

        # ===== [2/5] 准备镜像: 已有镜像直接使用, 缺失才上传/加载 =====
        task.phase = "准备镜像"
        log("[2/5] 准备镜像...\n")
        ssh.mkdir_p(work_dir)

        image_name = None
        local_tar_path = None
        remote_tar_path = ""
        if image.get("type") == "existing":
            image_name = (image.get("name") or "").strip()
            if not image_name:
                raise RuntimeError("未指定镜像名")
            log(f"  使用执行机已有镜像: {image_name}\n")
            if not docker.image_exists(image_name):
                raise RuntimeError(
                    f"镜像不存在: {image_name}\n"
                    f"  请刷新镜像列表重新选择, 或切换为\"上传tar包/远程tar包\"加载")
            log("  ✓ 镜像已就绪\n\n")
            self._check_cancel(task, ch)
        elif image.get("type") == "remote":
            remote_tar = image.get("remote_path", "").strip()
            if not remote_tar:
                raise RuntimeError("未提供远程镜像 tar 路径")
            log(f"  远程镜像包: {remote_tar}\n")
            if not ssh.file_exists(remote_tar):
                raise RuntimeError(f"远程镜像包不存在: {remote_tar}")
            image_name = docker.get_image_name_from_remote_tar(remote_tar)
            if image_name:
                log(f"  从 tar 包识别到镜像: {image_name}\n")
            remote_tar_path = remote_tar
        else:
            upload_id = image.get("upload_id", "")
            local_tar_path = _resolve_upload(state, upload_id)
            if not local_tar_path:
                raise RuntimeError("未提供镜像 tar 包")
            log(f"  镜像包: {os.path.basename(local_tar_path)}\n")
            image_name = DockerManager.get_image_name_from_tar(local_tar_path)
            if image_name:
                log(f"  从 tar 包 manifest 识别到镜像: {image_name}\n")
            remote_tar_path = f"{work_dir}/{os.path.basename(local_tar_path)}"

        if image.get("type") == "existing":
            pass  # 已有镜像, 上面已校验
        elif image_name and docker.image_exists(image_name):
            log(f"  ✓ 镜像 {image_name} 已存在, 跳过上传与加载\n\n")
        else:
            if image_name:
                log(f"  镜像 {image_name} 不存在, 需要上传并加载\n")
            else:
                log("  无法从tar包预读镜像名, 将上传后由 docker load 识别\n")

            if image.get("type") == "remote":
                # 远程包直接加载
                log("  加载Docker镜像...\n")
                ok, image_name = docker.load_image(remote_tar_path, callback=log)
                if not ok:
                    raise RuntimeError(f"镜像加载失败: {image_name}")
            else:
                tar_size = os.path.getsize(local_tar_path)
                log(f"  上传镜像tar包 ({tar_size // 1024 // 1024}MB)...\n")

                def _upload_progress(transferred, total):
                    pct = transferred * 100 // total if total > 0 else 0
                    log(f"\r  上传中: {transferred // 1024 // 1024}MB / "
                        f"{total // 1024 // 1024}MB ({pct}%)")

                ok = ssh.upload_file(local_tar_path, remote_tar_path, _upload_progress)
                log("\n")
                if not ok:
                    raise RuntimeError("镜像上传失败")
                log("  ✓ 镜像上传完成\n")

                log("  加载Docker镜像...\n")
                ok, image_name = docker.load_image(remote_tar_path, callback=log)
                if not ok:
                    raise RuntimeError(f"镜像加载失败: {image_name}")
            log("\n")

        self._check_cancel(task, ch)

        # ===== [3/5] 上传测试代码 =====
        task.phase = "上传测试代码"
        if code.get("source") == "zip":
            zip_path = _resolve_upload(state, code.get("zip_upload_id", ""))
            if not zip_path:
                raise RuntimeError("未提供代码 zip 包")
            log("[3/5] 上传并解压代码zip包...\n")
            zip_name = os.path.basename(zip_path)
            remote_zip = f"{work_dir}/{zip_name}"
            if not ssh.upload_file(zip_path, remote_zip):
                raise RuntimeError("代码包上传失败")
            ok, extracted_path = docker.extract_code_zip(
                remote_zip, work_dir, callback=log)
            if not ok:
                raise RuntimeError("代码解压失败")
        else:
            log("[3/5] 上传内置测试代码...\n")
            extracted_path = f"{work_dir}/aisbench_auto_tools_prefix-main"
            ok, msg = docker.upload_directory(str(core_code_dir), extracted_path,
                                              callback=log)
            if not ok:
                raise RuntimeError(f"代码上传失败: {msg}")

        code_host_path = extracted_path
        log(f"  ✓ 代码已就绪: {extracted_path}\n\n")
        self._check_cancel(task, ch)

        # ===== [4/5] 校验容器名 =====
        task.phase = "校验容器名"
        log(f"[4/5] 校验容器名: {container_name}\n")
        err = validate_container_name(container_name)
        if err:
            raise RuntimeError(err)
        if docker.container_exists(container_name):
            raise RuntimeError(
                f"容器名已被占用: {container_name}\n"
                f"  请换一个名称, 或先在\"容器管理\"中删除该容器")
        log("  ✓ 名称可用\n\n")
        self._check_cancel(task, ch)

        # ===== [5/5] 创建并启动容器 =====
        task.phase = "创建并启动容器"
        log("[5/5] 创建并启动容器...\n")
        log(f"  模型目录映射: {model_mount} -> {model_mount} (容器内同路径)\n")
        ok, msg = docker.create_container(
            image_name, model_mount, code_host_path, container_name, callback=log)
        if not ok:
            raise RuntimeError(msg)

        log(f"\n  ✓ 容器 {container_name} 已启动运行\n")
        log("  ✓ 容器创建完成! (后续步骤均可直接使用该容器)\n")

        # inspect 实际容器信息并登记到容器资源池
        info = docker.inspect_container(container_name) or {}
        info["work_dir"] = work_dir
        state.register_container(info, source="created")
        task.result = state.deploy_info.to_dict()

    # ------------------------------------------------------------ 执行测试

    def start_execute(self, designer_payload: dict, run_id: str,
                      container: str) -> TaskRun:
        """启动测试执行(按容器分槽, 不同容器可并发执行)"""
        with self._lock:
            existing = self._exec_tasks.get(container)
            if existing and existing.status == "running":
                raise RuntimeError(f"容器 {container} 已有测试任务正在执行")
            task = TaskRun(kind="execute", key=container)
            self._exec_tasks[container] = task
            self._last_exec = task

        def wrapper():
            try:
                self._run_execute(task, designer_payload, run_id, container)
                if task.status == "running":
                    task.status = "done"
            except TaskCancelled:
                task.status = "cancelled"
                task.error = "已取消"
            except Exception as e:  # noqa: BLE001
                task.status = "error"
                task.error = f"{e}"
                traceback.print_exc()
            finally:
                task.ended_at = datetime.now().isoformat(timespec="seconds")

        task.thread = threading.Thread(
            target=wrapper, daemon=True,
            name=f"task-execute-{container}")
        task.thread.start()
        return task

    def _run_execute(self, task: TaskRun, designer_payload: dict, run_id: str,
                     container: str):
        state = self.state
        bus = self.bus
        # 每个容器独立的日志通道, 多用户在不同容器执行测试时日志不串扰
        channel = f"exec@{container}"
        log = bus.channel_logger(channel)

        docker = state.docker_for_container(container)
        if not docker.container_is_running():
            raise RuntimeError(f"容器 {container} 未运行，请先启动容器")

        busy, busy_detail = docker.container_busy(container)
        if busy:
            raise RuntimeError(
                f"容器 {container} 正在执行测试，请等待完成或另选/新建容器\n"
                f"  检测到进程: {busy_detail}")

        designer = TestDesigner.from_payload(designer_payload)
        err = designer.validate_case_lengths()
        if err:
            raise RuntimeError(err)
        if not designer.test_cases:
            raise RuntimeError("请先生成测试用例")

        run_dir = state.results_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        log(f"执行容器: {container}  |  镜像: {docker.image_name or '-'}\n")

        # ===== 阶段0: 预检查模型路径 =====
        task.phase = "预检查MODEL_PATH"
        log("[0/4] 预检查: 验证容器内MODEL_PATH...\n")
        model_ok, model_msg = self._check_model_path_in_container(docker)
        if not model_ok:
            log(f"  ✗ {model_msg}\n")
            log("  请修正MODEL_PATH后重新保存config.py到容器，再重试\n")
            raise RuntimeError(f"MODEL_PATH预检查失败: {model_msg}")
        log(f"  ✓ {model_msg}\n\n")

        # ===== 阶段1: 生成.sh脚本 =====
        task.phase = "生成测试脚本"
        log("[1/4] 生成测试脚本...\n")
        script_content, log_dir_name = designer.generate_shell_script()
        log(f"  日志目录名: {log_dir_name}\n")

        # ===== 阶段2: 上传并运行脚本 =====
        task.phase = "执行测试脚本"
        log("\n[2/4] 上传脚本到容器并执行...\n")
        container_script = docker.write_script(script_content, "run_tests.sh")
        log(f"  脚本路径(容器内): {container_script}\n")
        log("  开始执行测试 (这可能需要较长时间)...\n\n")

        exit_code = docker.run_script("run_tests.sh", callback=log, timeout=7200)

        if task.cancel_event.is_set():
            raise TaskCancelled()

        if exit_code != 0:
            log(f"\n  ⚠ 部分测试失败 (exit_code={exit_code})，继续下载日志以便排查...\n")
        else:
            log("\n  ✓ 测试脚本执行完成\n")

        # ===== 阶段3: 下载日志 =====
        task.phase = "下载测试日志"
        log("\n[3/4] 下载测试日志到服务器...\n")
        remote_log_dir = docker.find_latest_log_dir()
        if not remote_log_dir:
            remote_log_dir = f"{docker.code_host_path}/{log_dir_name}"
        log(f"  远程日志目录: {remote_log_dir}\n")

        local_log_dir = run_dir / log_dir_name
        local_log_dir.mkdir(parents=True, exist_ok=True)

        local_log_files = docker.download_logs(remote_log_dir, str(local_log_dir),
                                               callback=log)
        extra_files = docker.download_extra_files(
            ["aisbench_all.log", "aisbench_result.csv"], str(local_log_dir),
            callback=log)
        if extra_files:
            log(f"  ✓ 已下载附加文件: "
                f"{', '.join(os.path.basename(p) for p in extra_files)}\n")

        if not local_log_files:
            raise RuntimeError("未下载到任何日志文件")

        log(f"  ✓ 已下载 {len(local_log_files)} 个日志文件到 {local_log_dir}\n")

        # ===== 阶段4: 解析日志生成CSV =====
        task.phase = "解析日志生成CSV"
        log("\n[4/4] 解析日志生成CSV结果...\n")
        csv_path = run_dir / f"results_{log_dir_name}.csv"

        def _progress(msg, ok, row):
            log(msg + "\n")

        results = parse_log_directory(str(local_log_dir), str(csv_path),
                                      progress_callback=_progress)

        if results:
            log(f"  ✓ 共提取 {len(results)} 条结果, CSV: {csv_path.name}\n")
            task.result = {
                "run_id": run_id,
                "log_dir_name": log_dir_name,
                "csv_file": csv_path.name,
                "rows": results,
                "row_count": len(results),
            }
        else:
            log("  ✗ 未能从日志中提取到有效结果\n")
            # 输出预检查日志帮助排查
            preflight = local_log_dir / "preflight.log"
            if preflight.exists():
                content = preflight.read_text(encoding='utf-8', errors='replace').strip()
                if content:
                    log(f"  预检查错误信息:\n{content}\n")
            task.result = {
                "run_id": run_id,
                "log_dir_name": log_dir_name,
                "csv_file": "",
                "rows": [],
                "row_count": 0,
            }
            task.status = "error"
            task.error = "测试已完成但未提取到有效结果，请检查执行日志"

    @staticmethod
    def _check_model_path_in_container(docker: DockerManager) -> tuple:
        """在容器内检查MODEL_PATH是否有效(路径存在且含config.json)"""
        check_script = (
            'import config, os, sys\n'
            'p = config.MODEL_PATH\n'
            'if not os.path.isdir(p):\n'
            '    print(f"FAIL: MODEL_PATH不存在: {p}")\n'
            '    print("请检查: 1)模型路径是否正确 2)Docker是否正确挂载了模型权重")\n'
            '    sys.exit(1)\n'
            'if not os.path.isfile(os.path.join(p, "config.json")):\n'
            '    print(f"FAIL: {p}/config.json 不存在")\n'
            '    print("模型权重可能未正确部署，Docker挂载可能创建了空目录")\n'
            '    sys.exit(1)\n'
            'print(f"OK: MODEL_PATH={p}")\n'
        )
        check_host_path = f"{docker.code_host_path}/_check_model_path.py"
        if not docker.ssh.write_file(check_host_path, check_script):
            return False, "无法写入预检查脚本到容器挂载目录"

        cmd = (f"docker exec -w {docker.code_mount_path} {docker.container_name} "
               f"python3 _check_model_path.py")
        code, out, err = docker.ssh.execute(cmd)
        docker.ssh.execute(f"rm -f {check_host_path}")

        out = out.strip()
        if code == 0 and "OK:" in out:
            return True, out
        return False, out if out else (err.strip() if err else "未知错误")

    # ------------------------------------------------------------ 清理

    def start_cleanup(self, containers: list, clean_work_dir: bool,
                      work_dir: str = "") -> TaskRun:
        return self._start(
            "cleanup",
            lambda task: self._run_cleanup(task, containers,
                                           clean_work_dir, work_dir))

    def _run_cleanup(self, task: TaskRun, containers: list,
                     clean_work_dir: bool, work_dir: str):
        state = self.state
        log = self.bus.channel_logger("deploy")

        log("\n===== 清理远程环境 =====\n")
        docker = DockerManager(state.require_ssh())

        removed, skipped = [], []
        for name in containers:
            name = (name or "").strip()
            if not name:
                continue
            if not is_managed_container_name(name):
                log(f"  ✗ 跳过 {name}: 非 aisbench 前缀容器, 拒绝清理\n")
                skipped.append(name)
                continue
            # 正在执行测试的容器不删除, 避免误伤其他用户的任务
            busy, detail = docker.container_busy(name)
            if busy:
                log(f"  ✗ 跳过 {name}: 正在执行测试 ({detail})\n")
                skipped.append(name)
                continue
            if self.is_executing(name):
                log(f"  ✗ 跳过 {name}: 本服务正在该容器执行测试\n")
                skipped.append(name)
                continue
            task.phase = f"删除容器 {name}"
            log(f"  停止并删除容器: {name}\n")
            docker.remove_container(name, callback=log)
            with state.lock:
                state.containers.pop(name, None)
            removed.append(name)

        if containers:
            log(f"  ✓ 已删除容器: {', '.join(removed) or '(无)'}\n")
            if skipped:
                log(f"  ⚠ 未清理: {', '.join(skipped)}\n")

        target_dir = work_dir or (docker.remote_work_base if docker else "")
        if clean_work_dir and target_dir:
            task.phase = f"清理工作目录 {target_dir}"
            cleaner = docker or DockerManager(state.require_ssh())
            cleaner.remote_work_base = target_dir
            log(f"  清理工作目录: {target_dir}\n")
            ok = cleaner.clean_work_dir(callback=log)
            if ok:
                log("  ✓ 工作目录已清理\n")
            else:
                log("  ✗ 工作目录清理失败\n")

        log("\n✓ 环境清理完成!\n")
        log("  服务器上的测试结果不受影响 (可在\"结果历史\"中下载)\n")

        # 当前容器被删除时重置部署信息
        if state.deploy_info and state.deploy_info.container_name in removed:
            state.reset_deploy()
        task.result = {"cleaned": True, "removed": removed, "skipped": skipped}


def _resolve_upload(state: AppState, upload_id: str) -> Optional[str]:
    """解析上传文件ID为本地路径(校验位于 uploads 目录内)"""
    if not upload_id:
        return None
    uploads = Path(state.uploads_dir).resolve()
    path = (uploads / upload_id).resolve()
    if not str(path).startswith(str(uploads)):
        return None
    if not path.is_file():
        return None
    return str(path)

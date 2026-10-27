"""API 集成测试 - FakeSSH 注入, 覆盖部署→配置→执行→结果→对比全链路"""

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from .fakessh import FakeSSH, make_image_tar


@pytest.fixture()
def client(tmp_path):
    ssh = FakeSSH()
    app = create_app(ssh_manager=ssh, data_dir=tmp_path, ssh_factory=FakeSSH)
    return TestClient(app), ssh, tmp_path


def wait_task(client, kind, timeout=10):
    """等待后台任务完成"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/{kind}/status" if kind != "cleanup" else "/api/state")
        status = r.json()["tasks"][kind] if kind == "cleanup" else r.json()
        if not status["running"]:
            return status
        time.sleep(0.05)
    raise TimeoutError(f"{kind} 任务超时")


def connect_ssh(client):
    r = client.post("/api/ssh/connect", json={
        "host": "10.0.0.1", "port": 22, "username": "root", "password": "pass",
    })
    assert r.status_code == 200, r.text
    return r.json()


class TestSystem:
    def test_health(self, client):
        c, _, _ = client
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_index_cache_busting(self, client):
        """首页: 版本注入 + no-cache 头, 防止升级后浏览器沿用旧入口/旧脚本混载"""
        c, _, _ = client
        r = c.get("/")
        assert r.status_code == 200
        # 入口页禁止直接使用缓存(每次协商), 升级后浏览器立即拿到新入口
        assert "no-cache" in r.headers.get("cache-control", "")
        body = r.text
        from app.version import __version__
        # 静态资源 URL 带版本号(缓存键), 新旧版本资源永不混用
        assert f"/static/js/main.js?v={__version__}" in body
        assert f"/static/css/app.css?v={__version__}" in body
        # 占位符必须被替换完毕
        assert "__APP_VERSION__" not in body
        # 加载失败兜底横幅存在(模块崩溃时可见提示而非空白页)
        assert 'id="bootError"' in body

    def test_state_no_ssh_roundtrip(self, client, tmp_path):
        """v2.0.2: /api/state 是高频轮询端点, 不做任何 SSH/docker 往返

        曾因容器运行状态走 docker ps / docker top(SSH 慢或长任务阻塞时
        耗时数秒~分钟), 前端首屏 init 被卡住, 启动自检误报"界面加载失败"。
        """
        c, ssh, _ = client
        # 建立已选择且运行中的容器
        connect_ssh(c)
        ssh.containers["aisbench_state_fast"] = "Up 1 hour"
        ssh.container_meta["aisbench_state_fast"] = {
            "image": "aisbench:v1",
            "binds": ["/mnt/model:/mnt/model"],
        }
        r = c.post("/api/containers/select",
                   json={"name": "aisbench_state_fast"})
        assert r.status_code == 200, r.text

        ssh.exec_log.clear()
        r = c.get("/api/state")
        assert r.status_code == 200
        data = r.json()
        # 运行状态来自容器注册表(选择/创建时登记), 无需 SSH
        assert data["container_running"] is True
        # 关键断言: 状态轮询零 SSH 命令
        assert ssh.exec_log == [], ssh.exec_log

        # busy 来自本服务执行分槽(同样零 SSH)
        assert data["container_busy"] is False

    def test_static_assets_served(self, client):
        c, _, _ = client
        for path in ("/static/js/main.js", "/static/css/app.css",
                     "/static/js/step2.js"):
            r = c.get(path)
            assert r.status_code == 200, path

    def test_state_initial(self, client):
        c, _, _ = client
        r = c.get("/api/state")
        data = r.json()
        assert data["ssh"]["connected"] is False
        assert data["deploy"] is None
        assert data["steps"]["ssh_connected"] is False


class TestSSH:
    def test_connect_success(self, client):
        c, ssh, _ = client
        data = connect_ssh(c)
        assert data["ok"] is True
        r = c.get("/api/ssh/status")
        data = r.json()
        assert data["connected"] is True
        assert data["host"] == "10.0.0.1"
        assert data["port"] == 22
        assert data["username"] == "root"
        # remembered: 上次连接参数(供前端表单回填, 不含密码)
        assert data["remembered"]["host"] == "10.0.0.1"
        assert data["remembered"]["username"] == "root"
        assert data["remembered"]["has_credential"] is True

    def test_connect_failure(self, client):
        c, _, _ = client
        r = c.post("/api/ssh/connect", json={
            "host": "10.0.0.1", "username": "root", "password": "wrong",
        })
        assert r.status_code == 400

    def test_connect_requires_credentials(self, client):
        c, _, _ = client
        r = c.post("/api/ssh/connect", json={"host": "h", "username": "u"})
        assert r.status_code == 400

    def test_fs_list(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        ssh.files["/mnt/models/ds/config.json"] = "{}"
        r = c.get("/api/fs/list", params={"path": "/mnt/models"})
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert entries[0]["name"] == "ds"
        assert entries[0]["type"] == "dir"

    def test_fs_list_requires_connection(self, client):
        c, _, _ = client
        r = c.get("/api/fs/list", params={"path": "/"})
        assert r.status_code == 400

    def test_disconnect(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        r = c.post("/api/ssh/disconnect")
        assert r.status_code == 200
        assert ssh.connected is False

    def test_session_persistence(self, client):
        """SSH连接参数持久化: 刷新/重启后表单可回填, 服务重启自动重连"""
        c, _, _ = client
        connect_ssh(c)
        state = c.app.state.app_state
        session = state.load_session()
        assert session["ssh"]["host"] == "10.0.0.1"
        assert session["ssh"]["username"] == "root"
        assert session["ssh"]["password"] == "pass"

        # 主动断开后清除持久化会话
        c.post("/api/ssh/disconnect")
        assert state.load_session() == {}
        r = c.get("/api/ssh/status")
        assert r.json()["remembered"] is None

    def test_session_restore(self, tmp_path):
        """服务重启后从 session.json 自动重连SSH并恢复部署信息"""
        (tmp_path / "session.json").write_text(json.dumps({
            "ssh": {"host": "10.0.0.1", "port": 22, "username": "root",
                    "password": "pass", "timeout": 10},
            "deploy": {"container_name": "aisbench-test-x",
                       "image_name": "img:latest",
                       "code_host_path": "/tmp/w/code",
                       "model_path": "/mnt/model",
                       "work_dir": "/tmp/w",
                       "deployed_at": "2026-01-01T00:00:00"},
        }), encoding="utf-8")
        ssh = FakeSSH()
        app = create_app(ssh_manager=ssh, data_dir=tmp_path, ssh_factory=FakeSSH)
        state = app.state.app_state
        for _ in range(100):  # 等待后台重连线程
            if ssh.connected:
                break
            time.sleep(0.05)
        assert ssh.connected is True
        assert ssh.host == "10.0.0.1"
        assert state.deploy_info is not None
        assert state.deploy_info.container_name == "aisbench-test-x"
        assert state.docker.container_name == "aisbench-test-x"


class TestUploads:
    def test_upload_image_tar(self, client, tmp_path):
        c, _, _ = client
        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path), "aisbench:v1")
        with open(tar_path, "rb") as f:
            r = c.post("/api/upload/image", files={"file": ("img.tar", f, "application/x-tar")})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["image_name"] == "aisbench:v1"
        assert data["size"] > 0

    def test_upload_code_zip(self, client):
        c, _, _ = client
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("aisbench_auto_tools_prefix-main/aisbench_test.py", "# test")
        buf.seek(0)
        r = c.post("/api/upload/code-zip", files={"file": ("code.zip", buf, "application/zip")})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_upload_code_zip_rejects_non_zip(self, client):
        c, _, _ = client
        r = c.post("/api/upload/code-zip", files={"file": ("code.txt", b"not a zip", "text/plain")})
        assert r.status_code == 400

    def test_upload_compare_csv(self, client, tmp_path):
        c, _, _ = client
        from app.core.log_parser import CSV_FIELDS
        import csv as csv_mod
        buf = io.StringIO()
        writer = csv_mod.DictWriter(buf, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow({f: "" for f in CSV_FIELDS} | {"input_len": 8192, "qps": 1.5})
        r = c.post("/api/upload/compare-csv",
                   files={"file": ("res.csv", buf.getvalue().encode(), "text/csv")})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["rows"] == 1

    def test_upload_compare_csv_invalid(self, client):
        c, _, _ = client
        r = c.post("/api/upload/compare-csv",
                   files={"file": ("bad.csv", b"a,b\n1,2\n", "text/csv")})
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "CSV格式" in r.json()["error"]

    def test_list_and_delete_uploads(self, client):
        c, _, _ = client
        r = c.post("/api/upload/code-zip",
                   files={"file": ("x.zip", b"PK", "application/zip")})
        upload_id = r.json()["id"]
        r = c.get("/api/uploads")
        assert any(i["id"] == upload_id for i in r.json()["items"])
        r = c.delete(f"/api/uploads/{upload_id}")
        assert r.status_code == 200
        r = c.get("/api/uploads")
        assert not any(i["id"] == upload_id for i in r.json()["items"])


class TestDesignerAPI:
    def test_presets(self, client):
        c, _, _ = client
        r = c.get("/api/designer/presets")
        assert r.status_code == 200
        assert 8192 in r.json()["input_lengths"]
        assert "grep" in r.json()["grep_command"]

    def test_generate(self, client):
        c, _, _ = client
        r = c.post("/api/designer/generate", json={
            "single_kv_cache": 198469, "dp": 4, "max_request_length": 131072,
            "input_lengths": [8192, 262144], "output_lengths": [512],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["skipped_inputs"] == [262144]
        assert "warning" in data
        assert len(data["cases"]) == 1
        case = data["cases"][0]
        assert case["input_len"] == 8192
        assert case["below_min"] is False
        assert case["kv_usage"] > 0

    def test_generate_invalid_kv(self, client):
        c, _, _ = client
        r = c.post("/api/designer/generate", json={
            "single_kv_cache": 0, "dp": 1, "max_request_length": 32768,
        })
        assert r.status_code == 400

    def test_cases_recompute(self, client):
        c, _, _ = client
        r = c.post("/api/designer/cases", json={
            "single_kv_cache": 198469, "dp": 4, "max_request_length": 131072,
            "repeat_rate": 0.9, "request_rate": 0,
            "cases": [{
                "input_len": 8192, "output_len": 512,
                "data_num_recommended": 5, "concurrency_recommended": 10,
            }],
        })
        assert r.status_code == 200
        case = r.json()["cases"][0]
        total_kv = 198469 * 4
        assert case["data_num_min"] == int(total_kv / 8192 / 0.9) + 1
        assert case["concurrency_max"] == int(total_kv / (8192 + 512))
        assert case["below_min"] is True  # 5 低于最小请求数

    def test_cases_rejects_over_length(self, client):
        c, _, _ = client
        r = c.post("/api/designer/cases", json={
            "single_kv_cache": 100000, "dp": 1, "max_request_length": 32768,
            "cases": [{"input_len": 32768, "output_len": 512,
                       "data_num_recommended": 10, "concurrency_recommended": 1}],
        })
        assert r.status_code == 400
        assert "超过模型最大上下文" in r.json()["detail"]

    def test_script(self, client):
        c, _, _ = client
        r = c.post("/api/designer/script", json={
            "single_kv_cache": 198469, "dp": 4, "max_request_length": 131072,
            "cases": [{"input_len": 8192, "output_len": 512,
                       "data_num_recommended": 216, "concurrency_recommended": 91}],
        })
        assert r.status_code == 200
        data = r.json()
        # -u: 无缓冲输出, 保证执行日志实时回传
        assert data["commands"][0].startswith("python3 -u aisbench_test.py")
        assert data["script"].startswith("#!/bin/bash")
        assert data["log_dir_name"].startswith("test_logs_")


class TestDeployFlow:
    def test_deploy_preflight(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        r = c.get("/api/deploy/preflight", params={"work_dir": "/tmp/AISBench_Prefix_Tools"})
        assert r.status_code == 200
        assert r.json() == {"work_dir": "/tmp/AISBench_Prefix_Tools",
                            "exists": False, "has_content": False, "entries": []}

    def test_deploy_full_flow(self, client, tmp_path):
        c, ssh, _ = client
        connect_ssh(c)

        # 上传镜像tar
        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path), "aisbench:v1")
        with open(tar_path, "rb") as f:
            upload = c.post("/api/upload/image",
                            files={"file": ("img.tar", f, "application/x-tar")}).json()

        # 创建容器(上传镜像 + 内置代码)
        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_mount": "/mnt/models/ds",
            "container_name": "aisbench_user1_vllm",
            "work_dir": "/tmp/AISBench_Prefix_Tools",
            "code": {"source": "builtin"},
        })
        assert r.status_code == 200, r.text

        status = wait_task(c, "deploy")
        assert status["status"] == "done", status
        result = status["result"]
        assert result["container_name"] == "aisbench_user1_vllm"
        assert result["image_name"] == "aisbench:test"
        assert result["model_path"] == "/mnt/models/ds"
        assert result["source"] == "created"

        # 状态聚合
        state = c.get("/api/state").json()
        assert state["steps"]["container_selected"] is True
        assert state["container_running"] is True

        # 内置代码已上传到远程
        assert any(p.startswith("/tmp/AISBench_Prefix_Tools/aisbench_auto_tools_prefix-main")
                   for p in ssh.files)

        # 部署信息已持久化(服务重启后可恢复)
        session = c.app.state.app_state.load_session()
        assert session["deploy"]["container_name"] == result["container_name"]
        assert session["deploy"]["model_path"] == "/mnt/models/ds"

    def test_deploy_requires_ssh(self, client):
        c, _, _ = client
        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": "x"},
            "model_mount": "/m", "container_name": "aisbench_x",
            "work_dir": "/tmp/x",
        })
        assert r.status_code == 400

    def test_deploy_invalid_container_name(self, client):
        """容器名必须 aisbench_ 前缀(多用户共享执行机的命名约定)"""
        c, _, _ = client
        connect_ssh(c)
        for bad in ["mybox", "aisbench-", "aisbench_", "aisbench_中文",
                    "aisbench_a$b", "AISBENCH_upper"]:
            r = c.post("/api/deploy/start", json={
                "image": {"type": "existing", "name": "aisbench:test"},
                "model_mount": "/mnt/models", "container_name": bad,
            })
            assert r.status_code == 400, bad

    def test_deploy_existing_image_no_upload(self, client):
        """选择执行机已有镜像: 无需上传/加载 tar 包"""
        c, ssh, _ = client
        connect_ssh(c)
        ssh.images.add("aisbench:v2")

        r = c.post("/api/deploy/start", json={
            "image": {"type": "existing", "name": "aisbench:v2"},
            "model_mount": "/mnt/models",
            "container_name": "aisbench_user2_existing",
            "work_dir": "/tmp/AISBench_Prefix_Tools",
        })
        assert r.status_code == 200, r.text
        status = wait_task(c, "deploy")
        assert status["status"] == "done", status
        # 没有任何 docker load 动作
        assert not any("docker load" in cmd for cmd in ssh.exec_log)

    def test_deploy_existing_image_missing(self, client):
        """所选镜像不存在时报错"""
        c, _, _ = client
        connect_ssh(c)
        r = c.post("/api/deploy/start", json={
            "image": {"type": "existing", "name": "aisbench:none"},
            "model_mount": "/mnt/models", "container_name": "aisbench_x1",
        })
        assert r.status_code == 200
        status = wait_task(c, "deploy")
        assert status["status"] == "error"
        assert "镜像不存在" in status["error"]

    def test_deploy_container_name_conflict(self, client, tmp_path):
        """同名容器冲突时报错, 绝不覆盖删除他人容器"""
        c, ssh, _ = client
        connect_ssh(c)
        ssh.containers["aisbench_shared_box"] = "Up 1 hour"
        ssh.container_meta["aisbench_shared_box"] = {
            "image": "other:latest", "binds": []}

        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path))
        with open(tar_path, "rb") as f:
            upload = c.post("/api/upload/image",
                            files={"file": ("img.tar", f, "application/x-tar")}).json()
        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_mount": "/mnt/models/ds",
            "container_name": "aisbench_shared_box",
        })
        assert r.status_code == 200
        status = wait_task(c, "deploy")
        assert status["status"] == "error"
        assert "已被占用" in status["error"]
        # 原容器未被删除
        assert "aisbench_shared_box" in ssh.containers

    def test_deploy_image_dedup(self, client, tmp_path):
        """镜像已存在时跳过上传加载"""
        c, ssh, _ = client
        connect_ssh(c)
        ssh.images.add("aisbench:v1")  # 预置镜像

        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path), "aisbench:v1")
        with open(tar_path, "rb") as f:
            upload = c.post("/api/upload/image",
                            files={"file": ("img.tar", f, "application/x-tar")}).json()

        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_mount": "/mnt/models/ds",
            "container_name": "aisbench_dedup_test",
            "work_dir": "/tmp/AISBench_Prefix_Tools",
        })
        assert r.status_code == 200
        status = wait_task(c, "deploy")
        assert status["status"] == "done"
        # 没有 docker load 命令被执行
        assert not any("docker load" in cmd for cmd in ssh.exec_log)


class TestContainersAPI:
    """v2.0 容器资源池: 列出/选择/启动/忙碌检测"""

    def _create(self, c, tmp_path, name="aisbench_pool_a"):
        connect_ssh(c)
        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path))
        with open(tar_path, "rb") as f:
            upload = c.post("/api/upload/image",
                            files={"file": ("img.tar", f, "application/x-tar")}).json()
        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_mount": "/mnt/models/ds",
            "container_name": name,
            "work_dir": "/tmp/AISBench_Prefix_Tools",
        })
        assert r.status_code == 200, r.text
        return wait_task(c, "deploy")

    def test_list_and_select(self, client, tmp_path):
        c, ssh, _ = client
        connect_ssh(c)
        r = c.get("/api/containers/list")
        assert r.status_code == 200
        assert r.json()["containers"] == []

        self._create(c, tmp_path, "aisbench_pool_a")
        # 他人创建的非 aisbench 容器不可见
        ssh.containers["nginx-proxy"] = "Up 2 hours"

        r = c.get("/api/containers/list")
        items = r.json()["containers"]
        assert [x["name"] for x in items] == ["aisbench_pool_a"]
        assert items[0]["running"] is True
        assert items[0]["busy"] is False

        # 选择已有容器(无需重新创建)
        r = c.post("/api/containers/select", json={"name": "aisbench_pool_a"})
        assert r.status_code == 200
        data = r.json()
        assert data["selected"] is True
        info = data["info"]
        assert info["name"] == "aisbench_pool_a"
        assert info["running"] is True
        assert info["model_mounts"] == [{"host": "/mnt/models/ds",
                                         "container": "/mnt/models/ds"}]
        assert "aisbench_auto_tools_prefix" in info["code_mount_path"]
        # v2.0.1 回归: inspect 解析 Binds 得到宿主机代码路径(config.py SFTP 写入依赖)
        assert info["code_host_path"].startswith("/tmp/AISBench_Prefix_Tools")
        assert info["code_host_path"].endswith("aisbench_auto_tools_prefix-main")
        assert info["state"] == "running"
        # 已登记为当前容器
        state = c.get("/api/state").json()
        assert state["deploy"]["container_name"] == "aisbench_pool_a"
        assert state["deploy"]["source"] == "selected"

    def test_select_busy_container_requires_force(self, client, tmp_path):
        """正在执行测试的容器: 默认拒绝选择, force 后允许"""
        c, ssh, _ = client
        self._create(c, tmp_path, "aisbench_busy_box")
        ssh.busy_containers.add("aisbench_busy_box")

        r = c.post("/api/containers/select", json={"name": "aisbench_busy_box"})
        data = r.json()
        assert data["selected"] is False
        assert data["busy"] is True
        assert "run_tests.sh" in data["busy_detail"]

        r = c.post("/api/containers/select",
                   json={"name": "aisbench_busy_box", "force": True})
        assert r.json()["selected"] is True

    def test_select_missing_container(self, client):
        c, _, _ = client
        connect_ssh(c)
        r = c.post("/api/containers/select", json={"name": "aisbench_none"})
        assert r.status_code == 400

    def test_select_rejects_non_aisbench(self, client):
        c, _, _ = client
        connect_ssh(c)
        r = c.post("/api/containers/select", json={"name": "nginx-proxy"})
        assert r.status_code == 400

    def test_start_stopped_container(self, client, tmp_path):
        c, ssh, _ = client
        self._create(c, tmp_path, "aisbench_stopped_box")
        ssh.containers["aisbench_stopped_box"] = "Exited (0) 5 seconds ago"

        r = c.post("/api/containers/start", json={"name": "aisbench_stopped_box"})
        assert r.status_code == 200
        assert ssh.containers["aisbench_stopped_box"].startswith("Up")

    def test_list_images(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        ssh.images.update({"aisbench:v1", "ubuntu:22.04"})
        r = c.get("/api/containers/images")
        names = {img["name"] for img in r.json()["images"]}
        assert names == {"aisbench:v1", "ubuntu:22.04"}

    def test_container_fs_browse(self, client, tmp_path):
        """步骤3 MODEL_PATH 浏览: 列出容器内目录, 路径即容器内路径"""
        c, ssh, _ = client
        self._create(c, tmp_path, "aisbench_fs_box")
        ssh.container_fs["aisbench_fs_box"] = {
            "/": ["mnt", "benchmark"],
            "/mnt": ["model"],
            "/mnt/model": ["ds_v3", "qwen3"],
        }

        r = c.get("/api/containers/fs",
                  params={"name": "aisbench_fs_box", "path": "/mnt/model"})
        assert r.status_code == 200, r.text
        entries = r.json()["entries"]
        assert [e["name"] for e in entries] == ["ds_v3", "qwen3"]
        assert entries[0]["path"] == "/mnt/model/ds_v3"
        assert entries[0]["type"] == "dir"

        # 不存在的目录 → 400(便于前端提示)
        r = c.get("/api/containers/fs",
                  params={"name": "aisbench_fs_box", "path": "/no/such"})
        assert r.status_code == 400

        # 容器停止后不能浏览
        ssh.containers["aisbench_fs_box"] = "Exited (0) 1 minute ago"
        r = c.get("/api/containers/fs",
                  params={"name": "aisbench_fs_box", "path": "/"})
        assert r.status_code == 400
        assert "未运行" in r.json()["detail"]

    def test_select_stopped_container_reports_state(self, client, tmp_path):
        """停止容器: state=exited/running=False(而非 unknown 解析失败)"""
        c, ssh, _ = client
        self._create(c, tmp_path, "aisbench_state_box")
        ssh.containers["aisbench_state_box"] = "Exited (137) 10 seconds ago"

        r = c.post("/api/containers/select", json={"name": "aisbench_state_box"})
        info = r.json()["info"]
        assert info["running"] is False
        assert info["state"] == "exited"

        # busy 端点返回真实 state(修复"一直显示已停止 unknown"的问题)
        r = c.get("/api/containers/busy", params={"name": "aisbench_state_box"})
        data = r.json()
        assert data["running"] is False
        assert data["state"] == "exited"


class TestConfigAPI:
    def test_config_defaults(self, client):
        c, _, _ = client
        r = c.get("/api/config/defaults")
        assert r.status_code == 200
        assert r.json()["defaults"]["work_path"] == "/benchmark"

    def test_config_preview(self, client):
        c, _, _ = client
        r = c.post("/api/config/preview", json={"config": {
            "model_name": "ds", "model_path": "/mnt/models/ds",
            "host_ip": "10.0.0.1", "host_port": "8000",
        }})
        assert r.status_code == 200
        data = r.json()
        assert data["errors"] == []
        assert 'MODEL_PATH = "/mnt/models/ds"' in data["content"]

    def test_config_preview_validation(self, client):
        c, _, _ = client
        r = c.post("/api/config/preview", json={"config": {}})
        assert r.status_code == 200
        assert len(r.json()["errors"]) > 0

    def test_config_save_and_verify(self, client, tmp_path):
        c, ssh, _ = client
        connect_ssh(c)
        # 先创建容器
        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path))
        with open(tar_path, "rb") as f:
            upload = c.post("/api/upload/image",
                            files={"file": ("img.tar", f, "application/x-tar")}).json()
        c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_mount": "/mnt/models/ds",
            "container_name": "aisbench_cfg_box",
            "work_dir": "/tmp/AISBench_Prefix_Tools",
        })
        wait_task(c, "deploy")

        config = {
            "model_name": "ds", "model_path": "/mnt/models/ds",
            "host_ip": "10.0.0.1", "host_port": "8000",
            "api_key": "", "pod_info": "10.0.0.1:8000",
        }
        r = c.post("/api/config/save", json={"config": config})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["mismatches"] == []

        # 验证
        r = c.post("/api/config/verify", json={"config": config})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # 读取
        r = c.get("/api/config/read")
        assert r.status_code == 200
        assert r.json()["fields"]["MODEL_PATH"] == "/mnt/models/ds"

    def test_config_with_explicit_container(self, client, tmp_path):
        """显式指定容器: 多用户各用各的容器, 互不干扰"""
        c, ssh, _ = client
        connect_ssh(c)
        for name in ("aisbench_user_a", "aisbench_user_b"):
            ssh.containers[name] = "Up 1 second"
            ssh.container_meta[name] = {
                "image": "aisbench:test",
                "binds": ["/mnt/models:/mnt/models",
                          f"/tmp/w/{name}:/benchmark/ais_bench/aisbench_auto_tools_prefix-main"],
            }

        config = {"model_name": "ds", "model_path": "/mnt/models/ds",
                  "host_ip": "10.0.0.1", "host_port": "8000"}
        r = c.post("/api/config/save",
                   json={"config": config, "container": "aisbench_user_b"})
        assert r.status_code == 200, r.text
        # 写入的是 user_b 容器的代码挂载目录
        assert "/tmp/w/aisbench_user_b/config.py" in ssh.files

        r = c.get("/api/config/read?container=aisbench_user_b")
        assert r.status_code == 200
        assert r.json()["fields"]["MODEL_PATH"] == "/mnt/models/ds"

    def test_config_save_requires_deploy(self, client):
        c, _, _ = client
        r = c.post("/api/config/save", json={"config": {}})
        assert r.status_code == 400


class TestExecuteFlow:
    def _deploy(self, c, tmp_path):
        connect_ssh(c)
        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path))
        with open(tar_path, "rb") as f:
            upload = c.post("/api/upload/image",
                            files={"file": ("img.tar", f, "application/x-tar")}).json()
        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_mount": "/mnt/models/ds",
            "container_name": "aisbench_exec_box",
            "work_dir": "/tmp/AISBench_Prefix_Tools",
        })
        assert r.status_code == 200
        return wait_task(c, "deploy")

    def test_execute_full_flow(self, client, tmp_path):
        c, ssh, _ = client
        self._deploy(c, tmp_path)

        designer = {
            "single_kv_cache": 198469, "dp": 4, "max_request_length": 131072,
            "repeat_rate": 0.9, "request_rate": 0,
            "input_lengths": [8192], "output_lengths": [512],
            "cases": [{
                "input_len": 8192, "output_len": 512,
                "data_num_recommended": 216, "data_num_min": 108,
                "concurrency_recommended": 91, "concurrency_max": 91,
                "repeat_rate": 0.9, "dp": 4, "is_custom": False,
            }],
        }
        r = c.post("/api/execute/start", json={
            "designer": designer, "run_name": "smoke",
            "container": "aisbench_exec_box",
        })
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        assert "smoke" in run_id

        # 指定容器查询状态
        r = c.get("/api/execute/status?container=aisbench_exec_box")
        assert r.status_code == 200
        assert r.json()["key"] == "aisbench_exec_box"

        status = wait_task(c, "execute", timeout=15)
        assert status["status"] == "done", status
        result = status["result"]
        assert result["row_count"] == 1
        assert result["rows"][0]["input_len"] == 8192
        assert result["rows"][0]["hbm_hit_rate"] == 85.32

        # 结果历史
        r = c.get("/api/results")
        runs = r.json()["runs"]
        assert any(run["run_id"] == run_id for run in runs)

        # run详情
        r = c.get(f"/api/results/{run_id}")
        detail = r.json()
        assert detail["csv_file"].endswith(".csv")
        assert any(f["name"].endswith("test_1_8192_512.log") for f in detail["files"])

        # 下载CSV
        r = c.get(f"/api/results/{run_id}/download",
                  params={"file": detail["csv_file"]})
        assert r.status_code == 200
        assert b"input_len" in r.content

        # 打包下载
        r = c.get(f"/api/results/{run_id}/download-zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"

    def test_execute_requires_deploy(self, client):
        c, _, _ = client
        r = c.post("/api/execute/start", json={"designer": {"cases": []}})
        assert r.status_code == 400

    def test_execute_busy_container_rejected(self, client, tmp_path):
        """正在执行测试的容器拒绝启动新任务(保护并发测试互不干扰)"""
        c, ssh, _ = client
        self._deploy(c, tmp_path)
        ssh.busy_containers.add("aisbench_exec_box")

        r = c.post("/api/execute/start", json={
            "designer": {"cases": [{"input_len": 8192, "output_len": 512,
                                    "data_num_recommended": 10,
                                    "concurrency_recommended": 5}]},
            "container": "aisbench_exec_box",
        })
        assert r.status_code == 400
        assert "正在执行测试" in r.json()["detail"]

    def test_cleanup(self, client, tmp_path):
        c, ssh, _ = client
        self._deploy(c, tmp_path)

        # 按名称清理指定容器(aisbench_exec_box 由 _deploy 创建)
        # 另一个无关容器不应被删除
        ssh.containers["aisbench_other_user"] = "Up 1 hour"
        ssh.container_meta["aisbench_other_user"] = {"image": "x:1", "binds": []}

        r = c.post("/api/cleanup", json={
            "containers": ["aisbench_exec_box"],
            "clean_work_dir": True,
            "work_dir": "/tmp/AISBench_Prefix_Tools",
        })
        assert r.status_code == 200
        status = wait_task(c, "cleanup")
        assert status["status"] == "done"
        assert status["result"]["removed"] == ["aisbench_exec_box"]
        # 未勾选的容器不受影响
        assert "aisbench_exec_box" not in ssh.containers
        assert "aisbench_other_user" in ssh.containers

        # 部署信息已重置
        state = c.get("/api/state").json()
        assert state["deploy"] is None

    def test_cleanup_rejects_non_aisbench(self, client):
        """拒绝清理非 aisbench 前缀容器(他人/其他业务容器)"""
        c, _, _ = client
        connect_ssh(c)
        r = c.post("/api/cleanup", json={"containers": ["nginx-proxy"]})
        assert r.status_code == 400

    def test_cleanup_skips_busy_container(self, client, tmp_path):
        """正在执行测试的容器会被跳过, 不误伤他人任务"""
        c, ssh, _ = client
        self._deploy(c, tmp_path)
        ssh.busy_containers.add("aisbench_exec_box")

        r = c.post("/api/cleanup", json={"containers": ["aisbench_exec_box"]})
        assert r.status_code == 200
        status = wait_task(c, "cleanup")
        assert status["status"] == "done"
        assert status["result"]["removed"] == []
        assert status["result"]["skipped"] == ["aisbench_exec_box"]
        assert "aisbench_exec_box" in ssh.containers

    def test_delete_run(self, client, tmp_path):
        c, _, _ = client
        self._deploy(c, tmp_path)
        r = c.post("/api/execute/start", json={"designer": {
            "single_kv_cache": 198469, "dp": 4, "max_request_length": 131072,
            "cases": [{"input_len": 8192, "output_len": 512,
                       "data_num_recommended": 216, "concurrency_recommended": 91}],
        }})
        run_id = r.json()["run_id"]
        wait_task(c, "execute", timeout=15)

        r = c.delete(f"/api/results/{run_id}")
        assert r.status_code == 200
        r = c.get(f"/api/results/{run_id}")
        assert r.status_code == 404


class TestLogtoolsAPI:
    def test_grep_docker_logs(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        ssh.containers["vllm-serve"] = "Up 3 hours"

        # 列出容器
        r = c.get("/api/logtools/containers")
        assert r.status_code == 200
        assert r.json()["containers"][0]["name"] == "vllm-serve"

    def test_list_host_dir(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        ssh.files["/var/log/vllm_serve.log"] = "log content"
        r = c.get("/api/logtools/list", params={"server": "session", "mode": "host",
                                                "path": "/var/log"})
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert entries[0]["name"] == "vllm_serve.log"
        assert entries[0]["type"] == "file"

    def test_view_log(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        ssh.files["/var/log/vllm_serve.log"] = "GPU KV cache size: 198,469 tokens\n"
        r = c.post("/api/logtools/view", json={
            "server": "session", "mode": "host", "path": "/var/log/vllm_serve.log",
        })
        assert r.status_code == 200
        assert "GPU KV cache size" in r.json()["content"]

    def test_grep_no_match(self, client):
        c, ssh, _ = client
        connect_ssh(c)
        ssh.files["/var/log/empty.log"] = "nothing"
        r = c.post("/api/logtools/grep", json={
            "server": "session", "mode": "host", "path": "/var/log/empty.log",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["parsed"] is False

    def test_other_server_connect(self, client):
        c, _, _ = client
        r = c.post("/api/logtools/other/connect", json={
            "host": "10.0.0.2", "username": "root", "password": "pass",
        })
        assert r.status_code == 200
        status = c.get("/api/logtools/status").json()
        assert status["other"]["connected"] is True
        assert status["other"]["host"] == "10.0.0.2"

        r = c.post("/api/logtools/other/disconnect")
        assert r.status_code == 200


class TestCompareAPI:
    def _upload_csv(self, c, qps):
        from app.core.log_parser import CSV_FIELDS
        import csv as csv_mod
        buf = io.StringIO()
        writer = csv_mod.DictWriter(buf, fieldnames=CSV_FIELDS)
        writer.writeheader()
        row = {f: "" for f in CSV_FIELDS}
        row.update({"input_len": 8192, "output_len": 512,
                    "total_req": 100, "max_cc": 10, "cc": 10, "qps": qps})
        writer.writerow(row)
        r = c.post("/api/upload/compare-csv",
                   files={"file": ("res.csv", buf.getvalue().encode(), "text/csv")})
        return r.json()["id"]

    def test_compare_flow(self, client):
        c, _, _ = client
        id_a = self._upload_csv(c, "10")
        id_b = self._upload_csv(c, "12.5")

        # 加载匹配
        r = c.post("/api/compare/load", json={
            "a": {"type": "upload", "upload_id": id_a},
            "b": {"type": "upload", "upload_id": id_b},
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["matches"]) == 1
        assert data["matches"][0]["exact"] is True
        assert len(data["metric_fields"]) == 21

        # 计算对比
        r = c.post("/api/compare/compute", json={
            "a": {"type": "upload", "upload_id": id_a},
            "b": {"type": "upload", "upload_id": id_b},
            "metrics": ["qps", "TTFT_avg"],
            "match_indices": [0],
        })
        assert r.status_code == 200
        tables = r.json()["tables"]
        assert tables["qps"]["rows"][0]["pct"] == 25.0
        assert "TTFT_avg" in tables

        # 导出
        r = c.post("/api/compare/export", json={
            "a": {"type": "upload", "upload_id": id_a},
            "b": {"type": "upload", "upload_id": id_b},
            "metrics": ["qps"],
        })
        assert r.status_code == 200
        assert "25.00" in r.content.decode("utf-8")

    def test_compare_with_result_history(self, client, tmp_path):
        c, _, _ = client
        # 构造一个结果run
        from app.core.log_parser import CSV_FIELDS
        import csv as csv_mod
        results_dir = tmp_path / "results" / "run_test1"
        results_dir.mkdir(parents=True)
        buf = io.StringIO()
        writer = csv_mod.DictWriter(buf, fieldnames=CSV_FIELDS)
        writer.writeheader()
        row = {f: "" for f in CSV_FIELDS}
        row.update({"input_len": 8192, "output_len": 512, "qps": 5.0})
        writer.writerow(row)
        (results_dir / "results_x.csv").write_text(buf.getvalue(), encoding="utf-8")
        # test_logs_* 下的原始CSV(字段格式不同)不应被自动选中
        raw_dir = results_dir / "test_logs_20260101_000000"
        raw_dir.mkdir()
        (raw_dir / "aisbench_result.csv").write_text(
            "model_name,current_time,input_len\nmodel,t,8192\n", encoding="utf-8")

        id_b = self._upload_csv(c, "6")
        r = c.post("/api/compare/load", json={
            "a": {"type": "result", "run_id": "run_test1"},
            "b": {"type": "upload", "upload_id": id_b},
        })
        assert r.status_code == 200
        assert len(r.json()["matches"]) == 1
        assert r.json()["a"]["label"] == "run_test1/results_x.csv"

    def test_compare_result_history_skips_raw_csv(self, client, tmp_path):
        """run 内只有原始aisbench_result.csv(字段不兼容)时应报明确错误"""
        c, _, _ = client
        results_dir = tmp_path / "results" / "run_raw"
        raw_dir = results_dir / "test_logs_20260101_000000"
        raw_dir.mkdir(parents=True)
        (raw_dir / "aisbench_result.csv").write_text(
            "model_name,current_time,input_len\nmodel,t,8192\n", encoding="utf-8")

        id_b = self._upload_csv(c, "6")
        r = c.post("/api/compare/load", json={
            "a": {"type": "result", "run_id": "run_raw"},
            "b": {"type": "upload", "upload_id": id_b},
        })
        assert r.status_code == 400
        assert "results_" in r.json()["detail"]

    def test_compare_missing_source(self, client):
        c, _, _ = client
        r = c.post("/api/compare/load", json={
            "a": {"type": "upload", "upload_id": "nope"},
            "b": {"type": "upload", "upload_id": "nope2"},
        })
        assert r.status_code == 400

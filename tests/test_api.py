"""API 集成测试 - FakeSSH 注入, 覆盖部署→配置→执行→结果→对比全链路"""

import io
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
        assert r.json() == {"connected": True, "host": "10.0.0.1",
                            "port": 22, "username": "root"}

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
        assert data["commands"][0].startswith("python3 aisbench_test.py")
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

        # 启动部署(内置代码)
        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_path": "/mnt/models/ds",
            "work_dir": "/tmp/AISBench_Prefix_Tools",
            "code": {"source": "builtin"},
        })
        assert r.status_code == 200, r.text

        status = wait_task(c, "deploy")
        assert status["status"] == "done", status
        result = status["result"]
        assert result["container_name"].startswith("aisbench-test-")
        assert result["image_name"] == "aisbench:test"
        assert result["model_path"] == "/mnt/models/ds"

        # 状态聚合
        state = c.get("/api/state").json()
        assert state["steps"]["deployed"] is True
        assert state["container_running"] is True

        # 内置代码已上传到远程
        assert any(p.startswith("/tmp/AISBench_Prefix_Tools/aisbench_auto_tools_prefix-main")
                   for p in ssh.files)

    def test_deploy_requires_ssh(self, client):
        c, _, _ = client
        r = c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": "x"},
            "model_path": "/m", "work_dir": "/tmp/x",
        })
        assert r.status_code == 400

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
            "model_path": "/mnt/models/ds",
            "work_dir": "/tmp/AISBench_Prefix_Tools",
        })
        assert r.status_code == 200
        status = wait_task(c, "deploy")
        assert status["status"] == "done"
        # 没有 docker load 命令被执行
        assert not any("docker load" in cmd for cmd in ssh.exec_log)


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
        # 先部署
        tar_path = tmp_path / "img.tar"
        make_image_tar(str(tar_path))
        with open(tar_path, "rb") as f:
            upload = c.post("/api/upload/image",
                            files={"file": ("img.tar", f, "application/x-tar")}).json()
        c.post("/api/deploy/start", json={
            "image": {"type": "upload", "upload_id": upload["id"]},
            "model_path": "/mnt/models/ds",
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
            "model_path": "/mnt/models/ds",
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
        r = c.post("/api/execute/start", json={"designer": designer, "run_name": "smoke"})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        assert "smoke" in run_id

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

    def test_cleanup(self, client, tmp_path):
        c, ssh, _ = client
        self._deploy(c, tmp_path)

        r = c.post("/api/cleanup", json={"remove_container": True,
                                         "clean_work_dir": True})
        assert r.status_code == 200
        status = wait_task(c, "cleanup")
        assert status["status"] == "done"

        # 部署信息已重置
        state = c.get("/api/state").json()
        assert state["deploy"] is None

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

        id_b = self._upload_csv(c, "6")
        r = c.post("/api/compare/load", json={
            "a": {"type": "result", "run_id": "run_test1"},
            "b": {"type": "upload", "upload_id": id_b},
        })
        assert r.status_code == 200
        assert len(r.json()["matches"]) == 1

    def test_compare_missing_source(self, client):
        c, _, _ = client
        r = c.post("/api/compare/load", json={
            "a": {"type": "upload", "upload_id": "nope"},
            "b": {"type": "upload", "upload_id": "nope2"},
        })
        assert r.status_code == 400

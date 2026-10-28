"""平台 v3.0 API 集成测试 —— 登录门禁 / 任务全流程 / 容器概念隐藏

用户视角验收:
- 未登录时平台 API 一律 401;
- 登录 = 建立执行机会话;
- 提交任务(镜像+配置+用例) → 排队 → 执行 → 完成 → 结果入库;
- API 响应不含 container/docker 等内部执行细节;
- 任务结束后内部容器被自动清理(用户机器不残留)。
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.platform.tasks import TaskStore
from .fakessh import FakeSSH


@pytest.fixture()
def pclient(tmp_path):
    # 平台登录会为每个用户建立 SSH 会话 —— 测试注入共享 FakeSSH
    ssh = FakeSSH()
    app = create_app(ssh_manager=ssh, data_dir=tmp_path,
                     ssh_factory=lambda: ssh)
    return TestClient(app), ssh, tmp_path


def login(c):
    r = c.post("/api/auth/login", json={
        "host": "10.0.0.1", "port": 22, "username": "root",
        "password": "pass",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"]
    return {"Authorization": f"Bearer {data['token']}"}, data


DESIGNER_PAYLOAD = {
    "single_kv_cache": 198469, "dp": 4, "max_request_length": 131072,
    "repeat_rate": 0.9, "request_rate": 0,
    "cases": [{
        "input_len": 8192, "output_len": 512,
        "data_num_recommended": 216, "data_num_min": 108,
        "concurrency_recommended": 91, "concurrency_max": 91,
        "repeat_rate": 0.9, "dp": 4, "is_custom": False,
    }],
}

TASK_BODY = {
    "name": "平台冒烟",
    "image": "aisbench:v1",
    "config": {
        "model_name": "ds", "model_path": "/mnt/models/ds",
        "host_ip": "10.0.0.1", "host_port": "8000",
    },
    "designer": DESIGNER_PAYLOAD,
}


def wait_task(c, task_id, headers=None, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/platform/tasks/{task_id}", headers=headers)
        assert r.status_code == 200, r.text
        t = r.json()
        if t["status"] not in ("queued", "preparing", "running", "collecting"):
            return t
        time.sleep(0.1)
    raise TimeoutError(f"任务未在 {timeout}s 内完成: {t}")


class TestAuth:
    def test_login_success_and_status(self, pclient):
        c, ssh, _ = pclient
        headers, data = login(c)
        assert data["user"] == "root"
        assert data["host"] == "10.0.0.1"

        r = c.get("/api/auth/status", headers=headers)
        assert r.json()["authenticated"] is True
        assert r.json()["user"] == "root"

    def test_login_bad_password(self, pclient):
        c, _, _ = pclient
        r = c.post("/api/auth/login", json={
            "host": "10.0.0.1", "username": "root", "password": "wrong",
        })
        assert r.status_code == 400

    def test_status_without_token(self, pclient):
        c, _, _ = pclient
        r = c.get("/api/auth/status")
        assert r.status_code == 200
        assert r.json()["authenticated"] is False

    def test_logout(self, pclient):
        c, _, _ = pclient
        headers, _ = login(c)
        r = c.post("/api/auth/logout", headers=headers)
        assert r.json()["ok"] is True
        r = c.get("/api/auth/status", headers=headers)
        assert r.json()["authenticated"] is False


class TestPlatformGate:
    def test_tasks_require_auth(self, pclient):
        c, _, _ = pclient
        assert c.get("/api/platform/tasks").status_code == 401
        assert c.get("/api/platform/images").status_code == 401
        assert c.get("/api/platform/fs/list?path=/").status_code == 401
        r = c.post("/api/platform/tasks", json=TASK_BODY)
        assert r.status_code == 401


class TestTaskFlow:
    def test_submit_and_run_to_done(self, pclient):
        c, ssh, tmp = pclient
        headers, _ = login(c)
        ssh.images.add("aisbench:v1")

        r = c.post("/api/platform/tasks", json=TASK_BODY, headers=headers)
        assert r.status_code == 200, r.text
        task_id = r.json()["id"]

        task = wait_task(c, task_id, headers)
        assert task["status"] == "done", task
        assert task["result"]["row_count"] == 1
        assert task["result"]["rows"][0]["input_len"] == 8192
        assert task["owner"] == "root"
        assert task["image"] == "aisbench:v1"

        # 容器概念隐藏: API 不暴露 container/docker 字段
        assert "container" not in task
        assert "token" not in task
        assert "work_dir" not in task

        # 结果持久化在服务端(results 体系, 供对比)
        result_dir = tmp / "results" / task_id
        assert result_dir.is_dir()
        assert any(f.name.startswith("results_") for f in result_dir.iterdir())
        r = c.get("/api/results")
        assert any(run["run_id"] == task_id for run in r.json()["runs"])

        # 内部容器已自动清理(机器无残留)
        assert not any(name.startswith("aisbench_task_")
                       for name in ssh.containers)

    def test_task_list_and_detail(self, pclient):
        c, ssh, _ = pclient
        headers, _ = login(c)
        ssh.images.add("aisbench:v1")
        r = c.post("/api/platform/tasks", json=TASK_BODY, headers=headers)
        task_id = r.json()["id"]
        wait_task(c, task_id, headers)

        r = c.get("/api/platform/tasks", headers=headers)
        items = r.json()["tasks"]
        assert len(items) == 1
        assert items[0]["id"] == task_id
        assert items[0]["status"] == "done"
        assert items[0]["result_summary"]["row_count"] == 1
        assert "container" not in items[0]

    def test_task_validation_errors(self, pclient):
        c, _, _ = pclient
        headers, _ = login(c)

        body = dict(TASK_BODY, image="")
        r = c.post("/api/platform/tasks", json=body, headers=headers)
        assert r.status_code == 422  # pydantic min_length

        body = dict(TASK_BODY, config={"model_name": "ds"})
        r = c.post("/api/platform/tasks", json=body, headers=headers)
        assert r.status_code == 400
        assert "配置" in r.json()["detail"]

        body = dict(TASK_BODY, designer={"single_kv_cache": 1})
        r = c.post("/api/platform/tasks", json=body, headers=headers)
        assert r.status_code == 400

    def test_delete_finished_task(self, pclient):
        c, ssh, tmp = pclient
        headers, _ = login(c)
        ssh.images.add("aisbench:v1")
        r = c.post("/api/platform/tasks", json=TASK_BODY, headers=headers)
        task_id = r.json()["id"]
        wait_task(c, task_id, headers)

        r = c.delete(f"/api/platform/tasks/{task_id}", headers=headers)
        assert r.json()["ok"] is True
        r = c.get(f"/api/platform/tasks/{task_id}", headers=headers)
        assert r.status_code == 404
        assert not (tmp / "results" / task_id).exists()

    def test_cancel_queued_task(self, pclient):
        c, ssh, _ = pclient
        headers, _ = login(c)
        ssh.images.add("aisbench:v1")
        # 提交后立刻取消(排队或执行初期)
        r = c.post("/api/platform/tasks", json=TASK_BODY, headers=headers)
        task_id = r.json()["id"]
        r = c.post(f"/api/platform/tasks/{task_id}/cancel", headers=headers)
        assert r.json()["ok"] is True
        # 等待任务状态稳定(取消或已完成 —— 竞态下二者都合法)
        task = wait_task(c, task_id, headers)
        assert task["status"] in ("cancelled", "done")


class TestStartupRecovery:
    def test_running_task_marked_interrupted(self, tmp_path):
        import json as _json
        store = TaskStore(tmp_path / "platform")
        store.create(name="旧任务", owner="root", image="i:1",
                     status="running", phase="执行测试")
        n = store.recover_on_startup()
        assert n == 1
        tasks = store.list()
        assert tasks[0].status == "interrupted"
        assert "中断" in tasks[0].error

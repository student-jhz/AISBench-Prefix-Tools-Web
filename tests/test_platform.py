"""平台 v3.0 API 集成测试 —— 登录门禁 / 任务全流程 / 容器概念隐藏

用户视角验收:
- 未登录时平台 API 一律 401;
- 登录 = 建立执行机会话;
- 提交任务(镜像+配置+用例) → 排队 → 执行 → 完成 → 结果入库;
- API 响应不含 container/docker 等内部执行细节;
- 任务结束后内部容器被自动清理(用户机器不残留)。
"""

import time
import threading

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
        # v3.0.2: 结果行字段契约 —— 详情 API 直接返回前端结果表字段
        # (ttft_avg 等), 不再泄漏解析层 CSV 字段名导致指标列全为空
        row = task["result"]["rows"][0]
        assert row["ttft_avg"] == 200.3
        assert row["tpot_avg"] == 25.6
        assert row["e2e_avg"] == 9000.5
        assert row["concurrency"] == 91
        assert row["data_num"] == 108
        assert row["total_throughput"] == 13134.6
        assert row["hbm_hit_rate"] == 85.32
        assert row["external_hit_rate"] == 92.15
        assert row["repeat_rate"] == 0.9   # 从任务设计的用例回填
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

    def test_legacy_csv_rows_normalized_in_detail(self, tmp_path):
        """旧版本任务持久化的是解析层 CSV 字段(TTFT_avg 等) ——
        详情 API 读时归一化为前端字段, 升级后历史任务结果表不空列"""
        store = TaskStore(tmp_path / "platform")
        task = store.create(
            name="旧任务", owner="root", image="aisbench:v1",
            designer={"cases": [{
                "input_len": 8192, "output_len": 512, "repeat_rate": 0.9,
            }]},
            result={
                "run_id": "legacy", "csv_file": "results_x.csv",
                "row_count": 1,
                "rows": [{
                    "input_len": 8192, "output_len": 512, "total_req": 108,
                    "max_cc": 91, "cc": 91, "qps": 1.5,
                    "TTFT_avg": 200.3, "TPOT_avg": 25.6, "E2EL_avg": 9000.5,
                    "E2E_throughput": 13134.6,
                    "hbm_hit_rate": 85.32, "external_hit_rate": 92.15,
                }],
            },
            status="done",
        )
        row = task.public(detail=True)["result"]["rows"][0]
        assert row["ttft_avg"] == 200.3
        assert row["tpot_avg"] == 25.6
        assert row["e2e_avg"] == 9000.5
        assert row["data_num"] == 108
        assert row["concurrency"] == 91
        assert row["total_throughput"] == 13134.6
        assert row["repeat_rate"] == 0.9
        # cc 缺失时回退设计并发 max_cc
        task.result["rows"][0]["cc"] = ""
        task.result["rows"][0].pop("concurrency", None)
        row = task.public(detail=True)["result"]["rows"][0]
        assert row["concurrency"] == 91

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

    def test_copy_source_returns_user_payload_only(self, pclient):
        """复制源: 返回镜像/配置/用例设计; 内部执行信息(container 等)不暴露"""
        c, ssh, _ = pclient
        headers, _ = login(c)
        ssh.images.add("aisbench:v1")
        # 新格式: 提交时一并持久化勾选的输入/输出长度
        body = dict(TASK_BODY)
        body["designer"] = dict(DESIGNER_PAYLOAD,
                                input_lengths=[8192, 16384],
                                output_lengths=[512, 1024])
        r = c.post("/api/platform/tasks", json=body, headers=headers)
        task_id = r.json()["id"]
        wait_task(c, task_id, headers)

        r = c.get(f"/api/platform/tasks/{task_id}/copy-source", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["id"] == task_id
        assert data["name"] == TASK_BODY["name"]
        assert data["image"] == "aisbench:v1"
        assert data["config"] == TASK_BODY["config"]
        assert data["designer"]["single_kv_cache"] == 198469
        assert data["designer"]["cases"] == DESIGNER_PAYLOAD["cases"]
        # 已持久化的勾选长度原样返回(复制后选择栏精确回显)
        assert data["designer"]["input_lengths"] == [8192, 16384]
        assert data["designer"]["output_lengths"] == [512, 1024]
        # 容器概念仍不暴露
        for hidden in ("container", "token", "work_dir"):
            assert hidden not in data
            assert hidden not in data["designer"]

    def test_copy_source_derives_lengths_for_legacy_tasks(self, pclient):
        """早期任务未持久化勾选长度 → 从用例反推, 保证选择栏可回显"""
        c, ssh, _ = pclient
        headers, _ = login(c)
        ssh.images.add("aisbench:v1")
        r = c.post("/api/platform/tasks", json=TASK_BODY, headers=headers)
        task_id = r.json()["id"]
        wait_task(c, task_id, headers)

        r = c.get(f"/api/platform/tasks/{task_id}/copy-source", headers=headers)
        assert r.status_code == 200, r.text
        designer = r.json()["designer"]
        assert designer["input_lengths"] == [8192]
        assert designer["output_lengths"] == [512]

    def test_copy_source_requires_auth(self, pclient):
        c, _, _ = pclient
        r = c.get("/api/platform/tasks/t0000000000000000ff/copy-source")
        assert r.status_code == 401

    def test_copy_source_not_found(self, pclient):
        c, _, _ = pclient
        headers, _ = login(c)
        r = c.get("/api/platform/tasks/t0000000000000000ff/copy-source",
                  headers=headers)
        assert r.status_code == 404

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


class TestTaskStoreAtomicity:
    def test_concurrent_get_never_sees_partial_file(self, tmp_path):
        """保存与并发读取竞争: get() 不能读到空/半截 JSON(v3.0.2 偶发
        任务详情 404"任务不存在"的根因, 测试套件中以 flake 形式暴露)。

        旧实现 write_text 截断重写, 读侧无锁 —— 命中写入窗口时
        json.loads 抛 ValueError, get() 返回 None, API 404。
        """
        store = TaskStore(tmp_path / "platform")
        task = store.create(name="race", owner="root", image="i:1",
                            status="running")

        stop = threading.Event()
        seen_404_style = []

        def reader():
            while not stop.is_set():
                t = store.get(task.id)
                if t is None:
                    seen_404_style.append(time.time())

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        try:
            for i in range(200):
                task.phase = f"phase-{i}"
                store.save(task)
        finally:
            stop.set()
            for t in threads:
                t.join()

        assert not seen_404_style, (
            f"并发读取观察到 {len(seen_404_style)} 次任务丢失(旧写入非原子)")
        # 最终状态完好
        assert store.get(task.id).phase == "phase-199"

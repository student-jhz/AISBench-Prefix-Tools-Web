"""DockerManager 单元测试 —— 直接用真实 docker 命令输出格式做回归

背景(v2.0.5): 测试替身若"太友好"会掩盖生产端解析 bug ——
- v2.0.1: FakeSSH 曾发真实 TAB, 而 docker --format 模板把 \t 按字面输出;
- v2.0.5: FakeSSH 曾把 Names 发成数组, 而真实 docker ps --format json
  输出的是逗号分隔字符串, 生产端 names[0] 取到首字符 "a",
  导致"已有容器列表恒为空"。
这里用最小 StubSSH 固化真实 docker 的输出字节, 防止再次回归。
"""

from app.core.docker_manager import DockerManager  # noqa: E402


class StubSSH:
    """按命令返回预置输出, 模拟真实 docker CLI 的字节级行为"""

    def __init__(self, outputs=None):
        self.connected = True
        self.sftp = None
        self.commands = []
        self.outputs = outputs or {}

    def execute(self, command, timeout=300):
        self.commands.append(command)
        for needle, out in self.outputs.items():
            if needle in command:
                return 0, out, ""
        return 0, "", ""


REAL_PS_OUTPUT = "\n".join([
    # 真实 docker ps -a --format '{{json .}}': Names 是逗号分隔字符串
    '{"Names":"aisbench_user1","Image":"img:1","State":"running",'
    '"Status":"Up 1 hour","RunningFor":"About a minute ago"}',
    '{"Names":"nginx-proxy","Image":"nginx:latest","State":"running",'
    '"Status":"Up 2 hours","RunningFor":"2 hours ago"}',
    '{"Names":"aisbench_multi,aisbench_multi2","Image":"img:2",'
    '"State":"exited","Status":"Exited (0) 5 seconds ago",'
    '"RunningFor":"1 hour ago"}',
    '{"Names":"","Image":"img:3","State":"created","Status":"Created",'
    '"RunningFor":"1 minute ago"}',
]) + "\n"


class TestListContainers:
    def test_names_string_from_real_docker(self):
        """真实 docker 的 Names 为逗号分隔字符串, 必须解析出完整名称"""
        ssh = StubSSH({"docker ps -a --format": REAL_PS_OUTPUT})
        items = DockerManager(ssh).list_containers()
        names = [i["name"] for i in items]
        assert "aisbench_user1" in names
        assert "aisbench_multi" in names          # 多名取第一个
        assert "nginx-proxy" not in names          # 非 aisbench 前缀不可见
        by_name = {i["name"]: i for i in items}
        assert by_name["aisbench_user1"]["running"] is True
        assert by_name["aisbench_multi"]["running"] is False

    def test_names_array_still_supported(self):
        """旧替身/异常实现返回数组时兼容, 不因格式差异丢容器"""
        out = ('{"Names":["aisbench_arr"],"Image":"i","State":"running",'
               '"Status":"Up","RunningFor":"now"}\n')
        ssh = StubSSH({"docker ps -a --format": out})
        items = DockerManager(ssh).list_containers()
        assert [i["name"] for i in items] == ["aisbench_arr"]


class TestContainerBusy:
    def test_running_process_detected(self):
        ssh = StubSSH({"docker top": "PID\tCMD\n123\tbash /x/run_tests.sh\n"})
        busy, detail = DockerManager(ssh).container_busy("aisbench_x")
        assert busy is True
        assert "run_tests.sh" in detail

    def test_zombie_process_not_busy(self):
        """测试结束后的 defunct/[bash] 僵尸进程不算"正在执行测试" """
        ssh = StubSSH({"docker top": (
            "PID\tCMD\n"
            "130\tbash run_tests.sh <defunct>\n"
            "131\t[bash]\n"
            "132\tpython3 sleep 100\n")})
        busy, _ = DockerManager(ssh).container_busy("aisbench_x")
        assert busy is False

    def test_empty_top_not_busy(self):
        ssh = StubSSH({"docker top": "PID\tCMD\n"})
        busy, detail = DockerManager(ssh).container_busy("aisbench_x")
        assert busy is False
        assert detail == ""

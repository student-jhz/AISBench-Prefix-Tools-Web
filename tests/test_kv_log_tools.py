"""KV日志工具核心逻辑测试"""

from app.core import kv_log_tools as kvt


class TestParseKV:
    def test_parse_normal(self):
        text = (
            "INFO GPU KV cache size: 198,469 tokens\n"
            "INFO Maximum concurrency for 131,072 tokens per request: 6.05x"
        )
        kv, max_len = kvt.parse_kv_results(text)
        assert kv == 198469
        assert max_len == 131072

    def test_parse_no_match(self):
        kv, max_len = kvt.parse_kv_results("nothing here")
        assert kv is None
        assert max_len is None


class TestCommands:
    def test_grep_host(self):
        cmd = kvt.build_grep_command("host", file_path="/var/log/vllm.log")
        assert cmd == "grep -e 'GPU KV cache size' -e 'Maximum concurrency' /var/log/vllm.log"

    def test_grep_container(self):
        cmd = kvt.build_grep_command("container", container="abc", file_path="/x/y.log")
        assert cmd == "docker exec abc grep -e 'GPU KV cache size' -e 'Maximum concurrency' /x/y.log"

    def test_grep_docker_logs(self):
        cmd = kvt.build_grep_command("docker_logs", container="abc")
        assert cmd == "docker logs abc 2>&1 | grep -e 'GPU KV cache size' -e 'Maximum concurrency'"

    def test_grep_missing_selection(self):
        assert kvt.build_grep_command("docker_logs") is None
        assert kvt.build_grep_command("host") is None
        assert kvt.build_grep_command("container", file_path="/x") is None

    def test_view_host_tail(self):
        cmd = kvt.build_view_command("host", file_path="/var/log/a.log")
        assert cmd == f"tail -c {kvt.VIEW_TAIL_BYTES} /var/log/a.log"

    def test_view_docker_logs_lines(self):
        cmd = kvt.build_view_command("docker_logs", container="c1")
        assert f"docker logs --tail {kvt.VIEW_DOCKER_LOGS_LINES} c1 2>&1" == cmd

    def test_path_with_spaces_quoted(self):
        cmd = kvt.build_grep_command("host", file_path="/var/log/my log.txt")
        assert cmd.endswith("'/var/log/my log.txt'")


class TestLsParse:
    def test_parse_ls_la(self):
        out = (
            "total 8\n"
            "drwxr-xr-x  2 root root 4096 Jan  1 00:00 subdir\n"
            "-rw-r--r--  1 root root 1024 Jan  1 00:00 vllm_serve.log\n"
            "drwxr-xr-x  2 root root 4096 Jan  1 00:00 .\n"
            "drwxr-xr-x 24 root root 4096 Jan  1 00:00 ..\n"
        )
        entries = kvt.parse_ls_la(out)
        assert len(entries) == 2
        assert entries[0]["name"] == "subdir"
        assert entries[0]["type"] == "dir"
        assert entries[1] == {"name": "vllm_serve.log", "type": "file", "size": 1024}


class TestPathHelpers:
    def test_join_and_parent(self):
        assert kvt.join_path("/var/log", "a.log") == "/var/log/a.log"
        assert kvt.join_path("/", "var") == "/var"
        assert kvt.parent_path("/var/log") == "/var"
        assert kvt.parent_path("/var") == "/"
        assert kvt.parent_path("/") == "/"

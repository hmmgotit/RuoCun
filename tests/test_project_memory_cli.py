"""Product CLI boundaries with a synthetic account and a loopback model."""
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def cli(tmp_path, script, *, cwd=None, url="http://127.0.0.1:1/v1", options=(), retain_tokens=None):
    home = tmp_path / "account"
    home.mkdir(exist_ok=True)
    workspace = cwd or tmp_path / "work"
    workspace.mkdir(exist_ok=True)
    config = home / "synthetic.json"
    config.write_text(json.dumps({"version": 2, "activeProfile": "local", "profiles": {"local": {
        "provider": "openai", "baseUrl": url, "model": "glm-5.3-flash", "key": "synthetic-key"}}}),
        encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if name.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "RUOCUN_MXC_EXEC"}}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), LOCALAPPDATA=str(home),
               ProgramData=str(home / "managed"), RUOCUN_LANG="zh")
    if retain_tokens is not None:
        env["RUOCUN_COMPACT_RETAIN_TOKENS"] = str(retain_tokens)
    result = subprocess.run([sys.executable, "-I", "-X", "utf8", "-m", "ruocun",
        "--cwd", str(workspace), "--config", str(config), "--permission-mode", "bypassPermissions",
        "--no-hooks", "--no-mcp", "--no-skills", "--no-web", *options],
        cwd=workspace, env=env, input=script, encoding="utf-8", capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_project_enable_survives_new_cli_and_other_worktree_isolated(tmp_path: Path):
    cli(tmp_path, "/memory project enable\n/exit\n")
    status = cli(tmp_path, "/memory project status\n/exit\n")
    other = cli(tmp_path, "/memory project status\n/exit\n", cwd=tmp_path / "other")
    assert "已开启" in status
    assert "未开启" in other


@contextmanager
def model(respond):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            delta = respond(body, len(requests))
            if "_http_status" in delta:
                self.send_response(delta["_http_status"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            finish = "tool_calls" if "tool_calls" in delta else "stop"
            frames = [{"choices": [{"delta": delta}]}, {"choices": [{"delta": {}, "finish_reason": finish}]},
                      {"choices": [], "usage": {"prompt_tokens": 16, "completion_tokens": 4}}]
            wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(wire.encode())

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", requests
        finally:
            server.shutdown()
            thread.join()


def tool_call(name, arguments, number):
    return {"role": "assistant", "tool_calls": [{"index": 0, "id": f"synthetic-{number}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def test_forget_removes_startup_and_manual_read_from_the_next_wire_request(tmp_path):
    from ruocun.platform.windows.durable_file import ensure_private_dir

    cli(tmp_path, "/memory project enable\n/exit\n")
    directory = tmp_path / "work/.ruocun/memory"
    directory.parent.mkdir(exist_ok=True)
    ensure_private_dir(directory)
    marker = "SYNTHETIC-EXPIRED-CHECKPOINT"
    (directory / "checkpoint.md").write_text(
        "---\nname: checkpoint\ntitle: Checkpoint\ntype: semantic\nstatus: active\n---\n" + marker,
        encoding="utf-8")
    def respond(body, number):
        if number == 1:
            return tool_call("MemoryRead", {"query": "Checkpoint"}, number)
        if number == 2:
            return tool_call("MemoryWrite", {"title": "Checkpoint", "delete": True}, number)
        return {"content": "done"}

    with model(respond) as (url, requests):
        cli(tmp_path, "先翻项目笔记，再忘掉 Checkpoint。\n/exit\n", url=url)
    assert marker in requests[0]["messages"][0]["content"]
    assert marker in next(m["content"] for m in requests[1]["messages"] if m["role"] == "tool")
    assert "已删除" in str(requests[2]["messages"])
    assert marker not in str(requests[2]["messages"])
    assert [m["tool_call_id"] for m in requests[2]["messages"] if m["role"] == "tool"] == [
        "synthetic-1", "synthetic-2"]


def test_learned_rule_is_used_only_in_its_workspace_and_explicit_batch_target(tmp_path):
    first = "今后这个项目的退款报表必须带上 CASHFLOW-417 核对列。"
    ignored = "今后这个项目的出库报表必须带上 SHIPPING-219 核对列。"
    allowed = "今后这个项目的库存报表必须带上 WAREHOUSE-716 核对列。"
    with model(lambda body, number: {"content": "已处理。"}) as (url, requests):
        cli(tmp_path, "/memory project enable\n" + first + "\n/exit\n", url=url)
        cli(tmp_path, "", url=url, options=(ignored,))
        cli(tmp_path, "", url=url, options=("--project-memory-write", str(tmp_path / "work"), allowed))
        status = cli(tmp_path, "/memory project list\n/exit\n", url=url)
        assert "CASHFLOW-417" in status and "WAREHOUSE-716" in status and "SHIPPING-219" not in status
        before = len(requests)
        cli(tmp_path, "给退款报表补一列。\n/exit\n", url=url)
        assert "CASHFLOW-417" in str(requests[before]["messages"])
        before = len(requests)
        cli(tmp_path, "给退款报表补一列。\n/exit\n", cwd=tmp_path / "other", url=url)
        assert "CASHFLOW-417" not in str(requests[before]["messages"])

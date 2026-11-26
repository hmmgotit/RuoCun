"""File sandbox routing and explicit startup selection of the isolation boundary."""

import asyncio
import os
import threading

import pytest


def test_legacy_settings_cannot_disable_an_explicitly_selected_native_sandbox():
    from ruocun.platform.sandbox import sandboxSpawn

    for platform, backend in (("linux", "/usr/bin/bwrap"), ("darwin", "/usr/bin/sandbox-exec")):
        for environment in ({}, {"RUOCUN_SANDBOX": "0"}, {"DAO_SANDBOX": "0"}):
            launch = sandboxSpawn("ls", "/tmp/work", environ=environment, platform=platform, exists=lambda path: path == backend)
            assert launch["file"] == backend
        rejected = sandboxSpawn("ls", "/tmp/work", True, platform=platform, environ={"RUOCUN_SANDBOX": "1"})
        assert "不能整次绕过" in rejected["error"]
    assert "不支持" in sandboxSpawn("ls", "/tmp/work", environ={}, platform="freebsd")["error"]


def test_source_sandbox_platform_file_routes_and_missing_binaries():
    from ruocun.platform.sandbox import sandboxSpawn

    enabled = {"RUOCUN_SANDBOX": "1"}
    checked = []

    def exists(path):
        checked.append(path)
        return path in ("/usr/local/bin/bwrap", "/usr/bin/sandbox-exec")

    linux = sandboxSpawn("ls -l", "/tmp/work", environ=enabled, platform="linux", exists=exists)
    assert checked == ["/usr/bin/bwrap", "/bin/bwrap", "/usr/local/bin/bwrap"]
    assert linux == {"file": "/usr/local/bin/bwrap", "args": [
        "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--bind", "/tmp/work", "/tmp/work",
        "--proc", "/proc", "--dev", "/dev", "--die-with-parent", "/bin/sh", "-c", "ls -l",
    ]}
    mac = sandboxSpawn("ls -l", '/tmp/a"b', environ=enabled, platform="darwin", exists=exists)
    assert mac["file"] == "/usr/bin/sandbox-exec" and mac["args"][-3:] == ["/bin/sh", "-c", "ls -l"]
    assert mac["args"][0] == "-p"
    assert mac["args"][1] == (
        '(version 1) (allow default) (deny file-write*) '
        '(allow file-write* (subpath "/tmp/a\\\"b") (subpath "/tmp") (subpath "/private/tmp") '
        '(subpath "/private/var/folders") (literal "/dev/null") (literal "/dev/stdout") '
        '(literal "/dev/stderr") (literal "/dev/dtracehelper"))'
    )
    for platform, executable in (("linux", "bwrap"), ("darwin", "sandbox-exec")):
        result = sandboxSpawn("ls", "/tmp/work", environ=enabled, platform=platform, exists=lambda _: False)
        assert executable in result["error"]


@pytest.mark.skipif(os.name != "nt", reason="Windows native fail-closed route")
def test_windows_bash_uses_only_the_selected_sandbox_mode(tmp_path):
    from ruocun.platform.shell_plan import buildShellPlan
    from ruocun.tools.exec_shell import execShellTool
    from ruocun.tools.managed_shell import ManagedShellManager
    from ruocun.tools.types import ToolContext, ToolRejected

    database = tmp_path / "fixture.db"
    database.write_text("existing test data", encoding="utf-8")
    command = "echo ok>shell-result.txt & type fixture.db"
    output = tmp_path / "shell-result.txt"
    backup = tmp_path / "fixture.db.ruocun-backup"
    environment = {**os.environ, "RUOCUN_MXC_EXEC": str(tmp_path / "missing-wxc-exec.exe")}
    selected_plan = buildShellPlan(command, str(tmp_path), environ=environment, analyze=False)

    class UnexpectedStore:
        async def prepare(self, *_):
            pytest.fail("unsupported sandbox prepared a durable runner")

    async def scenario():
        ctx = ToolContext(str(tmp_path), getShellPlan=lambda *_: selected_plan, sandboxEnabled=True)
        managed = ManagedShellManager(tmp_path / "session", "synthetic", UnexpectedStore())
        with pytest.raises(ToolRejected, match="MXC.*命令未执行"):
            await execShellTool.handler({"command": command}, ctx)
        ctx.managedShell = managed
        for background in (False, True):
            with pytest.raises(ToolRejected, match="MXC.*命令未执行"):
                await execShellTool.handler({"command": command, "background": background}, ctx)
            with pytest.raises(ToolRejected, match="MXC.*命令未执行"):
                await managed.execute(selected_plan, threading.Event(), background=background)
            with pytest.raises(ToolRejected, match="不能整次绕过"):
                await execShellTool.handler({"command": command, "background": background, "dangerouslyDisableSandbox": True}, ctx)
        assert not output.exists() and not backup.exists() and not (tmp_path / "session").exists()
        ctx.managedShell = None
        with pytest.raises(ToolRejected, match="不能整次绕过"):
            await execShellTool.handler({"command": command, "dangerouslyDisableSandbox": True}, ctx)
        ctx.getShellPlan = lambda *_: buildShellPlan(command, str(tmp_path), environ={**environment, "RUOCUN_SANDBOX": "0"},
                                                     analyze=False)
        with pytest.raises(ToolRejected, match="MXC.*命令未执行"):
            await execShellTool.handler({"command": command}, ctx)
        assert not output.exists() and not backup.exists()

        ctx.sandboxEnabled = False
        with pytest.raises(ToolRejected, match="未启用系统沙箱"):
            await execShellTool.handler({"command": command, "additionalWriteDirectories": [str(tmp_path)]}, ctx)
        assert not output.exists()
        result = await execShellTool.handler({"command": command}, ctx)
        assert "[exit 0" in result
        assert output.read_text().strip() == "ok"
        assert backup.read_text() == "existing test data"

    asyncio.run(scenario())

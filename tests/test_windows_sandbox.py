"""Pinned backend preparation, frozen profiles and raw Windows command contract."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from ruocun.platform.process_tree import run_foreground_plan
from ruocun.platform.shell_plan import buildShellPlan
from ruocun.platform.windows import process_tree, sandbox
from ruocun.tools.exec_shell import execShellTool
from ruocun.tools.types import ToolContext, ToolRejected

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows paths and private files")


@pytest.fixture
def backend(tmp_path, monkeypatch):
    cli = tmp_path / "dependency" / "wxc-exec.exe"
    cli.parent.mkdir()
    cli.write_bytes(b"synthetic CLI identity; never executed")
    monkeypatch.setattr(sandbox, "MXC_SHA256", hashlib.sha256(cli.read_bytes()).hexdigest())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    options = dict(executable=str(cli), stateRoot=str(tmp_path / "control"),
                   writableRoots=(str(workspace),), tempRoot=str(tmp_path / "temp"), cacheRoot=str(tmp_path / "cache"))
    env = sandbox.windowsSandboxEnvironment(
        {"SystemRoot": r"C:\Windows", "COMSPEC": r"C:\Windows\System32\cmd.exe",
         "PATH": str(tmp_path / "synthetic-bin"), "HOME": str(tmp_path / "existing-home"),
         "USERPROFILE": str(tmp_path / "existing-home"), "APPDATA": str(tmp_path / "existing-appdata"),
         "MAVEN_OPTS": "-Xmx128m", "SYNTHETIC_NORMAL": "retained", "SYNTHETIC_API_KEY": "removed"},
        stateRoot=options["stateRoot"], tempRoot=options["tempRoot"], cacheRoot=options["cacheRoot"])
    plan = buildShellPlan('echo "中文 空格 & argument"', str(workspace), environ=env, analyze=False)
    return options, plan


def test_environment_is_prepared_once_and_preserves_home_and_existing_options(backend):
    options, plan = backend
    env = dict(plan.environment)
    again = sandbox.windowsSandboxEnvironment(env, stateRoot=options["stateRoot"],
        tempRoot=options["tempRoot"], cacheRoot=options["cacheRoot"])
    assert again == env and env["HOME"].endswith("existing-home")
    assert env["USERPROFILE"] == env["HOME"] and env["APPDATA"].endswith("existing-appdata")
    assert env["MAVEN_OPTS"].startswith("-Xmx128m ") and env["MAVEN_OPTS"].count("-Dmaven.repo.local=") == 1
    assert env["SystemDrive"] == "C:" and env["SYNTHETIC_NORMAL"] == "retained"
    assert "SYNTHETIC_API_KEY" not in env
    for name in ("stateRoot", "tempRoot", "cacheRoot"):
        assert Path(options[name]).is_dir()
        sandbox.ensure_private_dir(Path(options[name]))


def test_environment_refuses_existing_public_temp_without_changing_acl(tmp_path):
    from ruocun.platform.windows.durable_file import _read_acl_sddl

    temporary = tmp_path / "ordinary-temp"
    temporary.mkdir()
    before = _read_acl_sddl(temporary)
    with pytest.raises(sandbox.WindowsSandboxError, match="启动前安全准备"):
        sandbox.windowsSandboxEnvironment({"SystemRoot": r"C:\Windows"},
            stateRoot=str(tmp_path / "control"), tempRoot=str(temporary), cacheRoot=str(tmp_path / "cache"))
    assert _read_acl_sddl(temporary) == before


def test_executor_consumes_a_frozen_native_process_container_policy(backend):
    options, plan = backend
    wrapped = sandbox.windowsSandboxExecutionPlan(plan, **options, noNet=True)
    executable, policy_file = wrapped.argv
    assert executable == options["executable"]
    policy_path = Path(policy_file)
    assert policy_path.parent == Path(options["stateRoot"])
    request = json.loads(policy_path.read_text(encoding="utf-8"))
    assert request["version"] == "1.0.0" and request["containment"] == "processcontainer"
    assert request["process"]["cwd"] == plan.cwd
    assert request["process"]["inheritDefaultEnv"] is False
    assert request["network"]["egress"]["default"] == "deny"
    assert request["filesystem"]["readwritePaths"] == sorted([
        options["writableRoots"][0], options["tempRoot"], options["cacheRoot"],
    ])


def test_snapshot_keeps_raw_command_and_environment_and_freezes_each_grant(backend, tmp_path):
    options, plan = backend
    wrapped = sandbox.windowsSandboxExecutionPlan(plan, **options)
    assert wrapped.environment == plan.environment and wrapped.command == plan.command and wrapped.commandLine is None
    profile_path = Path(wrapped.argv[1])
    original = profile_path.read_bytes()
    profile = json.loads(original)
    arguments = _windows_arguments(profile["process"]["commandLine"])
    assert arguments[3:6] == [plan.commandLine, plan.argv[0], options["tempRoot"]]
    rules = profile["filesystem"]
    assert options["writableRoots"][0] in rules["readwritePaths"]
    assert options["stateRoot"] in rules["readonlyPaths"]
    assert str(Path(options["executable"]).parent) in rules["readonlyPaths"]
    extra = tmp_path / "approved-once"
    extra.mkdir()
    expanded = sandbox.windowsSandboxExecutionPlan(plan, **{**options, "writableRoots": (*options["writableRoots"], str(extra))})
    assert expanded.argv[1] != wrapped.argv[1] and profile_path.read_bytes() == original
    assert str(extra) in json.loads(Path(expanded.argv[1]).read_bytes())["filesystem"]["readwritePaths"]
    restored = sandbox.windowsSandboxExecutionPlan(plan, **options)
    assert str(extra) not in json.loads(Path(restored.argv[1]).read_bytes())["filesystem"]["readwritePaths"]
    no_net = sandbox.windowsSandboxExecutionPlan(plan, **options, noNet=True)
    network = json.loads(Path(no_net.argv[1]).read_bytes())["network"]
    assert network["egress"]["default"] == network["ingress"]["hostLoopback"] == "deny"


def _windows_arguments(command: str) -> list[str]:
    count = ctypes.c_int()
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    args = parse(command, ctypes.byref(count))
    try:
        return args[:count.value]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(args, ctypes.c_void_p))


def test_changed_configuration_is_rejected_without_overwriting(backend):
    options, plan = backend
    wrapped = sandbox.windowsSandboxExecutionPlan(plan, **options)
    selected = Path(wrapped.argv[1])
    selected.write_text("# synthetic changed configuration\n", encoding="utf-8")
    with pytest.raises(sandbox.WindowsSandboxError, match="配置已改变"):
        sandbox.windowsSandboxLaunchMarkers(wrapped)
    assert selected.read_text(encoding="utf-8") == "# synthetic changed configuration\n"


def test_bash_returns_known_rejection_when_mxc_cannot_apply_its_request(backend, monkeypatch):
    options, original = backend
    environment = dict(original.environment)
    environment["RUOCUN_MXC_EXEC"] = options["executable"]
    original = replace(original, environment=tuple(sorted(environment.items())))

    def refuse_probe(argv, **kwargs):
        assert argv[1] == "--probe" and Path(argv[2]).is_file()
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(subprocess, "run", refuse_probe)
    ctx = ToolContext(original.cwd, getShellPlan=lambda *_: original, sandboxEnabled=True)
    with pytest.raises(ToolRejected, match="MXC.*命令未执行"):
        asyncio.run(execShellTool.handler({"command": original.command}, ctx))


def test_real_start_frame_is_consumed_before_recording_output(backend, monkeypatch):
    options, plan = backend
    wrapped = sandbox.windowsSandboxExecutionPlan(plan, **options)
    markers = sandbox.windowsSandboxLaunchMarkers(wrapped)
    assert markers is not None
    marker, _ = markers
    started: list[str] = []

    def launch(*args, stdoutFilter, onChunk, **kwargs):
        assert not started
        assert stdoutFilter(marker[:8]) == b""
        assert not started
        stdout = stdoutFilter(marker[8:] + b"workload output\r\n")
        assert started == ["started"]
        onChunk(0, stdout)
        return stdout, b"", 7, False, False

    monkeypatch.setattr(process_tree, "run_managed", launch)
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 0))
    result = run_foreground_plan(wrapped, threading.Event(), onStarted=lambda: started.append("started"))
    assert result.code == 7 and result.stdout.strip() == "workload output"
    assert result.stdoutRaw == b"workload output\r\n"
    assert not Path(wrapped.argv[1]).exists()


def test_pin_missing_dependency_unfrozen_env_and_protected_grants_fail_closed(backend, tmp_path):
    options, plan = backend
    with pytest.raises(sandbox.WindowsSandboxError, match="缺少固定"):
        sandbox.windowsSandboxExecutionPlan(plan, **{**options, "executable": str(tmp_path / "missing.exe")})
    cli = Path(options["executable"])
    cli.write_bytes(b"synthetic different build")
    with pytest.raises(sandbox.WindowsSandboxError, match="构建或文件已改变"):
        sandbox.windowsSandboxExecutionPlan(plan, **options)
    cli.write_bytes(b"synthetic CLI identity; never executed")
    with pytest.raises(sandbox.WindowsSandboxError, match="审批前冻结"):
        sandbox.windowsSandboxExecutionPlan(replace(plan, environment=tuple((k, v) for k, v in plan.environment if k != "TEMP")), **options)
    for protected in (options["stateRoot"], str(cli.parent)):
        with pytest.raises(sandbox.WindowsSandboxError, match="受保护"):
            sandbox.windowsSandboxExecutionPlan(plan, **{**options, "writableRoots": (protected,)})
    with pytest.raises(sandbox.WindowsSandboxError, match="保真"):
        sandbox.windowsSandboxExecutionPlan(replace(plan, commandLine="synthetic different raw line"), **options)
    assert not list(Path(options["stateRoot"]).glob("*.json"))


def test_excessive_command_is_rejected_before_control_files(backend):
    options, plan = backend
    command = "echo " + "长" * 33000
    oversized = buildShellPlan(command, plan.cwd, environ=dict(plan.environment), analyze=False)
    with pytest.raises(sandbox.WindowsSandboxError, match="长度限制"):
        sandbox.windowsSandboxExecutionPlan(oversized, **options)
    assert not list(Path(options["stateRoot"]).glob("*.json"))

"""通过固定版本的微软执行器创建 Windows MXC 沙箱执行计划。

提前准备私有临时目录，并把控制目录和解释器等可信资源排除在可写范围外。执行时沿用审批依据中的命令、工作目录和环境。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import re
import secrets
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from ..file_contract import AtomicFileError
from ..process_tree import scrubbed_env
from ..shell_plan import ShellExecutionPlan
from .durable_file import (
    PrivateDirectory,
    atomic_publish,
    ensure_private_dir,
    local_path,
    verify_private_file,
)

MXC_VERSION = "1.1.0"
MXC_SHA256 = "cb8f9253a2288bbb5e980d63216068ea3b640ebaf845ba3fccc0d3fe822f7a0d"
_MAX_COMMAND_UNITS = 32766
_POLICY_NAME = re.compile(r"ruocun-([0-9a-f]{32})-([0-9a-f]{32})\.json")


class WindowsSandboxError(RuntimeError):
    """Windows 沙箱无法在指定边界内准备，原命令尚未启动。"""


def _absolute(value: str) -> Path:
    if value.startswith("\\\\?\\"):
        value = value[4:]
    path = Path(value)
    if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or any(ord(char) < 32 for char in value)):
        raise WindowsSandboxError("Windows沙箱要求固定本地卷上的绝对路径。")
    return path.resolve()


def _within(path: Path, root: Path) -> bool:
    return path.is_relative_to(root)


def _setenv(values: dict[str, str], name: str, value: str) -> None:
    for key in tuple(values):
        if key.casefold() == name.casefold():
            del values[key]
    values[name] = value


def windowsSandboxEnvironment(
    environ: Mapping[str, str], *, stateRoot: str, tempRoot: str, cacheRoot: str,
) -> dict[str, str]:
    """审批前准备沙箱子进程的环境，沿用 HOME 和 PATH。"""
    state, temporary, cache = map(_absolute, (stateRoot, tempRoot, cacheRoot))
    values = scrubbed_env(environ)
    lowered = {key.casefold(): value for key, value in values.items()}
    system = _absolute(lowered.get("systemroot", r"C:\Windows"))
    _setenv(values, "SystemRoot", str(system))
    _setenv(values, "SystemDrive", system.drive)
    if not lowered.get("localappdata"):
        _setenv(values, "LOCALAPPDATA", str(state))
    for name, value in (
        ("TEMP", temporary), ("TMP", temporary), ("TMPDIR", temporary),
        ("PIP_CACHE_DIR", cache / "pip"), ("npm_config_cache", cache / "npm"),
        ("GRADLE_USER_HOME", cache / "gradle"),
    ):
        _setenv(values, name, str(value))
    maven = next((value for key, value in values.items() if key.casefold() == "maven_opts"), "")
    repository = f'-Dmaven.repo.local="{cache / "maven"}"'
    _setenv(values, "MAVEN_OPTS", maven if repository in maven else (maven + " " + repository).strip())
    # 先创建带私有权限的目录，再把 TEMP 指向它。
    # 进程身份查询等启动组件也会尝试创建 TEMP，
    # 如果在它们运行前目录不存在，就可能继承普通 ACL。
    try:
        for directory in (state, temporary, cache):
            _private_tree(directory)
    except (AtomicFileError, OSError) as error:
        raise WindowsSandboxError("Windows沙箱受控目录无法在启动前安全准备。") from error
    return values


def _private_tree(path: Path) -> None:
    missing: list[Path] = []
    ancestor = path
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    for item in reversed(missing):
        ensure_private_dir(item)
    ensure_private_dir(path)


def _immutable(path: Path, content: bytes) -> None:
    if path.exists():
        verify_private_file(path)
        if path.read_bytes() != content:
            raise WindowsSandboxError("Windows沙箱受控配置已改变，拒绝覆盖或执行。")
    else:
        atomic_publish(path, content)


def _executable(value: str) -> Path:
    executable = _absolute(value)
    if executable.name.casefold() != "wxc-exec.exe" or not executable.is_file():
        raise WindowsSandboxError(f"缺少固定 Microsoft MXC {MXC_VERSION} 执行器，请配置官方 wxc-exec.exe 的绝对路径。")
    digest = hashlib.sha256()
    with executable.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != MXC_SHA256:
        raise WindowsSandboxError(f"MXC 执行器不是已核验的官方 {MXC_VERSION} x64 构建或文件已改变，拒绝执行。")
    return executable


def validateWindowsSandbox(executable: str) -> None:
    """核对固定的本地 CLI 依赖，验证过程不启动命令。"""
    _executable(executable)


def _readable_volumes() -> set[Path]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    drives = kernel.GetLogicalDrives()
    if not drives:
        raise ctypes.WinError(ctypes.get_last_error())
    return {Path(root) for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            if drives & (1 << index) and kernel.GetDriveTypeW(root := f"{letter}:\\") in {2, 3, 5, 6}}


def _profile(command: str, cwd: str, environment: Mapping[str, str], writable: set[Path],
             protected: set[Path], no_net: bool) -> bytes:
    network_access = "deny" if no_net else "allow"
    config = {
        "version": "1.0.0", "containment": "processcontainer",
        "process": {"commandLine": command, "cwd": cwd, "inheritDefaultEnv": False,
                    "env": [f"{name}={value}" for name, value in environment.items()]},
        "filesystem": {"readwritePaths": sorted(map(str, writable)),
                       "readonlyPaths": sorted(map(str, _readable_volumes() | protected))},
        "network": {"egress": {"default": network_access},
                    "ingress": {"default": network_access, "hostLoopback": network_access}},
        # 开发工具仍需载入 Windows 用户界面系统库。文件与网络策略独立生效。
        "ui": {"disable": False},
    }
    return json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def windowsSandboxWaitsForWorkload(plan: ShellExecutionPlan) -> bool:
    return bool(plan.argv) and Path(plan.argv[0]).name.casefold() == "wxc-exec.exe"


def _verified_request(plan: ShellExecutionPlan) -> tuple[Path, str]:
    if len(plan.argv) != 2 or plan.commandLine is not None:
        raise WindowsSandboxError("Windows 沙箱执行参数未冻结。")
    path = _absolute(plan.argv[1])
    match = _POLICY_NAME.fullmatch(path.name)
    if match is None or (plan.sandbox is not None and path.parent != _absolute(plan.sandbox.stateRoot)):
        raise WindowsSandboxError("Windows 沙箱策略路径未冻结。")
    access_path = local_path(Path(plan.argv[1]))
    verify_private_file(access_path)
    if hashlib.sha256(access_path.read_bytes()).hexdigest()[:32] != match[2]:
        raise WindowsSandboxError("Windows 沙箱受控配置已改变，拒绝执行。")
    return access_path, match[1]


def windowsSandboxLaunchMarkers(plan: ShellExecutionPlan) -> tuple[bytes, bytes] | None:
    """核验已冻结的执行策略，返回原命令启动和拒绝的控制帧。"""
    if not windowsSandboxWaitsForWorkload(plan):
        return None
    _executable(plan.argv[0])
    _, launch_id = _verified_request(plan)
    from .mxc_bridge import launch_marker
    return launch_marker(launch_id), launch_marker(launch_id, rejected=True)


def probeWindowsSandboxRequest(plan: ShellExecutionPlan) -> None:
    """用固定的 MXC 检查已核验的请求。探测模式永远不启动原命令。"""
    try:
        result = subprocess.run(
            (plan.argv[0], "--probe", plan.argv[1]), cwd=plan.cwd,
            env=scrubbed_env(dict(plan.environment)), creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WindowsSandboxError("无法完成 MXC 策略预检，命令未执行。") from error
    if result.returncode != 0:
        raise WindowsSandboxError("当前宿主无法应用 MXC 沙箱策略，命令未执行。")


def discardWindowsSandboxRequest(plan: ShellExecutionPlan) -> None:
    """进程树已退出时清理一次性请求。签名运行记录与日志独立保留。"""
    try:
        path = _absolute(plan.argv[1])
        with PrivateDirectory(path.parent):
            verified, _ = _verified_request(plan)
            verified.unlink()
    except (WindowsSandboxError, AtomicFileError, OSError):
        # 请求清理失败时保留文件。进程树已确认的结果仍可交付。
        pass


def windowsSandboxExecutionPlan(
    plan: ShellExecutionPlan, *, executable: str, stateRoot: str, writableRoots: Sequence[str],
    tempRoot: str, cacheRoot: str, protectedRoots: Sequence[str] = (), noNet: bool = False,
) -> ShellExecutionPlan:
    """将已获批准的环境和限制转为固定的微软执行器调用。"""
    if sys.platform != "win32":
        raise WindowsSandboxError("Windows MXC后端仅支持原生Windows。")
    try:
        cli = _executable(executable)
        state, temporary, cache = map(_absolute, (stateRoot, tempRoot, cacheRoot))
        environment = dict(plan.environment)
        if windowsSandboxEnvironment(environment, stateRoot=stateRoot, tempRoot=tempRoot, cacheRoot=cacheRoot) != environment:
            raise WindowsSandboxError("沙箱环境尚未在审批前冻结，命令未执行。")
        python = _absolute(sys.executable)
        bridge = Path(__file__).resolve().with_name("mxc_bridge.py")
        protected = {state, cli.parent, bridge, *map(_absolute, protectedRoots),
                     _absolute(sys.prefix), _absolute(sys.base_prefix)}
        writable = {_absolute(root) for root in writableRoots} | {temporary, cache}
        if not writableRoots or any(_within(root, blocked) for root in writable for blocked in protected):
            raise WindowsSandboxError("沙箱写目录与受保护控制文件或运行库冲突，命令未执行。")
        command = plan.commandLine
        expected = f'"{plan.argv[0]}" /d /s /c "{plan.command}"' if plan.argv else None
        if (plan.dialect != "cmd" or command is None or command != expected
                or plan.argv[1:] != ("/d", "/s", "/c", plan.command)
                or Path(plan.argv[0]).name.casefold() != "cmd.exe"):
            raise WindowsSandboxError("Windows沙箱不能保真执行该Shell计划，命令未执行。")
        launch_id = secrets.token_hex(16)
        bridged = subprocess.list2cmdline([str(python), "-I", str(bridge), command, plan.argv[0], str(temporary), launch_id])
        for text in (command, bridged):
            if len(text.encode("utf-16-le")) // 2 > _MAX_COMMAND_UNITS:
                raise WindowsSandboxError("Windows沙箱命令超过原生命令行长度限制，命令未执行。")
        content = _profile(bridged, plan.cwd, environment, writable, protected, noNet)
        path = local_path(state / f"ruocun-{launch_id}-{hashlib.sha256(content).hexdigest()[:32]}.json")
        with PrivateDirectory(state):
            _immutable(path, content)
        return replace(plan, argv=(str(cli), str(path)), commandLine=None)
    except WindowsSandboxError:
        raise
    except (AtomicFileError, OSError, ValueError) as error:
        raise WindowsSandboxError("Windows沙箱依赖或受控目录不可用，命令未执行。") from error

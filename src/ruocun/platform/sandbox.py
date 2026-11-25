"""按实际平台封装受管 Shell 命令计划。

Windows 使用 MXC，macOS 使用 sandbox-exec，Linux 使用 bwrap。缺少后端或无法准备策略时拒绝启动。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, TypedDict

from ..config.compat import resolve_env

if TYPE_CHECKING:
    from .shell_plan import ShellExecutionPlan

_BINARY_DIRS = ("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin")
_BYPASS_ERROR = "受管 Shell 沙箱不能整次绕过。"


class SandboxUnavailable(RuntimeError):
    """请求的操作系统沙箱无法准备，命令未启动。"""


class SpawnSpec(TypedDict):
    file: str
    args: list[str]


class SandboxError(TypedDict):
    error: str


def sandboxNoNet(environ: Mapping[str, str] | None = None) -> bool:
    return resolve_env("SANDBOX_NO_NET", os.environ if environ is None else environ) == "1"


def _binary(name: str, exists: Callable[[str], bool]) -> str | None:
    return next((f"{directory}/{name}" for directory in _BINARY_DIRS if exists(f"{directory}/{name}")), None)


def _seatbelt(cwd: str, no_net: bool) -> str:
    workspace = cwd.replace("\\", "\\\\").replace('"', '\\"')
    return (
        '(version 1) (allow default) (deny file-write*) '
        f'(allow file-write* (subpath "{workspace}") (subpath "/tmp") (subpath "/private/tmp") '
        '(subpath "/private/var/folders") (literal "/dev/null") (literal "/dev/stdout") '
        '(literal "/dev/stderr") (literal "/dev/dtracehelper"))'
        + (" (deny network*)" if no_net else "")
    )


def sandboxSpawn(command: str, cwd: str, disableSandbox: bool = False, *,
                 environ: Mapping[str, str] | None = None, platform: str | None = None,
                 exists: Callable[[str], bool] = os.path.exists) -> SpawnSpec | SandboxError:
    """为 POSIX 沙箱组装启动参数。Windows 需要完整的原生命令计划。"""
    if disableSandbox:
        return {"error": _BYPASS_ERROR}
    current = sys.platform if platform is None else platform
    shell = ["/bin/sh", "-c", command]
    no_net = sandboxNoNet(environ)
    if current == "darwin":
        binary = _binary("sandbox-exec", exists)
        if binary is None:
            return {"error": "找不到 sandbox-exec，受管 Shell 命令未执行。"}
        return {"file": binary, "args": ["-p", _seatbelt(cwd, no_net), *shell]}
    if current == "linux":
        binary = _binary("bwrap", exists)
        if binary is None:
            return {"error": "找不到 bwrap(请装 bubblewrap)，受管 Shell 命令未执行。"}
        # 先挂载私有临时目录，再绑定工作区，避免位于 /tmp 的工作区被掩盖。
        args = ["--ro-bind", "/", "/", "--tmpfs", "/tmp", "--bind", cwd, cwd,
                "--proc", "/proc", "--dev", "/dev", "--die-with-parent"]
        if no_net:
            args.append("--unshare-net")
        return {"file": binary, "args": [*args, *shell]}
    if current == "win32":
        return {"error": "Windows MXC 沙箱需要原生 Shell 执行计划。"}
    return {"error": "当前平台不支持受管 Shell 沙箱，命令未执行。"}


def sandboxExecutionPlan(plan: ShellExecutionPlan, disableSandbox: bool = False) -> ShellExecutionPlan:
    """用本平台的沙箱包装已审批命令，保留计划中的原命令与工作目录。"""
    if disableSandbox:
        raise SandboxUnavailable(_BYPASS_ERROR)
    if sys.platform == "win32":
        from .sandbox_policy import canonical, createSandboxPolicy, sandboxEnvironment
        from .windows.sandbox import (
            WindowsSandboxError,
            validateWindowsSandbox,
            windowsSandboxExecutionPlan,
        )
        try:
            policy = plan.sandbox or createSandboxPolicy(plan.cwd, dict(plan.environment))
            if policy is None:
                raise SandboxUnavailable("Windows MXC 沙箱缺少执行策略，命令未执行。")
            if plan.sandbox is not None and plan.argv and plan.commandLine is None and canonical(plan.argv[0]) == policy.executable:
                validateWindowsSandbox(policy.executable)
                return plan
            environment = sandboxEnvironment(dict(plan.environment), policy)
            frozen = replace(plan, environment=tuple(sorted(environment.items())), sandbox=policy)
            return windowsSandboxExecutionPlan(
                frozen, executable=policy.executable, stateRoot=policy.stateRoot,
                writableRoots=policy.writableRoots, tempRoot=policy.tempRoot, cacheRoot=policy.cacheRoot,
                protectedRoots=policy.protectedRoots, noNet=sandboxNoNet(environment),
            )
        except (OSError, ValueError, WindowsSandboxError) as error:
            raise SandboxUnavailable(f"Windows MXC 沙箱启动准备失败，命令未执行：{error}") from error
    spec = sandboxSpawn(plan.command, plan.cwd, environ=dict(plan.environment))
    if "error" in spec:
        raise SandboxUnavailable(spec["error"])
    argv = (spec["file"], *spec["args"])
    if plan.argv == argv and plan.commandLine is None:
        return plan
    return replace(plan, argv=argv, commandLine=None)

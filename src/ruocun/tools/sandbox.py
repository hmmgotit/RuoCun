"""以命令的启动目录绑定 Windows 写入策略。额外写入目录经逐次审批加入该计划。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..platform.file_contract import AtomicFileError
from ..platform.sandbox_policy import createSandboxPolicy, sandboxEnvironment
from ..platform.shell_plan import ShellExecutionPlan
from ..platform.windows.sandbox import WindowsSandboxError
from .types import ToolContext, ToolRejected


async def prepareSandboxPlan(plan: ShellExecutionPlan, ctx: ToolContext, requested: Any = None) -> ShellExecutionPlan:
    roots = [] if requested is None else requested
    if not isinstance(roots, list) or len(roots) > 8 or not all(isinstance(root, str) and root for root in roots):
        raise ToolRejected("additionalWriteDirectories 必须是最多8个绝对目录的列表。")
    try:
        policy = plan.sandbox or createSandboxPolicy(plan.cwd, dict(plan.environment))
        environment = tuple(sorted(sandboxEnvironment(dict(plan.environment), policy).items()))
    except (OSError, ValueError, AtomicFileError, WindowsSandboxError) as error:
        raise ToolRejected(f"沙箱环境准备失败，命令未执行：{error}") from error
    if policy is None:
        if roots:
            raise ToolRejected("本次额外目录参数仅用于 Windows 沙箱。")
        return plan
    if plan.sandbox != policy or plan.environment != environment:
        plan = replace(plan, sandbox=policy, environment=environment)
    if roots:
        if ctx.authorizeSandboxShell is None:
            raise ToolRejected("未绑定本次额外目录的用户审批入口，命令未执行。")
        plan = await ctx.authorizeSandboxShell(plan, roots)
    return plan

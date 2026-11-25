"""在现有受管进程树上按批交付 Monitor 的输出行，沿用受管执行的控制和回收路径。"""

from __future__ import annotations

import asyncio
import os
import secrets
import shlex
import threading
from collections.abc import Awaitable, Callable
from dataclasses import replace
from time import monotonic
from typing import Literal

from ..platform.process_tree import ShellLaunchUnconfirmed, run_foreground_plan
from ..platform.sandbox import SandboxUnavailable, sandboxExecutionPlan
from ..platform.shell_plan import ShellExecutionPlan


async def runMonitor(plan: ShellExecutionPlan, description: str, signal: threading.Event, *,
                     timeout_ms: int | None, emit: Callable[[str], Awaitable[None]],
                     onStarted: Callable[[], Awaitable[None]] | None = None) -> str:
    plan = sandboxExecutionPlan(plan)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    pending = ""
    if os.name != "nt":
        # 启动确认由沙箱内的固定前置命令输出。确认写出后才运行原命令。
        token = secrets.token_hex(16)
        pending = token + "\n"
        command = f"printf '%s\\n' {token} && exec /bin/sh -c {shlex.quote(plan.command)}"
        plan = replace(plan, argv=(*plan.argv[:-1], command))

    def started() -> None:
        loop.call_soon_threadsafe(queue.put_nowait, None)

    def output(stream: Literal["stdout", "stderr"], text: str) -> None:
        nonlocal pending
        if stream != "stdout":
            return
        if pending:
            size = min(len(pending), len(text))
            if text[:size] != pending[:size]:
                raise ShellLaunchUnconfirmed("监控的沙箱启动确认无效。")
            pending, text = pending[size:], text[size:]
            if not pending:
                started()
        if text:
            loop.call_soon_threadsafe(queue.put_nowait, text)

    execution = asyncio.create_task(asyncio.to_thread(run_foreground_plan, plan, signal, onLogOutput=output, onStarted=started))
    end = monotonic() + timeout_ms / 1000 if timeout_ms is not None else None
    timed_out, partial = False, ""
    lines: list[str] = []
    deadline: float | None = None
    try:
        while not execution.done() or not queue.empty():
            now = monotonic()
            if end is not None and now >= end and not signal.is_set():
                timed_out = True
                signal.set()
            try:
                chunk = await asyncio.wait_for(queue.get(), .1)
            except TimeoutError:
                chunk = ""
            if chunk is None:
                if onStarted is not None:
                    await onStarted()
                continue
            if chunk:
                pieces = (partial + chunk).split("\n")
                partial = pieces.pop()
                lines.extend(line.removesuffix("\r") for line in pieces if line.removesuffix("\r"))
                if lines and deadline is None:
                    deadline = monotonic() + .2
            if lines and deadline is not None and monotonic() >= deadline:
                await emit("\n".join(lines))
                lines, deadline = [], None
        result = await execution
        if partial.strip():
            lines.append(partial)
        if lines:
            await emit("\n".join(lines))
        if timed_out:
            return f"监控超时({timeout_ms}ms)已自动终止:{description}"
        if result.aborted:
            return f"监控已被取消:{description}"
        if pending:
            raise SandboxUnavailable("系统沙箱拒绝启动监控命令。")
        return f"监控结束(命令已退出,exit {result.code}):{description}"
    finally:
        if not execution.done():
            signal.set()
        await asyncio.shield(execution)

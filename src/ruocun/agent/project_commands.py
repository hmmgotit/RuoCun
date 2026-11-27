"""处理前台项目记忆的向量配置和选定历史补录。"""
from __future__ import annotations

import asyncio
import shlex

from ..memory.command import memoryCommand
from ..memory.project_search import rebuildProjectIndex
from ..memory.project_store import ProjectMemory
from ..memory.project_vectors import configureVectors, vectorStatus
from ..memory.store import MemoryStore
from ..session.contract import RuntimeRecord
from .project_learning import backfill_project


async def projectCommand(text: str, project: ProjectMemory, store: MemoryStore, runtime: RuntimeRecord) -> str | None:
    if text.split()[:2] != ["/memory", "project"]:
        return None
    try:
        words = shlex.split(text, posix=False)
        args = [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in (chr(34), "'") else w for w in words[2:]]
        if args in ([], ["status"], ["enable"]):
            status = await memoryCommand(text, store, project=project)
            return (status or "") + "\n" + await asyncio.to_thread(vectorStatus, project)
        if not args or args[0] not in ("backfill", "vectors"):
            return None
        if not project.enabled:
            return "当前工作区尚未开启工程记忆。"
        match args:
            case ["vectors"] | ["vectors", "status"]:
                return await asyncio.to_thread(vectorStatus, project)
            case ["vectors", "off"]:
                return await asyncio.to_thread(configureVectors, project, "off")
            case ["vectors", "local", *path] if len(path) <= 1:
                return await asyncio.to_thread(configureVectors, project, "local", model=path[0] if path else "")
            case ["vectors", "remote", endpoint, model, *key] if len(key) <= 1:
                return await asyncio.to_thread(configureVectors, project, "remote", endpoint=endpoint, model=model,
                                               key_env=key[0] if key else "")
            case ["vectors", "rebuild"]:
                return await rebuildProjectIndex(store, project)
            case ["backfill", "session", *ids] if ids:
                counts = await asyncio.to_thread(backfill_project, project, runtime, session_ids=ids)
            case ["backfill", "range", lower, upper] if lower.isdecimal() and upper.isdecimal():
                counts = await asyncio.to_thread(backfill_project, project, runtime, since_ms=int(lower),
                                                 until_ms=int(upper))
            case ["vectors", *_]:
                return ("用法：/memory project vectors status|off|local [本机模型绝对路径]|"
                        "remote <embeddings地址> <模型> [密钥环境变量名]|rebuild")
            case _:
                return "用法：/memory project backfill session <会话ID…> 或 range <开始UTC毫秒> <结束UTC毫秒>。"
        return (f"补录完成。扫描会话 {counts['scanned']}，已入库 {counts['added']}，"
                f"已存在 {counts['existing']}，跳过 {counts['skipped']}。")
    except ValueError as exc:
        return "项目记忆操作未完成：" + str(exc)
    except OSError:
        return "项目记忆文件无法读取，操作未完成。"

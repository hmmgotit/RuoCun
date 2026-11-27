"""从当前执行明确绑定的记忆库读取内容，按精确名称或关键词返回相关记忆。"""

from __future__ import annotations

import asyncio
import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..config.compat import DEFAULT_COMPATIBILITY
from ..memory.project_projection import mark_memory
from ..memory.project_store import ProjectMemory
from ..memory.store import (
    MemoryEntry,
    MemoryStore,
    keepKnowledgeForProject,
    projectIdOf,
)
from ..memory.types import Memory
from ..memory.validate import validateMemory
from .schema import objectSchema
from .types import Tool, ToolContext

_TEXT: dict[str, Any] = json.loads(files("ruocun.tools").joinpath("resources/memory-read.json").read_text("utf-8"))


def _full(memory: Memory) -> str:
    source = f"·来源 {memory.source}" if memory.source else ""
    return f"[{memory.type}·重{memory.importance}·命中{memory.uses}{source}]\n{memory.text}"


async def _source_notice(entry: MemoryEntry, store: MemoryStore) -> str:
    memory = entry.memory
    same_project = entry.scope == "project" or (
        entry.scope == "knowledge" and memory.origin == projectIdOf(store.cwd)
    )
    if not same_project:
        return ""
    status = await validateMemory(memory, store.cwd, store.today())
    if status == "changed":
        return "来源文件已变更。请先读取当前来源文件，核对后再使用这条旧记录。"
    if status == "stale":
        return "来源无法核实或记录已过期。仅供回顾，使用前请核对当前项目规则。"
    return ""


def makeMemoryReadTool(store: MemoryStore, project: ProjectMemory | None = None) -> Tool:
    async def read(args: dict[str, Any], ctx: ToolContext) -> str:
        cwd = Path(ctx.cwd or ctx.workspaceRoot).resolve()
        if cwd != store.cwd.resolve() and (project is not None or ctx.subagentDepth):
            if project is None or not cwd.is_dir():
                return "当前执行工作区未绑定此记忆库。"
            local_store = MemoryStore(cwd, project.user_root, DEFAULT_COMPATIBILITY, today=store.today)
            local_project = ProjectMemory(project.user_root, cwd)
            return await makeMemoryReadTool(local_store, local_project).handler(args, ctx)
        entries = await store.load()
        tracked = project is not None and project.enabled
        if tracked or ctx.subagentDepth:
            entries = [e for e in entries if e.scope != "knowledge" or keepKnowledgeForProject(e.memory, projectIdOf(store.cwd))]
        memories = [entry.memory for entry in entries]
        async def render(memory: Memory, *, short: bool = False) -> str:
            entry = next(e for e in entries if e.memory is memory)
            text = f"- [{memory.type}·重{memory.importance}] {memory.title or memory.text}" if short else _full(memory)
            notice = await _source_notice(entry, store)
            if notice:
                text = f"{text}（{notice}）" if short else f"{notice}\n\n{text}"
            if tracked:
                return mark_memory(f"{entry.scope}/{memory.name}", entry.contentDigest, text)
            return text
        query: str = args["query"].strip()
        exact = next((e.memory for e in entries if query in (e.memory.name, e.memory.title)
                      or (tracked and query == f"{e.scope}/{e.memory.name}")), None)
        if exact is not None and args.get("mode") != "project":
            return await render(exact)
        shortcut = tracked and args.get("mode") != "markdown" and query.lower().startswith("project:")
        explicit = args.get("mode") == "project" or shortcut
        if shortcut:
            query = query.partition(":")[2].strip()
        if explicit and (not tracked or project is None):
            return "当前工作区尚未开启工程记忆。"
        if project is not None and tracked and args.get("mode") != "markdown" and re.fullmatch(r"pm-[0-9a-f]{32}", query):
            from ..memory.project_search import renderProjectRecord
            record = await asyncio.to_thread(project.get, query)
            if record is None:
                return "(未找到有效工程记忆)"
            body = await renderProjectRecord(project, record)
            if await asyncio.to_thread(project.get, query) != record:
                return "(工程记忆已失效)"
            await asyncio.to_thread(project.touch, record.id, record.version)
            return mark_memory(record.id, str(record.version), body)
        if explicit and project is not None:
            from ..memory.project_search import buildProjectRecall
            result = await buildProjectRecall(store, project, query, maxItems=args.get("limit", 6), maxChars=6000)
            return result.text or "(未找到有效工程记忆)"
        if not memories:
            return "(暂无记忆)"
        terms = query.lower().split()
        hits = []
        for memory in memories:
            haystack = f"{memory.name} {memory.title or ''} {memory.text}".lower()
            if all(term in haystack for term in terms):
                hits.append(memory)
                if len(hits) == args.get("limit", 6):
                    break
        if not hits:
            return f"(未找到记忆:{query})"
        if len(hits) == 1:
            return await render(hits[0])
        return "\n".join([await render(memory, short=True) for memory in hits])

    descriptions = _TEXT["parameters"]
    parameters: dict[str, Any] = {
        "query": {"type": "string", "minLength": 1, "description": descriptions["query"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": descriptions["limit"]},
    }
    description = str(_TEXT["description"])
    description_en = str(_TEXT["descriptionEn"])
    if project is not None and project.enabled:
        parameters["mode"] = {"type": "string", "enum": ["markdown", "project"],
                              "description": "project 同时检索本项目 Markdown 与获准工程摘录。默认查 Markdown。"}
        description += " 工程摘录可以用 pm-ID 直接读取；mode=project 或 project: 查询做项目混合查找。"
        description_en += " Use a pm-ID or mode=project for authorized project engineering evidence."
    return Tool(
        name="MemoryRead", description=description, descriptionEn=description_en,
        capability="read", handler=read,
        schema=objectSchema(parameters, ["query"]),
    )

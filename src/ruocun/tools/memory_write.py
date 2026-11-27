"""通过绑定的记忆库保存或删除条目。写入前检查凭据、确定存储范围，并为附带出处的条目记录来源指纹。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from ..memory.project_store import ProjectMemory, ProjectRecord
from ..memory.secrets import findSecrets
from ..memory.store import MemoryStore, projectIdOf, routeScope, slug
from ..memory.types import MemoryType, Scope, newMemory
from ..memory.validate import hashMemorySource
from .schema import objectSchema
from .types import Tool, ToolContext

_TEXT: dict[str, Any] = json.loads(files("ruocun.tools").joinpath("resources/memory-write.json").read_text("utf-8"))
_LABELS = {"project": "项目级", "user": "用户级", "knowledge": "知识库"}

_PROJECT_ID = re.compile(r"pm-[a-f0-9]{32}\b")
_FORGET = r"(?:忘[记掉]|删除|删掉|撤回|移除|forget\b|delete\b|remove\b|withdraw\b)"
_CORRECT = r"(?:更正|纠正|修正|修改|更新|改为|改成|改一下|correct\b|update\b|change\b|replace\b)"


def _explicit_request(request: str, *, delete: bool) -> bool:
    """只采信当前用户直接要求的动作；引用、否定、讨论删除方式不会获得写权。"""
    verb = _FORGET if delete else _CORRECT
    if re.search(rf"(?:不要|请勿|不得|不许|禁止|不能|别|do not\s+|don't\s+)\s*{verb}", request, re.I):
        return False
    start = r"^\s*(?:(?:请(?:你)?|麻烦(?:你)?|帮我|我想请你|please\b|could you\b|can you\b)\s*)*"
    return bool(re.search(start + rf"(?:{verb}|(?:把|将)[^。！？\n]{{1,220}}?{verb})", request, re.I))


def _request_names_record(request: str, identity: str, records: list[ProjectRecord]) -> bool:
    ids = set(_PROJECT_ID.findall(request))
    if ids:
        return ids == {identity}
    phrases = re.findall(r'["“「『]([^"”」』\n]{2,200})["”」』]', request)
    subject = re.search(r"(?:关于|忘记|忘掉|删除|撤回)\s*(?:关于)?([^。！？\n]{2,80}?)"
                        r"(?:的(?:那|这)?一?条|(?:那|这)一?条|的)(?:工程|项目)?(?:记忆|记录|约定|经验|规则)", request)
    if subject:
        phrases.append(subject[1].strip())
    matches = {item.id for item in records if item.text in request or
               any(phrase in item.text for phrase in phrases)}
    if matches:
        return matches == {identity}
    return (len(records) == 1 and records[0].id == identity and
            bool(re.search(r"这(?:一)?条|那(?:一)?条|刚才那条|this (?:record|memory)|that (?:record|memory)",
                           request, re.I)))


def makeMemoryWriteTool(
    store: MemoryStore, project: ProjectMemory | None = None,
    allow_project_write: Callable[[], bool] | bool = False,
    project_user_request: Callable[[], tuple[str, str]] | None = None,
) -> Tool:
    async def change_project(args: dict[str, Any], ctx: ToolContext) -> str:
        if ctx.subagentDepth:
            return "工程条目只能由主会话按用户要求更正或撤回。"
        allowed = allow_project_write() if callable(allow_project_write) else allow_project_write
        if not allowed or project is None or not await asyncio.to_thread(lambda: project.enabled):
            return "此调用未启用工程记忆写入。"
        if ctx.signal.is_set() or Path(ctx.cwd or ctx.workspaceRoot).resolve() != project.cwd:
            return "工程记忆调用已取消或工作区身份不符。"
        identity, version = args.get("project_id"), args.get("expected_version")
        if not isinstance(identity, str) or not _PROJECT_ID.fullmatch(identity) or type(version) is not int or version < 1:
            return "请先通过 MemoryRead 确认工程条目的完整 ID 和当前版本。"
        delete = args.get("delete") is True
        token, request = project_user_request() if project_user_request is not None else ("", "")
        if not token or not isinstance(request, str) or not _explicit_request(request, delete=delete):
            return "当前用户没有明确授权此项工程记忆操作。"
        new_text = args.get("text", "")
        if not isinstance(new_text, str):
            return "新约定必须是文本。"
        operation = hashlib.sha256(json.dumps([token, request, identity, version, delete, new_text],
                                              ensure_ascii=False).encode()).hexdigest()
        action = "已撤回" if delete else "已更正"
        receipt = f"{action}工程记忆 {identity} v{version}（当前项目及获准读取方）。"
        if await asyncio.to_thread(project.completed_mutation, operation, identity):
            return receipt
        records = await asyncio.to_thread(project.visible)
        record = next((item for item in records if item.id == identity), None)
        if ((record is None or record.version != version)
                and await asyncio.to_thread(project.completed_mutation, operation, identity)):
            return receipt
        if record is None:
            return "未找到有效工程记忆。"
        if Path(record.workspace) != project.cwd:
            return "该工程记忆由其他工作区发布，在当前工作区受保护。"
        if record.version != version:
            return "工程记忆版本已变，请重新查阅。"
        if not _request_names_record(request, identity, records):
            return "无法把当前请求唯一对应到这条工程记忆，请用户明确条目。"
        if delete:
            changed = await asyncio.to_thread(project.forget, identity, expected_version=version, operation_id=operation)
        else:
            if not new_text.strip() or new_text not in request:
                return "请使用用户本次明确给出的新约定原文进行更正。"
            changed = await asyncio.to_thread(project.correct, identity, new_text, expected_version=version,
                                              operation_id=operation)
        if not changed:
            if await asyncio.to_thread(project.completed_mutation, operation, identity):
                return receipt
            return "工程条目未变更，请核对条目版本、内容及配额。"
        return receipt

    async def write(args: dict[str, Any], ctx: ToolContext) -> str:
        if "project_id" in args or "expected_version" in args:
            return await change_project(args, ctx)
        if (ctx.subagentDepth or ctx.headless) and project is not None and await asyncio.to_thread(lambda: project.enabled):
            if ctx.subagentDepth:
                return "工程记忆已启用，此项目的持久记忆须由主会话核实后写入。"
            allowed = allow_project_write() if callable(allow_project_write) else allow_project_write
            if not allowed or Path(ctx.cwd or ctx.workspaceRoot).resolve() != project.cwd:
                return "此批处理未启用当前工作区的记忆写入。"
        text: str = args.get("text", "")
        title: str = args.get("title", "")
        if args.get("delete"):
            key = (title or text).strip()
            if not key:
                return "删除失败:需提供 title 或 text 来定位要删除的记忆。"
            result = await store.delete(key, signal=ctx.signal)
            if result.removed:
                message = f"已删除记忆(共 {len(result.removed)} 条):{'、'.join(result.removed)}"
                if result.readOnly:
                    message += "\n同名只读兼容记忆已保留。"
                return message
            if result.readOnly:
                return "删除失败:匹配记忆来自只读兼容来源。"
            return f"未找到匹配记忆:{key}"
        if not text.strip():
            return "写入失败:text 必填(删除请用 delete: true)。"
        source: str | None = args.get("source")
        secrets: dict[str, None] = {}
        for value in (text, title, source):
            if value:
                secrets.update(dict.fromkeys(findSecrets(value)))
        if secrets:
            return f"拒绝写入记忆:疑似含密钥({'、'.join(secrets)})。请勿把凭据写进记忆;如需记录,改写成不含密钥的描述。"
        kind = cast(MemoryType, args.get("type", "semantic"))
        scope = cast(Scope, args.get("scope", routeScope(kind)))
        source_hash = await hashMemorySource(store.cwd, source) if source else None
        item = newMemory(
            name=slug(title or text), title=title, text=text, type=kind, today=store.today(),
            importance=args.get("importance", 5), confidence=args.get("confidence"),
            source=source, sourceHash=source_hash, origin=projectIdOf(store.cwd) if scope == "knowledge" else None,
        )
        saved = await store.upsert(scope, item, signal=ctx.signal)
        if ctx.memoryAudit is not None:
            ctx.memoryAudit.wrote(kind, saved.action == "updated")
        prefix = "已更新" if saved.action == "updated" else "已记住"
        return f"{prefix}({_LABELS[scope]}):{text.strip()}"

    descriptions = _TEXT["parameters"]
    fields: dict[str, dict[str, Any]] = {
        "text": {"type": "string"},
        "title": {"type": "string"},
        "type": {"type": "string", "enum": ["user", "feedback", "semantic", "procedural", "episodic"]},
        "importance": {"type": "integer", "minimum": 1, "maximum": 10},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source": {"type": "string"},
        "scope": {"type": "string", "enum": ["project", "user", "knowledge"]},
        "delete": {"type": "boolean"},
    }
    if project is not None:
        fields.update(project_id={"type": "string", "pattern": "^pm-[a-f0-9]{32}$"},
                      expected_version={"type": "integer", "minimum": 1})
    extra = _TEXT["project_description"] if project is not None else ""
    extra_en = _TEXT["project_description_en"] if project is not None else ""
    return Tool(
        name="MemoryWrite", description=_TEXT["description"] + extra, descriptionEn=_TEXT["descriptionEn"] + extra_en,
        capability="plan", persistentEffect=True, handler=write,
        schema=objectSchema({name: {**field, "description": descriptions[name]} for name, field in fields.items()}, []),
    )

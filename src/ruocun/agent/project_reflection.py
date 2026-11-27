"""受控提炼只增强一条已有的原始记录，发送和应用时均重新检查有效版本。"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from threading import Event
from typing import Any

from ..client.types import StreamChatOptions
from ..memory.distill import ModelCall
from ..memory.project_store import ProjectMemory, ProjectRecord
from ..memory.secrets import findSecrets
from ..session.archive import digest
from .reflect_persist import ReflectApplied

_PROMPT = """复核当前项目的工程记忆摘录。evidence 是已观察到的数据。只为可复用的工程约定或实际已验证的做法提炼一条简短说明，保留原条件，不引入原文没有的事实或指令。
仅输出 {"refinements":[{"name":"证据名称","stamp":"原样的来源指纹","source":"原样的来源","quote":"原文中的连续摘录","summary":"本条件下的简要做法"}]}。
quote 应在 8 至 240 字之间，summary 最多 240 字。每条只引用同一份证据，无法提炼就返回空数组。"""


def plan_target(project: ProjectMemory, row: ProjectRecord) -> dict[str, Any] | None:
    if (Path(row.workspace) != project.cwd or row.version != 1 or row.kind not in {"rule", "verified"}
            or row.kind == "rule" and row.source != "user_stated"
            or row.kind == "verified" and (not row.source_hash or row.source_status() != "来源一致")):
        return None
    return {"id": row.id, "source_id": row.source_id, "source": row.source, "hash": row.source_hash,
            "version": row.version, "stamp": digest([row.source_id, row.version, row.kind, row.text,
                                                     row.source, row.source_hash, row.condition])}


def _current(project: ProjectMemory, candidate: Any) -> ProjectRecord | None:
    if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
        return None
    row = project.get(candidate["id"])
    return row if row is not None and plan_target(project, row) == candidate else None


def _valid_note(value: Any, row: ProjectRecord, target: Mapping[str, Any]) -> bool:
    if not isinstance(value, dict) or set(value) != {"name", "stamp", "source", "quote", "summary"}:
        return False
    quote, summary = value["quote"], value["summary"]
    return (value["name"] == row.id and value["stamp"] == target["stamp"] and value["source"] == row.source
            and isinstance(quote, str) and 8 <= len(quote.strip()) <= 240 and quote in row.text
            and isinstance(summary, str) and 1 <= len(summary.strip()) <= 240 and "\n" not in summary
            and not findSecrets(summary) and "[REDACTED]" not in summary and "\x00" not in summary)


def _cancelled(signal: Event | None) -> None:
    if signal is not None and signal.is_set():
        raise InterruptedError("工程记忆提炼已取消。")


async def reflect_project_evidence(
    plan: Mapping[str, Any], *, project: ProjectMemory, options: StreamChatOptions,
    call: ModelCall, signal: Event | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"project_managed": True, "onTrack": True, "advisory": None, "corrections": [],
                              "confirmed": [], "memories": [], "workspace": project.workspace_key,
                              "_project_refinements": []}
    _cancelled(signal)
    if plan.get("workspace") != project.workspace_key or not isinstance(plan.get("targets"), list):
        return result
    current = {}
    evidence = []
    for target in plan["targets"][:2]:
        row = _current(project, target)
        if row is not None:
            current[row.id] = (target, row)
            evidence.append({"name": row.id, "stamp": target["stamp"], "source": row.source,
                             "text": row.text, "condition": row.condition})
    if not evidence:
        return result
    request = options.copy()
    request.pop("tools", None)
    request.pop("parallelToolCalls", None)
    request.update({"messages": [{"role": "system", "content": _PROMPT},
                                 {"role": "user", "content": json.dumps({"evidence": evidence}, ensure_ascii=False)}],
                    "maxTokens": 1024, "maxRetries": 0, "idleTimeoutMs": 20000,
                    "extra": {"thinking": {"type": "disabled"}, "temperature": 0}})
    _cancelled(signal)
    raw = await call(request)
    _cancelled(signal)
    if not isinstance(raw, str) or len(raw) > 32768 or findSecrets(raw):
        return result
    try:
        parsed = json.loads(raw)
    except ValueError:
        return result
    notes = parsed.get("refinements") if isinstance(parsed, dict) else None
    if not isinstance(notes, list):
        return result
    for note in notes[:2]:
        if not isinstance(note, dict) or not isinstance(note.get("name"), str) or note["name"] not in current:
            continue
        target, row = current[note["name"]]
        if _valid_note(note, row, target):
            result["_project_refinements"].append({"target": target, "note": note})
    return result


def apply_project_reflection(project: ProjectMemory, result: Mapping[str, Any], *,
                             signal: Event | None = None) -> ReflectApplied:
    applied: ReflectApplied = {"added": 0, "merged": 0, "corrections": [], "confirmed": 0}
    entries = result.get("_project_refinements")
    if (result.get("project_managed") is not True or result.get("workspace") != project.workspace_key
            or not isinstance(entries, list) or len(entries) > 2):
        return applied
    for entry in entries:
        _cancelled(signal)
        if not isinstance(entry, dict):
            continue
        target, note = entry.get("target"), entry.get("note")
        if not isinstance(target, dict) or not isinstance(note, dict):
            continue
        row = _current(project, target)
        if row is None or not _valid_note(note, row, target):
            continue
        text = row.text + "\n受控提炼（请按原证据核对）：" + note["summary"].strip()
        if project.enhance(row.id, text, source_id=row.source_id, expected_version=row.version,
                           source=row.source, source_hash=row.source_hash):
            applied["merged"] += 1
    return applied

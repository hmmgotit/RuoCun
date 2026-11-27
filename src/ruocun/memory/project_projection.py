"""Identify memory-derived content while keeping the original conversation archive intact."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    from .project_store import ProjectMemory
    from .store import MemoryStore

_BLOCK = re.compile(r"\[\[ruocun-memory ([^\s\]]+) ([^\s\]]+)\]\]\n(.*?)\n\[\[/ruocun-memory\]\]", re.S)
_DERIVED = re.compile(r"\[\[ruocun-derived ([^\s\]]+)\]\]\n(.*?)\n\[\[/ruocun-derived\]\]", re.S)
_WITHDRAWN = "（先前读取的记忆已失效）"
_CHANGED_SOURCE = "（本次发送前复核：来源已变，需核对当前文件。）"
_STALE_SOURCE = "（来源已过期或无法核实，旧证据已略去。）"


def _refs(encoded: str) -> tuple[tuple[str, str], ...]:
    return tuple((unquote(i), unquote(v)) for part in encoded.split(",") for i, _, v in [part.partition("@")])


def memory_versions(messages: Iterable[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    refs: dict[tuple[str, str], None] = {}
    for message in messages:
        value = message.get("content")
        if message.get("role") not in {"system", "tool"} or not isinstance(value, str) or "[[ruocun-" not in value:
            continue
        for match in _BLOCK.finditer(value):
            refs[unquote(match[1]), unquote(match[2])] = None
        for match in _DERIVED.finditer(value):
            refs.update(dict.fromkeys(_refs(match[1])))
    return tuple(refs)


def mark_derived(text: str, versions: Iterable[tuple[str, str]]) -> str:
    refs = ",".join(f"{quote(i, safe='')}@{quote(v, safe='')}" for i, v in dict.fromkeys(versions))
    if not refs:
        return text
    body = text.replace("[[ruocun-", "［[ruocun-").replace("[[/ruocun-", "［[/ruocun-")
    return f"[[ruocun-derived {refs}]]\n{body}\n[[/ruocun-derived]]"


def mark_memory(identity: str, version: str, text: str) -> str:
    opening = f"[[ruocun-memory {quote(identity, safe='')} {quote(str(version), safe='')}]]"
    body = text.replace("[[ruocun-", "［[ruocun-").replace("[[/ruocun-", "［[/ruocun-")
    return f"{opening}\n{body}\n[[/ruocun-memory]]"


def filter_projection(messages: list[dict[str, Any]], permitted: Callable[[str, str], bool | str]) -> list[dict[str, Any]]:
    """Create a request-only view; never rewrite the original archive or tool protocol."""
    result = []
    for message in messages:
        value = message.get("content")
        if message.get("role") in {"system", "tool"} and isinstance(value, str) and "[[ruocun-" in value:
            def keep(match: re.Match[str]) -> str:
                state = permitted(unquote(match[1]), unquote(match[2]))
                return visible_body(match[3], [state])
            def keep_derived(match: re.Match[str]) -> str:
                return visible_body(match[2], [permitted(i, v) for i, v in _refs(match[1])])
            value = _DERIVED.sub(keep_derived, _BLOCK.sub(keep, value))
            # A tool result cut at its output limit can end inside the protected block.
            value = re.sub(r"\[\[ruocun-(?:memory|derived) [\s\S]*$", _WITHDRAWN, value)
            message = {**message, "content": value}
        result.append(message)
    return result


def visible_body(body: str, states: list[bool | str]) -> str:
    if False in states:
        return _WITHDRAWN
    if _STALE_SOURCE in states:
        return _STALE_SOURCE
    warnings = dict.fromkeys(state for state in states if isinstance(state, str))
    if warnings:
        return "\n".join([*warnings, body])
    return body


def workspace_view(store: MemoryStore, project: ProjectMemory, messages: list[dict[str, Any]], *,
                   enabled: bool = True) -> list[dict[str, Any]]:
    """在请求边界核对引用。权限不明则移除，来源变更不改变原归档。"""
    if not enabled:
        return filter_projection(messages, lambda _id, _ver: False)
    versions = memory_versions(messages)
    permissions: dict[tuple[str, str], bool | str] = {}
    markdown = {ref for ref in versions if not ref[0].startswith("pm-")}
    for key, state in store.projectionStates(markdown).items():
        permissions[key] = _CHANGED_SOURCE if state == "changed" else _STALE_SOURCE if state == "stale" else True
    for identity in dict.fromkeys(i for i, _ in versions if i.startswith("pm-")):
        row = project.get(identity)
        if row is None:
            continue
        source_state = row.source_status()
        key = row.id, str(row.version)
        permissions[key] = (_CHANGED_SOURCE if source_state == "来源已变" else _STALE_SOURCE
                            if source_state == "来源无法核实" else True)
    return filter_projection(messages, lambda identity, version: permissions.get((identity, version), False))

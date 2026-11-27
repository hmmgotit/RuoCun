"""在同一预算内查找当前工作区可见的工程摘录和既有 Markdown 规则。"""
from __future__ import annotations

import asyncio
import math
import re
from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from .inject import StartupMemorySnapshot
from .project_projection import mark_memory
from .project_store import ProjectMemory, ProjectRecord
from .project_vectors import (
    VectorSource,
    _profile,
    configureVectors,
    semanticScores,
    vectorStatus,
)
from .retrieval import (
    _CHANGED,
    _CHECK_HEADER,
    _HEADER,
    _QUERY_LIMIT,
    _TERM_LIMIT,
    _TEXT_LIMIT,
    TaskRecallResult,
    _record,
    _terms,
    _visible_markdown,
)
from .store import MemoryStore

_RAW = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\\:-]*|[\u3400-\u9fff]+")
_PROCESS = re.compile(r"(?:现在|接下来)?(?:我会|我将|先来|让我)(?:检查|查看|运行测试|看看代码)[。！.]?$|(?:now )?(?:let me|i will|i'll) (?:check|inspect|run the tests)[.!]?$", re.I)


@dataclass(frozen=True, slots=True)
class _Hit:
    identity: str
    version: str
    title: str
    text: str
    rendered: str
    condition: str = ""
    record: ProjectRecord | None = None


def _raw(text: str) -> frozenset[str]:
    return frozenset(s.casefold() for s in _RAW.findall(text))


def _rank(query: str, items: list[_Hit], dense: dict[str, float]) -> list[int]:
    scores: defaultdict[int, float] = defaultdict(float)
    for tokenize in (_raw, _terms):
        terms = set(sorted(tokenize(query), key=lambda t: (-len(t), t))[:_TERM_LIMIT])
        if not terms:
            continue
        features = [(tokenize(i.title), tokenize(i.text[:_TEXT_LIMIT])) for i in items]
        frequencies = Counter(term for title, body in features for term in terms & (title | body))
        leg = []
        for pos, (title, body) in enumerate(features):
            score = math.fsum(math.log1p((len(items) + 1) / (1 + frequencies[t])) *
                               (2 * int(t in title) + int(t in body)) for t in sorted(terms & (title | body)))
            if score > 0:
                leg.append((-score, items[pos].identity, pos))
        for rank, (_, _, pos) in enumerate(sorted(leg), 1):
            scores[pos] += 1 / (60 + rank)
    dense_leg = sorted(((-dense[item.identity], item.identity, i) for i, item in enumerate(items)
                        if item.identity in dense))
    for rank, (_, _, pos) in enumerate(dense_leg, 1):
        scores[pos] += 1 / (60 + rank)
    return sorted(scores, key=lambda i: (-scores[i] * (0.25 if _PROCESS.fullmatch(items[i].text.strip()) else 1),
                                         items[i].identity))


def _excerpt(text: str, query: str) -> str:
    """只合并同一原文中相接的片段，保留原有顺序和字符。"""
    width, stride, limit = 520, 440, 1100
    if len(text) <= limit:
        return text
    terms = _terms(query)
    spans = [(start, min(len(text), start + width)) for start in range(0, len(text), stride)]
    weights = [len(terms & _terms(text[a:b])) for a, b in spans]
    best = max(range(len(spans)), key=lambda i: weights[i])
    start, end = spans[best]
    neighbors = sorted((i for i in (best - 1, best + 1) if 0 <= i < len(spans) and weights[i]),
                       key=lambda i: -weights[i])
    for i in neighbors:
        left, right = min(start, spans[i][0]), max(end, spans[i][1])
        if right - left <= limit:
            start, end = left, right
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


async def _candidates(
    store: MemoryStore, project: ProjectMemory, startup: StartupMemorySnapshot | None,
    excludeFullText: Collection[tuple[str, str]] = (),
) -> tuple[list[_Hit], int, int, int]:
    entries, stale, changed, foreign = await _visible_markdown(store, startup is not None)
    full = {(e.scope, e.name): e.content_hash for e in startup.full_entries} if startup else {}
    items = []
    for entry, verdict in entries:
        mem = entry.memory
        key = (entry.scope, mem.name)
        if key in excludeFullText:
            continue
        identity = f"{entry.scope}/{mem.name}"
        title = f"{mem.name} {mem.title or ''} {mem.source or ''}"
        body = _record(entry, verdict)
        if entry.contentDigest and full.get(key) == entry.contentDigest:
            body = f"[{identity}] 同版本正文已在启动记忆提供。{_CHANGED if verdict == 'changed' else ''}"
        items.append(_Hit(identity, entry.contentDigest, title, mem.text, body))
    for row in await asyncio.to_thread(project.visible):
        items.append(_Hit(row.id, str(row.version), f"{row.kind} {row.source} {row.condition}",
                          row.text, "", row.condition, row))
    return items, stale, changed, foreign


def _vector_sources(items: list[_Hit]) -> list[VectorSource]:
    return [VectorSource(i.identity, i.version, (i.title + "\n" + i.text)[:8192]) for i in items]


def _live_versions(store: MemoryStore, project: ProjectMemory) -> set[tuple[str, str]]:
    related, _, _, _ = asyncio.run(_visible_markdown(store, False))
    valid = {(f"{e.scope}/{e.memory.name}", e.contentDigest) for e, _ in related}
    valid.update((r.id, str(r.version)) for r in project.visible())
    return valid


async def rebuildProjectIndex(store: MemoryStore, project: ProjectMemory) -> str:
    profile = await asyncio.to_thread(_profile, project)
    if profile.mode == "off":
        return "向量未开启，原文与代码词法检索可用。"
    await asyncio.to_thread(configureVectors, project, profile.mode, model=profile.model,
                            endpoint=profile.endpoint, key_env=profile.key_env)
    items, _, _, _ = await _candidates(store, project, None)
    await semanticScores(project, _vector_sources(items), None, lambda: _live_versions(store, project), timeout=8)
    return await asyncio.to_thread(vectorStatus, project)


async def renderProjectRecord(project: ProjectMemory, row: ProjectRecord, query: str | None = None) -> str:
    state = await asyncio.to_thread(row.source_status)
    notice = "\n来源状态：" + state
    if state == "来源已变":
        notice += _CHANGED
    if Path(row.workspace) != project.cwd:
        notice += "\n来自关联工作区，当前工作区的实现与适用条件仍需核对。"
    body = row.text if query is None else _excerpt(row.text, query)
    return (f"[工程摘录 {row.id} v{row.version} · {row.kind}] 证据：{row.source_id}\n"
            + (f"文件或来源：{row.source}\n" if row.source else "")
            + (f"适用条件：{row.condition}\n" if row.condition else "")
            + body + notice)


async def buildProjectRecall(
    store: MemoryStore, project: ProjectMemory, query: str, *, startup: StartupMemorySnapshot | None = None,
    maxItems: int = 2, maxChars: int = 2400, excludeFullText: Collection[tuple[str, str]] = (),
) -> TaskRecallResult:
    """自动补充固定使用默认预算；主动查阅可显式传入自己的上限。"""
    if type(maxItems) is not int or type(maxChars) is not int or min(maxItems, maxChars) < 0:
        raise ValueError("任务记忆预算必须是非负整数。")
    query = query.strip()[:_QUERY_LIMIT]
    header = _CHECK_HEADER if startup is not None else _HEADER
    if not query or maxItems == 0 or maxChars <= len(header):
        return TaskRecallResult()
    items, stale, changed, foreign = await _candidates(store, project, startup, excludeFullText)
    dense = await semanticScores(project, _vector_sources(items), query, lambda: _live_versions(store, project))
    ranked = _rank(query, items, dense)
    blocks: list[str] = []
    startup_rules = dict(startup.project_versions) if startup else {}
    versions: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    used = len(header)
    for index in ranked:
        hit = items[index]
        key_body = (hit.text.strip(), hit.condition)
        if key_body in seen:
            continue
        body = hit.rendered
        if hit.record is not None:
            row = hit.record
            current = await asyncio.to_thread(project.get, row.id)
            if current != row:
                continue
            body = await renderProjectRecord(project, row, query)
            if startup_rules.get(row.id) == str(row.version):
                state = await asyncio.to_thread(row.source_status)
                body = f"[工程规则 {row.id} v{row.version}] 同版本正文已在启动记忆提供。当前来源状态：{state}。"
        block = mark_memory(hit.identity, hit.version, body)
        additional = len(block) + (2 if blocks else 0)
        if used + additional > maxChars:
            continue
        seen.add(key_body)
        used += additional
        blocks.append(block)
        versions.append((hit.identity, hit.version))
        if hit.record is not None:
            await asyncio.to_thread(project.touch, hit.identity, hit.record.version)
        if len(blocks) == maxItems:
            break
    return TaskRecallResult(text=header + "\n\n".join(blocks) if blocks else "",
                            ids=tuple(identity for identity, _ in versions), versions=tuple(versions),
                            candidates=len(items), matched=len(ranked), stale=stale,
                            changed=changed, foreign=foreign, skipped=len(ranked)-len(blocks))

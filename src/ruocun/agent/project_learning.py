"""从已落盘的主会话收取可独立核对的工程摘录。"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..memory.hash import contentHash
from ..memory.project_store import ProjectMemory
from ..memory.secrets import findSecrets
from ..session.archive import digest
from ..session.contract import RuntimeRecord
from ..session.project_history import project_history
from .controlled_learning import MAX_SOURCE_BYTES, _relative_source, learningEvidence
from .project_reflection import (
    apply_project_reflection as apply_project_reflection,
)
from .project_reflection import (
    plan_target,
)
from .project_reflection import (
    reflect_project_evidence as reflect_project_evidence,
)
from .runtime_context import isRuntimeContext

_RAW = re.compile(r"([0-9a-f]{16})-r([1-9]\d*)v[1-9]\d*")
_PERSISTENT = re.compile(r"从现在(?:起|开始)|从今往后|自即日起|以后|今后|长期|约定|规范|统一|始终|不要再|"
                         r"\b(?:always|never|convention|henceforth|from now on)\b", re.I)
_ENGINEERING = re.compile(r"项目|工程|仓库|测试|接口|导出|代码|文件|字段|兼容|构建|分支|编译|报表|数据库|"
                          r"\b(?:project|repository|repo|test|api|export|field|build|file|parser|schema)\b", re.I)
_PRIVATE = re.compile(r"私聊|私下|勿保存|不要(?:记住|记录|保存)|忘记|撤回|/memory\b|"
                      r"\b(?:off the record|do not (?:store|remember)|forget)\b", re.I)
_TEMPORARY = re.compile(r"这次|此次|本次|临时|或许|可能|是否|考虑一下|[?？]|\b(?:this time|temporar\w*|perhaps)\b", re.I)
_MEMORY_SUBJECT = re.compile(r"(?:工程|项目)?(?:记忆|记忆条目)|这(?:一)?条(?:规则|约定)|那(?:一)?条(?:规则|约定)|"
                             r"pm-[0-9a-f]{32}\b|\b(?:memory|record|MemoryWrite)\b", re.I)
_MEMORY_ACTION = re.compile(r"^\s*(?:(?:请你?|麻烦你?|帮我|我想请你|please|could you|can you)\s*)*"
                            r"(?:把|将|更正|纠正|修正|修改|更新|替换|改为|改成|写入|保存|记住|删除|忘掉|撤回|"
                            r"(?:correct|change|replace|update|remove|delete|save|remember)\b)", re.I)
_DIAGNOSTIC = re.compile(r"^(?:FAILED\s|ERROR\s|E\s+|[\w.]+(?:Error|Exception):|"
                         r"\d+ (?:failed|passed)\b|FAIL(?:URE)?[:\s]|error\[)", re.I)
_CODE_FACT = re.compile(r"(?:[A-Za-z_][\w.]*\s*(?::[^=]+)?=\s*\S|(?:async\s+)?(?:def|class|function)\s+\w+)")


def _counts() -> dict[str, int]:
    return {"scanned": 0, "added": 0, "existing": 0, "skipped": 0}


def _raw_source(session: str, identity: str) -> str:
    match = _RAW.fullmatch(identity)
    if not match or match[1] != digest([session, "main"])[:16]:
        return ""
    return f"{session}/main/{match[1]}-r{match[2]}"


def _safe(text: str) -> bool:
    return not findSecrets(text) and "[REDACTED]" not in text and not _PRIVATE.search(text)


def _managed_turn(user: str, messages: Sequence[Mapping[str, Any]]) -> bool:
    if _MEMORY_SUBJECT.search(user) and _MEMORY_ACTION.search(user):
        return True
    return any(isinstance(call, dict) and isinstance(call.get("function"), dict)
               and call["function"].get("name") == "MemoryWrite"
               for message in messages if message.get("role") == "assistant"
               for call in message.get("tool_calls") or ())


def prepare_project_reflection(
    project: ProjectMemory, messages: Sequence[dict[str, Any]], *,
    session_id: str, lineage: Sequence[str], original_user: Any = None,
) -> dict[str, Any]:
    """只保留轮末已获准写入的主会话归档身份。待发送时重新读取对应版本。"""
    if not project.enabled or not messages or len(messages) != len(lineage):
        return {}
    start = next((i for i in range(len(messages) - 1, -1, -1)
                  if messages[i].get("role") == "user" and not isRuntimeContext(messages[i])), None)
    if (start is None or not isinstance(original_user, str) or messages[start].get("content") != original_user
            or not _safe(original_user) or _managed_turn(original_user, messages[start:])):
        return {}
    positions = [start, *(start + item["position"] for item in learningEvidence(messages[start:])
                          if item["kind"] == "validation")]
    targets = []
    for position in positions:
        source_id = _raw_source(session_id, lineage[position])
        if not source_id:
            continue
        row = project.get_by_source_id(source_id)
        if row is None or (target := plan_target(project, row)) is None:
            continue
        targets.append(target)
        if len(targets) == 2:
            break
    return {"workspace": project.workspace_key, "targets": targets} if targets else {}


def capture_project_turn(
    project: ProjectMemory, messages: Sequence[dict[str, Any]], *, session_id: str,
    lineage: Sequence[str], original_user: Any = None,
    source_snapshots: Mapping[str, Mapping[str, str]] | None = None, scope: str = "main",
    selected_sources: set[str] | None = None,
) -> dict[str, int]:
    """仅消费已确认投影的最后一轮。归档原文序号在恢复、元数据升级时不变。"""
    counts = _counts()
    if scope != "main" or not project.enabled or len(messages) != len(lineage):
        return counts
    start = next((i for i in range(len(messages) - 1, -1, -1)
                  if messages[i].get("role") == "user" and not isRuntimeContext(messages[i])), None)
    if start is None or not (origin := _raw_source(session_id, lineage[start])):
        return counts
    counts["scanned"] = 1

    def publish(text: str, source_id: str, kind: str, **details: str) -> None:
        if selected_sources is not None and source_id not in selected_sources:
            return
        identity, added = project.capture_result(text, source_id=source_id, kind=kind, **details)
        counts["added" if added else "existing" if identity else "skipped"] += 1

    if original_user is None or original_user != messages[start].get("content"):
        return counts
    user_text = (original_user if isinstance(original_user, str) else
                 "\n".join(str(part.get("text", "")) for part in original_user
                           if isinstance(part, dict) and part.get("type") == "text"))
    if not _safe(user_text) or _managed_turn(user_text, messages[start:]):
        return counts
    if (0 < len(user_text) <= 1200 and _PERSISTENT.search(user_text) and _ENGINEERING.search(user_text)
            and not _TEMPORARY.search(user_text)):
        publish(user_text.strip(), origin, "rule", source="user_stated", condition="当前项目的用户陈述")
    evidence = learningEvidence(messages[start:], include_failures=True)
    files = []
    handoffs = []
    failures: dict[str, int] = {}
    snapshots = source_snapshots or {}
    for item in evidence:
        position = item.get("position")
        if not isinstance(position, int) or not (ref := _raw_source(session_id, lineage[start + position])):
            continue
        if item["kind"] == "handoff":
            handoffs.append(item)
            continue
        if item["kind"] == "file":
            files.append(item)
            if item["tool"] != "Read" or not handoffs or not _safe(item["text"]):
                continue
            path = _relative_source(project.cwd, item["source"])
            if not path:
                continue
            fingerprint = snapshots.get("read:" + item["tool_call_id"], {}).get(path, "")
            if source_snapshots is not None and not fingerprint:
                continue
            reports = [report["text"] for report in handoffs if report["batch"] < item["batch"] and
                       path in report["text"] and _safe(report["text"])]
            for line in item["text"].splitlines():
                number, tab, body = line.partition("\t")
                body = body.strip()
                if (not tab or not number.isdecimal() or not 8 <= len(body) <= 160
                        or not _CODE_FACT.match(body) or not any(body in report for report in reports)):
                    continue
                publish(f"{path}:{number}\n{body}", ref, "observation", source=path, source_hash=fingerprint,
                        condition="主会话重新读取源码并核对了报告中的具体语句")
                break
            continue
        if item["kind"] != "validation":
            continue
        command, exit_code = item["command"], item["exit"]
        if not _safe(command) or not _safe(item["text"]):
            counts["skipped"] += 1
            continue
        check_versions = snapshots.get(item["tool_call_id"], {})
        source = fingerprint = ""
        edited = False
        for entry in reversed(files):
            path = _relative_source(project.cwd, entry["source"])
            if not path:
                continue
            candidate_hash = check_versions.get(path, "")
            if not source:
                source, fingerprint = path, candidate_hash
            edit_version = snapshots.get("edit:" + entry["tool_call_id"], {}).get(path)
            if (candidate_hash and edit_version == candidate_hash and command in failures
                    and failures[command] < entry["batch"] < item["batch"]):
                try:
                    with (project.cwd / path).open("rb") as stream:
                        raw = stream.read(MAX_SOURCE_BYTES + 1)
                    edited = len(raw) <= MAX_SOURCE_BYTES and contentHash(raw.decode("utf-8", "replace")) == candidate_hash
                except OSError:
                    edited = False
            if edited:
                source, fingerprint = path, candidate_hash
                break
        kind = "failure" if exit_code else "observation"
        if exit_code:
            failures[command] = item["batch"]
        elif edited:
            kind = "verified"
        lines = [line.strip()[:220] for line in item["text"].splitlines() if _DIAGNOSTIC.match(line.strip())]
        text = f"{command}\n[exit {exit_code}]"
        if lines:
            text += "\n" + "\n".join(lines[:3])
        if source:
            text += "\n相关文件：" + source
        condition = "当前会话中该命令的实际结果"
        if kind == "verified":
            condition = "同一检查先失败，文件修改后在所记版本通过；用于其他输入前须复核"
        publish(text, ref, kind, source=source, source_hash=fingerprint, condition=condition)
    return counts

def backfill_project(
    project: ProjectMemory, runtime: RuntimeRecord, *, session_ids: Sequence[str] = (),
    since_ms: int | None = None, until_ms: int | None = None,
) -> dict[str, int]:
    """按会话或原始事件毫秒时间选取。scanned 计读到的会话，其他计工程摘录或跳过的会话。"""
    if isinstance(session_ids, (str, bytes)) or any(
            not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", sid)
            or sid.endswith(".") for sid in session_ids):
        raise ValueError("补录只接受本项目的会话 ID。")
    if not session_ids and since_ms is None and until_ms is None:
        raise ValueError("请指定会话 ID 或时间范围。")
    if (any(type(stamp) is not int or stamp < 0 for stamp in (since_ms, until_ms) if stamp is not None)
            or since_ms is not None and until_ms is not None and since_ms >= until_ms):
        raise ValueError("补录时间范围无效。")
    counts = _counts()
    if not project.enabled:
        raise ValueError("当前工作区尚未开启工程记忆。")
    workspace = os.path.normcase(str(project.cwd.resolve()))
    base = project.user_root / "sessions" / hashlib.sha256(workspace.encode()).hexdigest()[:20]
    if not base.is_dir():
        counts["skipped"] = len(set(session_ids))
        return counts
    selected = dict.fromkeys(session_ids) if session_ids else sorted(
        child.name for child in base.iterdir() if child.is_dir())
    for sid in selected:
        history = project_history(base, project.cwd, sid, runtime, since_ms=since_ms, until_ms=until_ms)
        if history is None:
            counts["skipped"] += 1
            continue
        counts["scanned"] += 1
        records, inputs = history
        turn: list[dict[str, Any]] = []
        refs: list[str] = []
        eligible: set[str] = set()

        def collect() -> None:
            if not turn or not eligible:
                return
            result = capture_project_turn(project, turn, session_id=sid, lineage=refs,
                                          original_user=turn[0]["content"], selected_sources=eligible)
            for key in ("added", "existing", "skipped"):
                counts[key] += result[key]

        for row in records:
            message = row["message"]
            if message.get("role") == "user" and not isRuntimeContext(message):
                collect()
                turn, refs, eligible = [], [], set()
                if digest(message.get("content")) not in inputs:
                    continue
            elif not turn:
                continue
            turn.append(message)
            refs.append(row["id"])
            if row["selected"]:
                eligible.add(_raw_source(sid, row["id"]))
        collect()
    return counts

"""在已验证的会话只读事务中，选择当前项目、当前会话的主执行范围。"""
from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from typing import Any

from ..platform.windows.durable_file import PrivateDirectory
from . import archive
from .contract import USER_VERSION, RuntimeRecord, VersionRejectError
from .durable_store import (
    _read_meta,
    _validate_schema,
    _validated_checkpoint,
    _verify_private_database_files,
    assert_runtime_gate,
)
from .inputs import originalInput
from .log import _session_path
from .sqlite_port import connect_database


def project_history(
    base: Path, cwd: Path, session_id: str, runtime: RuntimeRecord, *,
    since_ms: int | None, until_ms: int | None,
) -> tuple[list[dict[str, Any]], set[str]] | None:
    """返回此事务中指定的原始事件和真实已消费输入。忽略从父会话继承的记录。"""
    directory = _session_path(base, session_id)
    if directory is None or not (directory / "session.db").is_file():
        return None
    assert_runtime_gate(runtime)
    with PrivateDirectory(directory, readonly=True, anchor_file="session.db"):
        _verify_private_database_files(directory)
        with closing(connect_database(directory / "session.db", readonly=True)) as con:
            con.execute("BEGIN")
            if con.execute("PRAGMA user_version").fetchone()[0] != USER_VERSION:
                raise VersionRejectError("补录会话的版本不受支持。")
            _validate_schema(con)
            checkpoint = _validated_checkpoint(con)
            identity = _read_meta(con, "identity")
            if (not isinstance(identity, dict) or not isinstance(identity.get("canonical_cwd"), str)
                    or checkpoint["session_id"] != session_id
                    or os.path.normcase(identity["canonical_cwd"]) != os.path.normcase(str(cwd.resolve()))):
                return None
            inputs = {archive.digest(originalInput(item["payload"])) for item in checkpoint["accepted_inputs"]
                      if item.get("state") == "consumed"}
            state = archive.manifest(con, session_id)["scopes"].get("main", {})
            index = [item for item in state.get("index", ()) if item.get("kind") == "raw"
                     and item.get("origin_scope") == "main" and item.get("origin_session") == session_id
                     and item.get("version") == 1]
            selected = {i for i, item in enumerate(index)
                        if (since_ms is None or item["created_at"] >= since_ms)
                        and (until_ms is None or item["created_at"] < until_ms)}
            if not selected:
                return [], inputs
            first = min(selected)
            # 起点可能落在工具结果上。仅向前取到本轮真实用户输入，作配对依据。
            records = []
            for i in range(first - 1, -1, -1):
                row = archive.record(con, "main", index[i]["id"], sources=False)
                records.append({**row, "selected": False})
                message = row["message"]
                if message.get("role") == "user" and archive.digest(message.get("content")) in inputs:
                    break
            records.reverse()
            for i in range(first, max(selected) + 1):
                row = archive.record(con, "main", index[i]["id"], sources=False)
                records.append({**row, "selected": i in selected})
            _verify_private_database_files(directory)
            return records, inputs

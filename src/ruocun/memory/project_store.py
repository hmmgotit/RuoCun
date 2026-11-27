"""按本机账户和实际工作区隔离的工程摘录。SQLite 中的有效记录是索引的读取依据。"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..platform.file_contract import AtomicFileError
from ..platform.windows.durable_file import PrivateDirectory, verify_private_file
from ..tools.paths import resolveInWorkspace
from .hash import contentHash
from .secrets import findSecrets

if TYPE_CHECKING:
    import sqlite3


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: str
    text: str
    kind: str
    source: str
    source_hash: str
    condition: str
    version: int
    source_id: str
    workspace: str

    def source_status(self) -> str:
        return _source_status(Path(self.workspace), self.source, self.source_hash)


def _source_status(workspace: Path, source: str, fingerprint: str) -> str:
    if not source or source in {"user_stated", "inferred"}:
        return "用户陈述" if source == "user_stated" else "会话证据"
    try:
        path = Path(resolveInWorkspace(str(workspace), source.partition("#")[0]))
        if not path.is_file():
            return "来源无法核实"
        if not fingerprint:
            return "未记录文件指纹"
        with path.open("rb") as saved:
            raw = saved.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            return "来源无法核实"
        return "来源一致" if contentHash(raw.decode("utf-8", "replace")) == fingerprint else "来源已变"
    except (OSError, ValueError):
        return "来源无法核实"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _workspace_key(path: Path) -> str:
    actual = path.resolve(strict=True)
    info = actual.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("目标工作区必须是目录。")
    return _digest(os.path.normcase(str(actual)), str(info.st_dev), str(info.st_ino))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    key TEXT PRIMARY KEY, path TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    quota INTEGER NOT NULL DEFAULT 8388608
);
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES projects(key),
    text TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, source_hash TEXT NOT NULL,
    condition TEXT NOT NULL, version INTEGER NOT NULL, source_id TEXT NOT NULL, active INTEGER NOT NULL,
    last_used INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS project_records ON records(owner, active);
CREATE TABLE IF NOT EXISTS links (
    reader TEXT NOT NULL REFERENCES projects(key), producer TEXT NOT NULL REFERENCES projects(key),
    PRIMARY KEY(reader, producer)
);
CREATE TABLE IF NOT EXISTS publications (
    target TEXT NOT NULL REFERENCES projects(key), record TEXT NOT NULL REFERENCES records(id),
    version INTEGER NOT NULL, PRIMARY KEY(target, record)
);
CREATE TABLE IF NOT EXISTS mutation_receipts (
    operation TEXT PRIMARY KEY, record TEXT NOT NULL REFERENCES records(id)
);
"""

_CONTENT_SIZE = "length(CAST(text||condition||source AS BLOB))"
_EXPIRE_UNUSED_AFTER = 30 * 86400


def _discard_vector(conn: sqlite3.Connection, identity: str) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_vector_index'").fetchone():
        conn.execute("DELETE FROM pm_vector_index WHERE identity=?", (identity,))


def _retire(conn: sqlite3.Connection, identity: str) -> None:
    # 同一稳定来源再到达时仍会命中失效标记；原始正文不进入标记。
    conn.execute("UPDATE records SET text='', condition='', source='', source_hash='', source_id='',"
                 " kind='', active=0, version=version+1 WHERE id=? AND active=1", (identity,))
    _discard_vector(conn, identity)


class ProjectMemory:
    def __init__(self, user_root: Path, cwd: Path) -> None:
        self.user_root = user_root.absolute()
        self.cwd = cwd.resolve(strict=True)
        self._key = _workspace_key(self.cwd)
        self._directory = self.user_root / "engineering-memory"
        self._database = self._directory / "projects.sqlite3"

    @contextmanager
    def _connection(self, *, create: bool = False, write: bool = False) -> Iterator[sqlite3.Connection]:
        # CLI 先装入并核验受管 sqlite.dll，模块导入不应抢先加载系统版本。
        import sqlite3

        if _workspace_key(self.cwd) != self._key:
            raise ValueError("工作区身份已改变，请重新打开。")
        if create:
            self.user_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "nt":
            guard = PrivateDirectory(self._directory)
        else:
            if create:
                self._directory.mkdir(mode=0o700, exist_ok=True)
            info = self._directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid != getattr(os, "getuid")()):
                raise ValueError("工程记忆目录不属于当前用户或不是私有目录。")
        with guard if os.name == "nt" else nullcontext():
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = self._database.with_name(self._database.name + suffix)
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    raise ValueError("工程记忆文件不是私有的普通文件。")
                if os.name == "nt":
                    try:
                        verify_private_file(path)
                    except (AtomicFileError, FileNotFoundError):
                        # SQLite 可在其他连接提交时删除其瞬态 journal；数据库本身与现存文件仍严格检查。
                        if not suffix or os.path.lexists(path):
                            raise
            conn = sqlite3.connect(self._database, timeout=10, isolation_level=None)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                if create:
                    conn.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + "\nCOMMIT;")
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
                conn.commit()
            finally:
                conn.close()

    @property
    def workspace_key(self) -> str:
        return self._key

    @contextmanager
    def index_connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """派生索引复用账户私有数据库边界；调用者仅处理其专用表。"""
        if not self.enabled:
            raise ValueError("工作区尚未开启工程记忆。")
        with self._connection(write=write) as conn:
            yield conn

    @property
    def enabled(self) -> bool:
        if not self._database.exists():
            return False
        with self._connection() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='projects' AND type='table'").fetchone():
                return False  # 首次开启尚未提交。
            return bool(conn.execute("SELECT 1 FROM projects WHERE key=? AND enabled=1", (self._key,)).fetchone())

    def enable(self) -> None:
        with self._connection(create=True, write=True) as conn:
            conn.execute("INSERT INTO projects(key,path) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET enabled=1",
                         (self._key, str(self.cwd)))

    def set_quota(self, amount: int) -> None:
        if type(amount) is not int or not 1 <= amount <= 2**40:
            raise ValueError("配额应为 1 至 2^40 字节的整数。")
        with self._connection(write=True) as conn:
            conn.execute("UPDATE projects SET quota=? WHERE key=?", (amount, self._key))

    def status(self) -> str:
        if not self.enabled:
            return "当前工作区尚未开启工程记忆。"
        with self._connection() as conn:
            quota = conn.execute("SELECT quota FROM projects WHERE key=?", (self._key,)).fetchone()[0]
            size, count = conn.execute(f"SELECT coalesce(sum({_CONTENT_SIZE}),0),count(*) FROM records "
                                       "WHERE owner=? AND active=1", (self._key,)).fetchone()
            links = conn.execute("SELECT p.path FROM links l JOIN projects p ON p.key=l.producer "
                                 "WHERE l.reader=? ORDER BY p.path", (self._key,)).fetchall()
        directions = "、".join(item[0] for item in links) or "无"
        return f"工程记忆已开启，当前项目 {count} 条，内容 {size}/{quota} 字节。\n当前项目引用：{directions}"

    def _reserve(self, conn: sqlite3.Connection, size: int, *, keep: str = "") -> bool:
        config = conn.execute("SELECT quota FROM projects WHERE key=? AND enabled=1", (self._key,)).fetchone()
        if config is None:
            return False
        used = conn.execute(f"SELECT coalesce(sum({_CONTENT_SIZE}),0) FROM records "
                            "WHERE owner=? AND active=1 AND id!=?", (self._key, keep)).fetchone()[0]
        excess = used + size - config[0]
        if excess <= 0:
            return True
        if size > config[0]:
            return False
        choices = conn.execute(f"SELECT id,{_CONTENT_SIZE} FROM records WHERE owner=? AND active=1 AND id!=? "
                               "AND kind IN ('observation','failure') AND last_used<? ORDER BY last_used,id",
                               (self._key, keep, int(time.time()) - _EXPIRE_UNUSED_AFTER))
        evicted = []
        for identity, length in choices:
            evicted.append(identity)
            excess -= length
            if excess <= 0:
                for entry_id in evicted:
                    _retire(conn, entry_id)
                return True
        return False

    def capture(self, text: str, *, source_id: str, kind: str, source: str = "",
                source_hash: str = "", condition: str = "") -> str | None:
        return self.capture_result(text, source_id=source_id, kind=kind, source=source,
                                   source_hash=source_hash, condition=condition)[0]

    def capture_result(self, text: str, *, source_id: str, kind: str, source: str = "",
                       source_hash: str = "", condition: str = "") -> tuple[str | None, bool]:
        if (not self.enabled or not text.strip() or not source_id.strip()
                or kind not in {"rule", "verified", "failure", "observation"}):
            return None, False
        fields = (text, kind, source, source_hash, condition, source_id)
        if (any(findSecrets(value) or "\0" in value for value in fields) or len(text) > 4096
                or len(condition) > 1000 or len(source_id) > 512 or len(source) > 512):
            return None, False
        identity = "pm-" + _digest(self._key, source_id)[:32]
        with self._connection(write=True) as conn:
            existing = conn.execute("SELECT active FROM records WHERE id=?", (identity,)).fetchone()
            if existing is not None:
                return (identity if existing["active"] else None), False
            if kind == "verified" and source_hash and _source_status(self.cwd, source, source_hash) != "来源一致":
                return None, False
            if not self._reserve(conn, len((text + condition + source).encode())):
                return None, False
            conn.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,1,?,1,?)",
                         (identity, self._key, text, kind, source, source_hash, condition, source_id, int(time.time())))
        return identity, True

    def visible(self) -> list[ProjectRecord]:
        return self._read_records(None)

    def _read_records(self, identity: str | None) -> list[ProjectRecord]:
        if not self.enabled:
            return []
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT r.id,r.text,r.kind,r.source,r.source_hash,r.condition,r.version,r.source_id,
                       p.path AS workspace,r.owner
                FROM records r JOIN projects p ON p.key=r.owner
                WHERE r.active=1 AND p.enabled=1 AND (:id IS NULL OR r.id=:id) AND
                    (r.owner=:workspace OR EXISTS(SELECT 1 FROM links l
                        WHERE l.reader=:workspace AND l.producer=r.owner)
                    OR EXISTS(SELECT 1 FROM publications a WHERE a.target=:workspace
                        AND a.record=r.id AND a.version=r.version))
                ORDER BY r.rowid
            """, {"id": identity, "workspace": self._key})
            records = []
            for row in rows:
                data = dict(row)
                owner = data.pop("owner")
                try:
                    if owner != self._key and _workspace_key(Path(data["workspace"])) != owner:
                        continue
                except (ValueError, OSError):
                    continue
                records.append(ProjectRecord(**data))
            return records

    def get(self, identity: str) -> ProjectRecord | None:
        return next(iter(self._read_records(identity)), None)

    def get_by_source_id(self, source_id: str) -> ProjectRecord | None:
        return self.get("pm-" + _digest(self._key, source_id)[:32])

    def enhance(self, identity: str, text: str, *, source_id: str, expected_version: int,
                source: str, source_hash: str) -> bool:
        """提炼只能补充原始证据的第一版，不能替代用户更正或制造新的独立来源。"""
        if (expected_version != 1 or not self.enabled or not text.strip() or len(text) > 4096
                or findSecrets(text) or "\0" in text):
            return False
        with self._connection(write=True) as conn:
            entry = conn.execute("""
                SELECT kind,condition FROM records WHERE owner=? AND id=? AND source_id=? AND version=1
                    AND source=? AND source_hash=? AND active=1 AND kind IN ('rule','verified')
            """, (self._key, identity, source_id, source, source_hash)).fetchone()
            if entry is None:
                return False
            user_statement = entry["kind"] == "rule" and source in ("", "user_stated") and not source_hash
            if not user_statement and _source_status(self.cwd, source, source_hash) != "来源一致":
                return False
            if not self._reserve(conn, len((text + source + entry["condition"]).encode()), keep=identity):
                return False
            conn.execute("UPDATE records SET text=?,version=2,last_used=? WHERE id=?", (text, int(time.time()), identity))
            _discard_vector(conn, identity)
            return True

    def touch(self, identity: str, version: int) -> None:
        """确认实际使用才更新保留时点，候选枚举不算使用。"""
        record = self.get(identity)
        if record is not None and record.version == version:
            with self._connection(write=True) as conn:
                conn.execute("UPDATE records SET last_used=? WHERE id=? AND version=? AND active=1",
                             (int(time.time()), identity, version))

    def completed_mutation(self, operation_id: str, identity: str) -> bool:
        if not self.enabled:
            return False
        with self._connection() as conn:
            return bool(conn.execute("SELECT 1 FROM mutation_receipts m JOIN records r ON m.record=r.id "
                                     "WHERE m.operation=? AND m.record=? AND r.owner=?",
                                     (operation_id, identity, self._key)).fetchone())

    def correct(self, identity: str, text: str, *, expected_version: int | None = None,
                operation_id: str | None = None) -> bool:
        if not self.enabled or not text.strip() or len(text) > 4096 or findSecrets(text):
            return False
        with self._connection(write=True) as conn:
            row = conn.execute("SELECT version,condition FROM records WHERE id=? AND owner=? AND active=1",
                               (identity, self._key)).fetchone()
            if row is None or (expected_version is not None and expected_version != row[0]):
                return False
            if not self._reserve(conn, len((text + "user_stated" + row["condition"]).encode()), keep=identity):
                return False
            # 明确更正来自当前用户。先前技术结论的源码指纹不能为新正文背书。
            conn.execute("UPDATE records SET text=?, kind='rule', source='user_stated', source_hash='',"
                         "version=version+1, last_used=? WHERE id=?", (text, int(time.time()), identity))
            _discard_vector(conn, identity)
            if operation_id:
                conn.execute("INSERT INTO mutation_receipts VALUES(?,?)", (operation_id, identity))
            return True

    def _target(self, conn: sqlite3.Connection, workspace: Path) -> str:
        target = _workspace_key(workspace if workspace.is_absolute() else self.cwd / workspace)
        if target == self._key:
            raise ValueError("不能指定当前工作区。")
        if not conn.execute("SELECT 1 FROM projects WHERE key=? AND enabled=1", (target,)).fetchone():
            raise ValueError("目标工作区尚未开启工程记忆。")
        return target

    def link(self, workspace: Path) -> None:
        with self._connection(write=True) as conn:
            target = self._target(conn, workspace)
            conn.execute("INSERT OR IGNORE INTO links VALUES(?,?)", (self._key, target))

    def unlink(self, workspace: Path) -> bool:
        path = (workspace if workspace.is_absolute() else self.cwd / workspace).resolve()
        with self._connection(write=True) as conn:
            relations = conn.execute("SELECT p.key,p.path FROM links l JOIN projects p ON p.key=l.producer "
                                     "WHERE l.reader=?", (self._key,)).fetchall()
            targets = [(self._key, key) for key, stored in relations if Path(stored) == path]
            conn.executemany("DELETE FROM links WHERE reader=? AND producer=?", targets)
            return bool(targets)

    def publish(self, identity: str, workspace: Path) -> bool:
        if not self.enabled:
            return False
        with self._connection(write=True) as conn:
            target = self._target(conn, workspace)
            item = conn.execute("SELECT version,source,source_hash FROM records WHERE id=? AND owner=? AND active=1 "
                                "AND kind IN ('verified','rule')", (identity, self._key)).fetchone()
            if item is None:
                return False
            if item["source_hash"] and _source_status(self.cwd, item["source"], item["source_hash"]) != "来源一致":
                return False
            conn.execute("INSERT INTO publications VALUES(?,?,?) ON CONFLICT(target,record) "
                         "DO UPDATE SET version=excluded.version", (target, identity, item["version"]))
            return True

    def forget(self, identity: str, *, expected_version: int | None = None,
               operation_id: str | None = None) -> bool:
        if not self.enabled:
            return False
        with self._connection(write=True) as conn:
            changed = conn.execute("""
                UPDATE records SET text='', condition='', source='', source_hash='', source_id='',
                                   kind='', active=0, version=version+1
                WHERE owner=? AND id=? AND active=1 AND (? IS NULL OR version=?)
            """, (self._key, identity, expected_version, expected_version)).rowcount
            if changed:
                _discard_vector(conn, identity)
                if operation_id:
                    conn.execute("INSERT INTO mutation_receipts VALUES(?,?)", (operation_id, identity))
            return bool(changed)

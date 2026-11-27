"""按工作区配置和重建语义索引；检索只对调用方当次授权的快照评分。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .project_encoder import encode_local, model_files, normalize
from .project_store import ProjectMemory
from .secrets import findSecrets

_GUIDE = ("可手动从 intfloat/multilingual-e5-small 的 onnx 目录准备 model.onnx 和 tokenizer.json，"
          "两文件约 487 MB，另需安装 ruocun[semantic]。将本机目录交给 vectors local；"
          "也支持已下载的标准文本格式 .vec 词向量。若存不会自动下载。")
_CONFIG = """CREATE TABLE IF NOT EXISTS pm_vector_config (
owner TEXT PRIMARY KEY, mode TEXT, model TEXT, endpoint TEXT, key_env TEXT, generation TEXT)"""
_INDEX = """CREATE TABLE IF NOT EXISTS pm_vector_index (
owner TEXT, profile TEXT, identity TEXT, version TEXT, embedding TEXT, PRIMARY KEY(owner, identity))"""


@dataclass(frozen=True, slots=True)
class VectorSource:
    identity: str
    version: str
    text: str


@dataclass(frozen=True, slots=True)
class _Profile:
    mode: str
    model: str
    endpoint: str = ""
    key_env: str = ""
    generation: str = ""


def _profile(project: ProjectMemory) -> _Profile:
    default = _Profile("local", str(project.user_root / "models" / "multilingual-e5-small"))
    if not project.enabled:
        return default
    with project.index_connection() as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_vector_config'").fetchone():
            row = conn.execute("SELECT mode,model,endpoint,key_env,generation FROM pm_vector_config WHERE owner=?",
                               (project.workspace_key,)).fetchone()
            if row:
                return _Profile(*row)
    return default


def _signature(profile: _Profile) -> str:
    features = [profile.mode, profile.model, profile.endpoint, profile.generation, "mean-v1"]
    if profile.mode == "local":
        for path in model_files(Path(profile.model)):
            st = path.stat()
            features.extend([str(st.st_size), str(st.st_mtime_ns), str(st.st_ino)])
    return hashlib.sha256(json.dumps(features).encode()).hexdigest()


def configureVectors(project: ProjectMemory, mode: str, *, model: str = "", endpoint: str = "",
                     key_env: str = "") -> str:
    if not project.enabled:
        raise ValueError("请先开启当前项目的工程记忆。")
    mode = mode.casefold()
    if mode not in ("off", "local", "remote"):
        raise ValueError("请选择 off、local 或 remote。")
    if mode == "remote":
        url = urlsplit(endpoint)
        local = url.hostname in ("localhost", "127.0.0.1", "::1")
        if (not model.strip() or not url.hostname or url.username or url.password or url.query or url.fragment
                or not (url.scheme == "https" or (url.scheme == "http" and local))
                or (key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env))):
            raise ValueError("请提供 HTTPS embeddings 地址、模型名及可选密钥环境变量名。")
    elif endpoint or key_env:
        raise ValueError("只有远端模式可配置服务和密钥环境变量。")
    if mode == "local":
        if model:
            path = Path(model)
            if not path.is_absolute() or str(path).startswith("\\\\"):
                raise ValueError("请使用本机模型的绝对路径。")
        else:
            path = project.user_root / "models" / "multilingual-e5-small"
        model = str(path)
    if mode == "off":
        model = ""
    with project.index_connection(write=True) as conn:
        conn.execute(_CONFIG)
        conn.execute(_INDEX)
        conn.execute("INSERT OR REPLACE INTO pm_vector_config VALUES(?,?,?,?,?,?)",
                     (project.workspace_key, mode, model, endpoint, key_env, uuid4().hex))
        conn.execute("DELETE FROM pm_vector_index WHERE owner=?", (project.workspace_key,))
    return vectorStatus(project)


_lock = threading.Lock()
_running: dict[tuple[str, str], tuple[str, Future[dict[str, float]]]] = {}
_diagnostics: dict[tuple[str, str], str] = {}


def _job_key(project: ProjectMemory, signature: str) -> tuple[str, str]:
    return str(project.user_root) + "#" + project.workspace_key, signature


def vectorStatus(project: ProjectMemory) -> str:
    if not project.enabled:
        return "工程记忆未开启。"
    profile = _profile(project)
    if profile.mode == "off":
        return "向量已关闭，仅使用本地原文与代码词法检索。"
    try:
        signature = _signature(profile)
    except OSError:
        return "本地模型未就绪，已降级到词法检索。" + _GUIDE
    with _lock:
        state = _diagnostics.get(_job_key(project, signature), "已配置，等待首次检索校验。")
    if profile.mode == "remote":
        return "显式远端向量：" + profile.model + "。" + state + " 发送查询和当前允许的项目材料。"
    return "本地语义（" + Path(profile.model).name + "）：" + state + " " + _GUIDE


def _remote(profile: _Profile, texts: Sequence[str]) -> list[list[float]]:
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class DirectOnly(HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    headers = {"Content-Type": "application/json"}
    if profile.key_env:
        token = os.environ.get(profile.key_env)
        if not token:
            raise ValueError("未设置向量服务密钥")
        headers["Authorization"] = "Bearer " + token
    body = json.dumps({"model": profile.model, "input": texts}, ensure_ascii=False).encode()
    req = Request(profile.endpoint, data=body, headers=headers, method="POST")
    with build_opener(DirectOnly()).open(req, timeout=3) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("向量响应过长")
    rows = sorted(json.loads(raw)["data"], key=lambda row: row["index"])
    if [row["index"] for row in rows] != list(range(len(texts))):
        raise ValueError("向量响应与输入不匹配")
    result = [normalize(list(map(float, row["embedding"]))) for row in rows]
    if len({len(v) for v in result}) != 1:
        raise ValueError("向量维度不一致")
    return result


def _encode(profile: _Profile, signature: str, texts: list[str], query: bool = False) -> list[list[float]]:
    if profile.mode == "remote":
        return _remote(profile, texts)
    return encode_local(profile.model, signature, texts, query=query)


def _vectors(project: ProjectMemory, profile: _Profile, signature: str, sources: list[VectorSource],
             query: str | None, live: Callable[[], set[tuple[str, str]]], deadline: float) -> dict[str, float]:
    owner = project.workspace_key
    with project.index_connection(write=True) as conn:
        conn.execute(_INDEX)
        rows = conn.execute("SELECT identity,version,embedding FROM pm_vector_index WHERE owner=? AND profile=?",
                            (owner, signature)).fetchall()
    cache = {(row[0], row[1]): json.loads(row[2]) for row in rows}
    active = live()
    sources = [s for s in sources if (s.identity, s.version) in active and (
        profile.mode != "remote" or ((s.identity.startswith("pm-") or s.identity.startswith("project/"))
                                     and not findSecrets(s.text)))]
    missing = [s for s in sources if (s.identity, s.version) not in cache]
    for start in range(0, len(missing), 12):
        if _profile(project) != profile:
            return {}
        batch = missing[start:start + 12]
        if profile.mode == "remote":
            active = live()
            batch = [s for s in batch if (s.identity, s.version) in active]
        if not batch:
            continue
        embeddings = _encode(profile, signature, [s.text for s in batch])
        with project.index_connection(write=True) as conn:
            current = conn.execute("SELECT generation FROM pm_vector_config WHERE owner=?", (owner,)).fetchone() if (
                conn.execute("SELECT 1 FROM sqlite_master WHERE name='pm_vector_config'").fetchone()) else None
            if (current[0] if current else "") != profile.generation:
                return {}
            active = live()
            valid = [(s, v) for s, v in zip(batch, embeddings, strict=True) if (s.identity, s.version) in active]
            conn.executemany("INSERT OR REPLACE INTO pm_vector_index VALUES(?,?,?,?,?)",
                             ((owner, signature, s.identity, s.version, json.dumps(v)) for s, v in valid))
        cache.update({(s.identity, s.version): v for s, v in valid})
    if (query is None or _profile(project) != profile or
            (profile.mode == "remote" and (time.monotonic() >= deadline or findSecrets(query)))):
        return {}
    active = live()
    sources = [s for s in sources if (s.identity, s.version) in active and (s.identity, s.version) in cache]
    if not sources:
        return {}
    q = _encode(profile, signature, [query], query=True)[0]
    result = {}
    for s in sources:
        v = cache.get((s.identity, s.version))
        if v and len(v) == len(q):
            similarity = math.fsum(a*b for a, b in zip(q, v))
            if similarity >= 0.84:
                result[s.identity] = similarity
    return result


async def semanticScores(project: ProjectMemory, sources: list[VectorSource], query: str | None,
                         live: Callable[[], set[tuple[str, str]]], *, timeout: float = 0.40) -> dict[str, float]:
    try:
        profile = await asyncio.to_thread(_profile, project)
        if profile.mode == "off" or not sources:
            return {}
        signature = _signature(profile)
    except Exception:
        # 配置、派生索引或模型不可用不能打断词法检索。
        return {}
    job = _job_key(project, signature)
    deadline = time.monotonic() + timeout
    key = hashlib.sha256(repr((signature, query, [(s.identity, s.version) for s in sources])).encode()).hexdigest()
    with _lock:
        pending = _running.get(job)
        if pending:
            if pending[0] != key:
                return {}
            future = pending[1]
        else:
            future = Future()
            _running[job] = (key, future)

            def work() -> None:
                try:
                    scores = _vectors(project, profile, signature, sources, query, live, deadline)
                    diagnostic = "已就绪。"
                except Exception:
                    scores = {}
                    diagnostic = "加载或调用失败，已降级到本地词法检索。"
                with _lock:
                    _diagnostics[job] = diagnostic
                    if len(_diagnostics) > 128:
                        _diagnostics.pop(next(iter(_diagnostics)))
                    _running.pop(job, None)
                future.set_result(scores)
            threading.Thread(target=work, name="ruocun-memory-index", daemon=True).start()
    try:
        return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout=timeout)
    except TimeoutError:
        with _lock:
            _diagnostics[job] = "本次查询超时，已降级到词法检索；索引继续在后台准备。"
        return {}

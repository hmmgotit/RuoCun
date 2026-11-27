"""项目开启后的正式任务召回入口。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ruocun.config.compat import DEFAULT_COMPATIBILITY
from ruocun.memory.inject import StartupMemorySnapshot
from ruocun.memory.retrieval import buildMemoryChecks
from ruocun.memory.store import MemoryStore
from ruocun.memory.types import newMemory


def test_interactive_task_uses_one_budget_for_markdown_and_engineering(tmp_path: Path) -> None:
    from ruocun.memory.project_store import ProjectMemory

    async def run() -> None:
        cwd = tmp_path / "exporter"
        cwd.mkdir()
        home = tmp_path / "home"
        store = MemoryStore(cwd, home, DEFAULT_COMPATIBILITY)
        project = ProjectMemory(home, cwd)
        project.enable()
        identity = project.capture("断点导出漏行，resumeCursor 先于写入完成。", source_id="trace/page-2",
                                   kind="observation", source="src/exporter.py")
        assert identity
        rule = newMemory(name="cursor-rule", text="断点导出时应检查落盘确认。", type="semantic", today=store.today())
        await store.upsert("project", rule)
        result = await buildMemoryChecks(store, "断点导出漏行 resume_cursor", startup=StartupMemorySnapshot(),
                                         project=project)
        assert set(result.ids) == {identity, "project/cursor-rule"}
        assert "先于写入完成" in result.text and "落盘确认" in result.text
        assert result.text.count("[[/ruocun-memory]]") == 2
        assert len(result.text) <= 2400

    asyncio.run(run())

def test_local_model_finds_paraphrase_and_missing_model_falls_back(tmp_path: Path) -> None:
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.memory.project_vectors import configureVectors, vectorStatus

    async def run() -> None:
        cwd = tmp_path / "app"
        cwd.mkdir()
        project = ProjectMemory(tmp_path / "home", cwd)
        project.enable()
        store = MemoryStore(cwd, project.user_root, DEFAULT_COMPATIBILITY)
        published = project.capture("checkpoint flush omitted records", source_id="history/export", kind="observation")
        project.capture("palette shade", source_id="history/color", kind="observation")
        assert published
        query = "断点恢复丢行"
        startup = StartupMemorySnapshot()
        assert not (await buildMemoryChecks(store, query, startup=startup, project=project)).ids
        model = tmp_path / "tiny.vec"
        model.write_text("6 3\n断点恢复丢行 1 0 0\ncheckpoint 1 0 0\nflush 1 0 0\nomitted 1 0 0\n"
                         "palette 0 1 0\nshade 0 1 0\n", encoding="utf-8")
        configureVectors(project, "local", model=str(model))
        result = await buildMemoryChecks(store, query, startup=startup, project=project)
        assert result.ids == (published,)
        assert "omitted records" in result.text
        model.unlink()
        result = await buildMemoryChecks(store, "checkpoint", startup=startup, project=project)
        assert result.ids == (published,)
        assert "词法" in vectorStatus(project)
    asyncio.run(run())

def test_delayed_remote_cannot_send_query_after_only_record_is_withdrawn(tmp_path: Path) -> None:
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from ruocun.memory.project_search import rebuildProjectIndex
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.memory.project_vectors import configureVectors

    received = []
    started = threading.Event()
    allow_response = threading.Event()
    body_sent = threading.Event()
    sent_query = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            inputs = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["input"]
            received.append(inputs)
            if len(received) == 1:
                started.set()
                allow_response.wait(3)
            else:
                sent_query.set()
            payload = json.dumps({"data": [{"index": i, "embedding": [1, 0]} for i in range(len(inputs))]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            body_sent.set()

    async def run(port: int) -> None:
        cwd = tmp_path / "target"
        cwd.mkdir()
        project = ProjectMemory(tmp_path / "home", cwd)
        project.enable()
        store = MemoryStore(cwd, project.user_root, DEFAULT_COMPATIBILITY)
        identity = project.capture("old cursor specifics", source_id="s1", kind="observation")
        assert identity
        configureVectors(project, "remote", model="fixture", endpoint=f"http://127.0.0.1:{port}/embeddings")
        result = await buildMemoryChecks(store, "cursor query", startup=StartupMemorySnapshot(), project=project)
        assert started.is_set() and identity in result.ids
        assert project.forget(identity)
        model = tmp_path / "replacement.vec"
        model.write_text("2 2\n替代 1 0\nreplacement 1 0\n", encoding="utf-8")
        project.capture("replacement", source_id="s2", kind="observation")
        configureVectors(project, "local", model=str(model))
        assert "已就绪" in await rebuildProjectIndex(store, project)
        allow_response.set()
        assert await asyncio.to_thread(body_sent.wait, 2)
        assert not await asyncio.to_thread(sent_query.wait, 0.5)
        follow = await buildMemoryChecks(store, "cursor", startup=StartupMemorySnapshot(), project=project)
        assert not follow.ids

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            asyncio.run(run(server.server_port))
        finally:
            allow_response.set()
            server.shutdown()

def test_memory_read_id_rechecks_source_and_legacy_query_stays_in_markdown(tmp_path: Path) -> None:
    from ruocun.memory.hash import contentHash
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.tools.memory_read import makeMemoryReadTool
    from ruocun.tools.types import ToolContext

    async def run() -> None:
        cwd = tmp_path / "app"
        cwd.mkdir()
        home = tmp_path / "home"
        project = ProjectMemory(home, cwd)
        project.enable()
        store = MemoryStore(cwd, home, DEFAULT_COMPATIBILITY)
        (cwd / "export.py").write_text("old", encoding="utf-8")
        identity = project.capture("cursor 实测线索", kind="observation", source_id="trace/17",
                                   source="export.py", source_hash=contentHash("old"), condition="仅限旧导出器")
        assert identity
        (cwd / "export.py").write_text("new", encoding="utf-8")
        tool = makeMemoryReadTool(store, project)
        ctx = ToolContext(str(cwd))
        reply = await tool.handler({"query": identity}, ctx)
        assert all(s in reply for s in (identity, "来源已变", "trace/17", "仅限旧导出器", "cursor 实测线索"))
        assert reply.endswith("[[/ruocun-memory]]")
        assert "cursor 实测线索" not in await tool.handler({"query": "cursor"}, ctx)
        assert identity in await tool.handler({"query": "cursor", "mode": "project"}, ctx)
        assert identity in await tool.handler({"query": "project: cursor"}, ctx)

    asyncio.run(run())

def test_fresh_start_keeps_local_rules_out_of_repeated_task_bodies(tmp_path: Path) -> None:
    from ruocun.memory.inject import buildStartupMemorySnapshot
    from ruocun.memory.project_store import ProjectMemory

    async def run() -> None:
        cwd = tmp_path / "app"
        cwd.mkdir()
        home = tmp_path / "home"
        project = ProjectMemory(home, cwd)
        store = MemoryStore(cwd, home, DEFAULT_COMPATIBILITY)
        md = newMemory(name="markdown", text="原有 Markdown 规则", type="semantic", today=store.today())
        await store.upsert("project", md)
        old = await buildStartupMemorySnapshot(store)
        assert old.text == (await buildStartupMemorySnapshot(store, project=project)).text
        project.enable()
        evidence = project.capture("pipeline failed once", kind="failure", source_id="outcome")
        rule = project.capture("导出组件必须保留 SYNTHETIC-CURSOR 列。", kind="rule", source_id="decision-old")
        assert evidence is not None and rule is not None
        for i in range(10):
            project.capture("大段约定" * 180 + str(i), kind="rule", source_id=f"filler-{i}")
        tracked = await buildStartupMemorySnapshot(store, tracked=True)
        snapshot = await buildStartupMemorySnapshot(store, project=ProjectMemory(home, cwd))
        assert snapshot.text.startswith(tracked.text)
        assert len(snapshot.text) - len(tracked.text) <= 2400
        assert "SYNTHETIC-CURSOR" in snapshot.text and "pipeline failed once" not in snapshot.text
        check = await buildMemoryChecks(store, "导出组件", startup=snapshot, project=project)
        assert rule in check.ids and "SYNTHETIC-CURSOR" not in check.text
        assert project.correct(rule, "导出组件现在使用 SYNTHETIC-NEW-COLUMN")
        updated = await buildMemoryChecks(store, "导出组件", startup=snapshot, project=project)
        assert "SYNTHETIC-NEW-COLUMN" in updated.text

    asyncio.run(run())


def test_child_tool_rebinds_to_its_actual_workspace(tmp_path: Path) -> None:
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.tools.memory_read import makeMemoryReadTool
    from ruocun.tools.types import ToolContext

    async def run() -> None:
        parent_root, child_root, home = tmp_path / "parent", tmp_path / "child", tmp_path / "home"
        parent_root.mkdir()
        child_root.mkdir()
        parent, child = ProjectMemory(home, parent_root), ProjectMemory(home, child_root)
        parent.enable()
        child.enable()
        a = parent.capture("cursor parent marker", kind="observation", source_id="trace")
        b = child.capture("cursor child marker", kind="observation", source_id="trace")
        store = MemoryStore(parent_root, home, DEFAULT_COMPATIBILITY)
        handler = makeMemoryReadTool(store, parent).handler
        context = ToolContext(str(parent_root), cwd=str(child_root), subagentDepth=1)
        result = await handler({"mode": "project", "query": "cursor"}, context)
        assert b in result and a not in result
        assert "parent marker" not in await handler({"query": a}, context)

    asyncio.run(run())

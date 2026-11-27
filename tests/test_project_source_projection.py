import asyncio

from ruocun.config.compat import DEFAULT_COMPATIBILITY
from ruocun.memory.hash import contentHash
from ruocun.memory.project_projection import mark_derived, mark_memory, workspace_view
from ruocun.memory.project_store import ProjectMemory
from ruocun.memory.store import MemoryStore
from ruocun.memory.types import newMemory


def test_each_model_send_rechecks_shared_source_for_memory_tools_and_summaries(tmp_path):
    cwd = tmp_path / "source"
    cwd.mkdir()
    account = tmp_path / "account"
    project = ProjectMemory(account, cwd)
    project.enable()
    store = MemoryStore(cwd, account, DEFAULT_COMPATIBILITY)
    path = cwd / "handler.py"
    path.write_text("before", encoding="utf-8")
    fingerprint = contentHash("before")
    record_id = project.capture("ENGINEERING", kind="verified", source_id="raw:1", source="handler.py",
                                source_hash=fingerprint)
    asyncio.run(store.upsert("project", newMemory(name="rule", text="MARKDOWN", type="semantic",
                                                  source="handler.py", sourceHash=fingerprint, today=store.today())))
    md_version = asyncio.run(store.loadNative("project"))[0].contentDigest
    literal = {"role": "user", "content": mark_memory(record_id, "1", "USER-LITERAL")}
    messages = [{"role": "system", "content": mark_memory("project/rule", md_version, "MARKDOWN")}, literal,
                {"role": "tool", "tool_call_id": "past", "content": mark_memory(record_id, "1", "ENGINEERING")},
                {"role": "system", "content": mark_derived("SUMMARY", [(record_id, "1"), ("project/rule", md_version)])}]
    original = [item.copy() for item in messages]
    assert [m["content"] for m in workspace_view(store, project, messages)][::2] == ["MARKDOWN", "ENGINEERING"]
    path.write_text("after", encoding="utf-8")
    changed = workspace_view(store, project, messages)
    assert all("来源已变" in changed[n]["content"] for n in [0, 2, 3])
    assert changed[1] == literal and changed[2]["tool_call_id"] == "past"
    path.unlink()
    stale = workspace_view(store, project, messages)
    assert all("无法核实" in stale[n]["content"] for n in [0, 2, 3])
    assert "MARKDOWN" not in stale[0]["content"] and "ENGINEERING" not in stale[2]["content"]
    assert "SUMMARY" not in stale[3]["content"]
    path.write_text("before", encoding="utf-8")
    assert workspace_view(store, project, messages)[2]["content"] == "ENGINEERING"
    assert messages == original
    assert project.forget(record_id)
    assert "ENGINEERING" not in workspace_view(store, project, messages)[2]["content"]

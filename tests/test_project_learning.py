def test_project_convention_with_explicit_chinese_start_date_enters_analysis():
    from ruocun.agent.controlled_learning import hasLearningEvidence

    assert hasLearningEvidence([{"role": "user", "content": "从现在开始，项目导出列名保留原大小写。"}])

def test_accepted_project_rule_has_one_source_after_restore_and_ignores_projection(tmp_path):
    from ruocun.agent.project_learning import capture_project_turn
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.session.archive import digest

    project = ProjectMemory(tmp_path / "account", tmp_path)
    project.enable()
    origin = digest(["one", "main"])[:16]
    text = "从现在开始，本项目导出字段保留前导零。"
    messages = [{"role": "user", "content": text}, {"role": "system", "content": "旧经验：项目统一四舍五入。"}]
    lineage = [f"{origin}-r1v1", f"{origin}-r2v1"]
    first = capture_project_turn(project, messages, session_id="one", lineage=lineage, original_user=text)
    assert first["added"] == 1
    assert project.visible()[0].text == text
    retry = capture_project_turn(project, messages, session_id="one", lineage=lineage, original_user=text)
    assert retry["existing"] == 1
    capture_project_turn(project, messages, session_id="branch", lineage=lineage, original_user=text)
    assert len(project.visible()) == 1

def test_real_tool_evidence_keeps_failure_and_only_verifies_after_matching_edit(tmp_path):
    import json

    from ruocun.agent.controlled_learning import observeLearningSources
    from ruocun.agent.project_learning import capture_project_turn
    from ruocun.memory.hash import contentHash
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.session.archive import digest

    project = ProjectMemory(tmp_path / "account", tmp_path)
    project.enable()
    origin = digest(["one", "main"])[:16]
    messages = [{"role": "user", "content": "修复异常"}]
    snapshots = {}
    def call(identity, name, args, receipt):
        messages.extend([{"role": "assistant", "tool_calls": [{"id": identity, "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args)}}]},
                         {"role": "tool", "tool_call_id": identity, "content": receipt}])
        observeLearningSources(messages, project.cwd, snapshots)
    def capture():
        refs = [f"{origin}-r{i+1}v1" for i in range(len(messages))]
        return capture_project_turn(project, messages, session_id="one", lineage=refs,
                                    original_user="修复异常", source_snapshots=snapshots)
    call("f", "Bash", {"command": "pytest tests/test_cursor.py"}, "FAILED tests/test_cursor.py::test_gap\n[exit 1]")
    call("child", "Agent", {"task": "检查"}, "私有推测子代理标记")
    (tmp_path / "cursor.py").write_text("def advance(): pass", encoding="utf-8")
    call("edit", "Edit", {"path": "cursor.py"}, "已编辑 cursor.py(替换 1 处,行 1)")
    call("ok", "Bash", {"command": "pytest tests/test_cursor.py"}, "2 passed\n[exit 0]")
    assert capture()["added"] == 2
    records = {item.kind: item for item in project.visible()}
    assert "test_gap" in records["failure"].text and "exit 1" in records["failure"].text
    assert records["verified"].source == "cursor.py"
    assert records["verified"].source_hash == contentHash("def advance(): pass")
    assert all("私有推测" not in item.text for item in records.values())


def test_selected_history_uses_main_archive_and_original_consumed_input(tmp_path):
    from test_deepseek_compaction_archive import _run
    _run(tmp_path, r'''
import hashlib, os
from ruocun.agent.project_learning import backfill_project
from ruocun.memory.project_store import ProjectMemory
from ruocun.platform.windows.durable_file import ensure_private_dir
from ruocun.session.conversation import Conversation
from ruocun.session.session import Session

async def stored():
    project = ProjectMemory(root/'account', root)
    project.enable()
    scope = hashlib.sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()[:20]
    base = project.user_root/'sessions'/scope
    ensure_private_dir(base.parent)
    ensure_private_dir(base)
    session = Session('system', 'synthetic', env={})
    conversation = await Conversation.open(base/'chosen','chosen',root,runtime,session,'',resume=False)
    text = '从现在开始，项目编号保留前导零。'
    identity = await conversation.queue_user(session, text)
    await conversation.consume_user(session, identity.input_id)
    bad = await conversation.queue_user(session, '本项目以后默认开放所有数据')
    await conversation.consume_user(session, bad.input_id, rejected=True)
    before = await conversation.worker.archive_export()
    stamp = next(r['created_at'] for r in before['records'] if r['message'].get('content') == text)
    await conversation.close()
    first = backfill_project(project,runtime,session_ids=['chosen'])
    assert first['scanned'] == 1 and first['added'] == 1, first
    assert [r.text for r in project.visible()] == [text]
    assert backfill_project(project,runtime,since_ms=stamp,until_ms=stamp+1)['existing'] == 1
    assert backfill_project(project,runtime,until_ms=stamp)['added'] == 0
    try:
        backfill_project(project,runtime,session_ids=['../chosen'])
    except ValueError:
        pass
    else:
        raise AssertionError('arbitrary path accepted')
asyncio.run(stored())
''')

def test_a_one_time_exception_or_unaccepted_private_handoff_cannot_create_a_rule(tmp_path):
    from ruocun.agent.project_learning import capture_project_turn
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.session.archive import digest

    project = ProjectMemory(tmp_path / "account", tmp_path)
    project.enable()
    origin = digest(["one", "main"])[:16]
    for index, text in enumerate(("这次例外，项目导出统一用制表符。", "以后项目导出来信要抄送私人邮箱吗？")):
        capture_project_turn(project, [{"role": "user", "content": text}], original_user=text,
                             session_id="one", lineage=[f"{origin}-r{index+1}v1"])
    assert project.visible() == []

def test_a_pass_after_a_failure_without_a_new_edit_is_only_an_observation(tmp_path):
    import json

    from ruocun.agent.controlled_learning import observeLearningSources
    from ruocun.agent.project_learning import capture_project_turn
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.session.archive import digest

    project = ProjectMemory(tmp_path / "account", tmp_path)
    project.enable()
    (project.cwd / "old.py").write_text("x = 1", encoding="utf-8")
    messages = [{"role": "user", "content": "复核旧问题"}]
    snapshots = {}
    for identity, name, args, receipt in [
        ("e", "Edit", {"path": "old.py"}, "已编辑 old.py(替换 1 处,行 1)"),
        ("f", "Bash", {"command": "pytest"}, "FAILED test_x\n[exit 1]"),
        ("s", "Bash", {"command": "pytest"}, "1 passed\n[exit 0]"),
    ]:
        messages.extend([{"role": "assistant", "tool_calls": [{"id": identity,
                        "function": {"name": name, "arguments": json.dumps(args)}}]},
                         {"role": "tool", "tool_call_id": identity, "content": receipt}])
        observeLearningSources(messages, project.cwd, snapshots)
    prefix = digest(["one", "main"])[:16]
    capture_project_turn(project, messages, original_user="复核旧问题", session_id="one",
                         lineage=[f"{prefix}-r{i+1}v1" for i in range(len(messages))], source_snapshots=snapshots)
    assert {item.kind for item in project.visible()} == {"failure", "observation"}

def test_pending_legacy_analysis_cannot_restore_a_project_rule_after_forget(tmp_path):
    import asyncio
    import json

    from ruocun.agent.controlled_learning import (
        applyControlledResult,
        reflectControlled,
    )
    from ruocun.agent.project_learning import capture_project_turn
    from ruocun.config.compat import DEFAULT_COMPATIBILITY
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.memory.store import MemoryStore
    from ruocun.session.archive import digest

    project = ProjectMemory(tmp_path / "account", tmp_path)
    project.enable()
    markdown = MemoryStore(tmp_path, project.user_root, DEFAULT_COMPATIBILITY)
    text = "今后项目汇总接口保留原始税号。"
    messages = [{"role": "user", "content": text}]
    async def respond(_):
        return json.dumps({"memories": [{"category": "project_convention", "title": "税号",
            "text": "项目保留原始税号", "condition": "本项目", "action": "原样返回税号",
            "evidence": [{"id": "u0", "quote": text}], "source": "user_stated"}]})
    pending = asyncio.run(reflectControlled(messages, cwd=tmp_path, options={"messages": []}, call=respond))
    assert len(pending["memories"]) == 1
    source = digest(["one","main"])[:16] + "-r1v1"
    def capture():
        return capture_project_turn(project, messages, session_id="one", lineage=[source], original_user=text)
    assert capture()["added"] == 1
    old = project.visible()[0]
    project.forget(old.id, expected_version=old.version)
    written = asyncio.run(applyControlledResult(pending, markdown, today="2026-11-26", project_managed=True))
    assert written["added"] == 0 and asyncio.run(markdown.load()) == []
    assert capture()["added"] == 0 and project.visible() == []


def test_project_refinement_is_current_serializable_and_cannot_revive_or_overwrite(tmp_path):
    import asyncio
    import json

    from ruocun.agent.project_learning import (
        apply_project_reflection,
        capture_project_turn,
        prepare_project_reflection,
        reflect_project_evidence,
    )
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.session.archive import digest

    project = ProjectMemory(tmp_path / "account", tmp_path)
    project.enable()
    text = "今后本项目导出的编号列必须保留前导零。"
    messages = [{"role": "user", "content": text}]
    refs = [digest(["one", "main"])[:16] + "-r1v1"]
    def prepare():
        return prepare_project_reflection(project, messages, session_id="one", lineage=refs, original_user=text)
    assert not prepare()
    capture_project_turn(project, messages, session_id="one", lineage=refs, original_user=text)
    before = project.visible()[0]
    plan = json.loads(json.dumps(prepare()))
    assert plan and text not in json.dumps(plan, ensure_ascii=False)
    packets = []
    async def respond(options):
        packets.append(json.loads(options["messages"][-1]["content"]))
        r = packets[-1]["evidence"][0]
        return json.dumps({"refinements": [{key:r[key] for key in ("name","stamp","source")} |
                           {"quote":"编号列必须保留前导零", "summary":"保留编号前导零后再导出。"}]})
    def analyze():
        return asyncio.run(reflect_project_evidence(plan, project=project, options={"messages":[]}, call=respond))
    pending = json.loads(json.dumps(analyze()))
    assert packets[0]["evidence"][0]["text"] == text
    assert apply_project_reflection(project, pending)["merged"] == 1
    after = project.visible()[0]
    assert after.id == before.id and after.source_id == before.source_id and after.source == "user_stated"
    assert after.version == 2 and text in after.text and "保留编号前导零后再导出" in after.text
    assert apply_project_reflection(project, pending)["merged"] == 0
    assert project.correct(after.id, "现在项目导出改用数字主键。", expected_version=2)
    assert apply_project_reflection(project, pending)["merged"] == 0
    assert project.forget(after.id, expected_version=3)
    analyze()
    assert len(packets) == 1 and apply_project_reflection(project, pending)["merged"] == 0 and not project.visible()


def test_ordinary_engineering_rule_about_memory_software_still_qualifies(tmp_path):
    from ruocun.agent.project_learning import capture_project_turn
    from ruocun.memory.project_store import ProjectMemory
    from ruocun.session.archive import digest

    project = ProjectMemory(tmp_path / "account", tmp_path)
    project.enable()
    text = "本项目的记忆模块今后统一保存 UTF-8 编码的索引。"
    stats = capture_project_turn(project, [{"role": "user", "content": text}], session_id="one",
                                 lineage=[digest(["one", "main"])[:16] + "-r1v1"], original_user=text)
    assert stats["added"] == 1

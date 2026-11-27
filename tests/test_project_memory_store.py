"""从 /memory 管理入口核对工程记忆在独立会话间的可见性。"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from ruocun.config.compat import DEFAULT_COMPATIBILITY
from ruocun.memory.command import memoryCommand
from ruocun.memory.hash import contentHash
from ruocun.memory.project_store import ProjectMemory
from ruocun.memory.store import MemoryStore


def _command(project, text):
    markdown = MemoryStore(project.cwd, project.user_root, DEFAULT_COMPATIBILITY)
    return asyncio.run(memoryCommand("/memory project " + text, markdown, project=project))


def _project(root: Path, name: str):
    directory = root / name
    directory.mkdir(parents=True)
    return ProjectMemory(root / "account", directory)


def test_project_capture_requires_enable_and_survives_restart(tmp_path):
    project = _project(tmp_path, "one/app")
    assert project.capture("空页仍有下一页", source_id="session/scope/raw-1", kind="observation") is None
    assert "已开启" in _command(project, "enable")
    identity = project.capture("空页仍有下一页", source_id="session/scope/raw-1", kind="observation")
    assert identity
    assert project.capture("空页仍有下一页", source_id="session/scope/raw-1", kind="observation") == identity
    reopened = ProjectMemory(project.user_root, project.cwd)
    assert [entry.text for entry in reopened.visible()] == ["空页仍有下一页"]
    assert reopened.get(identity).version == 1
    assert identity in _command(reopened, "list")


def test_links_are_one_way_and_publication_does_not_relay_authority(tmp_path):
    producer = _project(tmp_path, "one/app")
    reader = _project(tmp_path, "two/app")
    other = _project(tmp_path, "three/app")
    for project in (producer, reader, other):
        _command(project, "enable")
    first, created = producer.capture_result("只在 A 验证的修复", source_id="a/main/r1", kind="verified")
    second = reader.capture("B 的失败线索", source_id="b/main/r1", kind="failure")
    assert first and second and created
    assert reader.get(first) is None and producer.get(second) is None
    assert "已关联" in _command(reader, f'link "{producer.cwd}"')
    assert reader.get(first).text == "只在 A 验证的修复"
    assert "已关联" in _command(other, f'link "{reader.cwd}"')
    assert other.get(first) is None
    assert "受保护" in _command(reader, f"forget {first}")
    assert "已发布" in _command(producer, f'publish {first} "{other.cwd}"')
    assert other.get(first).id == first
    assert "已解除" in _command(reader, f'unlink "{producer.cwd}"')
    assert reader.get(first) is None
    assert other.get(first) is not None
    assert "已撤回" in _command(producer, f"forget {first}")
    assert other.get(first) is None
    assert producer.capture("旧提炼迟到", source_id="a/main/r1", kind="verified") is None
    assert other.get(second).text == "B 的失败线索"


def test_correction_replaces_a_version_without_late_retry_or_silent_republication(tmp_path):
    project = _project(tmp_path, "owner")
    target = _project(tmp_path, "reader")
    for site in (project, target):
        _command(site, "enable")
    identity = project.capture("旧界面按秒", kind="rule", source_id="session/event", condition="仅客户端 v2")
    assert project.publish(identity, target.cwd)
    assert "已更正" in _command(project, f'correct {identity} "新界面按毫秒"')
    assert project.capture("旧界面按秒", kind="rule", source_id="session/event") == identity
    assert target.get(identity) is None
    entry = project.get(identity)
    assert (entry.version, entry.text) == (2, "新界面按毫秒")
    assert entry.condition == "仅客户端 v2"
    assert not project.forget(identity, expected_version=1)
    assert not project.correct(identity, "陈旧提交", expected_version=1)
    assert project.get(identity).text == "新界面按毫秒"


def test_small_quota_keeps_rules_and_verified_evidence(tmp_path, monkeypatch):
    project = _project(tmp_path, "root")
    _command(project, "enable")
    assert "配额" in _command(project, "quota 220")
    rule = project.capture("R" * 120, kind="rule", source_id="policy")
    failed = project.capture("F" * 30, kind="failure", source_id="old-observation")
    assert rule and failed
    later = time.time() + 32 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    assert project.capture("P" * 80, kind="verified", source_id="verified")
    assert project.get(failed) is None and project.get(rule) is not None
    assert project.capture("X" * 100, kind="observation", source_id="later") is None
    status = _command(project, "status")
    assert "220" in status and "字节" in status
    assert "已撤回" in _command(project, f"forget {rule}")


def test_publish_rechecks_version_and_shows_where_evidence_was_verified(tmp_path):
    project = _project(tmp_path, "producer")
    reader = _project(tmp_path, "receiver")
    for site in (project, reader):
        _command(site, "enable")
    file = project.cwd / "parser.py"
    file.write_text("current", encoding="utf-8")
    identity = project.capture("空输入已实际验证", source_id="event/raw/12", kind="verified", source="parser.py",
                               source_hash=contentHash("current"), condition="空输入")
    assert identity
    file.write_text("changed", encoding="utf-8")
    assert "未发布" in _command(project, f'publish {identity} "{reader.cwd}"')
    assert reader.get(identity) is None
    assert "来源已变" in _command(project, f"show {identity}")
    assert project.capture("迟到的旧验证", source_id="event/raw/13", kind="verified", source="parser.py",
                           source_hash=contentHash("current")) is None
    file.write_text("current", encoding="utf-8")
    receipt = _command(project, f'publish {identity} "{reader.cwd}"')
    assert identity in receipt and str(reader.cwd) in receipt
    view = _command(reader, f"show {identity}")
    assert "来源一致" in view and "空输入" in view and "parser.py" in view
    receipt = _command(project, f"forget {identity}")
    assert identity in receipt and "v1" in receipt


def test_workspace_replacement_hides_old_shares_but_reader_can_revoke_link(tmp_path):
    owner = _project(tmp_path, "one/app")
    reader = _project(tmp_path, "two/app")
    for site in (owner, reader):
        _command(site, "enable")
    identity = owner.capture("此标记属于原始目录", source_id="raw/first", kind="rule")
    _command(reader, f'link "{owner.cwd}"')
    prior = owner.cwd.with_name("archived")
    owner.cwd.rename(prior)
    owner.cwd.mkdir()
    assert reader.get(identity) is None
    assert "已解除" in _command(reader, f'unlink "{owner.cwd}"')
    assert str(owner.cwd) not in _command(reader, "status")


def test_concurrent_retries_and_revocation_share_one_committed_source(tmp_path):
    project = _project(tmp_path, "app")
    _command(project, "enable")
    meet = Barrier(4)

    def submit(_):
        session = ProjectMemory(project.user_root, project.cwd)
        meet.wait(timeout=5)
        return session.capture_result("并行补录一条来源", kind="rule", source_id="session/event")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit, range(4)))
    ids = {identity for identity, _ in results}
    assert len(ids) == 1 and sum(created for _, created in results) == 1
    identity = ids.pop()
    assert identity and project.forget(identity)
    assert project.capture_result("撤回前的迟到候选", kind="rule", source_id="session/event") == (None, False)


def test_automatic_enhancement_commits_once_for_the_same_live_evidence(tmp_path):
    project = _project(tmp_path, "project")
    _command(project, "enable")
    path = project.cwd / "handler.py"
    path.write_text("v1", encoding="utf-8")
    fingerprint = contentHash("v1")
    args = dict(source_id="session/checked", source="handler.py", source_hash=fingerprint)
    identity = project.capture("检查通过", kind="verified", condition="以前失败的同一输入", **args)
    path.write_text("v2", encoding="utf-8")
    assert not project.enhance(identity, "迟到的分析", expected_version=1, **args)
    path.write_text("v1", encoding="utf-8")
    assert project.enhance(identity, "保留最后一个分块再执行检查", expected_version=1, **args)
    entry = project.get(identity)
    assert (entry.version, entry.kind, entry.source_hash, entry.condition, entry.source_id) == (
        2, "verified", fingerprint, "以前失败的同一输入", "session/checked")
    assert not project.enhance(identity, "重复分析", expected_version=2, **args)

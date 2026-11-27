"""通过既有 MemoryWrite 工具核对本回合用户授权及工程版本边界。"""

import asyncio

from ruocun.config.compat import DEFAULT_COMPATIBILITY
from ruocun.memory.project_store import ProjectMemory
from ruocun.memory.store import MemoryStore
from ruocun.tools.memory_write import makeMemoryWriteTool
from ruocun.tools.types import ToolContext


def test_model_cannot_change_project_evidence_without_current_unambiguous_user_request(tmp_path):
    cwd = tmp_path / "service"
    cwd.mkdir()
    home = tmp_path / "account"
    project = ProjectMemory(home, cwd)
    project.enable()
    a = project.capture("提交以前检查余额", source_id="user/1", kind="rule")
    b = project.capture("提交以后触发通知", source_id="user/2", kind="rule")
    markdown = MemoryStore(cwd, home, DEFAULT_COMPATIBILITY)
    request = ("q1", "这个余额问题是什么原因？")
    allowed = True
    tool = makeMemoryWriteTool(markdown, project=project, allow_project_write=lambda: allowed,
                               project_user_request=lambda: request)
    args = {"project_id": a, "expected_version": 1, "delete": True}
    ctx = ToolContext(str(cwd))

    def invoke():
        return asyncio.run(tool.handler(args, ctx))

    assert "授权" in invoke()
    request = ("q2", f"请忘记工程记忆 {a}")
    allowed = False
    assert "未启用" in invoke()
    allowed = True
    ctx.subagentDepth = 1
    assert "主会话" in invoke()
    ctx.subagentDepth = 0
    request = ("q3", "请忘记之前那条工程记忆")
    assert "明确" in invoke()
    request = ("q4", f"请忘记工程记忆 {b}")
    assert "明确" in invoke()
    request = ("q5", "请忘记关于检查余额的那条工程记忆")
    receipt = invoke()
    assert "已撤回" in receipt and project.get(a) is None
    assert invoke() == receipt
    assert project.get(b) is not None
    assert set(args).issubset(tool.schema["properties"])


def test_correction_requires_current_version_and_exact_user_words(tmp_path):
    cwd = tmp_path / "billing"
    cwd.mkdir()
    project = ProjectMemory(tmp_path / "account", cwd)
    project.enable()
    identity = project.capture("结算保留两位", source_id="user/historical", kind="rule", condition="新接口")
    store = MemoryStore(cwd, project.user_root, DEFAULT_COMPATIBILITY)
    request = ("input1", f"请把工程规则 {identity} 保留，不要更正。")
    tool = makeMemoryWriteTool(store, project, True, lambda: request)
    args = {"project_id": identity, "expected_version": 1, "text": "结算保留三位"}
    ctx = ToolContext(str(cwd))

    def invoke():
        return asyncio.run(tool.handler(args, ctx))

    assert "授权" in invoke()
    request = ("input2", f"请更正规则 {identity} 为“结算保留四位”。")
    assert "新约定原文" in invoke()
    args["text"] = "结算保留四位"
    args["expected_version"] = 2
    assert "版本已变" in invoke()
    args["expected_version"] = 1
    first = invoke()
    assert "已更正" in first and invoke() == first
    record = project.get(identity)
    assert (record.text, record.version, record.condition) == ("结算保留四位", 2, "新接口")


def test_enabled_project_disallows_child_and_unauthorized_batch_markdown_bypass(tmp_path):
    cwd = tmp_path / "parent"
    child = tmp_path / "child"
    cwd.mkdir()
    child.mkdir()
    project = ProjectMemory(tmp_path / "account", cwd)
    project.enable()
    store = MemoryStore(cwd, project.user_root, DEFAULT_COMPATIBILITY)
    authorized = False
    tool = makeMemoryWriteTool(store, project, lambda: authorized)
    args = {"scope": "project", "title": "测试平台", "text": "使用本地测试器。"}
    ctx = ToolContext(str(cwd), cwd=str(child), subagentDepth=1)
    assert "主会话" in asyncio.run(tool.handler(args, ctx))
    ctx = ToolContext(str(cwd), headless=True)
    assert "未启用" in asyncio.run(tool.handler(args, ctx))
    assert not asyncio.run(store.load())
    authorized = True
    assert "已记住" in asyncio.run(tool.handler(args, ctx))
    ctx.subagentDepth = 1
    assert "主会话" in asyncio.run(tool.handler({"delete": True, "title": "测试平台"}, ctx))
    assert len(asyncio.run(store.load())) == 1

"""子执行共享权限与主会话核实的真实 CLI 边界。"""
from __future__ import annotations

import json

from test_project_memory_cli import cli, model, tool_call


def test_fork_rechecks_visible_memory_on_each_actual_model_request(tmp_path):
    from ruocun.memory.project_store import ProjectMemory

    cli(tmp_path, "/memory project enable\n/exit\n")
    project = ProjectMemory(tmp_path / "account/.ruocun", tmp_path / "work")
    marker = "RECHECK-FORK-719"
    identity = project.capture("项目凭单核对列 " + marker, kind="rule", source_id="synthetic/project/rule")
    assert identity
    child_messages = []
    main_asked = False

    def respond(body, number):
        nonlocal main_asked
        messages = body["messages"]
        if any("fork-boilerplate" in str(m.get("content", "")) for m in messages if m["role"] == "user"):
            child_messages.append(json.dumps(messages, ensure_ascii=False))
            if len(child_messages) == 1:
                assert project.forget(identity)
                return tool_call("ListDir", {"path": "."}, number)
            return {"content": "子任务完成。"}
        if not main_asked:
            main_asked = True
            return tool_call("Agent", {"task": "检查项目凭单约定", "fork": True}, number)
        return {"content": "完成。"}

    with model(respond) as (url, _):
        cli(tmp_path, "核对项目凭单的核对列。\n/exit\n", url=url)
    assert len(child_messages) == 2
    assert marker in child_messages[0] and marker not in child_messages[1]
    assert all("[[ruocun-memory " not in value for value in child_messages)


def test_child_claim_is_published_as_local_evidence_only_after_main_reads_the_source(tmp_path):
    from ruocun.memory.project_store import ProjectMemory

    cli(tmp_path, "/memory project enable\n/exit\n")
    project = ProjectMemory(tmp_path / "account/.ruocun", tmp_path / "work")
    project_file = project.cwd / "policy.py"
    project_file.write_text("RETRY_LIMIT = 7\nUNRELATED_SETTING = 123", encoding="utf-8")
    private = "CHILD-PRIVATE-NARRATIVE-700"
    stage = 0
    def respond(body, number):
        nonlocal stage
        if body.get("model") == "synthetic-child":
            return {"content": "请主会话核查 policy.py 中的 RETRY_LIMIT = 7。 " + private}
        stage += 1
        if stage == 1:
            return tool_call("Agent", {"task": "报告当前重试上限", "model": "synthetic-child"}, number)
        if stage == 2:
            assert not project.visible()
            return tool_call("Read", {"path": "policy.py"}, number)
        return {"content": "已核对。"}

    with model(respond) as (url, requests):
        cli(tmp_path, "核对当前重试上限。\n/exit\n", url=url)
        assert any(r.get("model") == "synthetic-child" for r in requests)
    records = project.visible()
    assert len(records) == 1
    assert records[0].kind == "observation" and records[0].source == "policy.py"
    assert "RETRY_LIMIT = 7" in records[0].text and records[0].source_hash
    assert "UNRELATED_SETTING" not in records[0].text and private not in records[0].text


def test_child_in_an_independent_worktree_cannot_read_the_parent_record(tmp_path):
    import subprocess

    from ruocun.memory.project_store import ProjectMemory

    workspace = tmp_path / "work"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-q", "--allow-empty", "-m", "synthetic base"], cwd=workspace, check=True)
    cli(tmp_path, "/memory project enable\n/exit\n")
    parent = ProjectMemory(tmp_path / "account/.ruocun", workspace)
    marker = "PRIVATE-PARENT-PROCEDURE-318"
    identity = parent.capture("项目核对列：" + marker, source_id="one/main/private", kind="rule")
    assert identity
    child_requests = []
    parent_sent = False
    def respond(body, number):
        nonlocal parent_sent
        if body.get("model") == "isolated-child":
            child_requests.append(body)
            if len(child_requests) == 1:
                return tool_call("MemoryRead", {"query": identity}, number)
            return {"content": "独立工作树核查完毕。"}
        if not parent_sent:
            parent_sent = True
            return tool_call("Agent", {"model": "isolated-child", "task": "查询指定身份。",
                                       "isolation": "worktree"}, number)
        return {"content": "结束。"}
    with model(respond) as (url, _):
        cli(tmp_path, "核查项目核对列。\n/exit\n", url=url)
    assert len(child_requests) == 2
    assert marker not in json.dumps(child_requests, ensure_ascii=False)
    result = [m for m in child_requests[1]["messages"] if m["role"] == "tool"][-1]
    assert "未找到" in result["content"] or "暂无" in result["content"]


def test_branch_switch_and_return_verify_source_and_delivered_file(tmp_path):
    import re
    import subprocess

    from ruocun.memory.hash import contentHash
    from ruocun.memory.project_store import ProjectMemory

    workspace = tmp_path / "work"
    workspace.mkdir()
    def git(*args):
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", *args],
                       cwd=workspace, check=True, capture_output=True)
    git("init", "-q", "-b", "legacy")
    policy = workspace / "policy.py"
    legacy = 'WIRE_FORMAT = "decimal-v1"'
    modern = 'WIRE_FORMAT = "minor-units-v2"'
    policy.write_text(legacy, encoding="utf-8")
    git("add", "policy.py")
    git("commit", "-qm", "legacy format")
    git("switch", "-qc", "modern")
    policy.write_text(modern, encoding="utf-8")
    git("commit", "-qam", "modern format")
    git("switch", "-q", "legacy")

    cli(tmp_path, "/memory project enable\n/exit\n")
    project = ProjectMemory(tmp_path / "account/.ruocun", workspace)
    identity = project.capture("上次核实接口采用 decimal-v1 格式。", source_id="one/main/proof",
                               kind="verified", source="policy.py", source_hash=contentHash(legacy))
    assert identity
    for step, (branch, expected, status) in enumerate([
        ("legacy", "decimal-v1", "来源一致"), ("modern", "minor-units-v2", "已变"),
        ("legacy", "decimal-v1", "来源一致"),
    ]):
        git("switch", "-q", branch)
        output_name = f"delivery-{step}.txt"
        def respond(body, number):
            tools = [m for m in body["messages"] if m.get("role") == "tool"]
            if not tools:
                return tool_call("MemoryRead", {"query": identity}, number)
            if len(tools) == 1:
                return tool_call("Read", {"path": "policy.py"}, number)
            if len(tools) == 2:
                match = re.search(r'WIRE_FORMAT\s*=\s*"([\w-]+)"', tools[-1]["content"])
                assert match, tools[-1]["content"]
                return tool_call("Write", {"path": output_name, "content": match[1]}, number)
            return {"content": "已生成交付文件。"}
        with model(respond) as (url, requests):
            cli(tmp_path, "按当前接口约束生成交付文件。\n/exit\n", url=url)
        memory_receipt = next(m["content"] for m in requests[1]["messages"] if m["role"] == "tool")
        assert status in memory_receipt, memory_receipt
        assert (workspace / output_name).read_text(encoding="utf-8") == expected

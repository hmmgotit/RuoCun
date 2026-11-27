"""同名真实工作区在新会话、既有会话恢复和下一条线上请求中的产品边界。"""
import hashlib
import os
import re

from test_project_memory_cli import cli, model, tool_call


def test_link_publish_unlink_and_source_forget_follow_the_next_request_in_resumed_reader(tmp_path):
    a, b, c = (tmp_path / label / "same" for label in ("owner", "reader", "third"))
    for root in (a, b, c):
        root.mkdir(parents=True)
    marker, private = "AUDIT-42459", "RATES-11852"
    first = "今后这个项目的图表必须加入 " + marker + " 审核列。"
    second = "今后这个项目的税率必须加入 " + private + " 复核列。"
    with model(lambda body, n: {"content": "收到"}) as (url, wire):
        def run(root, script, *options):
            return cli(tmp_path, script + "\n/exit\n", cwd=root, url=url, options=options)

        def answer(root, *options):
            before = len(wire)
            run(root, "请检查图表审核列和税率复核列。", *options)
            return str(wire[before]["messages"])

        run(a, "/memory project enable\n" + first)
        run(b, "/memory project enable\n" + second)
        run(c, "/memory project enable")
        aid = re.search(r"pm-[a-f0-9]{32}", run(a, "/memory project list"))[0]
        assert marker not in answer(b)
        assert private not in answer(a)
        assert "已关联" in run(b, f'/memory project link "{a}"')
        assert "已关联" in run(c, f'/memory project link "{b}"')
        assert marker in answer(b)
        assert marker not in answer(c)
        assert private not in answer(a)
        assert "已解除" in run(b, f'/memory project unlink "{a}"')
        assert marker not in answer(b)
        assert "已发布" in run(a, f'/memory project publish {aid} "{b}"')
        scope = hashlib.sha256(os.path.normcase(str(b.resolve())).encode()).hexdigest()[:20]
        sessions = tmp_path / "account/.ruocun/sessions" / scope
        before = set(sessions.iterdir())
        assert marker in answer(b)
        latest, = [p for p in sessions.iterdir() if p.is_dir() and p not in before]
        assert "已撤回" in run(a, f"/memory project forget {aid}")
        assert marker not in answer(b, "--continue=" + latest.name)
        assert marker not in answer(c)
        assert private in answer(b)


def test_natural_correction_and_forget_revoke_manual_tool_output_on_same_wire(tmp_path):
    old, new = "REGION-OLD-351", "ZONE-NEW-715"
    with model(lambda body, n: {"content": "收到"}) as (url, _):
        cli(tmp_path, "/memory project enable\n今后项目的地址表必须增加 " + old + " 字段。\n/exit\n", url=url)
        entry = re.search(r"pm-[a-f0-9]{32}", cli(tmp_path, "/memory project list\n/exit\n", url=url))[0]

    def correction(body, n):
        if n == 1:
            return tool_call("MemoryRead", {"query": entry}, n)
        if n == 2:
            return tool_call("MemoryWrite", {"project_id": entry, "expected_version": 1,
                                             "text": "今后项目的地址表使用 " + new + " 字段。"}, n)
        return {"content": "已处理。"}

    with model(correction) as (url, wire):
        cli(tmp_path, f"请更正工程记忆 {entry}，改为“今后项目的地址表使用 {new} 字段。”\n/exit\n", url=url)
        assert old in str(wire[1]["messages"])
        assert "已更正" in str(wire[2]["messages"]) and old not in str(wire[2]["messages"])

    def forget(body, n):
        if n == 1:
            return tool_call("MemoryRead", {"query": entry}, n)
        if n == 2:
            return tool_call("MemoryWrite", {"project_id": entry, "expected_version": 2, "delete": True}, n)
        return {"content": "已处理。"}

    with model(forget) as (url, wire):
        cli(tmp_path, f"请忘记工程记忆 {entry}。\n/exit\n", url=url)
        assert new in str(wire[1]["messages"])
        assert "已撤回" in str(wire[2]["messages"])
        assert old not in str(wire[2]["messages"]) and new not in str(wire[2]["messages"])
        assert [row["tool_call_id"] for row in wire[2]["messages"] if row["role"] == "tool"] == [
            "synthetic-1", "synthetic-2"]


def test_compact_then_forget_omits_derived_summary_in_next_real_request(tmp_path):
    from ruocun.agent.context_runtime import COMPACT_INSTRUCTION

    with model(lambda body, n: {"content": "收到"}) as (url, _):
        cli(tmp_path, "/memory project enable\n今后项目的审计报表带上 AUDIT-ARCHIVE-365 标识。\n/exit\n", url=url)
        entry = re.search(r"pm-[a-f0-9]{32}", cli(tmp_path, "/memory project list\n/exit\n", url=url))[0]

    def respond(body, n):
        if any(m.get("content") == COMPACT_INSTRUCTION for m in body["messages"]):
            return {"content": "<摘要>DERIVED-ARCHIVE-365</摘要>"}
        return {"content": "本次运行的普通结果。" * 80}

    script = "\n".join(["检查审计报表。", "核对别的功能。", "整理接下来的问题。", "/compact", "汇总一下。",
                        f"/memory project forget {entry}", "再继续。", "/exit", ""])
    with model(respond) as (url, wire):
        receipt = cli(tmp_path, script, url=url, retain_tokens=16)
    assert "已撤回" in receipt
    assert "DERIVED-ARCHIVE-365" in str(wire[-2]["messages"])
    assert "DERIVED-ARCHIVE-365" not in str(wire[-1]["messages"])
    assert "AUDIT-ARCHIVE-365" not in str(wire[-1]["messages"])

"""普通 CLI 中显式提炼、旧会话补录和冷重启的工程记忆生命周期。"""
import hashlib
import json
import os
import re

from test_project_memory_cli import cli, model


def test_controlled_refines_only_one_real_source_in_the_same_record(tmp_path):
    analyses = []

    def respond(body, n):
        if "复核当前项目的工程记忆摘录" not in body["messages"][0]["content"]:
            return {"content": "收到。"}
        entries = json.loads(body["messages"][-1]["content"])["evidence"]
        analyses.append(entries)
        item, = entries
        result = {"refinements": [{"name": item["name"], "source": item["source"], "stamp": item["stamp"],
                                   "quote": item["text"], "summary": "在付款汇总时检查金额字段。"}]}
        return {"content": json.dumps(result, ensure_ascii=False)}

    with model(respond) as (url, _):
        cli(tmp_path, "/memory project enable\n你好。\n今后项目的付款汇总必须检查 PAY-274 金额字段。\n/exit\n",
            options=("--memory-learning", "controlled"), url=url)
        report = cli(tmp_path, "/memory project list\n/exit\n", url=url)
    assert len(analyses) == 1
    assert re.findall(r"pm-[a-f0-9]{32}", report) == [analyses[0][0]["name"]]
    assert "v2" in report and "在付款汇总时检查金额字段" in report


def test_explicit_backfill_cannot_revive_source_after_forget_rebuild_and_restart(tmp_path):
    rule = "今后项目的盘点必须记录 INVENTORY-NOREVIVE-81。"
    with model(lambda body, n: {"content": "收到。"}) as (url, wire):
        cli(tmp_path, rule + "\n/exit\n", url=url)
        work = tmp_path / "work"
        scope = hashlib.sha256(os.path.normcase(str(work.resolve())).encode()).hexdigest()[:20]
        sessions = tmp_path / "account/.ruocun/sessions" / scope
        original, = [p.name for p in sessions.iterdir() if p.is_dir()]
        fill = "/memory project backfill session " + original
        out = cli(tmp_path, "/memory project enable\n" + fill + "\n" + fill + "\n/memory project list\n/exit\n", url=url)
        record = re.search(r"pm-[a-f0-9]{32}", out)[0]
        assert "已入库 1" in out and "已存在 1" in out
        after = cli(tmp_path, f"/memory project forget {record}\n/memory project vectors rebuild\n{fill}\n"
                              "/memory project list\n/exit\n", url=url)
        assert "已撤回" in after and "已入库 0" in after and "暂无工程记忆" in after
        before = len(wire)
        cli(tmp_path, "核对库存盘点。\n/exit\n", url=url)
        assert "INVENTORY-NOREVIVE-81" not in str(wire[before]["messages"])

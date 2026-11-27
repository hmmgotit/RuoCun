import re

from test_project_memory_cli import cli, model


def test_http_retry_rechecks_revocation_by_another_cli_before_resending(tmp_path):
    with model(lambda body, n: {"content": "收到。"}) as (url, _):
        cli(tmp_path, "/memory project enable\n今后项目的支付报表必须记录账目 PAY-RETRY-867。\n/exit\n", url=url)
        status = cli(tmp_path, "/memory project list\n/exit\n")
        identity = re.search(r"pm-[a-f0-9]{32}", status)[0]

    def respond(body, number):
        if number == 1:
            assert "已撤回" in cli(tmp_path, f"/memory project forget {identity}\n/exit\n")
            return {"_http_status": 503}
        return {"content": "已处理。"}

    with model(respond) as (url, wire):
        cli(tmp_path, "核对支付报表。\n/exit\n", url=url)
    assert len(wire) >= 2
    assert "PAY-RETRY-867" in str(wire[0]["messages"])
    assert "PAY-RETRY-867" not in str(wire[1]["messages"])

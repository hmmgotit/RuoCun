"""从正式 CLI 输入向量配置与人工选择的历史补录指令。"""
from test_project_memory_cli import cli, tool_call
from test_project_memory_cli import model as loopback


def test_cli_vector_status_and_scoped_backfill(tmp_path):
    output = cli(tmp_path, "/memory project enable\n/memory project status\n"
                 "/memory project vectors off\n/memory project backfill session synthetic-missing\n"
                 "/memory project backfill range 2000 1000\n/exit\n")
    assert "487 MB" in output
    assert "向量已关闭" in output
    assert "扫描会话 0" in output and "已存在 0" in output and "跳过 1" in output
    assert "时间范围无效" in output


def test_preinstalled_local_model_rebuild_and_followup_search_reach_the_wire(tmp_path):
    vectors = tmp_path / "semantic weights.vec"
    vectors.write_text("3 2\n断点复原 1 0\ncheckpoint 1 0\npalette 0 1\n", encoding="utf-8")
    with loopback(lambda _body, _n: {"content": "好的。"}) as (url, _requests):
        learned = cli(tmp_path, "/memory project enable\n以后项目的导出代码必须先记录 checkpoint。\n/exit\n", url=url)
        assert "487 MB" in learned
    setup = cli(tmp_path, f'/memory project vectors local "{vectors}"\n/memory project vectors rebuild\n/exit\n')
    assert "已就绪" in setup

    def reply(_body, number):
        if number == 1:
            return tool_call("MemoryRead", {"mode": "project", "query": "断点复原"}, 1)
        return {"content": "已核对。"}

    with loopback(reply) as (url, requests):
        cli(tmp_path, "读一段项目历史。\n/exit\n", url=url)
    assert len(requests) == 2
    result = next(msg["content"] for msg in requests[1]["messages"] if msg["role"] == "tool")
    assert "checkpoint" in result and "pm-" in result and "v1" in result

def test_time_window_includes_tool_evidence_whose_user_turn_started_before_it(tmp_path):
    from test_deepseek_compaction_archive import _run

    _run(tmp_path, r'''
import hashlib, os, json
from ruocun.agent.project_learning import backfill_project
from ruocun.memory.project_store import ProjectMemory
from ruocun.platform.windows.durable_file import ensure_private_dir
from ruocun.session.conversation import Conversation
from ruocun.session.session import Session

async def run():
    project = ProjectMemory(root/'account', root)
    project.enable()
    base = project.user_root/'sessions'/hashlib.sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()[:20]
    ensure_private_dir(base.parent)
    ensure_private_dir(base)
    session = Session('system', 'synthetic', env={})
    conversation = await Conversation.open(base/'original','original',root,runtime,session,'',resume=False)
    ticket = await conversation.queue_user(session, '今后项目的导出必须有审计字段。请验证。')
    await conversation.consume_user(session, ticket.input_id)
    await asyncio.sleep(.025)
    pair = [
        {'role':'assistant','tool_calls':[{'id':'check','type':'function','function':{
            'name':'Bash','arguments':json.dumps({'command':'pytest tests/test_cursor.py'})}}]},
        {'role':'tool','tool_call_id':'check','content':'FAILED tests/test_cursor.py::test_gap\n[exit 1]'}]
    await conversation.replace_context(session, [*session.messages,*pair])
    records = (await conversation.worker.archive_export())['records']
    stamp = next(r['created_at'] for r in records if r['message'].get('role') == 'tool')
    await conversation.close()
    result = backfill_project(project, runtime, since_ms=stamp, until_ms=stamp+1)
    assert result['added'] == 1, result
    assert [row.kind for row in project.visible()] == ['failure']

asyncio.run(run())
''')

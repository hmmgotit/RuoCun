"""Upgrade retirement through a real synthetic Windows runner and durable session."""
import sys

import pytest
from test_n15_managed_shell import _CODE, _run_case

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 可恢复受管 Shell")


_RETIRE = _CODE[:_CODE.index("async def scenario():")] + r"""
import hashlib
from unittest.mock import patch

async def scenario():
    def controller(store):
        return ManagedShellManager(root/'session','one',store,
            runner_command=role+['--ruocun-shell-runner'],helper_command=role+['--ruocun-ipc-helper'])

    store,session=await open_store()
    manager=controller(store)
    try:
        script=root/'worker.py'
        script.write_text("from pathlib import Path\nimport time\np=Path('ticks');i=0\nwhile True:\n p.write_text(str(i));print(i,flush=True);i+=1;time.sleep(.02)\n",encoding='utf-8')
        command=f'"{sys.executable}" -I "{script}"'
        await store.intent('legacy','Bash',json.dumps({'command':command}),True)
        # Produce the old, directly launched Windows plan with the normal runner protocol.
        with patch('ruocun.tools.managed_shell.sandboxExecutionPlan', lambda plan, _: plan):
            ack=await manager.execute(buildShellPlan(command,str(root)),threading.Event(),background=True)
        session.messages.extend([
            {'role':'assistant','content':'','tool_calls':[{'id':'legacy','type':'function',
              'function':{'name':'Bash','arguments':json.dumps({'command':command})}}]},
            {'role':'tool','tool_call_id':'legacy','content':ack},
        ])
        await store.result('legacy',ack)
        record=(await store.recover())[0]
        directory=Path(record.snapshot['run_dir'])
        with_references=(root/'keep-references').exists()
        if with_references:
            proof=directory/'grant.json'
            digest=hashlib.sha256(proof.read_bytes()).hexdigest()
            await store.register_background_attachment(str(proof),digest)
            args=json.dumps({'id':record.run_ref.run_id})
            await store.intent('previous-output','BashOutput',args,False)
            previous=f"Saved execution output: {(await manager.poll(record.run_ref.run_id))['runnerDir']}"
            await store.result('previous-output',previous)
            session.messages.extend([
                {'role':'assistant','content':'','tool_calls':[{'id':'previous-output','type':'function',
                  'function':{'name':'BashOutput','arguments':args}}]},
                {'role':'tool','tool_call_id':'previous-output','content':previous},
            ])
        await store.save(session)
        assert 'legacy' == record.snapshot['tool_ref']['tool_call_id']
        deadline=time.monotonic()+15
        while not (root/'ticks').exists():
            assert time.monotonic()<deadline
            await asyncio.sleep(.025)
        await manager.close(keep_background=True)
        await store.close()
        store,session=await open_store(resume=True)
        manager=controller(store)
        if (root/'unconfirmed-identity').exists():
            manifest=directory/'manifest.json'
            original_manifest=manifest.read_bytes()
            manifest.write_bytes(b'invalid synthetic manifest')
            try:
                unresolved=await manager.restore(retire_unsandboxed=True)
                assert len(unresolved)==1 and unresolved[0].status=='unknown'
                assert directory.exists()
                before=(root/'ticks').read_bytes()
                await asyncio.sleep(.15)
                assert (root/'ticks').read_bytes()!=before
            finally:
                manifest.write_bytes(original_manifest)
        assert await manager.restore(retire_unsandboxed=True) == ()
        assert (await manager.poll(record.run_ref.run_id))['status']=='not-found'
        assert await store.recover()==()
        assert await store.intent('legacy','Bash',json.dumps({'command':command}),True)==ack
        if with_references:
            assert hashlib.sha256(proof.read_bytes()).hexdigest()==digest
            assert (directory/'stdout.txt').read_text().strip()
            assert not (directory/'manifest.json').exists()
            assert session.messages[-1]['content']==previous
        else:
            assert not directory.exists()
        legacy=next(message for message in session.messages if message.get('tool_call_id')=='legacy')
        assert legacy['content']==ack
        assert not (await store.background_view()).missing_attachments
        ticks=(root/'ticks').read_bytes()
        await asyncio.sleep(.15)
        assert (root/'ticks').read_bytes()==ticks
        assert await manager.restore(retire_unsandboxed=True)==()
        await manager.close()
        await store.close()
        store,session=await open_store(resume=True)
        manager=controller(store)
        assert await manager.restore(retire_unsandboxed=True)==()
        assert await store.intent('legacy','Bash',json.dumps({'command':command}),True)==ack
    finally:
        # Also adopt the fixture after a failing migration test so no test worker is left behind.
        await manager.restore()
        await cleanup(manager,store)

asyncio.run(scenario())
"""


@pytest.mark.parametrize("fixture", ["plain", "keep-references", "unconfirmed-identity"])
def test_stops_and_removes_only_live_legacy_run_on_restore(tmp_path, fixture):
    if fixture != "plain":
        (tmp_path / fixture).touch()
    _run_case(_RETIRE, tmp_path)

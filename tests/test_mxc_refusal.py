"""A proved Microsoft MXC launch refusal leaves the coding session usable."""

import sys

import pytest
from test_n15_managed_shell import _CODE, _run_case

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows native MXC"),
    pytest.mark.native_mxc,
]

_REFUSAL = _CODE[:_CODE.index("async def scenario():")] + r"""
from dataclasses import replace
from ruocun.tools.exec_shell import execShellTool
from ruocun.tools.types import ToolContext, ToolRejected

async def scenario():
    def controller(store):
        return ManagedShellManager(
            root/'session','one',store,
            runner_command=role+['--ruocun-shell-runner'],
            helper_command=role+['--ruocun-ipc-helper'],
        )

    store,session=await open_store()
    manager=controller(store)
    try:
        command="echo must-not-run>unexpected.txt"
        args={"command":command}
        raw=buildShellPlan(command,str(root),analyze=False)
        absent=root/'absent'/'cmd.exe'
        plan=replace(raw,argv=(str(absent),*raw.argv[1:]),
                     commandLine=f'"{absent}" /d /s /c "{command}"')
        ctx=ToolContext(str(root),managedShell=manager,getShellPlan=lambda *_:plan)
        await store.intent('refused','Bash',json.dumps(args),True)
        try:
            await execShellTool.handler(args,ctx)
        except ToolRejected as error:
            failure="Error: "+str(error)
        else:
            raise AssertionError("absent shell must be a known launch refusal")
        assert not (root/'unexpected.txt').exists()
        await store.result('refused',failure)
        session.messages.extend([
            {'role':'assistant','content':'','tool_calls':[{'id':'refused','type':'function',
             'function':{'name':'Bash','arguments':json.dumps(args)}}]},
            {'role':'tool','tool_call_id':'refused','content':failure},
        ])
        await store.save(session)
        await manager.close()
        await store.close()

        store,session=await open_store(resume=True)
        manager=controller(store)
        await manager.restore()
        assert not store.blocked, "known launch refusal incorrectly blocked the reopened session"
        assert session.messages[-1]['content']==failure
        following="echo continued>continued.txt"
        await store.intent('next','Bash',json.dumps({'command':following}),True)
        ctx=ToolContext(str(root),managedShell=manager)
        result=await execShellTool.handler({'command':following},ctx)
        assert 'exit 0' in result and (root/'continued.txt').read_text().strip()=="continued"
        await store.result('next',result)
    finally:
        await cleanup(manager,store)

asyncio.run(scenario())
"""


def test_proved_refusal_keeps_managed_bash_session_available(tmp_path):
    _run_case(_REFUSAL, tmp_path)

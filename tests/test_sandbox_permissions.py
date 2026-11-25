"""Write-sandbox grants must come from this call's human approval, never broad allow state."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from ruocun.permissions.runtime import PermissionRuntime
from ruocun.platform.sandbox_policy import SandboxPolicy
from ruocun.platform.windows.durable_file import ensure_private_dir
from ruocun.session.session import Session
from ruocun.tools.paths import approvedPath
from ruocun.tools.sandbox import prepareSandboxPlan
from ruocun.tools.schema import objectSchema
from ruocun.tools.tool_batch import BatchCall
from ruocun.tools.types import Tool, ToolContext, ToolRejected


async def _unused(args, ctx):
    return ""


BASH = Tool("Bash", "", objectSchema({}, []), "exec", _unused)
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows sandbox permission environment")


def _policy(tmp_path: Path) -> SandboxPolicy:
    project = tmp_path / "project"
    project.mkdir()
    for name in ("state", "temp", "cache"):
        ensure_private_dir(tmp_path / name)
    (tmp_path / "outside").mkdir()
    protected = project / "protected"
    protected.mkdir()
    return SandboxPolicy(str(tmp_path / "dependency/codex.exe"), str(tmp_path / "state"),
                         str(tmp_path / "temp"), str(tmp_path / "cache"), (str(project),),
                         (str(protected), str(tmp_path / "state")))


def _runtime(tmp_path: Path, policy: SandboxPolicy, prompt, *, interactive: bool = True) -> PermissionRuntime:
    return PermissionRuntime(tmp_path / "project", Session("", "synthetic", env={}), [], prompt,
                             interactive, True, (), {"external-write"},
                             executionEnv={"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\Windows\System32",
                                           "ComSpec": r"C:\Windows\System32\cmd.exe"}, sandboxPolicy=policy)


async def _approved_plan(runtime: PermissionRuntime, command: str = "echo synthetic"):
    assert await runtime.authorizeBatch([BatchCall("fixture", BASH, json.dumps({"command": command}))]) == {"fixture": True}
    return runtime.getShellPlan(command, str(runtime.cwd))


def test_file_tools_reuse_session_approval_outside_the_workspace(tmp_path):
    async def scenario():
        policy = _policy(tmp_path)
        requests = []

        async def prompt(batch):
            requests.extend(batch)
            return {item["id"]: "session" for item in batch}

        runtime = PermissionRuntime(tmp_path / "project", Session("", "synthetic", env={}), [], prompt,
                                    True, False, (), set(), executionEnv={}, sandboxPolicy=policy)
        ctx = ToolContext(str(tmp_path / "project"), approveExternalWrite=runtime.approveExternalWrite)
        for name in ("a.txt", "b.txt"):
            target = str(tmp_path / "outside" / name)
            with runtime.callScope():
                assert await approvedPath(ctx, target, write=True) == target
        assert [item["id"] for item in requests] == ["extw"]
        assert not runtime.approvalsFile.exists()
    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["always", "deny"])
def test_file_write_preserves_normal_persistent_approval_behavior(tmp_path, decision):
    async def scenario():
        policy = _policy(tmp_path)
        calls = []

        async def prompt(batch):
            calls.append(batch)
            return {item["id"]: decision for item in batch}

        runtime = PermissionRuntime(tmp_path / "project", Session("", "synthetic", env={}), [], prompt,
                                    True, False, (), set(), executionEnv={}, sandboxPolicy=policy)
        target = str(tmp_path / "outside/a.txt")
        assert await runtime.approveExternalWrite(target) is (decision == "always")
        assert await runtime.approveExternalWrite(target) is (decision == "always")
        assert len(calls) == (1 if decision == "always" else 2)
        assert runtime.approvalsFile.exists() is (decision == "always")
    asyncio.run(scenario())


def test_file_tools_keep_separate_read_and_write_permissions(tmp_path):
    async def scenario():
        _policy(tmp_path)
        outside_calls = []

        async def once_write(path):
            outside_calls.append(path)
            return False

        async def read(path):
            return True

        ctx = ToolContext(str(tmp_path / "project"), approveExternalWrite=once_write,
                          approveExternalRead=read)
        assert await approvedPath(ctx, "inside.txt", write=True) == str(tmp_path / "project/inside.txt")
        assert outside_calls == []
        with pytest.raises(ToolRejected):
            await approvedPath(ctx, str(tmp_path / "outside/a.txt"), write=True)
        assert len(outside_calls) == 1
        assert await approvedPath(ctx, str(tmp_path / "outside/a.txt")) == str(tmp_path / "outside/a.txt")
    asyncio.run(scenario())


def test_file_approval_cancellation_cannot_return_a_writable_path(tmp_path):
    async def scenario():
        _policy(tmp_path)
        ctx = ToolContext(str(tmp_path / "project"))

        async def canceled(path):
            ctx.signal.set()
            return True

        ctx.approveExternalWrite = canceled
        with pytest.raises(InterruptedError):
            await approvedPath(ctx, str(tmp_path / "outside/a.txt"), write=True)
    asyncio.run(scenario())


def test_direct_file_context_still_requires_approval_to_write_outside(tmp_path):
    async def scenario():
        project = tmp_path / "project"
        project.mkdir()
        ctx = ToolContext(str(project))
        assert await approvedPath(ctx, "inside.txt", write=True) == str(project / "inside.txt")
        with pytest.raises(ToolRejected):
            await approvedPath(ctx, str(tmp_path / "outside.txt"), write=True)
    asyncio.run(scenario())


def test_shell_extra_roots_are_local_to_returned_plan_and_not_inherited(tmp_path):
    async def scenario():
        policy = _policy(tmp_path)
        prompts = []

        async def prompt(batch):
            prompts.extend(batch)
            return {item["id"]: "once" for item in batch}

        runtime = _runtime(tmp_path, policy, prompt)
        plan = await _approved_plan(runtime)
        assert plan.sandbox is policy
        outside = str(tmp_path / "outside")
        granted = await runtime.authorizeSandboxShell(replace(plan), [outside])
        assert granted is not plan and granted.sandbox is not None
        assert granted.sandbox.allowsWrite(str(tmp_path / "outside/a.txt"))
        assert not policy.allowsWrite(str(tmp_path / "outside/a.txt"))
        assert runtime.getShellPlan(plan.command, plan.cwd) is plan
        await runtime.authorizeSandboxShell(plan, [outside])
        assert len(prompts) == 2
        assert all(item["noPersist"] and "echo synthetic" in item["summary"] for item in prompts)
        child = runtime.forAgent(tmp_path / "outside", Session("", "synthetic", env={}), "auto")
        assert child.sandboxPolicy is policy
        assert not child.sandboxPolicy.allowsWrite(str(tmp_path / "outside/a.txt"))
        assert not runtime.approvalsFile.exists()
        assert not runtime.localSettingsFile.exists()
    asyncio.run(scenario())


def test_shell_write_scope_follows_the_approved_command_start_directory(tmp_path):
    async def scenario():
        policy = _policy(tmp_path)
        cwd = tmp_path / "project" / "worktree"
        cwd.mkdir()

        async def prompt(batch):
            return {item["id"]: "once" for item in batch}

        runtime = _runtime(tmp_path, policy, prompt)
        command = "echo synthetic"
        assert await runtime.authorizeBatch(
            [BatchCall("fixture", BASH, json.dumps({"command": command}))], executionCwd=cwd,
        ) == {"fixture": True}
        ctx = ToolContext(str(tmp_path / "project"), authorizeSandboxShell=runtime.authorizeSandboxShell)
        approved = runtime.getShellPlan(command, str(cwd))
        plan = await prepareSandboxPlan(approved, ctx, [str(tmp_path / "outside")])
        assert plan.sandbox is not None
        assert plan.sandbox.allowsWrite(str(cwd / "result.txt"))
        assert plan.sandbox.allowsWrite(str(tmp_path / "outside" / "result.txt"))
        assert not plan.sandbox.allowsWrite(str(cwd.parent / "sibling.txt"))
    asyncio.run(scenario())


def test_shell_extra_roots_reject_invalid_or_unnecessary_requests_before_prompt(tmp_path):
    async def scenario():
        policy = _policy(tmp_path)

        async def prompt(batch):
            raise AssertionError("invalid scope must be refused before a human prompt")

        runtime = _runtime(tmp_path, policy, prompt)
        plan = await _approved_plan(runtime)
        outside = tmp_path / "outside"
        child = outside / "child"
        child.mkdir()
        file = outside / "file.txt"
        file.write_text("synthetic", encoding="utf-8")
        requests = [["relative"], [str(outside / "absent")], [str(file)], [str(outside), str(outside)],
                    [str(outside), str(child)], [str(tmp_path / "project")], [str(tmp_path / "state")],
                    [str(tmp_path)], [Path(outside).anchor]]
        for requested in requests:
            with pytest.raises(ToolRejected):
                await runtime.authorizeSandboxShell(plan, requested)
    asyncio.run(scenario())


def test_shell_expansion_is_denied_headless_and_for_non_once_answers(tmp_path):
    async def scenario():
        policy = _policy(tmp_path)
        answers = iter(["always", "session", "deny"])

        async def prompt(batch):
            return {batch[0]["id"]: next(answers)}

        runtime = _runtime(tmp_path, policy, prompt)
        plan = await _approved_plan(runtime)
        for _ in range(3):
            with pytest.raises(ToolRejected):
                await runtime.authorizeSandboxShell(plan, [str(tmp_path / "outside")])
        runtime._interactive = False
        with pytest.raises(ToolRejected):
            await runtime.authorizeSandboxShell(plan, [str(tmp_path / "outside")])
        assert runtime.getShellPlan(plan.command, plan.cwd).sandbox is policy
    asyncio.run(scenario())


def test_windows_sandbox_bypass_is_denied_without_requesting_approval(tmp_path):
    async def scenario():
        policy = _policy(tmp_path)

        async def prompt(batch):
            raise AssertionError("Windows sandbox bypass must not be presented as approvable")

        runtime = _runtime(tmp_path, policy, prompt)
        raw = json.dumps({"command": "echo synthetic", "dangerouslyDisableSandbox": True})
        assert await runtime.authorizeBatch([BatchCall("bypass", BASH, raw)]) == {"bypass": False}
        with pytest.raises(ToolRejected):
            runtime.getShellPlan("echo synthetic", str(runtime.cwd))
    asyncio.run(scenario())

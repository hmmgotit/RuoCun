"""Check the source adapters on POSIX without claiming a POSIX product install."""

import ast
import asyncio
import os
import sys
import threading
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src"
sys.path.insert(0, str(SOURCE))


def main() -> int:
    from ruocun import __version__
    from ruocun.cli import main as cli
    from ruocun.platform.process_tree import run_foreground_plan
    from ruocun.platform.sandbox import sandboxSpawn
    from ruocun.platform.shell_plan import buildShellPlan
    from ruocun.tools.sandbox import prepareShellExecutionPlan
    from ruocun.tools.types import ToolContext

    assert sys.platform in {"linux", "darwin"} and sys.version_info[:2] >= (3, 11)
    paths = sorted((SOURCE / "ruocun").rglob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_bytes(), filename=str(path), feature_version=(3, 11))
        compile(tree, str(path), "exec")
    package = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
    assert package["project"]["requires-python"] == "==3.11.*"
    assert '"py311-none-win_amd64"' in (REPO / "hatch_build.py").read_text("utf-8")
    assert cli(["--version"]) == cli(["--help"]) == 0
    with TemporaryDirectory(prefix="ruocun-posix-design-") as tmp:
        value = "合成 POSIX 🙂"
        plan = buildShellPlan("printf '%s\\n' \"$SYNTHETIC_VALUE\"", tmp,
                              environ={"HOME": tmp, "PATH": "/usr/bin:/bin", "SYNTHETIC_VALUE": value})
        execution = asyncio.run(prepareShellExecutionPlan(plan, ToolContext(tmp)))
        assert execution is plan
        result = run_foreground_plan(execution, threading.Event())
        assert result.code == 0 and result.stdout == value + "\n" and not result.stderr
        selected = sandboxSpawn("ls", tmp, platform=sys.platform, exists=lambda _: True,
                                environ={"RUOCUN_SANDBOX_NO_NET": "1"})
        assert selected and "file" in selected and selected["args"][-3:] == ["/bin/sh", "-c", "ls"]
        expected = "--unshare-net" if sys.platform == "linux" else "(deny network*)"
        assert expected in " ".join(selected["args"])
    if sys.platform == "darwin":
        with TemporaryDirectory(prefix=".seatbelt-check-", dir=REPO) as tmp:
            root = Path(tmp).resolve()
            workspace = root / "project"
            workspace.mkdir()
            command = "printf inside > accepted.txt; if (printf outside > ../blocked.txt) 2>/dev/null; then exit 1; fi"
            plan = buildShellPlan(command, str(workspace), environ={"HOME": tmp, "PATH": "/usr/bin:/bin"})
            context = ToolContext(str(workspace), sandboxEnabled=True)
            execution = asyncio.run(prepareShellExecutionPlan(plan, context))
            result = run_foreground_plan(execution, threading.Event())
            assert result.code == 0 and (workspace / "accepted.txt").read_text() == "inside"
            assert not (root / "blocked.txt").exists()
        print("Seatbelt permitted workspace writes and denied the outside write.")
    print(f"{sys.platform} / Python {sys.version.split()[0]}: {len(paths)} files passed Python 3.11 grammar and compile.")
    print(f"Source CLI {__version__}; native POSIX shell passed.")
    print("Windows x64 wheel tag checked; POSIX package install and product runtime are outside this design check.")
    assert not os.environ.get("PYTHONPATH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

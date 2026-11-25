"""从 Windows 桥接进程的真实标准流验证命令输出。"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ruocun.platform.windows import mxc_bridge

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows MXC 标准流桥接")


def _bridge_process(workload: Path, *arguments: Path) -> subprocess.Popen[bytes]:
    bridge = str(Path(mxc_bridge.__file__).resolve())
    command = subprocess.list2cmdline([sys.executable, "-I", str(workload), *map(str, arguments)])
    return subprocess.Popen(
        [sys.executable, "-I", bridge, command, sys.executable, str(workload.parent), "synthetic"],
        cwd=workload.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=os.environ.copy(),
    )


def test_bridge_delivers_all_output_after_the_receiver_pauses(tmp_path: Path) -> None:
    workload = tmp_path / "write_output.py"
    done = tmp_path / "producer-finished"
    workload.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.stdout.buffer.write(bytes(range(256)) * 128)\n"
        "sys.stdout.buffer.flush()\n"
        "print('synthetic stderr', file=sys.stderr, flush=True)\n"
        "Path(sys.argv[1]).touch()\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    with _bridge_process(workload, done) as process:
        try:
            deadline = time.monotonic() + 10
            while not done.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert done.is_file(), "子命令未完整写出合成输出"
            time.sleep(1.5)
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 7
            assert stdout == b"\x1eRUOCUN-MXC-START:synthetic\n" + bytes(range(256)) * 128
            assert stderr == b"synthetic stderr\r\n"
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_bridge_delivers_the_final_output_when_a_descendant_still_holds_stdout(tmp_path: Path) -> None:
    descendant = tmp_path / "hold_stdout.py"
    release = tmp_path / "release"
    descendant.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "deadline = time.monotonic() + 20\n"
        "while not Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    workload = tmp_path / "exit_main.py"
    workload.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-I', *sys.argv[1:]], stderr=subprocess.DEVNULL)\n"
        "sys.stdout.buffer.write(b'final payload')\n"
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    with _bridge_process(workload, descendant, release) as process:
        try:
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 9
            assert stdout == b"\x1eRUOCUN-MXC-START:synthetic\nfinal payload"
            assert stderr == b""
        finally:
            release.touch()
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

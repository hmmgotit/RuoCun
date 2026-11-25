"""在 MXC 内启动原命令，并在转发命令输出前确认进程已创建。"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import IO


def launch_marker(launch_id: str, *, rejected: bool = False) -> bytes:
    status = b"ERROR" if rejected else b"START"
    return b"\x1eRUOCUN-MXC-" + status + b":" + launch_id.encode("ascii") + b"\n"


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if not written:
            raise OSError("管道写入未完成。")
        remaining = remaining[written:]


def _forward_output(pipe: IO[bytes], output: int, process: subprocess.Popen[bytes]) -> None:
    import msvcrt

    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD)
    peek.restype = wintypes.BOOL
    descriptor = pipe.fileno()
    handle = msvcrt.get_osfhandle(descriptor)
    available = wintypes.DWORD()
    with pipe:
        try:
            while True:
                # 原命令退出后，转发当时已经写进管道的数据即可。
                # 子孙进程仍持有写端时，外层 MXC 会在桥接进程退出后回收它们。
                exited = process.poll() is not None
                if not peek(handle, None, 0, None, ctypes.byref(available), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                remaining = int(available.value)
                while remaining:
                    data = os.read(descriptor, min(remaining, 65536))
                    if not data:
                        return
                    _write_all(output, data)
                    remaining -= len(data)
                if exited:
                    return
                if not available.value:
                    time.sleep(0.02)
        except BrokenPipeError:
            pass


def main(command_line: str, executable: str, temporary: str, launch_id: str) -> int:
    import msvcrt

    # MXC 可能改写临时目录。实际命令仍用计划审批时冻结的可写目录。
    os.environ.update(dict.fromkeys(("TEMP", "TMP", "TMPDIR"), temporary))
    output = sys.stdout.fileno()
    msvcrt.setmode(output, os.O_BINARY)
    try:
        process = subprocess.Popen(command_line, executable=executable, shell=False, stdin=sys.stdin,
                                   stdout=subprocess.PIPE, stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW)
    except FileNotFoundError:
        # 创建接口明确返回路径不存在。其他错误仍交给宿主按未确认处理。
        _write_all(output, launch_marker(launch_id, rejected=True))
        return 1
    assert process.stdout is not None
    # 内部命令没有继承此 stdout 管道，在确认写出前不能向父进程抢先发送数据。
    _write_all(output, launch_marker(launch_id))
    _forward_output(process.stdout, output, process)
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))

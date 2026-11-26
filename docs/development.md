# 开发与构建

v2.2.0 的 wheel 面向 Windows x64 和 CPython 3.11。程序入口在 `src/ruocun/cli.py`，版本由 `src/ruocun/version.py` 提供。项目按 Hatchling 配置生成 wheel 和源码包。

## 安装开发依赖

在仓库根目录打开 PowerShell。`tools/run.py` 为子进程建立合成的用户目录、配置目录和临时目录，并保留每次命令的退出状态。给每次运行一个新的 `--label`。

```powershell
$bootPy = (py -3.11 -c 'import sys; print(sys.executable)').Trim()
& $bootPy -I -X utf8 tools/run.py --label create-venv -- $bootPy -I -X utf8 -m venv .venv
$devPy = (Resolve-Path .venv/Scripts/python.exe).Path
& $bootPy -I -X utf8 tools/run.py --label download-dev -- $devPy -I -X utf8 -m pip download --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r requirements-dev-win311.lock --dest .cache/wheels
& $bootPy -I -X utf8 tools/run.py --label install-dev --wheelhouse .cache/wheels -- $devPy -I -X utf8 -m pip install --require-hashes -r requirements-dev-win311.lock
```

锁文件固定 Windows x64 与 CPython 3.11 的依赖版本及 wheel 摘要。已有相同的本地依赖包时，直接从安装步骤开始即可。

## 构建并运行公开测试

```powershell
& $bootPy -I -X utf8 tools/run.py --label build-package --wheelhouse .cache/wheels -- $devPy -I -X utf8 -m build
$wheel = (Resolve-Path dist/ruocun-2.2.0-py311-none-win_amd64.whl).Path
& $bootPy -I -X utf8 tools/run.py --label install-product --wheelhouse .cache/wheels -- $devPy -I -X utf8 -m pip install --no-deps --force-reinstall $wheel
& $bootPy -I -X utf8 tools/run.py --label check-wheel -- $devPy -I -X utf8 -m pip check
$testTemp = Join-Path ([System.IO.Path]::GetTempPath()) ('ruocun-tests-' + [guid]::NewGuid())
& $bootPy -I -X utf8 tools/run.py --label test-package -- $devPy -I -X utf8 -m pytest --basetemp $testTemp
```

公开测试使用已安装的 wheel，覆盖入口、模型请求构造、工具循环、文件编辑、上下文压缩与归档、记忆读写和 SQLite 会话等。少量请求测试使用本机环回的合成服务。接入真实模型请参考[安装与恢复](install-and-recovery.md)。

构建完成后，`dist/` 内有 `ruocun-2.2.0-py311-none-win_amd64.whl` 和 `ruocun-2.2.0.tar.gz`。命令输出收在 `.cache/engineering/raw/<label>/stdout.bin` 和 `stderr.bin`，退出状态在同目录的 `command.json`。

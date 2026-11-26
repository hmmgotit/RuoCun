# 安装与恢复

在 Windows x64 安装 CPython 3.11，打开 PowerShell 或 Windows Terminal。下载 wheel，把下面的路径换成实际下载位置。[v2.2.0 下载](https://github.com/hmmgotit/RuoCun/releases/tag/v2.2.0)

```powershell
$venvPath = Join-Path $env:LOCALAPPDATA 'RuoCun/venv-2.2.0'
py -3.11 -m venv $venvPath
$ruocunPython = Join-Path $venvPath 'Scripts/python.exe'
$wheel = '.\ruocun-2.2.0-py311-none-win_amd64.whl'
& $ruocunPython -I -X utf8 -m pip install $wheel
& $ruocunPython -I -X utf8 -m pip check
& $ruocunPython -I -X utf8 -m ruocun --version
& $ruocunPython -I -X utf8 -m ruocun --help
```

首次运行时，在向导中选择服务商、配置模型账户并确认是否信任当前项目。用 `/doctor` 查看配置、平台能力和当前沙箱模式，用 `/lang zh` 切换中文。将 `--cwd` 指向目标项目目录。默认启动时，受管 Shell 走权限规则和审批，Windows 无需安装 MXC。

```powershell
& $ruocunPython -I -X utf8 -m ruocun --cwd 'C:\path\to\project'
```

要为本次启动的 Bash、Monitor 和子代理同类命令启用系统沙箱，在 Windows 上先配置 Microsoft MXC v1.1.0 的 `wxc-exec.exe`，再加 `--sandbox` 启动。启用后，后端不可用会拒绝该次命令。[MXC 下载与配置](windows-sandbox.md)

```powershell
& $ruocunPython -I -X utf8 -m ruocun --cwd 'C:\path\to\project' --sandbox
```

从 v2.1.0 升级后，如果希望新命令继续在沙箱内执行，请在启动命令里加 `--sandbox`。以前依赖 `RUOCUN_SANDBOX` 的启动脚本也改用这一参数。

离线安装需要若存 wheel 和 Python 依赖。如果目标机器要开启沙箱，一起带上独立的 MXC 执行器。先在 Windows x64 的 Python 3.11 环境里下载 `requirements-runtime-win311.lock` 对应的依赖，再从本地安装。

```powershell
py -3.11 -m pip download --only-binary=:all: --require-hashes -r requirements-runtime-win311.lock --dest wheelhouse
# 复制 wheelhouse、锁文件和若存 wheel 后，在目标机器运行
& $ruocunPython -I -X utf8 -m pip install --no-index --find-links wheelhouse --require-hashes -r requirements-runtime-win311.lock
& $ruocunPython -I -X utf8 -m pip install --no-index --no-deps $wheel
```

## 继续会话

若存把会话与配置存在用户目录下的 `.ruocun`。回到同一个项目，用 `--continue` 继续最近的未完成会话，或加上会话 ID 打开指定历史记录。也可以在交互界面用 `/resume` 选择会话。运行 `/recovery` 可查看需要核对的未决操作。

恢复会话时，`--sandbox` 适用于本次启动的新命令。Windows 上已分离且仍在运行的后台任务沿用启动时的执行方式，`/tasks` 显示任务实际使用的沙箱状态。旧任务若在沙箱外启动，显示为「无系统沙箱」。历史对话和仍被引用的输出可以继续查看。

```powershell
& $ruocunPython -I -X utf8 -m ruocun --cwd 'C:\path\to\project' --continue
```

## 更新和回退

1. 先用 `/tasks` 查看后台任务，等待完成或停止后用 `/exit` 退出。
2. 备份用户目录下的 `.ruocun` 整个目录，包括会话数据库及其伴随文件。若另用 `--config` 指定配置文件，也一起备份。跨 Windows 用户恢复时，通过向导重新录入系统凭据。
3. 在新的虚拟环境安装新包并运行 `pip check`。保留旧环境和旧数据备份。
4. 需要回退时，退出相关程序，用旧虚拟环境配合同版本的数据备份重新启动。遇到旧会话格式迁移提示，先读 `--help` 中 `--upgrade-session` 的用法。[v2.1.0 下载](https://github.com/hmmgotit/RuoCun/releases/tag/v2.1.0) · [v2.0.0 下载](https://github.com/hmmgotit/RuoCun/releases/tag/v2.0.0)

卸载时，对安装若存的虚拟环境运行 `python -m pip uninstall ruocun`。写入范围和审批规则见 [Windows MXC 沙箱](windows-sandbox.md)。

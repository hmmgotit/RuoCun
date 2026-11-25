# Windows MXC 沙箱

Windows 上的受管 Bash、Monitor 和子代理同类命令必须在 MXC ProcessContainer 中运行。依赖缺失、摘要不匹配或宿主无法执行策略时，该次命令报错。普通聊天和会话查看仍可用。旧的沙箱开关和整次绕过参数无法授权沙箱外执行。

## 独立固定依赖

从[微软 MXC v1.1.0 官方发行包](https://github.com/microsoft/mxc/releases/tag/v1.1.0)获取 Windows x64 `wxc-exec.exe`。它作为独立依赖安装，若存 wheel 继续使用标准 Python 包形式。

Windows x64 `wxc-exec.exe` 的 SHA256 为：

`cb8f9253a2288bbb5e980d63216068ea3b640ebaf845ba3fccc0d3fe822f7a0d`

在本次启动的 PowerShell 中设置：

```powershell
$env:RUOCUN_MXC_EXEC = 'D:\tools\mxc-1.1.0\bin\x64\wxc-exec.exe'
python -m ruocun
```

将路径替换为实际解包位置。未设置时，若存从 `~/.ruocun/dependencies/mxc/1.1.0/bin/x64/wxc-exec.exe` 查找执行器。

该程序负责本地受限进程启动。目标机器须支持该次任务所需的 MXC 策略。

## 写范围与批准

默认可写范围为命令启动时的工作目录和专用临时目录。命令内的 `cd` 不扩大范围。控制配置、执行器、Python 运行时及会话恢复材料受只读保护。

命令可以通过 `additionalWriteDirectories` 请求额外可写目录。若存在审批端显示本次命令和具体目录，用户逐次批准后才加入这次执行计划。`Write/Edit` 走独立的文件工具审批，可使用已授予的一次、会话级或长期权限。后台任务按原签名计划接回。

Windows Shell 的临时文件和 pip/npm/Maven/Gradle 缓存指向 `~/.ruocun/sandbox/work/<工作目录身份>/temp/`，沙箱控制配置位于 `~/.ruocun/sandbox/control/`。缓存冷启动可能重新下载依赖。

网络默认保留；现有 `RUOCUN_SANDBOX_NO_NET=1` 要求限制该命令网络。它不控制若存自身的模型请求或可信扩展。

## 能力边界

Hook/MCP/LSP 是已配置的可信扩展，排除在首版保证之外；框架自身配置、记忆、会话及管理操作继续使用各自的受控端口。这是内置文件/命令入口的限制，不是整个若存进程或所有管理功能的系统隔离。

当前验证聚焦 Windows x64 默认 cmd 协议，额外 Python 启动桥保留原 `/d /s /c`、引号、标准句柄和退出码。MXC 内 PowerShell 的语言限制可能影响 .NET 操作。后台接回指宿主未重启且原 runner 仍存活时，不保证跨 Windows 重启继续运行。真实终端视觉与缩放体验单列验收。

MXC 的目录规则和具体行为由原生 ProcessContainer 执行。完整能力及宿主限制见[微软后端说明](https://learn.microsoft.com/en-us/windows/apps/develop/mxc/platform-support)。

<div align="center">

# 若存 RuoCun

**Coding Agent 工程参考实现**

[![发布 v2.3.0](https://img.shields.io/badge/Release-v2.3.0-23604B)](https://github.com/hmmgotit/RuoCun/releases/tag/v2.3.0)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](docs/install-and-recovery.md)
[![Windows x64 安装包](https://img.shields.io/badge/Windows-x64-2563A8)](docs/install-and-recovery.md)
[![MIT 许可证](https://img.shields.io/badge/License-MIT-596575)](LICENSE)

[![中文课程 21 章](https://img.shields.io/badge/%E4%B8%AD%E6%96%87%E8%AF%BE%E7%A8%8B-21_%E7%AB%A0-23604B)](https://hmmgotit.github.io/RuoCun/learning/课程学习地图.html)
[![Terminal-Bench 2.1 项目自测 71/89](https://img.shields.io/badge/TB_2.1_%E8%87%AA%E6%B5%8B-71%2F89-245BA7)](docs/benchmark/README.md)
[![v2.0.0 离线验证 1,983 项通过](https://img.shields.io/badge/v2.0.0_%E7%A6%BB%E7%BA%BF%E9%AA%8C%E8%AF%81-1983_%E9%80%9A%E8%BF%87-31734D)](https://github.com/hmmgotit/RuoCun/releases/tag/v2.0.0)

**[开始读课](https://hmmgotit.github.io/RuoCun/learning/课程学习地图.html)** · [快速安装](#快速开始) · [测评记录](docs/benchmark/README.md) · [项目主页](https://hmmgotit.github.io/RuoCun/)

</div>

<p align="center">
  <img src="docs/assets/ruocun-welcome-demo.gif" alt="若存的原生欢迎形象，以及在 Windows Terminal 中验证普通价 180 元、会员价 200 元的过程">
</p>
<p align="center"><sub>折扣案例 · Windows Terminal 实录 · 本地模拟模型响应</sub></p>

若存是一套可运行的终端编码智能体，为已经接触过 Agent 或 Harness、想读懂一套完整工程的开发者而做。

这套 21 章中文学习笔记面向写过代码、调过 API、大致了解 function calling 的读者。每章结合源码和项目案例，讲清 Agent harness 的关键设计与取舍，说明为什么这样选、条件变了该怎么判断。学完能用具体例子回答相关面试题。

## 五题缓存对照

**五道同题实测，若存输入缓存命中率 99.34%，DeepSeek Harness 为 99.07%。**

| Terminal-Bench 2.1 任务 | 若存 | DeepSeek Harness |
| --- | ---: | ---: |
| `cobol-modernization` | 99.57% | 99.20% |
| `db-wal-recovery` | 98.71% | 95.70% |
| `multi-source-data-merger` | 98.44% | 97.04% |
| `adaptive-rejection-sampler` | 99.18% | 99.17% |
| `bn-fit-modify` | 98.54% | 96.32% |
| **按输入 Token 加权** | **99.34%** | **99.07%** |

每个框架每题各跑一次。若存通过 5 题，DeepSeek Harness 通过 4 题。已确认用量按低峰价折算，分别约为 ¥1.66 和 ¥1.82。命中率按命中的输入 Token 占比计算，费用还取决于未命中的输入和输出量。[实验口径与用量表](docs/benchmark/README.md#五道同题的缓存命中)

前缀缓存会复用已经处理过的输入。若存把每轮可能变化的目录快照放在请求尾部，让前面的内容更容易复用。[第 15 章 · 两次请求开头一样，模型服务能复用多少计算](https://hmmgotit.github.io/RuoCun/learning/revised/prefix-cache.html)

## Terminal-Bench 2.1

**在 Terminal-Bench 2.1 项目自测中，若存通过 71 / 89 项任务（79.78%）。与官方榜的 Codex + GPT‑6 Astra（xhigh，85.8%）相差约 6 个百分点，按 89 题折算约 5 题。**

89 题共记录 4,927 次 API 请求，其中 4,922 次有确认用量。按这些请求的输入 token 汇总，**缓存命中率为 99.22%**（348,210,560 / 350,961,719）。先计算每题的缓存命中率，再对 89 题等权平均，得到 **98.82%**。

89 题自测使用 DeepSeek 官方模型 **DeepSeek-V4.1-Flash-1017**，API 模型名为 `deepseek-flash`，主请求配置为 `max`。五题缓存对照的双方也使用该模型，前三题的主请求配置为 `high`，后两题为 `max`。 [模型版本](https://api-docs.deepseek.com/updates/#deepseek-v41-flash-1017-update) · [五题对照条件](docs/benchmark/README.md#五道同题的缓存命中)

上方的折扣案例录于 v1.0.0，使用本地模拟模型响应。89 题和五题缓存对照的历史制品与条件见[测评记录](docs/benchmark/README.md)。v2.0.0 的 **1,983 项离线验证**使用合成回包和本地对照。 [发布验收记录](https://github.com/hmmgotit/RuoCun/releases/tag/v2.0.0)

Terminal-Bench 2.1 是第三方基准，通过 Harbor 在独立容器中评测 Agent，任务覆盖系统管理、安全、数据科学、科学计算、调试和机器学习等领域。89 道题的最终成绩、判分来源和可公开的用量数据均可查阅。[Harbor](https://www.harborframework.com/) · [评测报告](docs/benchmark/README.md) · [逐题 CSV](docs/benchmark/results.csv) · [精简 XLSX](docs/benchmark/terminal-bench-2.1.xlsx)

若存 89 题的已确认模型用量按 DeepSeek 低峰价估算为 **¥31.07**，按高峰价估算为 **¥62.15**，分别约为 **Codex + GPT‑6 Astra（xhigh）榜单总费用的 0.52% 和 1.04%**。榜单的 **$888.54** 按 2026 年 11 月 18 日中间价（1 美元兑 6.7118 元）折合 **¥5,963.70**。

若存按历史测评每题一个选定的有效结果汇总，其中 5 次请求缺少模型用量。官方榜单汇总多次试验，两边使用的模型和试次数有差异。 [成本与评测口径](docs/benchmark/README.md#与官方榜单对照) · [Harbor 官方榜单](https://hub.harborframework.com/datasets/terminal-bench/terminal-bench-2-1/latest?leaderboard=main&tab=leaderboard) · [人民币汇率中间价](https://www.chinamoney.com.cn/chinese/bkccpr/index.html?tab=2)

<details>
<summary>查看 Terminal-Bench 2.1 历史榜单原图</summary>

![Terminal-Bench 2.1 历史榜单，列出各条目的 Agent、模型、得分和费用](docs/benchmark/leaderboard.png)

</details>

## 上下文与记忆

### 压缩之后，原始测试回执还能找回来吗？

调试跑了几十轮，构建和测试日志把上下文挤满了。若存会把消息、工具定义和输出余量一起纳入预算，先裁剪特别长的工具输出，仍超预算才摘要较早的对话。生成摘要时保留近期消息，工具调用和对应结果按组处理。

新视图和原文索引一起写入 SQLite，确认保存后才切换。想核对「会员价测试失败」的细节，可按归档 ID 用 `ArchiveRead` 回查原始测试输出。压缩失败时保留原视图。压缩策略参考 DeepSeek Harness 的 `compaction-basic`。 [上下文设计](docs/context-management.md)

### 以前修过的坑，换个任务还能用吗？

比如上次分页遇到空页时提前退出了。可以把经验记成「空页仍有下一页游标时，继续翻页」，并附上空页后成功读到下一页的测试证据。遇到相似任务，先核对当前接口是否仍遵循这个规则，再跑相应用例确认。

v2.3.0 默认使用新版记忆实现，普通 CLI 启动时仍加载已有记忆。用 `/memory project enable` 在当前工作区开启工程记忆，跨会话复用规则、失败线索和验证结果。用 `/memory project status` 查看来源范围、占用和检索状态。[记忆设计与操作](docs/memory-design.md)

## 快速开始

当前 wheel 面向 **Windows x64 / CPython 3.11**。下载 wheel，在文件所在目录打开 PowerShell，安装到独立环境。[v2.3.0 下载](https://github.com/hmmgotit/RuoCun/releases/tag/v2.3.0) · [v2.2.0 下载](https://github.com/hmmgotit/RuoCun/releases/tag/v2.2.0) · [v2.1.0 下载](https://github.com/hmmgotit/RuoCun/releases/tag/v2.1.0) · [v2.0.0 下载](https://github.com/hmmgotit/RuoCun/releases/tag/v2.0.0)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\ruocun-2.3.0-py311-none-win_amd64.whl
.\.venv\Scripts\python.exe -m ruocun --cwd "C:\path\to\project"
```

受管 Shell 的系统沙箱默认关闭，Bash、Monitor 和子代理的同类命令按权限规则和审批执行。需要系统隔离时，在启动命令中加 `--sandbox`。Windows 使用 Microsoft MXC v1.1.0，Linux 使用 Bubblewrap，macOS 使用 Seatbelt。启用后，后端不可用或无法应用隔离会拒绝该次命令。[Windows MXC 安装配置](docs/windows-sandbox.md)

第一次启动时，终端引导会设置模型服务、API 密钥和工作目录信任。想知道一条用户请求怎样经过模型、工具和结果回流，可以直接读[第 1 章的一次查价案例](https://hmmgotit.github.io/RuoCun/learning/revised/task-loop.html)。

[安装、更新与恢复](docs/install-and-recovery.md) · [从源码构建](docs/development.md)

## 许可证

若存原创内容采用 MIT 许可证。第三方材料的来源与许可见附带声明。[若存开源许可](LICENSE) · [来源说明](NOTICE.md) · [第三方许可证](THIRD-PARTY-LICENSES.txt)

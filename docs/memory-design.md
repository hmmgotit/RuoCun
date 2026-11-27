# 一条旧经验怎样帮到新任务

上一次调分页接口，程序遇到空页就提前返回，但接口约定只要还有下一页游标就必须继续。下一次修类似功能时，有用的记忆能说清哪种条件会触发旧错、应该怎么做、测哪个用例才能确认修好了。若存把这类经验写成可读的项目 Markdown。

## 启动时给背景，接到任务后再挑经验

1. **加载启动记忆。** 普通 CLI 默认走 `startup`。若存按作用域、来源和预算加载启动快照。记录较多时，高价值条目进全文，其余可进短索引，模型可以用 `MemoryRead` 查详情。[启动注入](../src/ruocun/memory/inject.py#L122) · [读取工具](../src/ruocun/tools/memory_read.py#L22)
2. **选出少量旧经验。** 在交互会话显式开启受控学习后，若存按任务中的中英文词项和代码标识符匹配知识与操作经验，最多选两条，补充段总计不超过 2400 字符。启动时已加载同版全文的条目只补引用和定位，来源文件变化时提示核对。[召回与去重](../src/ruocun/memory/retrieval.py#L108) · [来源校验](../src/ruocun/memory/validate.py)
3. **回到当前代码验证。** 执行模型先核对当前接口。如果仍以游标为准，就在现有待办里加入「空页但仍有游标」的检查，并用最后一次相关修改后的工具结果确认。缺少证据时报告未验证。这是交给执行模型的核对约定，实际依据来自源码和工具结果。[旧错检查约定](../src/ruocun/memory/retrieval.py#L31)

## 好经验从哪里来

普通 CLI 的自动学习默认是 `off`。受控学习使用 `--memory-learning controlled` 显式开启，先从当前项目中的明确纠错、稳定约定和验证过的做法选候选。技术经验包含「适用条件、做法、证据」，写入之前还要核对所引用的文件版本。历史桌面启动配置曾显式开启受控学习，仍按启动参数决定 CLI 行为。选择 `--memory-strategy task` 也能为任务补充相关历史记录，自动提炼仍由学习开关控制。[CLI 默认值](../src/ruocun/agent/runtime.py#L283) · [经验提炼](../src/ruocun/agent/controlled_learning.py#L295) · [写入前校验](../src/ruocun/agent/controlled_learning.py#L426)

## 跨会话工程记忆

在目标工作区启动交互 CLI，执行 `/memory project enable`。新会话沿用本地开启状态。原生 Markdown、锁定项与兼容来源仍按既有规则读取。开启后，新任务可按原文、代码符号和当前可用的语义模型召回工程摘录。任务自动补充合计最多两条、2400 字符。批处理可读取适用的项目经验，只有显式指定当前目标才允许积累，例如 `ruocun --cwd D:/work/api --project-memory-write D:/work/api "检查导出"`。

| 要做的事 | 在交互 CLI 中输入 |
| --- | --- |
| 查看当前库与一条记录 | `/memory project status`、`/memory project list`、`/memory project show <ID>` |
| 让当前 B 读取 A 的摘录 | `/memory project link "D:/work/A"`。取消用 `unlink` |
| 把本库核实的单条记录发布给 B | `/memory project publish <ID> "D:/work/B"` |
| 更正、撤回 | `/memory project correct <ID> 新规则`、`/memory project forget <ID>`。也可直接说“请忘记工程记忆 <ID>” |
| 选定旧材料 | `/memory project backfill session <会话ID> [其他ID]` 或 `/memory project backfill range <开始UTC毫秒> <结束UTC毫秒>`，时间范围左闭右开 |
| 设置本库内容配额 | `/memory project quota <字节>`，默认 8 MiB |
| 配置、检查本地语义检索 | `/memory project vectors local "D:/models/multilingual-e5-small"`、`/memory project vectors status`、`/memory project vectors rebuild` |
| 显式使用外部向量服务 | `/memory project vectors remote <HTTPS embeddings端点> <模型> [密钥环境变量名]`。该服务会收到查询和已授权的候选摘录。用 `vectors off` 切回纯词法 |

本地 E5 需要可选依赖 `ruocun[semantic]` 和预先放在指定目录的 `tokenizer.json`、`model.onnx`。本地也支持有词数、维度文件头的文本 `.vec`。模型缺失或超时时，若存继续用原文及符号检索。开启项目无须下载权重。使用 `MemoryRead` 时，工程 ID 可精确读取，`project: 查询词` 可搜索获准的项目摘录。手动按名管理 Markdown 仍使用原入口。

更正和撤回以存储提交为界。下一次外发请求重新核对原条目、共享授权、启动片段、工具回执及压缩摘要。旧会话原文继续由会话归档管理。工作区关联只授权短摘录，原会话与工具日志仍按执行范围访问。[项目存储](../src/ruocun/memory/project_store.py) · [发送前投影](../src/ruocun/memory/project_projection.py)

## 工程与论文参考

| 来源 | 对照阅读 |
| --- | --- |
| Claude Code 的记忆文档 | 启动加载短索引，按需读取主题详情。[官方说明](https://code.claude.com/docs/en/memory#how-it-works) |
| ReasoningBank | 从历史经历提炼可复用策略，再按任务召回。[论文](https://arxiv.org/abs/2509.25140v2) |
| Memory Transfer Learning | 从具体编码任务中提炼可迁移的做法。[论文](https://aclanthology.org/2026.emnlp-main.2666/) |
| MUSE | 启动背景与执行时的选择性补充分工。[论文](https://arxiv.org/abs/2610.38332v1) |
| MemDev-Bench | 在随时间演进的仓库里评估历史经验的相关性与作用。[论文](https://arxiv.org/abs/2610.27524v1) |
| LCM | 用压缩后的视图续作，同时保留回查完整历史的入口。[论文](https://arxiv.org/abs/2605.04050v1) |

继续读 [第 17 章 · 经验怎样用到当前任务](learning/17-经验怎样用到当前任务.html) 和 [第 18 章 · 新经验怎样产生](learning/18-新经验怎样产生.html)。

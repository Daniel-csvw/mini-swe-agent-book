# 概览 —— 一百行代码，74% 的 SWE-bench

> 本章事实来源：`README.md`、`docs/advanced/v2_migration.md` 与整个 `src/minisweagent` 包。本书基于 **v2.4.6**（`src/minisweagent/__init__.py:11`），所有引用为仓库根目录相对路径加行号，可直接跳转核对。

## 一、引子：同一个团队，两次答案

2024 年，普林斯顿与斯坦福的团队做了 SWE-bench 和 SWE-agent，点燃了 coding agent 这把火。一年之后，他们问了自己一个问题（`README.md:21`）：

> **What if our agent was 100x simpler, and still worked nearly as well?**

答案就是 mini-swe-agent。它在 SWE-bench verified 上拿到 **超过 74%** 的成绩（`README.md:28`），而核心 agent 类只有约一百行有效代码。Meta、NVIDIA、IBM、Nebius、Anyscale 等团队都在用它（`README.md:25`）。

这不是一个"够用就行"的玩具。它是一个被刻意压到极简的研究基线：把聚光灯从 agent 脚手架挪回语言模型本身。本书把这个项目的源码逐章拆开。读完你应该能回答三个问题：

1. 为什么这个 agent **只有 bash 一个工具**，反而跑赢了带几十个工具的复杂脚手架？
2. 为什么它**不保持一个持久的 shell 会话**，每条命令都开新子壳？
3. 为什么说它的历史是**完全线性**的，这对调试和微调意味着什么？

## 二、先把词对齐

| 术语 | 在本书里的含义 |
|---|---|
| agent / scaffold | 驱动模型循环行动的脚手架程序，即本项目本身 |
| step | 一次「问模型 → 执行动作 → 回填观察」的完整迭代 |
| action | 模型要求执行的一条 bash 命令 |
| observation | 命令执行结果格式化后喂回模型的消息 |
| trajectory（轨迹） | 一次运行的完整消息记录，落盘为 `*.traj.json` |
| SWE-bench | 用真实 GitHub issue 评测修 bug 能力的基准；verified 是其中人工校验过的子集 |
| tool call | 模型原生的函数调用接口；v2 起默认用它传递 bash 命令 |

## 三、全景：小，是一种可以量化的性质

"极简"不是口号，是行数。核心文件实测（v2.4.6，含空行与注释）：

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/minisweagent/agents/default.py` | 190 | agent 主循环 |
| `src/minisweagent/agents/interactive.py` | 209 | 人在环路的交互版 |
| `src/minisweagent/models/litellm_model.py` | 164 | 默认模型接口 |
| `src/minisweagent/environments/local.py` | 92 | 本地执行环境 |
| `src/minisweagent/environments/docker.py` | 161 | Docker 执行环境 |
| `src/minisweagent/run/hello_world.py` | 42 | 最小入口脚本 |
| `src/minisweagent/run/mini.py` | 109 | 默认 CLI 入口 |
| `src/minisweagent/exceptions.py` | 26 | 全部控制流异常 |

README 说 agent 类"约 100 行"（`README.md:26`），指的是刨去配置类、序列化方法之后的有效逻辑。整个核心链路——入口、循环、模型、环境——加起来不到七百行，且**没有任何重型框架依赖**：没有 LangChain，没有工作流引擎，只有 litellm、pydantic、jinja2 这类单一职责的工具库（`pyproject.toml:33-48`）。

项目结构五个核心目录，各管一件事（`utils/` 是公共辅助，不算主格）：

```text
src/minisweagent/
├── __init__.py      # 三个协议（Model / Environment / Agent）+ 全局配置加载
├── agents/          # 控制流：循环怎么转
├── models/          # LM 接口：怎么问模型、怎么解析动作
├── environments/    # 动作执行：命令在哪里跑
├── run/             # 入口脚本：把三者组装起来
├── config/          # 内置 YAML 配置（提示词模板都住在这里）
└── utils/           # 日志、递归合并等小工具
```

记住这五个核心目录，后面每一章都落在其中一格上。

## 四、三根设计支柱

README 的动机部分（`README.md:38-54`）把设计哲学说得很直白，归纳为三根支柱。

### 4.1 支柱一：除了 bash，没有别的工具

传统脚手架（包括团队自己的 SWE-agent）为每类操作设计专门工具：打开文件、编辑行、搜索符号……mini 反其道而行：**只给模型一个 bash 工具**（`src/minisweagent/models/utils/actions_toolcall.py:11-27`），甚至可以不依赖模型的原生工具调用接口，用正则从文本里抠命令（textbased 路线，见 m06）。

这个决定的影响是深远的：

- **任何模型都能跑**——不支持 function calling 的模型走 textbased 路线即可；
- **沙箱里零安装**——环境里只要有 bash 就够，不用装任何工具包；
- 想让 agent 做新事情（比如开 PR）？写在提示词里让模型自己想办法，而不是给脚手架加代码（`README.md:73-74`）。

### 4.2 支柱二：每条命令都是独立子壳

mini 用 `subprocess` 执行动作，**每条命令完全独立**，不维护一个长期存活的 shell 会话（`README.md:48-50`）。`cd`、环境变量在命令之间不持久——提示词里明确告诉模型这一点，并教它用 `MY_VAR=x cd /path && ...` 单行复合命令绕过（`src/minisweagent/config/mini.yaml:39-40`）。

代价是模型要多写几个 `cd`；收益是工程上的巨大简化：

- 把执行搬进沙箱，只是把 `subprocess.run` 换成 `docker exec`（`README.md:49` 原文就是这么说的）；
- 没有会话状态就没有状态腐化，并发扩容平凡；
- 超时处理干净——杀整个进程组即可（m07 细讲）。

官方 FAQ 专门有一节 "why no shell session"，README 两次强调 "this is a big deal, trust me"（`README.md:50`、`README.md:76`）。

### 4.3 支柱三：完全线性的历史

每一步只是往 `messages` 列表**追加**消息，从不修剪、从不压缩、从不重排（`README.md:45-47`）。于是：

> **轨迹（trajectory）和喂给模型的 messages 是同一个东西。**

没有"模型实际看到的"和"日志里记的"两份历史之分。调试时你看到的就是模型看到的；做微调时轨迹可以直接当训练数据。v2 之后唯一的加工是：发给 API 前剥掉 `extra` 元数据字段、做少量 API 兼容性重排（m05 细讲），语义内容不变。

**这三根支柱共同指向一个立场：把复杂度让给模型，把确定性留给代码。** 模型负责聪明，脚手架负责可预测。

## 五、v1 → v2：一次值得知道的演进

当前是 v2 时代（`README.md:16-17` 的警告框）。如果你在网上读到旧教程，注意这些差异（详见 `docs/advanced/v2_migration.md`）：

| 维度 | v1 | v2（本书） |
|---|---|---|
| 动作传递 | 正则解析 ```` ```bash ```` 代码块（`v2_migration.md:48`） | 原生 tool call 为默认，textbased 保留为备选（正则改用 ```` ```mswea_bash_command ````） |
| 动作解析职责 | agent 管 | **模型类管**——换模型类即换解析方式（`v2_migration.md:166`） |
| 成本记账 | model 持有 | **agent 持有**，model 无状态化（`v2_migration.md:168`） |
| 配置类 | dataclass | pydantic `BaseModel`（`v2_migration.md:170`） |
| 消息格式 | 手工规范化 | 模型原生输出 + `extra` 字段（`v2_migration.md:122`） |
| 完成信号 | `MINI_SWE_AGENT_FINAL_OUTPUT` | `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`（`v2_migration.md:52`） |
| 组件对接 | 继承 | **协议 + 鸭子类型**，不强制继承（`v2_migration.md:176-179`） |

其中"职责转移"最重要：v2 的 agent 成了纯协调者，解析动作、格式化观察都下沉到模型类。这是理解 m03、m05、m06 三章分工的钥匙。

## 六、本书地图

十二章，按「先全景、后机制、再工程」排序：

| 章 | 主题 | 一句话 |
|---|---|---|
| m02 | 包地图 | 五个核心目录、三个协议与一条 `mini` 命令的旅程 |
| m03 | Agent 循环 | `run/step/query/execute_actions` 与异常控制流 |
| m04 | 消息与模板 | 消息结构、`extra`、Jinja 模板变量从哪来 |
| m05 | 模型调用 | 重试、成本核算、FormatError 契约 |
| m06 | 动作解析 | toolcall / textbased / Responses API 三条路线 |
| m07 | 环境 | 子壳执行、哨兵命令、进程组、Docker 生命周期 |
| m08 | 交互模式 | human / confirm / yolo 三态与中断恢复 |
| m09 | 配置系统 | YAML 合并、UNSET 哨兵、三个工厂函数 |
| m10 | 轨迹持久化 | traj.json 格式与 inspector 回放器 |
| m11 | 基准测试 | 批量跑 SWE-bench 的并发与断点续跑 |
| m12 | 扩展指南 | 不改源码，写你自己的组件 |

建议阅读路径：m02 → m03 → m04 是地基，必读；之后 m05/m06/m07 可按兴趣乱序；做评测读 m11，做二次开发读 m12。

## 七、小结

- mini-swe-agent 是 SWE-agent 团队对自己的"100x 简化"回答：核心链路不足七百行，SWE-bench verified >74%。
- 三根支柱：**只有 bash**、**无状态子壳**、**线性历史**。每一根都在后面的章节里有具体的代码落点。
- v2 的关键演进：解析职责下沉到模型类、agent 纯协调化、协议代替继承。
- 下一章先把包结构、三个协议和程序入口看清楚，再进循环。
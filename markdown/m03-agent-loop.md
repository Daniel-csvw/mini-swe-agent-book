# Agent 循环 —— 异常是唯一的控制流

> 本章源文件：`src/minisweagent/agents/default.py`（190 行）、`src/minisweagent/exceptions.py`（26 行）。官方控制流图见 `docs/advanced/control_flow.md`。

## 一、引子：一百行怎么转起来

`DefaultAgent` 是全书的主角。它的全部工作可以用一句话概括：**反复问模型、执行命令，直到某条消息说"停"**。但魔鬼在细节里：停下来的方式有五种，格式错误要计费，每一步都要落盘，而这些全塞在不到两百行里。读完你应该能回答三个问题：

1. 这个 `while True` 循环到底有几种退出路径？分别由谁触发？
2. 为什么"提交任务""超出限额""格式错误"都是**异常**，而不是返回码？
3. 模型输出格式错误时 API 已经计费了，这笔钱记在哪里？

## 二、先把词对齐

| 术语 | 含义 | 代码落点 |
|---|---|---|
| step | 一次 query + execute_actions | `default.py:126-128` |
| query | 问一次模型（含限额检查） | `default.py:130-152` |
| action | 待执行的命令，`{"command": ...}` | 由模型类解析产出 |
| observation | 执行结果格式化后的消息 | `default.py:157` |
| exit 消息 | `role="exit"` 的特殊消息，循环的停止哨兵 | `default.py:122` |
| InterruptAgentFlow | 所有控制流异常的基类，携带要追加的消息 | `exceptions.py:1-6` |

## 三、全景：四层调用，一个哨兵

```text
run(task)                          default.py:88
│  初始化 system + user 两条消息      :92-95
│
└─ while True:                     :96
   │
   ├─ step()                       :126  ← 一行：execute_actions(query())
   │   ├─ query()                  :130
   │   │   ├─ 限额闸门（步数/成本/时间）:132-147
   │   │   ├─ model.query(messages) :149  → 助手消息（含 actions）
   │   │   └─ 计费 + 追加消息        :150-151
   │   └─ execute_actions(msg)     :154
   │       ├─ env.execute(action)  :156  （逐条；可能抛 Submitted）
   │       └─ 格式化观察 + 追加      :157
   │
   ├─ except FormatError           :100  → 补计费，追加纠错消息
   ├─ except InterruptAgentFlow    :115  → 追加异常携带的消息
   ├─ except Exception             :117  → 记录后 re-raise
   ├─ finally: save()              :120  → 每步都落盘
   │
   └─ messages[-1].role == "exit"? :122  → 是：break；否：下一轮
```

注意一个容易看错的结构：**except 分支并不直接退出循环**。它们只是往 `messages` 里追加消息，然后统一走到 122 行检查最后一条消息的 role。退出不退，只看哨兵。

## 四、配置与状态：先看清手里有什么牌

### 4.1 AgentConfig（19-35 行）

| 字段 | 默认 | 含义 |
|---|---|---|
| `system_template` | 必填 | 系统消息模板（第一条消息） |
| `instance_template` | 必填 | 任务消息模板（第二条消息） |
| `step_limit` | `0` | 最大步数，0 = 不限 |
| `cost_limit` | `3.0` | 成本上限（美元），**超过**才停 |
| `wall_time_limit_seconds` | `0` | 墙钟时间上限，0 = 不限 |
| `max_consecutive_format_errors` | `3` | 连续格式错误上限，0 = 不限 |
| `output_path` | `None` | 轨迹落盘路径 |

两个模板必填、其余皆有限额——配置的形状直接反映了 agent 的职责：**给提示词、看住资源、别的不多管**。

### 4.2 实例状态（39-50 行）

`__init__` 收下 model 与 env 两个组件，然后初始化六个可变状态：

```python
self.messages: list[dict] = []            # :42  线性历史，只增不减
self.cost = 0.0                           # :47  本实例累计成本
self.n_calls = 0                          # :48  模型调用次数
self.n_consecutive_format_errors = 0      # :49  连续格式错误计数
self._start_time = time.time()            # :50  墙钟起点
```

`config_class` 参数（39 行）是扩展点：子类传入自己的配置类即可加字段（m08 的 `InteractiveAgentConfig` 就是这么做的）。

## 五、run()：逐段精读（88-124 行）

### 5.1 开场两条消息（90-95 行）

```python
self.extra_template_vars |= {"task": task, **kwargs}     # :90
self.messages = []                                       # :91
self.add_messages(
    self.model.format_message(role="system", content=self._render_template(self.config.system_template)),
    self.model.format_message(role="user", content=self._render_template(self.config.instance_template)),
)                                                        # :92-95
```

三个细节：

- **每次 run 清空历史**（91 行）——同一个 agent 对象可以跑多个任务；
- 任务文本不进消息模板硬编码，而是进 `extra_template_vars`，由 Jinja 的 `{{task}}` 渲染（m04）；
- 消息要经过 `model.format_message` 包装——因为不同 API 的消息格式不同（chat completions vs Responses API），格式化的职责在模型侧（v2 的职责转移，`v2_migration.md:166`）。

### 5.2 异常处理矩阵（96-121 行）

`while True` 里的 try/except 是本章的核心。四个分支各司其职：

| 分支 | 行号 | 处理 | 之后 |
|---|---|---|---|
| 正常 step | 98-99 | 清零连续格式错误计数 | 检查哨兵 |
| `FormatError` | 100-114 | 补计费 + 计数 + 追加纠错消息（或追加 exit） | 检查哨兵 |
| `InterruptAgentFlow` | 115-116 | 追加异常携带的消息 | 检查哨兵 |
| 其他 `Exception` | 117-119 | `handle_uncaught_exception` 记录 exit 消息后 **re-raise** | 进程崩溃，但轨迹已保存 |
| `finally` | 120-121 | `self.save(self.config.output_path)` | 每步必执行 |

**为什么用异常而不是返回码？** 官方文档的回答（`control_flow.md:123`）："Using exceptions for the control flow is a lot easier than passing around flags and states, especially when extending or subclassing the agent." 看调用栈就明白了：`Submitted` 在 `env.execute` 里抛出（`local.py:50`），隔着 `execute_actions` → `step` 两层直接飞到 `run` 的 except。如果用返回码，每一层都要检查、透传"是否该停"的标志——两层还好，扩展成五层就是灾难。异常让"深处产生的停止意图"零成本地抵达唯一该处理它的地方。

### 5.3 FormatError 的特殊账本（100-114 行）

这是全章最微妙的一段：

```python
except FormatError as e:
    # The call was billed before parsing failed, so query() never got to charge it.
    self.cost += e.messages[0].get("extra", {}).get("cost", 0.0)   # :102
    self.n_consecutive_format_errors += 1                          # :103
    if 0 < self.config.max_consecutive_format_errors <= self.n_consecutive_format_errors:  # :104
        self.add_messages(*e.messages, {"role": "exit", ... "RepeatedFormatError" ...})    # :105-112
    else:
        self.add_messages(*e.messages)                             # :114
```

- **102 行的注释揭示了一个契约**：模型类在解析失败时，必须把已产生的成本塞进 FormatError 的消息里（`litellm_model.py:87-98` 履行它），因为正常路径上 `query()` 的 150 行计费没机会执行。钱花了就得记账，哪怕输出是废的。
- 连续计数在**任何干净 step 后清零**（99 行）——惩罚的是"屡教不改"，不是"偶尔犯错"。
- 达到上限时追加 `RepeatedFormatError` 的 exit 消息；没达到就只追加纠错消息（role=user），下一轮模型会读到纠错指引（模板见 m04）。

### 5.4 哨兵与返回值（122-124 行）

```python
if self.messages[-1].get("role") == "exit":   # :122
    break
return self.messages[-1].get("extra", {})     # :124
```

循环唯一的退出条件：**最后一条消息是 exit**。返回值是它的 `extra`——按惯例含 `exit_status` 与 `submission` 两个键。这就是 Agent 协议里 `run() -> dict` 的全部含义（`__init__.py:78`）。

## 六、step / query / execute_actions：三层的分工

### 6.1 step：一行（126-128 行）

```python
def step(self) -> list[dict]:
    """Query the LM, execute actions."""
    return self.execute_actions(self.query())
```

一行函数为什么存在？因为它是**子类挂插头的标准位置**：`InteractiveAgent` 覆写它来捕获 Ctrl-C（`interactive.py:109`），`ProgressTrackingAgent` 覆写它来报进度（`run/benchmarks/utils/common.py:15-17`）。覆写 step 不需要理解 run 的异常矩阵。

### 6.2 query：三道闸门 + 计费（130-152 行）

```python
if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:  # :132
    raise LimitsExceeded({... "exit_status": "LimitsExceeded" ...})                        # :133-139
if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):         # :140
    raise TimeExceeded({...})                                                              # :141-147
self.n_calls += 1                                                                          # :148
message = self.model.query(self.messages)                                                  # :149
self.cost += message.get("extra", {}).get("cost", 0.0)                                     # :150
```

- 限额检查在**调用前**：`0 < limit <= current` 的写法让 `0` 天然表示"不限"；
- 注意 `cost_limit` 的语义是"超过才停"（配置 docstring，28-29 行）：本次调用前成本还没超就允许花，花超了下一轮才拦。所以实际成本可能略微越限——这是刻意的简单化；
- `TimeExceeded` 继承 `LimitsExceeded`（`exceptions.py:17`），但在交互 agent 里待遇不同：步数/成本限额可以问用户提额，墙钟不行（`interactive.py:75-79`，m08 细讲）；
- 149 行把**整个 messages 列表**传给模型——线性历史的直接体现：不剪枝、不摘要、不挑选。

### 6.3 execute_actions：两行（154-157 行）

```python
outputs = [self.env.execute(action) for action in message.get("extra", {}).get("actions", [])]  # :156
return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))  # :157
```

- 动作列表从模型消息的 `extra.actions` 里取——**agent 自己不懂工具调用格式**，解析是模型类的事（m06）；
- 逐条顺序执行；任何一条抛出 `Submitted`，后面的动作不再执行（列表推导中断）；
- 观察格式化又交回模型类：toolcall 路线要生成 `role="tool"` 且带 `tool_call_id` 的消息，textbased 路线生成 `role="user"` 消息——agent 无需知道差别。

## 七、异常家族：26 行的控制流词汇表

`exceptions.py` 全文 26 行，继承树如下：

```text
InterruptAgentFlow                    :1   基类：__init__ 收下 *messages 存到 self.messages
├── Submitted                         :9   任务完成（环境抛出）
├── LimitsExceeded                    :13  步数/成本超限（agent.query 抛出）
│   └── TimeExceeded                  :17  墙钟超限（agent.query 抛出）
├── UserInterruption                  :21  用户插话/拒绝（交互 agent 抛出）
└── FormatError                       :25  模型输出不合预期（模型类抛出）
```

基类只做一件事（4-6 行）：`self.messages = messages`。**异常即消息信封**——抛出者把要追加进轨迹的消息装进异常，捕获者（`run` 的 115-116 行）原样倒出。谁抛、谁捕、带什么消息：

| 异常 | 抛出者 | 携带消息的 role | 循环是否停 |
|---|---|---|---|
| `Submitted` | `env._check_finished`（`local.py:50-56`） | `exit`（含 submission） | 停 |
| `LimitsExceeded` / `TimeExceeded` | `agent.query`（`default.py:133/141`） | `exit` | 停 |
| `FormatError` | 模型类解析器（`actions_toolcall.py:41/66`） | `user`（纠错指引） | 不停（除非连续超限） |
| `UserInterruption` | 交互 agent（`interactive.py:41`） | `user`（用户的话） | 不停 |
| 其他 Exception | 任何地方 | `exit`（记录 traceback） | **re-raise，进程退出** |

最后一行值得停顿：`handle_uncaught_exception`（74-86 行）先把异常名、字符串、完整 traceback 包装成 exit 消息追加，然后 117-119 行 re-raise。配合 finally 的 save（120-121 行），**即使 agent 崩溃，轨迹也完整落盘且标明了死因**——调试线上事故时这是救命的性质。

## 八、贯穿全章的哲学：只增不减

数一数 `self.messages` 被写的地方：初始化清空（91 行）、`add_messages` 追加（69-72 行）。没有删除、没有修改、没有压缩。整个类里**唯一**的"状态机"就是 122 行那个 role 检查。

这就是 m01 说的线性历史在代码层面的样子。对比 pi-agent 等系统的上下文压缩、历史剪枝，mini 的立场是：**那些是模型能力不足时代的补丁；上下文超了就让 API 报错（`ContextWindowExceededError` 在 abort 列表里，`litellm_model.py:54`），把问题暴露给用户而不是悄悄修剪。** 简单本身就是一种功能。

## 九、小结

- 循环四件套：`run`（异常矩阵 + 哨兵）、`step`（一行，子类插头）、`query`（三道闸门 + 计费）、`execute_actions`（执行 + 格式化观察）。
- 退出只看一件事：`messages[-1].role == "exit"`。五种死法：Submitted、LimitsExceeded、TimeExceeded、RepeatedFormatError、未捕获异常（后者 re-raise）。
- 异常是消息信封：`InterruptAgentFlow.messages` 携带要追加的消息，深层抛出、run 层统一追加。
- FormatError 必须携带成本（模型侧契约），连续 3 次才判死刑；每步 finally 落盘，崩溃也有完整轨迹。
- 下一章看消息本身：模板变量从哪里来，`extra` 里装了什么。
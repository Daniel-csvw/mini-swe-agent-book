# 交互模式 —— human/confirm/yolo 三态

> 本章源文件：`src/minisweagent/agents/interactive.py`（209 行）、`agents/utils/prompt_user.py`（20 行）。

## 一、引子：把方向盘交给人

`DefaultAgent` 一旦开跑就不问人。`InteractiveAgent`（`mini` CLI 的默认 agent）在循环的四个关键位置开了口子：执行前确认、Ctrl-C 插话、提交前把关、限额时提额。它只比父类多 19 行，却是日常使用最常打交道的类。读完你应该能回答三个问题：

1. human / confirm / yolo 三种模式各拦什么、放什么？运行中怎么切换？
2. Ctrl-C 之后 agent 为什么没有死，用户的话是怎么进入对话的？
3. 限额触发时，什么情况下问用户提额，什么情况下直接停？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| confirm（默认） | 模型发的命令，非白名单者需用户回车确认 |
| yolo | 模型的命令直接执行，不问 |
| human | 不问模型，用户手输命令直接执行 |
| 白名单 | `whitelist_actions` 正则列表，匹配者免确认 |
| 斜杠命令 | `/y /c /u /h /m`，任何提示处可用的模式/帮助指令 |
| UserInterruption | 携带用户消息的控制流异常，追加后循环继续 |

## 三、全景：四个覆写，四个口子

`InteractiveAgent` 继承 `DefaultAgent`，只覆写四个方法（外加一堆私有辅助）：

| 覆写 | 行号 | 开口位置 | 干什么 |
|---|---|---|---|
| `add_messages` | 43-56 | 每条消息入列时 | 顺带打印到终端 |
| `query` | 58-94 | 问模型之前 | human 模式改问用户；限额时提额 |
| `step` | 109-122 | 包住整个 step | 捕获 KeyboardInterrupt，转为插话 |
| `execute_actions` | 124-139 | 执行命令之前 | 确认/拒绝；提交前问是否接新任务 |

配置类 `InteractiveAgentConfig`（24-30 行）在父类基础上加三字段：`mode`（默认 `"confirm"`）、`whitelist_actions`（默认空）、`confirm_exit`（默认 `True`）。

## 四、query：human 模式与限额提额（58-94 行）

### 4.1 human 模式：伪造一条"模型消息"（60-71 行）

```python
if self.config.mode == "human":
    match command := self._prompt_and_handle_slash_commands(...):
        case "/y" | "/c":
            pass                                   # 切模式后落回正常 query
        case _:
            msg = {"role": "user",
                   "content": f"User command: \n```bash\n{command}\n```",
                   "extra": {"actions": [{"command": command}]}}   # :65-69
            self.add_messages(msg)
            return msg
```

精妙之处：用户手输的命令被包装成**带 `extra.actions` 的 user 消息**，直接当作 query 的返回值。下游 `execute_actions` 只认 `extra.actions`（`default.py:156`），根本不关心这条消息来自模型还是人——**协议的一致性让"人接管模型"只需要伪造一个数据结构**。观察消息也因此走 `role="user"` 分支（`actions_toolcall.py:109`，无 tool_call_id），格式天然正确。

### 4.2 限额提额与它的两个例外（72-94 行）

正常模式包一层 `console.status` 转圈等模型（73-74 行）。两个 except 是本章最见设计功力的地方：

```python
except TimeExceeded:
    # A wall-clock limit can't be lifted by raising the step/cost limits ...
    raise                                          # :75-79
except LimitsExceeded:
    if not self._stdin_is_interactive():
        raise                                      # :81-87
    ...
    self.config.step_limit = int(input("New step limit: "))    # :92
    self.config.cost_limit = float(input("New cost limit: "))  # :93
    return super().query()                         # :94  用新限额重查
```

- **TimeExceeded 直接放行**（75-79 行）：注释解释——墙钟超限不是提额能救的，重查会再次撞钟，问用户只会无限循环。所以干净地停。
- **LimitsExceeded 先查有没有终端**（81-87 行）：`_stdin_is_interactive`（96-107 行）判断 `sys.stdin.isatty()`。CI 里 `--yolo` 无人值守、stdin 重定向自 `/dev/null` 时，`input()` 会抛 EOFError 把运行搞崩——不如直接停，轨迹里留下 LimitsExceeded 死因。
- 有终端才问新限额，然后**递归重查**（94 行）：新限额生效，这次调用不算浪费。

## 五、step：Ctrl-C 不是终止，是插话（109-122 行）

```python
try:
    console.print(Rule())                          # :112  终端分隔线
    return super().step()
except KeyboardInterrupt:
    interruption_message = self._prompt_and_handle_slash_commands(
        "Interrupted. Type a comment/command (/h for available commands) > ").strip()  # :115-119
    if not interruption_message or interruption_message in self._MODE_COMMANDS_MAPPING:
        interruption_message = "Temporary interruption caught."              # :120-121
    self._interrupt(f"Interrupted by user: {interruption_message}")          # :122
```

Ctrl-C 落在 step 任何深处（模型调用中、命令执行中）都会飞到这里。用户输入一句评论，`_interrupt`（40-41 行）抛 `UserInterruption`，携带 `role="user"` 的消息；`run` 的 except 分支追加后循环继续——**模型下一轮就能读到你的话**。输入为空或只是切模式，则补一句 "Temporary interruption caught." 让模型知道被打断过但没什么要说的。

这就是"steering"的极简实现：没有专门的消息通道、没有队列，复用异常控制流 + 线性历史。

## 六、execute_actions：确认、拒绝与提交把关（124-139 行）

```python
try:
    self._ask_confirmation_or_interrupt(commands)    # :130
    for action in actions:
        outputs.append(self.env.execute(action))   # :131-132
except Submitted as e:
    self._check_for_new_task_or_submit(e)          # :133-134
finally:
    result = self.add_messages(*self.model.format_observation_messages(message, outputs, ...))  # :135-138
```

**try/finally 保住部分输出**：假设 3 个动作，第 2 个触发 Submitted——`outputs` 里已有前 2 个的结果，finally 保证它们进轨迹（没执行的由 m06 的 padding 补齐）。用户中途拒绝同理：已执行的部分不丢。

### 6.1 确认逻辑（162-182 行）

`_should_ask_confirmation`（162-163 行）：`mode == "confirm"` 且不匹配任何白名单正则。注意是 `re.match`（前缀匹配）且**任一命令需要确认就整批问**（166 行）。

提示处的三种回答（173-182 行）：

| 输入 | 效果 |
|---|---|
| 回车 / `/y` | 确认执行（`/y` 同时永久切到 yolo） |
| `/u` | 拒绝执行 + 切 human 模式（`UserRejection` 插话） |
| 其他文本 | 拒绝执行，文本作为拒绝理由插话给模型 |

拒绝不是报错，是**对话**：模型下一轮读到 "The user rejected your commands with the following message: ..."（180 行），可以改方案。

### 6.2 提交把关（144-160 行）

模型发出哨兵命令后，`confirm_exit=True` 时先问用户（147-153 行）："Agent wants to finish. Type new task or Enter to quit"。

- 回车 → 放行 `Submitted`，任务真结束；
- 输入新任务 → 抛 `UserInterruption`（`itype="UserNewTask"`，159 行），**循环继续，模型带着新任务接着干**；
- `/u` → 切 human 模式继续（154-155 行）；
- `/y` `/c` → 切完模式再问一遍（156-157 行递归）。

`mini -y`（yolo）搭配 `--exit-immediately` 会把 `confirm_exit` 关掉（`mini.py:81`），无人值守跑批就靠这个组合。

## 七、斜杠命令与输入会话（184-209 行）

所有提示都过 `_prompt_and_handle_slash_commands`，统一处理五个斜杠命令：

| 命令 | 作用 | 实现 |
|---|---|---|
| `/y` `/c` `/u` | 切 yolo / confirm / human | 查 `_MODE_COMMANDS_MAPPING`（34 行），改 `config.mode` |
| `/h` | 打印当前模式与命令帮助，然后重新提示 | 192-200 行 |
| `/m` | 转入多行输入 | 190-191 行递归调 `_multiline_prompt` |

两个小机关：已在目标模式时提示 "Already in ... mode" 并重新问（202-205 行）；切模式成功返回斜杠命令本身，让调用方（如确认逻辑）知道刚切了模式。

输入会话在 `prompt_user.py`：全局共享一个 `FileHistory`（7 行），历史落盘在全局配置目录的 `interactive_history.txt`——**跨运行记住你输过的命令**，方向键翻历史、Ctrl-R 搜索（12-20 行的多行提示工具栏有写）。

## 八、打印：add_messages 的顺差（43-56 行）

每条消息入列时打印：assistant 消息带步数与累计成本横幅（48-52 行），其他 role 绿字标题。内容提取靠 `get_content_string`（`models/utils/content_string.py:32-87`）——它要兼容字符串 content、多模态块、Anthropic tool_use、OpenAI tool_calls、Responses API output 等七种形状（37-43 行 docstring），m10 的 inspector 也复用它。**一处提取，两处展示**。

## 九、小结

- 四个覆写开四个口子：打印（add_messages）、human/提额（query）、Ctrl-C 插话（step）、确认/提交把关（execute_actions）。
- human 模式的本质：伪造带 `extra.actions` 的 user 消息，下游协议无感。
- Ctrl-C → UserInterruption → 追加 user 消息 → 循环继续；拒绝执行也是对话而非报错。
- 提额的两个例外都写了注释：墙钟不可提额、无终端不可提问——无人值守场景必须干净地停。
- try/finally + 观察 padding：任何中断下已执行部分的输出都不丢。
- 下一章退后一步，看这一切的配置从哪里来：YAML、UNSET 与三个工厂。
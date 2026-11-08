# 消息与模板 —— 一切皆 dict，渲染靠 Jinja

> 本章源文件：`src/minisweagent/agents/default.py:52-72`、`src/minisweagent/utils/serialize.py`、`src/minisweagent/config/mini.yaml`、`src/minisweagent/config/default.yaml`。

## 一、引子：模型看到的第一个字是怎么来的

agent 发给模型的每条消息都是普通的 Python dict；提示词不是写在代码里的字符串，而是 YAML 里的 Jinja 模板。这一章回答"模型到底看到了什么"。读完你应该能回答三个问题：

1. 消息里的 `extra` 字段是什么？为什么发给 API 前要剥掉它？
2. 模板里的 `{{task}}`、`{{system}}`、`{{model_cost}}` 分别从哪来？合并顺序是什么？
3. 命令输出太长时，模型看到的是什么？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| system / instance 模板 | 第一条（系统人设）与第二条（任务描述）消息的模板 |
| observation 模板 | 把执行结果渲染成消息的模板 |
| format_error 模板 | 解析失败时渲染纠错指引的模板 |
| 模板变量（template vars） | 渲染时可用的全部键值，来自六层合并 |
| `extra` | mini 附加在原生消息上的元数据袋，不进 API |

## 三、全景：一条消息的生命周期

```text
YAML 模板（config/mini.yaml）
   │  Jinja 渲染（StrictUndefined）         default.py:66-67
   │  变量 = 六层递归合并                    default.py:52-64
   ▼
model.format_message(role=..., content=...)  litellm_model.py:137-138
   │  （可选）多模态展开
   ▼
messages 列表（线性历史，只增不减）           default.py:42
   │
   ├─→ 落盘：原样进 traj.json                default.py:177
   └─→ 发 API：剥掉 extra + 兼容性重排        litellm_model.py:76-79
```

**同一份消息，两个视图**：轨迹里是全量（含 extra），API 请求里是净版。v2 的设计是"以模型原生输出为基底，只加 `extra`"（`v2_migration.md:122`）——所以消息结构随模型/API 不同而不同，解析轨迹时要按模型类型适配（`v2_migration.md:127-130`）。

## 四、消息的结构：原生格式 + extra 袋

### 4.1 五种 role

| role | 谁产生 | 内容 |
|---|---|---|
| `system` | run 开场 | 渲染后的 system_template |
| `user` | run 开场 / 纠错 / 用户插话 / textbased 观察 | 任务、指引、命令输出 |
| `assistant` | 模型返回 | 推理文本 + tool_calls（原生格式） |
| `tool` | toolcall 路线的观察 | 命令输出，带 `tool_call_id`（`actions_toolcall.py:105-107`） |
| `exit` | 各控制流异常 | 停止哨兵，`extra` 含 `exit_status`/`submission` |

`exit` 是 mini 自创的 role，**永远不会发给模型**——循环在 122 行（`default.py`）看到它就 break 了。

### 4.2 extra 里装什么

以模型返回的助手消息为例（`litellm_model.py:100-105`）：

```python
message["extra"] = {
    "actions": actions,                    # 解析好的动作列表，agent 只认这个
    "response": response.model_dump(),     # 完整原始响应，调试/审计用
    **cost_output,                         # {"cost": ...}
    "timestamp": time.time(),
}
```

观察消息的 extra（`actions_toolcall.py:97-103`）：`raw_output`（未截断的原始输出）、`returncode`、`timestamp`、`exception_info`。**模板渲染出的是给模型看的精简版，extra 里留的是给人事后看的完整版**——比如输出被截断时，模型看到 head/tail，轨迹里的 `raw_output` 仍是全量。

发 API 前的剥离只有一行（`litellm_model.py:77`）：

```python
prepared = [{k: v for k, v in msg.items() if k != "extra"} for msg in messages]
```

## 五、模板变量：六层合并，后者为王

`get_template_vars`（`default.py:52-64`）是全书最重要的"数据从哪来"答案：

```python
return recursive_merge(
    self.config.model_dump(),        # 1. agent 配置（step_limit、cost_limit...）
    self.env.get_template_vars(),    # 2. 环境贡献
    self.model.get_template_vars(),  # 3. 模型贡献
    {                                # 4. 运行时统计
        "n_model_calls": self.n_calls,
        "model_cost": self.cost,
        "elapsed_seconds": int(time.time() - self._start_time),
    },
    self.extra_template_vars,        # 5. run(task, **kwargs) 传入
    kwargs,                          # 6. 本次调用临时传入
)
```

**顺序即优先级**：越靠后越优先。所以 `run("修 bug", system="Linux")` 能覆盖环境探测到的同名变量。

各方贡献的内容：

| 来源 | 变量 | 代码 |
|---|---|---|
| agent 配置 | `system_template`、`step_limit`、`cost_limit` 等全部配置键 | `default.py:54` |
| LocalEnvironment | 配置键 + `platform.uname()`（`system`/`release`/`version`/`machine`...）+ **整个 os.environ** | `local.py:58-59` |
| DockerEnvironment | 配置键 + `platform.uname()`（**不含**宿主环境变量） | `docker.py:61-62` |
| 模型 | 模型配置键（`model_name`、`model_kwargs`...） | `litellm_model.py:153-154` |
| 运行时 | `n_model_calls`、`model_cost`、`elapsed_seconds` | `default.py:57-61` |
| run 调用 | `task` + 任意 kwargs | `default.py:90` |

两个值得注意的点：

- 本地环境的模板里可以写 `{{HOME}}`、`{{PATH}}`——因为 `os.environ` 整个倒进了变量池（`local.py:59`）。Docker 环境刻意不倒，避免宿主信息泄漏进容器任务的提示词。
- `mini.yaml:51-53` 的 `<system_information>{{system}} {{release}} {{version}} {{machine}}</system_information>` 就是靠 uname 那层填上的——模型由此知道自己在什么操作系统上，模板甚至按 `system == "Darwin"` 切换 sed 语法教学（`mini.yaml:69-73`）。

### 5.1 recursive_merge：合并的具体规则

`utils/serialize.py:6-29`，规则三条：

1. `None` 字典跳过（17-18 行）；
2. 值为 `UNSET`（`serialize.py:3` 定义的模块级哨兵对象）的键跳过（20-21 行）——CLI 未设置的选项靠它不覆盖 YAML（m09）；
3. 两边都是 dict 就递归合并（22-23 行），否则后者直接覆盖（27-28 行）。

注意**列表不合并，只覆盖**——`whitelist_actions` 这类列表配置在 YAML 里写了就是整组替换。

### 5.2 StrictUndefined：写错变量名立刻炸

`_render_template`（`default.py:66-67`）：

```python
return Template(template, undefined=StrictUndefined).render(**self.get_template_vars())
```

Jinja 默认对未定义变量渲染成空字符串；`StrictUndefined` 则直接抛 `UndefinedError`。这是刻选择：**提示词里悄悄少一块比响亮地报错危险得多**。观察模板、错误模板同样全部用 StrictUndefined（`actions_toolcall.py:44/69/92`、`actions_text.py:30/53`）。

## 六、两份内置模板：toolcall 与 textbased 的对照

配置目录里 `mini.yaml`（CLI 默认，toolcall 路线）与 `default.yaml`（hello_world 默认，textbased 路线）是同一份提示词的两种方言。

### 6.1 system_template：一句话 vs 一段格式法

`mini.yaml:2-3` 的 system 只有一句 "You are a helpful assistant that can interact with a computer."——格式约束交给原生 tool call schema 强制。

`default.yaml:2-17` 的 system 则长篇规定格式：必须恰好一个 ```` ```mswea_bash_command ```` 代码块、前面要有 THOUGHT 段、附带格式示例——因为 textbased 路线没有 API 层面的约束，只能靠提示词恳求模型守规矩。**约束放在哪一层，是两条路线最本质的区别**（m06 展开）。

### 6.2 instance_template：同构的五段式

两份的任务模板结构一致（`mini.yaml:4-100`）：

1. `Please solve this issue: {{task}}` —— 任务注入点；
2. Recommended Workflow：读代码 → 写复现脚本 → 改源码 → 再跑脚本 → 测边界 → 提交（六步）；
3. Command Execution Rules：子壳无持久状态的告知与绕过技巧；
4. `<system_information>`：uname 变量注入；
5. Useful command examples：heredoc 写文件、sed 编辑、`nl -ba | sed -n` 看行——**把工具用法写进提示词，而不是实现成工具**，这是 m01 支柱一的具体手法。

### 6.3 observation_template：截断的艺术

命令输出可能无限长，模板负责截断。`mini.yaml:112-128`（toolcall 版，JSON 风格）：

```text
输出 < 10000 字符：{"returncode": ..., "output": 全量}
输出 ≥ 10000 字符：{"returncode": ..., "output_head": 前5000, "output_tail": 后5000,
                    "elided_chars": N, "warning": "Output too long."}
```

`default.yaml:114-141`（textbased 版，XML 风格）阈值相同，但多了给模型的建议：用 head/tail/sed 少看一点、grep 写精确一点、或者重定向到文件再搜（`default.yaml:124-130`）。

**头尾保留、中间挖掉**是刻意的：命令输出的关键信息（报错、总结）通常在两端。配合 extra 里的 `raw_output` 全量留存，模型看精简版、人看完整版，两不耽误。

### 6.4 format_error_template：区分"真错误"与"被截断"

`mini.yaml:129-149` 有个精妙的分支（130 行）：

```text
{% if finish_reason == "length" or (finish_reason == "tool_calls" and not has_tool_calls) %}
    → "你的输出被 token 上限截断了，说简短点，以一个 bash tool call 结尾"
{% else %}
    → 通用格式纠错 + 正确调用示例
{% endif %}
```

模型没产出工具调用，**不一定是格式错误，也可能是话太长被截了**（`finish_reason="length"`）。两种情况的纠正策略完全不同：前者要教格式，后者要教模型长话短说。模板变量 `finish_reason` 由解析器传入（`actions_toolcall.py:31-37` 的 `template_kwargs`），Responses API 路线还专门做了状态映射（`actions_toolcall_response.py:45-55`）。

## 七、小结

- 消息 = 模型原生格式 + `extra` 元数据袋；轨迹存全量，API 发净版（剥 extra 仅一行）。
- 模板变量六层合并：agent 配置 → 环境 → 模型 → 运行时统计 → run 传入 → 临时传入，后者为王；列表不合并只覆盖。
- 一切模板用 `StrictUndefined`：变量写错立刻抛错，绝不悄悄渲染成空。
- 观察截断策略：10000 字符阈值、头 5000 + 尾 5000、extra 留全量；格式错误模板区分截断与真错误。
- 下一章看这些消息如何变成一次真实的 API 调用：重试、计费、缓存标记。
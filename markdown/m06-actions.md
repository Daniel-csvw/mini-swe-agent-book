# 动作解析 —— 一个 bash 工具打天下

> 本章源文件：`models/utils/actions_toolcall.py`（113 行）、`models/utils/actions_text.py`（70 行）、`models/utils/actions_toolcall_response.py`（141 行）、`models/litellm_textbased_model.py`（48 行）、`models/litellm_response_model.py`（98 行）、`models/utils/openai_multimodal.py`。

## 一、引子：模型说"跑这条命令"，有几种说法

m01 说过 mini 只有 bash 一个工具。但"模型如何表达要跑命令"这件事，项目里其实养了**三条路线**：原生 tool call（chat completions）、正则解析文本（textbased）、Responses API 的 function_call。三条路线解析器不同、观察格式不同，但产出的 action 完全一致——agent 无感。读完你应该能回答三个问题：

1. 三条路线分别怎么解析动作？各自的格式错误长什么样？
2. 为什么 textbased 要求"恰好一个"动作，toolcall 却允许若干个？
3. 执行到一半抛了 Submitted，没执行的动作在轨迹里怎么交代？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| tool call | chat completions 原生函数调用：`tool_calls[i].function.{name, arguments}` |
| textbased | v1 遗留路线：从文本里正则抠 ```` ```mswea_bash_command ```` 代码块 |
| Responses API | OpenAI 新一代端点：`output` 数组里 `type="function_call"` 项 |
| action | 三条路线的统一产物：`{"command": ..., "tool_call_id": ...}`（textbased 无 id） |
| observation | 执行结果消息；toolcall 路线 `role="tool"`，textbased 路线 `role="user"` |

## 三、全景：三条路线一张表

| | toolcall（默认） | textbased | Responses API |
|---|---|---|---|
| 模型类 | `LitellmModel` | `LitellmTextbasedModel` | `LitellmResponseModel` |
| API 调用 | `litellm.completion(tools=[BASH_TOOL])` | `litellm.completion()`（无 tools） | `litellm.responses(tools=[BASH_TOOL_RESPONSE_API])` |
| 动作来源 | `message.tool_calls` | 正则匹配 content | `output` 里的 `function_call` 项 |
| 解析器 | `parse_toolcall_actions` | `parse_regex_actions` | `parse_toolcall_actions_response` |
| 动作数量 | ≥1，可多个 | **恰好 1 个** | ≥1，可多个 |
| 观察 role | `tool`（带 tool_call_id） | `user` | `function_call_output`（带 call_id） |
| 适用模型 | 支持 function calling 的模型 | **任何模型** | 支持 Responses API 的模型 |
| 配置 | `mini.yaml` | `mini_textbased.yaml` / `default.yaml` | `model_class: litellm_response` |

路线选择就是换模型类——v2 把解析职责下沉到模型侧的意义在此（`v2_migration.md:166`）：agent 一行不改。

## 四、路线一：toolcall（默认）

### 4.1 BASH_TOOL：全部家当（actions_toolcall.py:11-27）

```python
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash command",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The bash command to execute"}},
            "required": ["command"],
        },
    },
}
```

一个工具、一个参数。没有 `read_file`、没有 `edit_file`、没有 `search`——模型要用 bash 自己组合出这些能力。schema 层面的约束（必须有 command 字符串）由 API 保证，这是 toolcall 路线比提示词恳求可靠的地方。

### 4.2 解析：三道校验（parse_toolcall_actions，30-76 行）

**第零道**：没有工具调用直接 FormatError（40-52 行），纠错消息写明 "Every response MUST include at least one tool call"。

然后对每个 tool_call 做三道校验（54-74 行），错误**拼接**而非短路：

```python
args = json.loads(tool_call.function.arguments)     # :58  ① 参数必须是合法 JSON
if tool_call.function.name != "bash": ...           # :61  ② 只认识 bash
if not isinstance(args, dict) or "command" not in args: ...  # :63  ③ 必须有 command
```

注意 61 行：模型若幻觉出别的工具名（比如 `python`），得到的不是执行错误而是格式纠错——**工具表之外的世界不存在**。任何一道不过就抛 FormatError，消息由 `format_error_template` 渲染（m04 的截断分支在此生效）。

全部通过才产出 action（75 行）：

```python
actions.append({"command": args["command"], "tool_call_id": tool_call.id})
```

`tool_call_id` 必须随观察消息带回（API 靠它配对调用与结果），这是 toolcall 路线的记账凭证。

### 4.3 为什么允许许多个

`mini.yaml:38` 要求 "AT LEAST ONE bash tool call"，swebench 配置更开了 `parallel_tool_calls: true`（`swebench.yaml:183`）并在提示词里鼓励独立命令并行发（`swebench.yaml:55`）。agent 侧逐条顺序执行（`default.py:156`）——**"并行"是模型一次多说几条，不是真的并发执行**。顺序执行保证了输出的确定性与哨兵检查的简单性。

## 五、路线二：textbased（任何模型都能跑）

### 5.1 子类只改三处（litellm_textbased_model.py，48 行）

`LitellmTextbasedModel` 继承 `LitellmModel`，差异屈指可数：

1. 配置加 `action_regex`，默认 `r"```mswea_bash_command\s*\n(.*?)\n```"`（8 行）；
2. `_query` 不传 tools（20-27 行）；
3. `_parse_actions` 换用正则解析（29-37 行）；观察格式化换成 user 消息版（39-48 行）。

重试、计费、缓存标记、FormatError 契约全部继承——**这就是"每个类都简单，多态提供选择"的实证**。

### 5.2 恰好一个（actions_text.py:15-40）

```python
actions = [a.strip() for a in re.findall(action_regex, content, re.DOTALL)]   # :24
if len(actions) != 1:                                                         # :25
    raise FormatError(... f"Expected exactly 1 action, found {len(actions)}." ...)
```

为什么 toolcall 允许多个而这里只许一个？看错误消息的 extra（33-37 行）：`n_actions` 和 `model_response` 都记下了。文本路线里多个代码块的执行顺序、部分失败语义都不好界定；v1 时代干脆约定"恰好一个"，把多命令的需求挤进 `&&` 复合命令（`default.yaml:5` 明说 "or commands connected with && or ||"）。**约束即简化**。

另一个 v2 细节：代码块标签从 v1 的 ```` ```bash ```` 改成 ```` ```mswea_bash_command ````（`v2_migration.md:48`）——避免和提示词里的 bash 示例冲突，模型复述示例时不再被误解析。

### 5.3 观察是 user 消息（actions_text.py:43-70）

没有 tool_call_id 可带，观察以 `role="user"` 追加（57 行）。模型看到的是"用户告诉我命令结果"，而非"工具返回"。extra 里同样留 `raw_output`/`returncode`/`timestamp` 全量（59-65 行）。

## 六、路线三：Responses API

OpenAI 的 Responses API 消息形状不同：没有 `choices[0].message`，而是 `output` 数组；工具调用项是平铺的 `{"type": "function_call", "call_id": ..., "name": "bash", "arguments": ...}`（`actions_toolcall_response.py:64-65`）。

三个适配点：

1. **工具 schema 平铺**：`BASH_TOOL_RESPONSE_API` 没有嵌套的 `function` 键（11-25 行，注释明说）；
2. **历史扁平化**：`_prepare_messages_for_api` 把轨迹里的 response 对象拆成 output 项再发（`litellm_response_model.py:29-38`）——无状态端点，每次全量重放；
3. **finish_reason 映射**：Responses API 用 `status="incomplete"` + `incomplete_details.reason="max_output_tokens"` 表示截断，`finish_reason_from_responses_api`（45-55 行）把它映射成 `"length"`，让 m04 那份格式错误模板无需改动照常工作。

解析校验逻辑与 toolcall 版同构（58-104 行），观察消息是 `type="function_call_output"` + `call_id`（132-135 行）。`LitellmResponseModel.query`（52-78 行）完整继承了重试与 FormatError 契约，只多了 dict/pydantic 双兼容的序列化兜底（62-71 行注释）。

## 七、观察格式化：未执行的动作也要有交代

三条路线的格式化器共享一个关键设计，以 toolcall 版为例（`actions_toolcall.py:88-89`）：

```python
not_executed = {"output": "", "returncode": -1, "exception_info": "action was not executed"}
padded_outputs = outputs + [not_executed] * (len(actions) - len(outputs))
```

场景：模型发了 3 个动作，第 2 个触发 Submitted 哨兵，第 3 个没执行。但 API 要求**每个 tool_call_id 都必须有对应的 tool 结果**，否则下一轮请求会被拒。padding 保证消息配对完整，同时用 `exception_info` 如实告诉模型"这条没跑"。

交互 agent 里这个性质还有第二层保障：`execute_actions` 的 try/finally（`interactive.py:129-138`）即使用户中途拒绝，已执行部分的输出也会格式化进轨迹（m08）。

role 分派（105-109 行）：action 带 `tool_call_id` → `role="tool"`；不带（human 模式用户手输的命令）→ `role="user"`。一个 if 兼容了两种动作来源。

## 八、多模态：一个正则开关

`openai_multimodal.py` 提供可选的图像注入：消息文本里写

```text
<MSWEA_MULTIMODAL_CONTENT><CONTENT_TYPE>image_url</CONTENT_TYPE>https://... 或 data:...</MSWEA_MULTIMODAL_CONTENT>
```

配置 `multimodal_regex`（默认空 = 关闭，`litellm_model.py:45-46`）后，`expand_multimodal_content`（34-50 行）递归地把匹配段展开成 OpenAI 多模态 content 块（`{"type": "image_url", ...}`）。`format_message` 与观察格式化都会过这道（`litellm_model.py:137-138`、`actions_toolcall.py:110-111`）。SWE-bench Multimodal 子集靠它把截图喂给模型。

## 九、小结

- 三条路线一个产物：`{"command", "tool_call_id"?}`。换路线 = 换模型类，agent 零改动。
- toolcall：schema 约束 + 三道校验（JSON/工具名/command），允许多动作，顺序执行；textbased：正则 + 恰好一个，任何模型可用；Responses API：平铺 schema + 历史扁平化 + finish_reason 映射。
- 观察必须配对：未执行的动作 padding 成 "action was not executed"，既满足 API 又如实告知模型。
- 多模态是默认关闭的正则开关，不改消息主流程。
- 下一章看命令真正执行的地方：本地子壳、Docker 容器，以及那个著名的哨兵命令。
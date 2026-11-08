# 模型调用 —— 重试、计费与格式错误契约

> 本章源文件：`src/minisweagent/models/litellm_model.py`（164 行）、`models/utils/retry.py`、`models/utils/cache_control.py`、`models/utils/anthropic_utils.py`、`models/__init__.py`。

## 一、引子：一次 query 背后的五件事

`DefaultAgent.query` 里只有一行 `self.model.query(self.messages)`（`default.py:149`）。这一行背后，`LitellmModel` 要做五件事：清洗消息、重试调用、核算成本、解析动作、组装返回。任何一件出错都有讲究。读完你应该能回答三个问题：

1. 哪些错误会重试、哪些立刻放弃？重试的节奏是什么？
2. 成本是怎么算出来的？算不出来会怎样？全局限额和实例限额有什么区别？
3. 解析失败时，为什么异常里必须塞进原始响应和成本？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| litellm | 统一上百家模型 API 的适配层，`completion()` 一个入口打天下 |
| abort exceptions | 不重试、立刻抛出的异常清单 |
| cache control | Anthropic 的显式提示缓存标记，省钱关键 |
| FormatError 契约 | 解析失败时异常必须携带 cost 与 response 的约定 |
| GLOBAL_MODEL_STATS | 进程级成本/调用计数，跨所有限额实例共享 |

## 三、全景：query() 的流水线（81-106 行）

```text
query(messages)
│
├─ ① _prepare_messages_for_api        :76-79
│     剥 extra → thinking 块重排 → 缓存标记
│
├─ ② retry 包装的 _query              :82-84
│     litellm.completion(model, messages, tools=[BASH_TOOL])  :66-71
│     最多 10 次，指数退避 4~60s，abort 清单除外
│
├─ ③ _calculate_cost                  :85 → :108-126
│     litellm.cost_calculator.completion_cost
│     GLOBAL_MODEL_STATS.add(cost)    :86（全局限额在这里触发）
│
├─ ④ _parse_actions                   :89 → :128-135
│     成功 → actions 列表
│     失败 → FormatError：回填 cost + response 后抛出  :90-98
│
└─ ⑤ 组装消息                          :99-106
      response.choices[0].message.model_dump()
      + extra{actions, response, cost, timestamp}
```

## 四、① 消息清洗：三步流水线（76-79 行）

```python
def _prepare_messages_for_api(self, messages: list[dict]) -> list[dict]:
    prepared = [{k: v for k, v in msg.items() if k != "extra"} for msg in messages]  # :77
    prepared = _reorder_anthropic_thinking_blocks(prepared)                          # :78
    return set_cache_control(prepared, mode=self.config.set_cache_control)           # :79
```

**第一步剥 extra**（m04 讲过）。第二步和第三步都是 Anthropic 兼容性补丁。

### 4.1 thinking 块重排（anthropic_utils.py:11-28）

Anthropic 要求 assistant 消息里 thinking 块必须在其他块之前。轨迹里的消息顺序是模型原生输出，重放时可能不满足这个约束，于是发送前重排：thinking 块提前（24 行）；如果一条消息**只有** thinking 块，补一个空 text 块兜底（26 行）。不改轨迹本身，只改发送副本。

### 4.2 缓存标记：只标最后一条（cache_control.py:49-67）

`set_cache_control` 的 `default_end` 模式逻辑出人意料地简单（62-66 行）：**倒序遍历，清掉所有旧标记，只给最新一条消息打上 `cache_control: {"type": "ephemeral"}`**。

为什么只标一条？Anthropic 的缓存语义是"标记点之前的全部内容可缓存"。标在最后一条 = 整个历史都是缓存前缀，下一轮只需为新增消息付全价。每轮标记位置后移，前缀持续命中——这就是长会话省钱的机制。

两个兼容细节：content 为 None 的纯 tool_use 消息直接在消息级打标（29-31 行）；`role="tool"` 的消息要把标记从 content 块挪到消息级（43-46 行，注释自称 "Workaround for weird bug"）。

**默认开关在工厂里**：`get_model` 发现模型名含 anthropic/sonnet/opus/claude 且用户没显式配置时，自动设 `set_cache_control="default_end"`（`models/__init__.py:55-60`）。用 Anthropic 模型而不自知地省了钱，就是这一行。

## 五、② 重试：tenacity 的薄包装（retry.py:19-25）

```python
return Retrying(
    reraise=True,
    stop=stop_after_attempt(int(os.getenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "10"))),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    retry=retry_if_not_exception_type(tuple(abort_exceptions)),
)
```

- 最多 **10 次**（环境变量可调），指数退避 **4 秒起步、60 秒封顶**；
- `reraise=True`：重试耗尽后抛原始异常，不包 tenacity 自己的 RetryError；
- 每次重试前 WARNING 级日志。

**abort 清单**（`litellm_model.py:50-57`）是"重试也没用"的集合：

| 异常 | 为什么不重试 |
|---|---|
| `UnsupportedParamsError` | 参数不被该模型支持，重试一万次也一样 |
| `NotFoundError` | 模型名写错了 |
| `PermissionDeniedError` / `AuthenticationError` | 权限/key 问题 |
| `ContextWindowExceededError` | 历史超上下文——重试不会变短（m03 的"不剪枝"立场在此收场） |
| `KeyboardInterrupt` | 用户要停 |

其余异常（网络抖动、429 限流、5xx）都会重试。认证错误还有一处人性化处理：`_query` 捕获 `AuthenticationError` 后在消息尾部追加"用 `mini-extra config set KEY VALUE` 永久设置 key"的提示再抛出（72-74 行）。

## 六、③ 计费：一笔成本，两处限额

### 6.1 单次成本核算（108-126 行）

```python
cost = litellm.cost_calculator.completion_cost(response, model=self.config.model_name)  # :110
if cost <= 0.0:
    raise ValueError(...)                                                               # :111-112
```

litellm 维护各模型价目表，按 response 里的 usage 算钱。算不出来（模型未注册、本地模型无价格）或算出非正值时的行为由 `cost_tracking` 配置决定（113-125 行）：

- `"default"`：打 CRITICAL 日志并抛 `RuntimeError`，错误信息里给出三条出路（配置 `ignore_errors`、环境变量 `MSWEA_COST_TRACKING`、读本地模型文档）；
- `"ignore_errors"`：成本记 0，继续跑。

**宁可响亮地失败，也不默不作声地免费**——默认策略防止你在"以为有成本护栏"的情况下裸奔。本地模型/自托管模型记得配 `litellm_model_registry`（32-33 行）注册价格，或直接 `ignore_errors`。

### 6.2 全局统计与全局限额（models/__init__.py:13-42）

每次调用后 `GLOBAL_MODEL_STATS.add(cost)`（`litellm_model.py:86`）。这个进程级单例（42 行）：

- 用 `threading.Lock` 保护累计（27-29 行）——m11 的多线程批量跑全靠它；
- 支持两个环境变量限额：`MSWEA_GLOBAL_COST_LIMIT`、`MSWEA_GLOBAL_CALL_LIMIT`（20-21 行）；
- 触发条件是 `0 < cost_limit < self._cost or 0 < call_limit < self._n_calls + 1`（30 行）——注意 **`+1`**：调用次数限额是"预判下一次会超就现在停"，比成本限额（花超了才停）更保守一格；
- 超限抛 `RuntimeError`——这不是 `InterruptAgentFlow`，会走 `run` 的未捕获异常分支：记录轨迹、re-raise、进程退出（m03）。

**两级限额的关系**：实例级 `cost_limit`（默认 $3）管单个任务；全局级管整个进程。批量跑 500 个 SWE-bench 实例时，单实例 $3 × 500 = $1500 的保险丝就是全局限额。

## 七、④ FormatError 契约：失败也要留下证据

87 行的注释是契约原文：

```python
# Note: all model.query() implementations must persist the response and cost on FormatError.
```

履行代码（90-98 行）：

```python
except FormatError as e:
    e.messages[0]["extra"].update(cost_output)                       # :91  回填成本
    try:
        e.messages[0]["extra"]["response"] = response.model_dump(mode="json")  # :93
    except Exception:
        e.messages[0]["extra"]["response"] = repr(response)          # :97  序列化失败也要留 repr
    raise
```

为什么必须这样？回忆 m03 的账本：FormatError 绕过了 `query()` 的正常计费点，agent 只能从 `e.messages[0].extra.cost` 补记（`default.py:102`）。而 `response` 的留存是为了**事后能回答"模型到底回了什么"**——格式错误是调试提示词的第一现场，没有原始响应就只能猜。97 行的 repr 兜底保证契约无条件成立：哪怕响应对象序列化不了，也留下可读的痕迹。

自定义模型类必须履行同样的契约（m12 检查清单里有这一条）。

## 八、⑤ 组装返回与工厂

成功路径的组装（99-106 行）m04 已讲：原生 message + extra 四件套。最后看工厂 `get_model`（`models/__init__.py:45-62`）如何把配置变成实例：

1. **模型名三级解析**（`get_model_name`，65-75 行）：显式参数 > 配置里的 `model_name` > `MSWEA_MODEL_NAME` 环境变量；都没有则抛错并指引跑 `mini-extra config setup`；
2. **模型类选择**（`get_model_class`，92-113 行）：`model_class` 配置优先，短名查 `_MODEL_CLASS_MAPPING`（78-89 行，10 个注册名），否则当全限定 import 路径；都没给就默认 `LitellmModel`；
3. Anthropic 自动缓存标记（55-60 行，本章 4.2 节）；
4. `model_class(**config)` 实例化。

模型家族一览（映射表 78-89 行）：`litellm`（默认）、`litellm_textbased`、`litellm_response`、`openrouter` 三变体、`portkey` 两变体、`requesty`、`deterministic`（测试用）。三条解析路线的差异是下一章的主题。

## 九、小结

- 一次 query 五件事：清洗（剥 extra/thinking 重排/缓存标记）→ 重试调用 → 计费 → 解析 → 组装。
- 重试 10 次、4~60s 指数退避；参数错误、认证失败、上下文超限等"重试无意义"异常直接放弃。
- 成本默认必须算得出，算不出就抛错；实例限额管单任务，全局限额（线程安全）管整进程。
- FormatError 契约：异常必须带 cost 与 response——agent 补记账，人类留证据。
- Anthropic 模型自动获得"只标最后一条"的缓存策略，长会话持续命中前缀。
- 下一章：解析动作的三条路线，以及"一个 bash 工具"的完整含义。
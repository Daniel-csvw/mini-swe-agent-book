# 轨迹持久化 —— traj.json 与 inspector

> 本章源文件：`src/minisweagent/agents/default.py:159-190`、`run/utilities/inspector.py`（316 行）、`models/utils/content_string.py`（87 行）。

## 一、引子：跑完留下的那个文件

每次 `mini` 运行结束（甚至崩溃），磁盘上都会留下一个 `*.traj.json`。它是调试现场、是评测输入、也是微调数据——m01 说"轨迹和 messages 是同一个东西"，落盘格式就是这个性质的载体。读完你应该能回答三个问题：

1. traj.json 的顶层结构是什么？`trajectory_format: "mini-swe-agent-1.1"` 标记在哪？
2. 为什么 agent 每步都保存而不是跑完才保存？崩溃时文件里会有什么？
3. inspector 怎么把一串消息切成"步"来翻页？快捷键体系是什么？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| trajectory | 一次运行的完整记录：消息列表 + 元信息 |
| exit_status | 死因标签：Submitted / LimitsExceeded / TimeExceeded / RepeatedFormatError / 异常类名 |
| submission | 任务产出（SWE-bench 场景就是 git patch 文本） |
| inspector | `mini-extra inspect`，textual 写的终端轨迹浏览器 |
| step（inspector 语义） | 一条助手消息 + 紧随其后的观察消息，作为一页展示 |

## 三、serialize：轨迹的解剖（default.py:159-180）

```python
agent_data = {
    "info": {
        "model_stats": {"instance_cost": self.cost, "api_calls": self.n_calls},   # :165-168
        "config": {"agent": self.config.model_dump(mode="json"),
                   "agent_type": f"{module}.{class}"},                            # :169-172
        "mini_version": __version__,                                              # :173
        "exit_status": last_extra.get("exit_status", ""),                         # :174
        "submission": last_extra.get("submission", ""),                           # :175
    },
    "messages": self.messages,                                                    # :177
    "trajectory_format": "mini-swe-agent-1.1",                                    # :178
}
return recursive_merge(agent_data, self.model.serialize(), self.env.serialize(), *extra_dicts)  # :180
```

顶层三键：`info`、`messages`、`trajectory_format`。要点：

- **exit_status/submission 取自最后一条消息的 extra**（161-162 行）——exit 消息既是循环哨兵又是结果载体，一处两用；
- `agent_type`/`model_type`/`environment_type` 都是全限定类名（`litellm_model.py:156-164`、`local.py:61-69`）——轨迹自描述，事后知道是哪个实现跑出来的；
- 180 行的合并顺序：agent 基底 → 模型元数据 → 环境元数据 → 调用方 extra。m11 的批量跑就是靠最后的 `*extra_dicts` 塞进 `instance_id` 和覆写 exit_status；
- `messages` **原样**落盘，含全部 extra（原始响应、未截断输出、成本、时间戳）——m04 的"两个视图"在这里闭合。

## 四、save：每步都落盘（182-190 行）

```python
def save(self, path: Path | None, *extra_dicts) -> dict:
    data = self.serialize(*extra_dicts)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)   # :188
        path.write_text(json.dumps(data, indent=2))      # :189
    return data
```

调用点在 `run` 的 **finally**（`default.py:120-121`）：每个 step 之后、无论正常还是异常，都写一次。三个后果：

1. **崩溃有现场**：未捕获异常 re-raise 前，finally 已把含 traceback 的 exit 消息写进文件（m03 的 74-86 行）；
2. **可实时围观**：长任务跑着，另一个终端 `mini-extra inspect` 打开同一个文件，按 `R` 刷新就能看到最新步（inspector 的 reload，278-285 行）；
3. **path=None 也工作**：`save(None)` 只返回 dict 不写盘——Python bindings 里拿结构化结果用。

代价是每步全量重写 JSON。对几百步的轨迹这是几十毫秒级开销，相比模型调用的秒级延迟可忽略——又是"简单优先"的取舍。

## 五、inspector：终端里的轨迹浏览器

`mini-extra inspect [path]`（`inspector.py:296-312`）：path 是文件就直接看，是目录就 `rglob("*.traj.json")` 收集全部轨迹排序看（305 行）。textual App，vim 风格键位。

### 5.1 消息怎么切成"步"（_messages_to_steps，25-38 行）

```python
for message in messages:
    if message.get("extra", {}).get("actions") or message.get("role") == "assistant":  # :31
        steps.append(current_step)
        current_step = [message]
    else:
        current_step.append(message)
```

遇到助手消息（或带 actions 的消息——human 模式的用户命令）就开新页，其余消息（观察、纠错、插话）归入当前页。**一页 = 模型的一次决策 + 其全部后果**，正好是回顾时的自然单位。开头的 system/user 两条消息自成第 0 页。

### 5.2 键位表（81-95 行）

| 键 | 动作 | 键 | 动作 |
|---|---|---|---|
| `→/l` `←/h` | 下一步 / 上一步 | `L` `H` | 下一条 / 上一条轨迹 |
| `0` `$` | 首页 / 末页 | `j/k` | 滚动 |
| `e` | 当前步扔进 jless 看 JSON | `E` | 整条轨迹扔进 jless |
| `r` | 显隐 reasoning 内容 | `R` | 从磁盘重载（保留步位置） |
| `q` | 退出 | | |

索引设置器自动夹取边界并回滚顶部（121-127、139-146 行）——翻到头不会炸。`e` 键把当前步 dump 成临时 JSON 调 `jless`（255-272 行），看 extra 里的原始响应最方便。

### 5.3 渲染：复用 get_content_string（195-225 行）

每条消息的展示文本来自 `get_content_string`（m08 提过，`content_string.py:32-87`）：字符串 content、多模态块、tool_use/tool_result、OpenAI tool_calls、Responses output 都能提取；观察 JSON 还会被美化成键值分段（`_format_observation`，17-29 行）。assistant 的 `reasoning_content` 单独分块展示（213-219 行），`r` 键切换。加载时容忍两种文件形状：纯消息列表或带 `messages` 键的 dict（164-169 行）——v1 旧轨迹也能看。

## 六、轨迹的三重身份

把本章串起来，traj.json 在项目里扮演三个角色：

1. **调试现场**：崩溃有 traceback、格式错误有原始响应（m05 契约）、每步落盘可围观；
2. **评测输入**：m11 的批量跑从轨迹取 `submission` 写进 preds.json，exit_status 汇总成报告；
3. **训练数据**：线性历史 + 原生消息格式，意味着轨迹可直接用于微调/RL——README 把这列为设计目标（`README.md:45-47`、`:91`）。

一个格式服务三种用途，靠的是"不加工"这个纪律。

## 七、小结

- 轨迹三键：info（成本/配置/版本/死因/产出）、messages（全量含 extra）、trajectory_format 标记。
- save 在 run 的 finally 里每步执行：崩溃有现场、可实时围观、None 路径纯返回。
- inspector 按"助手消息开新页"切步，vim 键位 + jless 集成 + reasoning 开关 + 热重载。
- 消息文本提取 `get_content_string` 一处实现，CLI 打印与 inspector 两处复用。
- 下一章看轨迹的最大消费者：SWE-bench 批量评测的工程细节。
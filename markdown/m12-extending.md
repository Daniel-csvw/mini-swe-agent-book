# 扩展指南 —— 写你自己的 Model/Environment/Agent

> 本章源文件：`src/minisweagent/__init__.py:43-80`、`models/test_models.py`、`models/extra/roulette.py`、`run/hello_world.py`、`docs/advanced/cookbook.md`。

## 一、引子：不改源码，能走多远

前十一章拆的都是"项目给了什么"。这一章反过来：你想加一个自定义工具、换一种执行后端、插一个校验钩子，需要动源码吗？答案是不需要——协议 + 鸭子类型 + 全路径类名 + config_class 模式，四件套把所有扩展点都留在了包外。读完你应该能回答三个问题：

1. 自定义一个 Model/Environment/Agent，最少要实现什么？有哪些容易漏掉的契约？
2. 测试里不想真调 API，官方给的确定性模型怎么用？
3. 自己的组件写在哪、怎么让 CLI 用上？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| 鸭子类型 | 方法签名对上就能用，无需继承/注册（`v2_migration.md:176-179`） |
| config_class 模式 | 每个组件 `__init__(*, config_class=XxxConfig, **kwargs)`，子类换配置类即加字段 |
| 全路径类名 | `mypackage.mymodule.MyClass`，工厂直接 importlib 加载 |
| run 脚本 | 组装三组件的入口，一切用例的起点（`AGENTS.md`） |

## 三、全景：四个扩展层级

```text
层级 0：改 YAML / 提示词          → 换性格、换格式约束（m04/m09）
层级 1：换组件类（内置）          → --model-class litellm_textbased 等
层级 2：写自己的组件类（包外）     → 本章主菜
层级 3：写自己的 run 脚本         → 完全掌控组装与 CLI
```

## 四、协议清单：最少实现什么

### 4.1 自定义 Environment（最容易）

三个方法（`__init__.py:61-70`）。最小可用实现：

```python
class MyEnvironment:
    def __init__(self, **kwargs):
        self.config = MyConfig(**kwargs)  # 任何带 model_dump() 的配置对象

    def execute(self, action: dict, cwd: str = "") -> dict:
        # 返回 {"output": str, "returncode": int, "exception_info": str}
        ...

    def get_template_vars(self, **kwargs) -> dict: ...
    def serialize(self) -> dict: ...
```

**容易漏掉的契约**：

1. **哨兵检查**：想支持 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 提交，要复制 `_check_finished` 逻辑（`local.py:45-56`）——首行精确匹配 + 返回码 0 + 余行作 submission，抛 `Submitted`；
2. **异常不出环境**：执行失败包成 `returncode: -1` + `exception_info`，别让普通异常穿透（m07）；
3. `serialize()` 返回 `{"info": {"config": {...}}}` 形状，轨迹元数据靠它。

cookbook 的 `EnvironmentWithPythonFunctions`（`cookbook.md:164-171`）示范了最常见改法：拦截特定前缀命令走 Python 函数，其余 `super().execute()`——继承内置类时哨兵等契约自动保留。

### 4.2 自定义 Model（契约最多）

五个方法（`__init__.py:43-58`）。除了 query 本身，三个契约必须履行：

| 契约 | 内容 | 出处 |
|---|---|---|
| 返回形状 | 原生消息 + `extra{actions, cost, timestamp, response?}` | `litellm_model.py:100-105` |
| FormatError 契约 | 解析失败时异常消息的 extra 必须带 cost 与 response | `litellm_model.py:87-98`（m05） |
| 全局统计 | 每次真实调用后 `GLOBAL_MODEL_STATS.add(cost)` | `litellm_model.py:86` |

`format_observation_messages` 决定观察格式：想走 toolcall 格式就复用 `actions_toolcall.py:79-113`，想走 user 消息就复用 `actions_text.py:43-70`——解析与格式化器都是公开工具函数，**不用重写，组装即可**。

### 4.3 自定义 Agent（自由最大）

协议只有 `run` 和 `save`（`__init__.py:73-80`），但实践中继承 `DefaultAgent` 挑方法覆写更划算。四个标准插头（前几章已见全部实例）：

| 覆写点 | 实例 | 用途 |
|---|---|---|
| `step` | `ProgressTrackingAgent`（`common.py:15-17`） | 每步钩子：进度、日志 |
| `query` | `InteractiveAgent.query`（`interactive.py:58`） | 调用前钩子 |
| `execute_actions` | cookbook 的 `ValidatingAgent`（`cookbook.md:246-254`） | 执行前校验/拦截 |
| `add_messages` | `InteractiveAgent.add_messages`（`interactive.py:43`） | 消息入列钩子 |

校验示例（`cookbook.md:235-254`）的写法值得照抄：配置类继承 `AgentConfig` 加 `forbidden_patterns` 字段，`__init__` 传 `config_class=ValidatingAgentConfig`，`execute_actions` 里命中禁则抛 `FormatError`——**拒绝即对话**，模型下轮读到拒绝理由自己改。这就是 config_class 模式（`default.py:39`）的标准用法。

## 五、确定性模型：不调 API 的测试

`models/test_models.py` 的 `DeterministicModel`（104-143 行）按预设序列返回消息：

```python
from minisweagent.models.test_models import DeterministicModel, make_output

model = DeterministicModel(outputs=[
    make_output("先看目录", [{"command": "ls"}]),
    make_output("完成", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]),
])
```

- `make_output`（16-28 行）造好消息壳（role/content/extra 含 actions/cost/timestamp）；
- `query` 逐次推进 `current_index`（110-116 行），同样过全局统计；
- 特殊动作（75-87 行）：`{"raise": 异常实例}` 直接抛、`/sleep N` 睡 N 秒、`/warning` 打日志——测试重试与超时路径的遥控器。

另有 `DeterministicToolcallModel`（160-201 行）与 `DeterministicResponseAPIToolcallModel`（218-269 行）覆盖另两条路线。注册名 `deterministic` 已在工厂映射表（`models/__init__.py:88`）。项目的测试纪律（`AGENTS.md`）：**pytest、不 mock 未明确要求之物**——确定性模型就是"不 mock 也能测"的基础设施。

## 六、元模型：模型组合模型

`models/extra/roulette.py` 展示了协议的另一层威力——**Model 可以包 Model**：

- `RouletteModel`（15-43 行）：配置里给一组 `model_kwargs`，每次 query 随机挑一个转发（25-33 行），响应上贴 `model_name` 标签；
- `InterleavingModel`（54 行起）：按 `sequence` 交替，比如 `[0,0,1]` 表示 A 两次 B 一次循环。

注意它们**并不是完整的 Model**：只实现了 `query`/`get_template_vars`/`serialize`，缺 `format_message` 与 `format_observation_messages`（协议要求见 `__init__.py:50-54`）——直接塞进 DefaultAgent 会在 run 开场渲染系统消息时 AttributeError。这个示例展示的是"Model 包 Model"的组合思路；想做"便宜模型探路、贵模型收尾"的混合策略，以它为模板、把两个格式化方法也转发给被选中的模型，才是能上线的完整版。

## 七、接线：让 CLI 用上你的组件

三条路，按侵入性排序：

1. **全路径类名**：`mini --model-class mypkg.mymodel.MyModel --environment-class mypkg.myenv.MyEnv`——工厂查不到短名就当 import 路径（m09），你的包只要在 Python 路径上即可；
2. **自己的 run 脚本**：照 `hello_world.py`（42 行）组装三组件，`typer` 加 CLI。项目哲学"每个用例从一个 run 脚本开始"（`AGENTS.md`）。注意 `cookbook.md:28` 提到的 `MSWEA_DEFAULT_RUN` 环境变量在 v2.4.6 源码里**并无实现**（`mini` 入口硬编码为 `run.mini:app`，`pyproject.toml:90`）——文档超前于代码，别依赖它；
3. **YAML 里写全路径**：`model_class: mypkg.mymodel.MyModel` 放进配置，效果同 1。

### 7.1 端到端：写一个自定义环境并跑通

把上面的零件连成一条线。新建 `myenv.py`（放在当前目录即可，Python 路径已包含它）：

```python
from minisweagent.environments.local import LocalEnvironment

class MyEnv(LocalEnvironment):
    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        if action["command"].startswith("hello "):
            return {"output": f"hi {action['command'][6:]}", "returncode": 0, "exception_info": ""}
        return super().execute(action, cwd, timeout=timeout)
```

然后照常用 CLI，只是把类名换成全路径：

```bash
mini -c mini.yaml --environment-class myenv.MyEnv -t "先运行 hello world，再用 ls 看目录"
```

这条命令背后发生了什么，前面各章都已拆过：`mini.py:73-92` 把 `environment_class` 合进配置 dict → `get_environment`（`environments/__init__.py:30-33`）弹出该键并 `importlib` 加载 `MyEnv` → 剩下的 `env`/`timeout` 等 YAML 键作为 kwargs 传给 `LocalEnvironment` 的配置类 → agent 照旧调 `env.execute`。**继承 `LocalEnvironment` 让§4 的三个契约（哨兵、异常包成 returncode -1、serialize 形状）在走 `super()` 的命令上自动保留**；只拦截一个前缀，其余照旧。跑完打开轨迹文件，`info.config.environment_type` 会是 `myenv.MyEnv`（m10 的自描述）——从写类到接线到落盘，整条链路闭合。

想更进一步就换自定义 Model：把 `DeterministicModel`（§5）当模板补齐五个方法，再用 `--model-class` 指过去即可；检查清单在下一节。

## 八、检查清单与全书收尾

自定义组件上线前对照：

- [ ] 协议方法齐全（Model 五 / Environment 三 / Agent 二）；
- [ ] Model：extra 四件套、FormatError 带 cost+response、GLOBAL_MODEL_STATS 记账；
- [ ] Environment：哨兵检查、异常包成 returncode -1、serialize 形状；
- [ ] Agent：run 返回 exit 消息的 extra、save 兼容 None 路径；
- [ ] 配置类走 config_class 模式，`model_dump()` 可用；
- [ ] 测试用 DeterministicModel，不 mock。

十二章走完，回到 m01 的问题：为什么一百行能拿 74%？现在的答案具体了——**因为每一行都在做确定性的事**（循环、记账、哨兵、落盘），而所有需要聪明的事（读代码、改 bug、写 patch）都通过那一个 bash 工具让给了模型。脚手架极简，不是功能的缺失，而是责任的清晰。

这，就是 mini-swe-agent 全部源码教给我们的事。
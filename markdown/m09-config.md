# 配置系统 —— YAML、UNSET 与递归合并

> 本章源文件：`src/minisweagent/config/__init__.py`（64 行）、`utils/serialize.py`（29 行）、`run/mini.py`（109 行）、`run/utilities/config.py`（131 行）、三个组件工厂。

## 一、引子：-c 后面能跟什么

`mini -c mini.yaml -c model.model_kwargs.temperature=0.5 -c agent.cost_limit=5` —— 一个 `-c` 既能跟文件名又能跟键值对，多个 `-c` 还能叠加。这套配置系统全部实现加起来不到一百行，却支撑了从 CLI 到基准测试的所有入口。读完你应该能回答三个问题：

1. 一个 config spec 字符串是怎么变成嵌套 dict 的？文件找不到时去哪些地方找？
2. `UNSET` 这个哨兵对象解决了什么问题？为什么不能用 `None`？
3. 配置里的 `model_class: "litellm"` 是怎么变成活生生的模型对象的？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| config spec | `-c` 接受的字符串：YAML 文件名/路径，或 `key.path=value` |
| 递归合并 | 多个配置 dict 深度合并，后者为王（`recursive_merge`） |
| UNSET | 模块级哨兵对象，表示"此键未设置"，合并时跳过 |
| 全局配置 | 用户配置目录下的 `.env`，存 API key 与默认模型 |
| 内置配置 | 包内 `config/` 目录的 YAML，随包分发 |

## 三、全景：mini 的配置流水线（mini.py:68-101）

```text
mini -c mini.yaml -c model.model_kwargs.temperature=0.5 -m anthropic/... -y
│
├─ configure_if_first_time()            :68  首次运行引导 setup
│
├─ 每个 spec → get_config_from_spec     :72
│     "mini.yaml"        → 查找文件 → yaml.safe_load
│     "a.b=c"            → 按 = 拆分 → 嵌套 dict
│
├─ 追加 CLI 覆盖层（未设置的键填 UNSET）  :73-91
│     {"agent": {"mode": "yolo", "cost_limit": UNSET, ...}, ...}
│
├─ recursive_merge(*configs)            :92  UNSET 被跳过，YAML 值存活
│
├─ task 缺失 → 多行提示输入              :94-97
│
└─ 三个工厂实例化                        :99-101
      get_model(config["model"])
      get_environment(config["environment"], default_type="local")
      get_agent(model, env, config["agent"], default_type="interactive")
```

## 四、spec 解析：六十行的全部

### 4.1 分流（get_config_from_spec，56-61 行）

```python
if isinstance(config_spec, str) and "=" in config_spec:
    return _key_value_spec_to_nested_dict(config_spec)
path = get_config_path(config_spec)
return yaml.safe_load(path.read_text(encoding="utf-8"))
```

含 `=` 就是键值对，否则当文件。注意顺序：**键值对优先**——`a=b.yaml` 会被解析成键值对而非文件名。

### 4.2 文件查找：五个候选（get_config_path，12-28 行）

后缀不是 `.yaml` 先补上（15-16 行），然后按序尝试（17-23 行）：

1. 原样路径（`./mini.yaml` 或绝对路径）；
2. `MSWEA_CONFIG_DIR` 环境变量指向的目录（默认 `.`）；
3. **包内置 config 目录**；
4. 内置 `config/extra/`；
5. 内置 `config/benchmarks/`。

所以 `-c swebench.yaml` 能找到 `config/benchmarks/swebench.yaml`，`-c mini` 也行（自动补后缀）。全部落空则 `FileNotFoundError`，错误信息列出所有候选路径——照着改即可。

### 4.3 键值对：点号嵌套 + JSON 试探（31-53 行）

`model.model_kwargs.temperature=0.5` 的旅程：

```python
key, value = config_spec.split("=", 1)     # :39  只切第一个 =，值里可以有 =
try:
    value = json.loads(value)              # :41  "0.5"→float、"true"→bool、"[1,2]"→list
except json.JSONDecodeError:
    pass                                   # :43  不是 JSON 就当字符串
keys = key.split(".")                      # :44
# 逐层建嵌套 dict                            :47-52
```

两个细节：值先过 `json.loads`，所以数字、布尔、列表、带引号的字符串都能正确类型化——`temperature=0.5` 得到的是 float 不是 str；空键段（`a..b=c`）直接抛 `ValueError`（45-46 行），防手滑。

## 五、UNSET：为什么 None 不够用

CLI 参数的困境：`mini.py` 的 `cost_limit` 选项默认 `None`（62 行）。合并配置时，`None` 应该理解为"用户没给，别覆盖 YAML 里的值"——但 YAML 里某个键可能**真的就是 null**。两种语义需要一个区分。

解法（`utils/serialize.py:3`）：

```python
UNSET = object()
```

一个没有任何含义的对象，唯一用途就是"未设置"标记。`mini.py:73-91` 把 CLI 参数包成覆盖层时，没给的键一律填 UNSET：

```python
"agent": {
    "mode": "yolo" if yolo else UNSET,
    "cost_limit": cost_limit if cost_limit is not None else UNSET,
    ...
}
```

`recursive_merge` 遇到 UNSET 跳过（`serialize.py:20-21`），且**递归地**过滤嵌套 dict 里的 UNSET（24-26 行）——所以覆盖层可以放心地构造完整形状，没设置的键自然消失。合并规则全文三条（m04 已述）：None 字典跳过、UNSET 键跳过、dict 递归合并否则覆盖。

**优先级链最终形态**：内置 YAML < 用户 YAML（多个 `-c` 按序）< CLI 显式参数。`mini.py:33-47` 的帮助文本用红色大字警告：一旦用了 `-c`，默认配置文件**不再自动加载**，必须自己显式 `-c mini.yaml`——这是新手最常踩的坑。

## 六、三个工厂：配置键到对象

配置合并完，三个 `*_class` 键决定实例化谁。模式统一（m02 已见 `get_agent`）：深拷贝 → 弹出 class 键 → 短名查表 / 全路径 importlib → 实例化。

| 工厂 | 映射表 | 默认 | 特殊逻辑 |
|---|---|---|---|
| `get_agent`（`agents/__init__.py:25-28`） | default / interactive（8-11 行） | 由调用方 `default_type` 给 | mini.py 传 `"interactive"` |
| `get_environment`（`environments/__init__.py:30-33`） | 七种环境（8-16 行） | mini.py 传 `"local"` | 无 |
| `get_model`（`models/__init__.py:45-62`） | 十个模型类（78-89 行） | `LitellmModel` | 模型名三级解析 + Anthropic 自动缓存标记 |

`get_model` 的模型名解析（65-75 行）：显式参数 > 配置 `model_name` > `MSWEA_MODEL_NAME` 环境变量 > 抛错指引 setup。这就是为什么 `mini-extra config setup` 设一次模型名之后 `mini` 裸跑即可。

**全路径是一等公民**：`get_agent_class`（`agents/__init__.py:14-22`）先查映射表，查不到就把 spec 当 `module.Class` 全路径 import。你的自定义类写在任何可 import 的地方都行，无需注册（m12 实践）。

## 七、全局 .env：另一条配置线

YAML 管"运行行为"，`.env` 管"密钥与默认值"。加载点 m02 讲过（`__init__.py:26-36`，import 即加载）。管理工具是 `mini-extra config`（`run/utilities/config.py`）：

- **首次引导**：`mini` 启动时 `configure_if_first_time`（62-66 行）检查 `MSWEA_CONFIGURED` 环境变量，没有就跑 `setup()`；
- **setup**（70-96 行）：问默认模型名、问 API key 名与值，用 `dotenv.set_key` 写进 `.env`，最后写入 `MSWEA_CONFIGURED=true`（92 行）并 `_reload_config()` 立即生效；
- `set` / `unset` / `edit` 子命令（99-127 行）：改键、删键、用 `$EDITOR` 打开文件。

litellm 读环境变量里的各家 API key，所以这条线本质是**给 litellm 准备环境变量**。模型调用时认证失败的那句提示（`litellm_model.py:73`）引导用户来的就是这里。

## 八、内置 YAML 家族

| 文件 | 用途 | 关键差异 |
|---|---|---|
| `config/mini.yaml` | `mini` CLI 默认 | toolcall 提示词、`mode: confirm`、cost_limit 3 |
| `config/default.yaml` | hello_world 默认 | textbased 提示词、cost_limit 0（不限） |
| `config/mini_textbased.yaml` | CLI 的 textbased 版 | 同 default 的格式约束 |
| `config/benchmarks/swebench.yaml` | SWE-bench 默认 | docker、`/testbed`、step_limit 250、提交 patch 流程（m11） |
| `config/benchmarks/swebench_backticks.yaml` 等 | 变体 | textbased / xml / modal 版本 |
| `config/benchmarks/programbench.yaml` | ProgramBench | 另一种评测 |

读模板差异时对照 m04：YAML 不只是参数表，**提示词工程的全部产物都住在 config 里**，换 YAML 等于换 agent 的"性格"。

## 九、小结

- config spec 两种形态：文件（五候选路径查找）与 `key.path=value`（JSON 类型试探 + 点号嵌套）。
- 合并优先级：内置 < 用户 `-c` 序列 < CLI 显式参数；UNSET 哨兵让"未设置"与"设为 null"语义分离。
- 用了 `-c` 就必须显式带上基础配置文件——默认不会自动叠加。
- 三工厂同构：短名映射表 + 全路径 importlib；模型名三级解析；`.env` 由 `mini-extra config` 管理、import 时加载。
- 下一章看运行的产物：traj.json 的结构，以及用 inspector 回放它。
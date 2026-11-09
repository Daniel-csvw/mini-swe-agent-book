# 包地图 —— 五个核心目录，三个协议

> 本章源文件：`src/minisweagent/__init__.py`、`pyproject.toml`、三个组件目录的 `__init__.py`。

## 一、引子：import 的那一刻发生了什么

你在 Python 里写下 `from minisweagent.agents.default import DefaultAgent`，在进程里实际发生的事比你想象的多：打印横幅、创建全局配置目录、加载 `.env` 文件、注册日志器。这一章把「这个包由什么构成、入口在哪、组件之间靠什么对接」讲清楚。读完你应该能回答三个问题：

1. 三个协议（Protocol）各自规定了什么？为什么说"可以完全无视它们"？
2. `mini` 这个命令是从哪个函数开始执行的？包安装时还装了哪些命令？
3. 全局配置 `.env` 是什么时候、从哪里加载的？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| Protocol | `typing.Protocol`，结构化子类型：只规定方法签名，不要求继承 |
| 鸭子类型 | "走起来像鸭子就是鸭子"——实现了协议方法的任何类都能直接用 |
| src 布局 | 包代码放在 `src/` 下，避免未安装时误导入源码目录 |
| entry point | `pyproject.toml [project.scripts]` 声明的命令行入口 |
| 工厂函数 | `get_model / get_agent / get_environment`，把配置字符串变成组件实例 |

## 三、全景：一张带行数的地图

```text
src/minisweagent/                # 版本 2.4.6（__init__.py:11）
├── __init__.py            92 行  # 协议 + 全局配置 + 启动横幅
├── exceptions.py          26 行  # 控制流异常家族（m03）
├── agents/                       # 控制流
│   ├── __init__.py        28 行  # get_agent 工厂
│   ├── default.py        190 行  # DefaultAgent（m03）
│   ├── interactive.py    209 行  # InteractiveAgent（m08）
│   └── utils/prompt_user.py      # prompt_toolkit 输入会话
├── models/                       # LM 接口
│   ├── __init__.py       113 行  # get_model 工厂 + 全局成本统计（m05）
│   ├── litellm_model.py  164 行  # 默认模型类（m05）
│   ├── litellm_textbased_model.py # 正则路线（m06）
│   ├── litellm_response_model.py  # Responses API 路线（m06）
│   ├── openrouter_*.py / portkey_*.py / requesty_model.py  # 其他网关
│   ├── test_models.py            # 确定性测试模型（m12）
│   ├── extra/roulette.py         # 随机/交替多模型（m12）
│   └── utils/                    # 动作解析、观察格式化、重试、缓存标记
├── environments/                 # 动作执行
│   ├── __init__.py        33 行  # get_environment 工厂
│   ├── local.py           92 行  # 本地子壳（m07）
│   ├── docker.py         161 行  # Docker 容器（m07）
│   ├── singularity.py            # HPC 场景（m07）
│   └── extra/                    # bubblewrap / swerex / contree
├── run/                          # 入口脚本
│   ├── mini.py           109 行  # `mini` CLI（m09）
│   ├── hello_world.py     42 行  # 最小示例
│   ├── utilities/                # mini-extra 子命令：config / inspector
│   └── benchmarks/               # swebench / swebench_single / programbench（m11）
├── config/                       # 内置 YAML（提示词模板，m04/m09）
└── utils/                        # log.py、serialize.py（recursive_merge）
```

**读法提示**：三个组件目录（agents/models/environments）的 `__init__.py` 都是工厂，真正的逻辑在同级模块里；`run/` 只做组装，不含 agent 逻辑。这是项目"多态"哲学的落地形式——每个类都简单，选择则很丰富（`AGENTS.md`）。

## 四、`__init__.py`：被 import 时执行的三段代码

`src/minisweagent/__init__.py` 只有 92 行，但 import 即执行，分三段。

### 4.1 版本与路径（11-23 行）

```python
__version__ = "2.4.6"                                # :11
package_dir = Path(__file__).resolve().parent        # :23
```

`__version__` 是单一事实来源：`pyproject.toml:98-99` 用 `version = {attr = "minisweagent.__version__"}` 动态读取它，轨迹文件里的 `mini_version` 也来自它（`default.py:173`）。`package_dir` 让入口脚本能找到内置 YAML（如 `hello_world.py:35`）。

### 4.2 全局配置与启动横幅（26-36 行）

```python
global_config_dir = Path(os.getenv("MSWEA_GLOBAL_CONFIG_DIR") or user_config_dir("mini-swe-agent"))  # :26
global_config_dir.mkdir(parents=True, exist_ok=True)   # :27
global_config_file = Path(global_config_dir) / ".env"  # :28
```

- 默认位置由 `platformdirs` 决定（各操作系统惯例的用户配置目录），可用环境变量 `MSWEA_GLOBAL_CONFIG_DIR` 覆盖；
- **import 即建目录**——所以哪怕只是 import 一下，磁盘上也会出现这个目录；
- 30-35 行打印版本横幅和 v2 迁移指南链接，`MSWEA_SILENT_STARTUP` 可静音；
- 36 行 `dotenv.load_dotenv(dotenv_path=global_config_file)`：**API key 与默认模型名就这样进入环境变量**。这是全局配置的唯一加载点，`mini-extra config set` 写的也是同一个文件（m09 细讲）。

### 4.3 三个协议（43-80 行）

文件 docstring 说得很诚实（`__init__.py:6-8`）："By the magic of protocols & duck typing, you can pretty much ignore them, unless you want the static type checking."

**Model 协议**（43-58 行），五个方法一个属性：

| 成员 | 签名要点 | 谁在用 |
|---|---|---|
| `config` | 任意配置对象 | 序列化、模板变量 |
| `query(messages, **kwargs) -> dict` | 核心：问模型，返回**一条**原生格式消息 + `extra` | `DefaultAgent.query`（`default.py:149`） |
| `format_message(**kwargs) -> dict` | 把 role/content/extra 包装成该模型的消息格式 | agent 初始化系统/任务消息 |
| `format_observation_messages(message, outputs, template_vars) -> list[dict]` | 把执行结果包装成观察消息 | `execute_actions`（`default.py:157`） |
| `get_template_vars(**kwargs) -> dict` | 向 Jinja 模板贡献变量 | `default.py:56` |
| `serialize() -> dict` | 贡献轨迹元数据 | `default.py:180` |

**Environment 协议**（61-70 行），三个方法一个属性：`execute(action, cwd="") -> dict`、`get_template_vars`、`serialize`、`config`。注意 `execute` 收的是 **action dict**（含 `command` 键）而不是裸字符串——这是 v2 的签名变化（`v2_migration.md:198`）。

**Agent 协议**（73-80 行）最薄：`config`、`run(task, **kwargs) -> dict`、`save(path, *extra_dicts) -> dict`。`run` 返回最后一条 exit 消息的 `extra`（含 `exit_status` 与 `submission`），不再返回 v1 的元组（`v2_migration.md:245-255`）。

**关键认知**：协议里没有 `messages`、`cost`、`n_calls`——这些是实现细节而非契约。自定义组件只要方法对得上就能用，不需要 import 或继承任何东西（m12 会实际写一个）。

## 五、`pyproject.toml`：入口与依赖的哲学

### 5.1 四个命令，两个入口（89-93 行）

```toml
[project.scripts]
mini = "minisweagent.run.mini:app"
mini-swe-agent = "minisweagent.run.mini:app"
mini-extra = "minisweagent.run.utilities.mini_extra:main"
mini-e = "minisweagent.run.utilities.mini_extra:main"
```

`mini` 与 `mini-swe-agent` 是同一个 typer app 的别名；`mini-extra`（别名 `mini-e`）是子命令集散地，`mini_extra.py:13-19` 注册了五组：`config`、`inspect/i/inspector`、`swebench`、`swebench-single`、`programbench`。分发方式是 `import_module(module_path).app(args[1:])`（`mini_extra.py:43`）——纯字符串到模块的映射，没有插件框架。

### 5.2 依赖清单即设计宣言（33-48 行）

| 依赖 | 用途 | 对应章节 |
|---|---|---|
| `litellm` | 统一各家模型 API | m05 |
| `pydantic >= 2.0` | 配置类（注释指明：v2 的可变默认值安全） | m03/m09 |
| `jinja2` | 一切提示词/观察模板渲染 | m04 |
| `tenacity` | 模型调用重试 | m05 |
| `typer` + `rich` | CLI 与终端渲染 | m08/m09 |
| `python-dotenv` + `platformdirs` | 全局 `.env` 加载 | m09 |
| `prompt_toolkit` | 交互输入、历史记录 | m08 |
| `textual` | inspector 轨迹浏览器 | m10 |
| `datasets` | 加载 SWE-bench 数据集 | m11 |
| `pyyaml`、`requests`、`openai` | YAML 配置、HTTP、Responses API | 各章 |

两个安全细节值得一提：`litellm` 排除了被投毒的 1.82.7/1.82.8 版本，`openai` 排除了有问题的 1.100.0/1.100.1（`pyproject.toml:38`、`:47` 注释）。可选依赖按场景分组：`modal`、`contree`、`dev`、`full`（50-82 行）——核心保持轻，沙箱后端按需安装。

### 5.3 打包的一个易踩坑点

`[tool.setuptools.package-data]` 显式包含 `config/**/*`（`pyproject.toml:105-106`）。YAML 模板是**包内数据**，靠 `package_dir` / `builtin_config_dir` 定位；如果你 fork 后加了新配置文件却忘了这行，pip 安装后配置会神秘失踪。

## 六、三个工厂：字符串如何变成对象

三个组件目录的 `__init__.py` 是同一个模式的三份拷贝，以 `get_agent` 为例（`agents/__init__.py:25-28`）：

```python
def get_agent(model: Model, env: Environment, config: dict, *, default_type: str = "") -> Agent:
    config = copy.deepcopy(config)
    agent_class = get_agent_class(config.pop("agent_class", default_type))
    return agent_class(model, env, **config)
```

四步：深拷贝防污染 → 弹出 `agent_class` 键 → 短名查映射表（`"interactive"` → 全限定路径，`agents/__init__.py:8-11`）或直接当 import 路径 → `importlib` 加载并实例化。**短名是便利，全路径是逃生门**——你自己的类不需要进任何注册表，写全限定名即可（m09/m12 展开）。

`get_model` 多两步：模型名三级解析（参数 > 配置 > `MSWEA_MODEL_NAME` 环境变量，`models/__init__.py:65-75`），以及 Anthropic 系模型自动开启缓存标记（55-60 行）。`get_environment` 与 `get_agent` 几乎逐行同构（`environments/__init__.py:30-33`）。

## 七、小结

- 包的第一动作发生在 import 时：建全局配置目录、加载 `.env`、打印横幅（`__init__.py:26-36`）。
- 三个协议是**可选的静态检查辅助**，运行时靠鸭子类型；协议方法之外的属性都是实现细节。
- 入口只有两处：`run/mini.py`（CLI）与 `run/utilities/mini_extra.py`（子命令）；其余 run 脚本都是 Python bindings 的组装示例。
- 工厂函数 = 映射表 + importlib，全路径类名是一等公民。这为 m12 的"不改源码做扩展"埋下伏笔。
- 下一章进入全书核心：190 行的 agent 主循环。
# 包地图 —— 五个核心目录，三个协议

> 本章源文件：`src/minisweagent/__init__.py`、`pyproject.toml`、`run/mini.py`、三个组件目录的 `__init__.py`、`config/__init__.py`。

## 一、引子：import 的那一刻发生了什么

你在 Python 里写下 `from minisweagent.agents.default import DefaultAgent`，在进程里实际发生的事比你想象的多：打印横幅、创建全局配置目录、加载 `.env` 文件、注册日志器。这一章把「这个包由什么构成、入口在哪、组件之间靠什么对接」讲清楚。读完你应该能回答三个问题：

1. 三个协议（Protocol）各自规定了什么？为什么说"可以完全无视它们"？
2. `mini` 这个命令是从哪个函数开始执行的？包安装时还装了哪些命令？
3. 全局配置 `.env` 是什么时候、从哪里加载的？

如果读一遍还是觉得目录、协议、工厂是三堆散零件，直接跳到 **第四节：一条 `mini` 命令的旅程**——那一节用一个具体命令把全章串起来，读完再回来看前面的清单会顺很多。

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| Protocol | `typing.Protocol`，结构化子类型：只规定方法签名，不要求继承 |
| 鸭子类型 | "走起来像鸭子就是鸭子"——实现了协议方法的任何类都能直接用 |
| src 布局 | 包代码放在 `src/` 下，避免未安装时误导入源码目录 |
| entry point | `pyproject.toml [project.scripts]` 声明的命令行入口 |
| 工厂函数 | `get_model / get_environment / get_agent`，把配置字典变成组件实例 |
| config spec | `-c` 参数：一个 YAML 文件名，或一条 `agent.mode=yolo` 式的键值覆盖 |
| 组件 | 一次运行所需的三样东西：Model（问模型）、Environment（跑命令）、Agent（管循环） |

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

### 3.1 五个核心目录其实是四层

按"谁依赖谁"重排一遍，比按字母顺序好记：

| 层 | 目录/文件 | 干什么 | 被谁依赖 |
|---|---|---|---|
| 契约层 | `__init__.py` 里的三个 Protocol + `exceptions.py` | 只定义方法签名和控制流异常，不含业务逻辑 | 全部（但只用于类型注解） |
| 实现层 | `agents/`、`models/`、`environments/` | 各自提供多个可替换的实现类 | 组装层 |
| 数据层 | `config/` 里的 YAML、`utils/` 里的辅助函数 | 提示词模板、配置定位、字典合并、日志 | 组装层与实现类 |
| 组装层 | `run/` | 读配置 → 造三个对象 → 调 `agent.run()` | 用户（命令行） |

关键：**依赖是单向向下的**。`config/` 里没有一个 `import` 指向 agent 类；`models/` 不知道 `agents/` 的存在；三个实现类之间唯一的耦合是协议里那几个方法。所以"换掉模型实现"不需要碰 agent 一行代码。这就是项目"多态"哲学的落地形式——每个类都简单，选择则很丰富（`AGENTS.md`）。

三个组件目录的 `__init__.py` 都只是工厂，真正的逻辑在同级模块里；`run/` 只做组装，不含 agent 逻辑。

### 3.2 想改 X，该打开哪个文件

| 想做的事 | 打开 | 说明 |
|---|---|---|
| 改默认提示词 | `config/mini.yaml` 或 `-c` 指定的文件 | 模板用 Jinja，变量来源见第四节 |
| 换模型/环境/agent 实现 | 对应的 `*_class` 配置项（短名或全路径） | 工厂查映射表，见第六节 |
| 改主循环 | `agents/default.py` | m03 专讲 |
| 改命令怎么执行 | `environments/local.py` / `docker.py` | m07 专讲 |
| 加自己的类 | 不打开任何现有文件 | 写全限定名即可，m12 专讲 |
| 加内置 YAML | 改完还要看 `pyproject.toml:105-106` | 否则 pip 安装后配置失踪（5.3 节） |

## 四、一条 `mini` 命令的旅程

前面都是静态清单。这一节跟着一条真实的命令走一遍：

```bash
mini -c mini.yaml -m anthropic/claude-sonnet-4-5 -y -t "修复这个 bug"
```

### 4.1 第 0 步：import 就先干了四件事

`pyproject.toml:90` 把 `mini` 指向 `minisweagent.run.mini:app`。加载 `run/mini.py` 之前，Python 先执行 `minisweagent/__init__.py`：定版本（11 行）、算出 `global_config_dir` 并**建目录**（26-27 行）、打印横幅（30-35 行）、`load_dotenv` 把全局 `.env` 灌进环境变量（36 行）。所以 `MSWEA_MODEL_NAME`、API key 在 CLI 代码跑之前就已经在位了（详见第五节）。

### 4.2 第 1 步：配置从四个来源汇成一个 dict（`mini.py:68-92`）

`config_spec` 默认值是 `[str(DEFAULT_CONFIG_FILE)]`（`mini.py:22`，即内置的 `config/mini.yaml`），命令行每个 `-c` 都被 `get_config_from_spec` 变成 dict（`mini.py:72`）：

- 是文件名 → `get_config_path` 按五个候选位置依次找：原样路径 → `$MSWEA_CONFIG_DIR/` → 包内 `config/` → `config/extra/` → `config/benchmarks/`（`config/__init__.py:12-28`），找到后 `yaml.safe_load`；
- 含 `=` → 按点号拆成嵌套 dict，值先试 `json.loads`（`config/__init__.py:31-53`）——所以 `-c agent.step_limit=20` 里的 `20` 是整数不是字符串。

然后 `mini.py:73-91` 追加第四份 dict：把 `-t`、`-m`、`-y`、`-l`、`--*-class`、`-o` 这些 CLI 选项塞进 `run`/`agent`/`model`/`environment` 四个顶层键里，没给的选项一律填 `UNSET` 哨兵。最后 `recursive_merge(*configs)`（92 行）：**后面的覆盖前面的，嵌套 dict 递归合并，值为 `UNSET` 的键跳过**（`utils/serialize.py:6-29`）。

于是产出一个大约长这样的 dict：

```python
{
    "agent": {"system_template": "...", "instance_template": "...",
              "mode": "yolo", "step_limit": ..., "output_path": ...},
    "model": {"model_name": "anthropic/claude-sonnet-4-5", "model_kwargs": {...}},
    "environment": {"env": {...}, ...},
    "run": {"task": "修复这个 bug"},
}
```

注意这个形状：**三个组件各占一个顶层键，工厂拿到的就是对应那一块**；`run.task` 不传给任何组件，只在 `mini.py:94-97` 决定要不要弹出多行输入框。这也解释了 `-c` 的红色警告（33-47 行）：一旦你自己写了 `-c`，默认的 `mini.yaml` 就不在列表里了，提示词模板也就没了——必须显式 `-c mini.yaml -c ...`。

### 4.3 第 2 步：三个工厂，按依赖顺序造对象（`mini.py:99-101`）

```python
model = get_model(config=config.get("model", {}))                                   # :99
env = get_environment(config.get("environment", {}), default_type="local")         # :100
agent = get_agent(model, env, config.get("agent", {}), default_type="interactive") # :101
```

顺序不是随便排的：**Agent 的构造函数要收 model 和 env 两个现成对象**（`default.py:39-50` 把二者存成 `self.model` / `self.env`），所以 model、env 必须先造好。三行都走同一个套路——深拷贝配置防止污染原 dict、弹出 `*_class` 键查映射表、剩下的 kwargs 交给类自己：

- `get_model` 三级解析模型名（参数 > 配置 > `MSWEA_MODEL_NAME`，`models/__init__.py:65-75`），Anthropic 系自动加缓存标记（55-60 行），另外维护一份全局成本统计（13-42 行）；
- `get_environment` 几乎逐行同构（`environments/__init__.py:30-33`），`default_type="local"` 是 `mini` 这个入口给的默认值；
- `get_agent` 见第六节，`default_type="interactive"` 意味着**直接敲 `mini` 用的是 InteractiveAgent**（会确认每一步），`-y` 改的是 `agent.mode` 而不是换类。

### 4.4 第 3 步：agent 把三方变量合进模板（`default.py:52-64`）

三个对象造好后，`agent.run(run_task)`（102 行）开始循环。这里先看清一个对象是怎么用上另外两个的：`DefaultAgent.get_template_vars` 把 **agent 自己的配置 + env 的贡献 + model 的贡献 + 运行时计数 + 调用方注入 + 本次 kwargs** 依次递归合并：

```python
recursive_merge(self.config.model_dump(), self.env.get_template_vars(),
                self.model.get_template_vars(),
                {"n_model_calls": ..., "model_cost": ..., "elapsed_seconds": ...},
                self.extra_template_vars, kwargs)
```

所以协议里那两个 `get_template_vars` 不是摆设：模板 `{{task}}` 来自 `run` 传进的 kwargs（88-89 行注入），`{{system}}` / `{{machine}}` / `{{release}}` 来自环境（`local.py:58-59` 把 `platform.uname()` 与 `os.environ` 合进来），模型则贡献自己的配置字段（`litellm_model.py:153-154`）——谁的数据谁贡献，agent 只做汇总。渲染用 `StrictUndefined`（66-67 行），模板里写了没人提供的变量就直接报错，不静默写空。

一轮循环里协议方法的调用时机（细节在 m03/m04）：

```text
run(task) → add_messages(system/instance 模板渲染) → loop:
            step() → model.query(messages)               # Model 五方法之一
                   → env.execute(action)                # Environment 三方法之一
                   → format_observation_messages(...)   # 把执行结果包成观察消息
                   → 检查哨兵 / 限额 / 格式错误
            finally: save(...) → model.serialize() + env.serialize()
```

### 4.5 一张图收束

```text
__init__.py 副作用（.env / 横幅 / 协议定义）
        │
mini.py:  -c 文件 + -c 键值 + CLI 选项 ──recursive_merge──> config dict
        │                                   ├─["model"]──> get_model ──> Model 实例
        │                                   ├─["environment"]──> get_environment ──> Env 实例
        │                                   └─["agent"] + 上面两个对象 ──> get_agent ──> Agent 实例
        │                                                                   │
        └──["run"]["task"]─────────────────────────────────────────────> agent.run(task)
                                                                            │
                                                          query / execute / serialize 按协议对接
```

读完这一节再回头看第三节的目录树：`config/` 是数据、三个组件目录是实现、`run/` 是把它们拼起来的那几十行，协议只是拼装时对齐接口用的图样。

## 五、`__init__.py`：被 import 时执行的三段代码

`src/minisweagent/__init__.py` 只有 92 行，但 import 即执行，分三段。

### 5.1 版本与路径（11-23 行）

```python
__version__ = "2.4.6"                                # :11
package_dir = Path(__file__).resolve().parent        # :23
```

`__version__` 是单一事实来源：`pyproject.toml:98-99` 用 `version = {attr = "minisweagent.__version__"}` 动态读取它，轨迹文件里的 `mini_version` 也来自它（`default.py:173`）。`package_dir` 让入口脚本能找到内置 YAML（如 `hello_world.py:35`）；`config/__init__.py:9` 另有 `builtin_config_dir`，是 `config/` 自己的位置——第四节找配置文件用的就是它。

### 5.2 全局配置与启动横幅（26-36 行）

```python
global_config_dir = Path(os.getenv("MSWEA_GLOBAL_CONFIG_DIR") or user_config_dir("mini-swe-agent"))  # :26
global_config_dir.mkdir(parents=True, exist_ok=True)   # :27
global_config_file = Path(global_config_dir) / ".env"  # :28
```

- 默认位置由 `platformdirs` 决定（各操作系统惯例的用户配置目录），可用环境变量 `MSWEA_GLOBAL_CONFIG_DIR` 覆盖；
- **import 即建目录**——所以哪怕只是 import 一下，磁盘上也会出现这个目录；
- 30-35 行打印版本横幅和 v2 迁移指南链接，`MSWEA_SILENT_STARTUP` 可静音；
- 36 行 `dotenv.load_dotenv(dotenv_path=global_config_file)`：**API key 与默认模型名就这样进入环境变量**。这是全局配置的唯一加载点，`mini-extra config set` 写的也是同一个文件（m09 细讲）。

分清两种配置：这里加载的全局 `.env` 只提供环境变量（密钥、默认模型名）；第四节那套 `config spec` 提供的是 YAML 提示词与组件参数。两者不是同一个机制，前者早于后者生效。

### 5.3 三个协议（43-80 行）

文件 docstring 说得很诚实（`__init__.py:6-8`）："By the magic of protocols & duck typing, you can pretty much ignore them, unless you want the static type checking."

**Model 协议**（43-58 行），五个方法一个属性：

| 成员 | 签名要点 | 谁在用、何时用 |
|---|---|---|
| `config` | 任意配置对象 | 序列化、模板变量 |
| `query(messages, **kwargs) -> dict` | 核心：问模型，返回**一条**原生格式消息 + `extra` | 每步循环开头，`DefaultAgent.query`（`default.py:130-131`） |
| `format_message(**kwargs) -> dict` | 把 role/content/extra 包装成该模型的消息格式 | `run` 开场造系统/任务消息 |
| `format_observation_messages(message, outputs, template_vars) -> list[dict]` | 把执行结果包装成观察消息 | 动作执行后，`execute_actions`（`default.py:154-157`） |
| `get_template_vars(**kwargs) -> dict` | 向 Jinja 模板贡献变量 | 每次渲染模板前（`default.py:52-64` 汇总） |
| `serialize() -> dict` | 贡献轨迹元数据 | 每步保存轨迹时（`default.py:180`） |

**Environment 协议**（61-70 行），三个方法一个属性：`execute(action, cwd="") -> dict`、`get_template_vars`、`serialize`、`config`。注意 `execute` 收的是 **action dict**（含 `command` 键）而不是裸字符串——这是 v2 的签名变化（`v2_migration.md:198`）。它在每步"模型输出动作之后、生成观察消息之前"被调用。

**Agent 协议**（73-80 行）最薄：`config`、`run(task, **kwargs) -> dict`、`save(path, *extra_dicts) -> dict`。`run` 返回最后一条 exit 消息的 `extra`（含 `exit_status` 与 `submission`），不再返回 v1 的元组（`v2_migration.md:245-255`）。

**关键认知**：协议里没有 `messages`、`cost`、`n_calls`——这些是实现细节而非契约。协议只被用作 `get_agent(model: Model, env: Environment, ...)` 这类类型注解；运行时没有任何注册、继承或 `isinstance` 检查，自定义组件只要方法对得上就能用，不需要 import 或继承任何东西（m12 会实际写一个）。反过来说，**想让静态检查帮自己，就得照着协议把方法补齐**（m12 里 RouletteModel 缺两个格式化方法就是反面例子）。

## 六、`pyproject.toml`：入口与依赖的哲学

### 6.1 四个命令，两个入口（89-93 行）

```toml
[project.scripts]
mini = "minisweagent.run.mini:app"
mini-swe-agent = "minisweagent.run.mini:app"
mini-extra = "minisweagent.run.utilities.mini_extra:main"
mini-e = "minisweagent.run.utilities.mini_extra:main"
```

`mini` 与 `mini-swe-agent` 是同一个 typer app 的别名；`mini-extra`（别名 `mini-e`）是子命令集散地，`mini_extra.py:13-19` 注册了五组：`config`、`inspect/i/inspector`、`swebench`、`swebench-single`、`programbench`。分发方式是 `import_module(module_path).app(args[1:], prog_name=...)`（`mini_extra.py:40`）——纯字符串到模块的映射，没有插件框架。

第四节走的就是第一条入口：`mini` → `run/mini.py` 的 `main()`（55 行）→ 组装三组件。而批量评测（m11）走 `mini-extra swebench`，它会自己组装组件、外加并发与进度管理——**入口不同，但第四节那套 config dict + 三个工厂的拼法一样**。`AGENTS.md` 说"每个用例从一个 run 脚本开始"，指的就是这两类组装脚本。

### 6.2 依赖清单即设计宣言（33-48 行）

| 依赖 | 用途 | 对应章节 |
|---|---|---|
| `litellm` | 统一各家模型 API | m05 |
| `pydantic >= 2.0` | 配置类（注释指明：v2 的可变默认值安全） | m03/m09 |
| `jinja2` | 一切提示词/观察模板渲染 | m04 |
| `tenacity` | 模型调用重试 | m05 |
| `typer` + `rich` | CLI 与终端渲染 | m08/m09 |
| `python-dotenv` + `platformdirs` | 全局 `.env` 加载 | 5.2 节 / m09 |
| `prompt_toolkit` | 交互输入、历史记录 | m08 |
| `textual` | inspector 轨迹浏览器 | m10 |
| `datasets` | 加载 SWE-bench 数据集 | m11 |
| `pyyaml`、`requests`、`openai` | YAML 配置、HTTP、Responses API | 各章 |

两个安全细节值得一提：`litellm` 排除了被投毒的 1.82.7/1.82.8 版本，`openai` 排除了有问题的 1.100.0/1.100.1（`pyproject.toml:38`、`:47` 注释）。可选依赖按场景分组：`modal`、`contree`、`dev`、`full`（50-82 行）——核心保持轻，沙箱后端按需安装。

### 6.3 打包的一个易踩坑点

`[tool.setuptools.package-data]` 显式包含 `config/**/*`（`pyproject.toml:105-106`）。YAML 模板是**包内数据**，靠 `package_dir` / `builtin_config_dir` 定位；如果你 fork 后加了新配置文件却忘了这行，pip 安装后配置会神秘失踪。

## 七、三个工厂：字符串如何变成对象

三个组件目录的 `__init__.py` 是同一个模式的三份拷贝，以 `get_agent` 为例（`agents/__init__.py:25-28`）：

```python
def get_agent(model: Model, env: Environment, config: dict, *, default_type: str = "") -> Agent:
    config = copy.deepcopy(config)
    agent_class = get_agent_class(config.pop("agent_class", default_type))
    return agent_class(model, env, **config)
```

四步：深拷贝防污染 → 弹出 `agent_class` 键 → 短名查映射表（`"interactive"` → 全限定路径，`agents/__init__.py:8-11`）或直接当 import 路径 → `importlib` 加载并实例化（14-22 行）。剩下的 config 键全部作为关键字参数交给类——`DefaultAgent` 用 `AgentConfig`（pydantic BaseModel，`default.py:19-35`）接收，多一个不认识的键就报错。**短名是便利，全路径是逃生门**——你自己的类不需要进任何注册表，写全限定名即可（m09/m12 展开）。

`get_model` 多两步：模型名三级解析（参数 > 配置 > `MSWEA_MODEL_NAME` 环境变量，`models/__init__.py:65-75`），以及 Anthropic 系模型自动开启缓存标记（55-60 行）。`get_environment` 与 `get_agent` 几乎逐行同构（`environments/__init__.py:30-33`）。

## 八、小结

- 包的第一动作发生在 import 时：建全局配置目录、加载 `.env`、打印横幅（`__init__.py:26-36`）；全局 `.env`（环境变量）与 `-c` 的 YAML（提示词/组件参数）是两套机制。
- 一条 `mini` 命令 = 四份配置递归合并成一个 dict → model / env / agent 依次造对象 → `agent.run(task)`；Agent 持有另外两个对象，靠协议方法对接。
- 五个核心目录按四层记：契约层（协议）、实现层（三个组件目录）、数据层（config/utils）、组装层（run），依赖单向向下。
- 三个协议是**可选的静态检查辅助**，运行时靠鸭子类型；协议方法之外的属性都是实现细节。
- 入口只有两处：`run/mini.py`（CLI）与 `run/utilities/mini_extra.py`（子命令）；其余 run 脚本都是 Python bindings 的组装示例。
- 工厂函数 = 映射表 + importlib，全路径类名是一等公民。这为 m12 的"不改源码做扩展"埋下伏笔。
- 下一章进入全书核心：190 行的 agent 主循环。

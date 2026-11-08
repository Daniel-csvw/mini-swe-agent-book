# 环境 —— 子壳、哨兵与进程组

> 本章源文件：`src/minisweagent/environments/local.py`（92 行）、`docker.py`（161 行）、`singularity.py`、`extra/bubblewrap.py`、`environments/__init__.py`。

## 一、引子：同一句话，换个地方执行

agent 循环对执行环境的全部认知是：`env.execute(action)` 返回一个 dict。把这个 dict 背后的实现从"本地 subprocess"换成"docker exec"，agent 一行不改——m01 说的"沙箱化平凡"在这一章兑现。读完你应该能回答三个问题：

1. 命令超时后，怎么保证不留孤儿进程？
2. `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 这串魔法字符串是怎么让 agent 停下来的？为什么检查它的是环境而不是 agent？
3. Docker 环境的容器什么时候启动、什么时候销毁？`docker exec` 和本地 `subprocess` 差在哪几行？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| 子壳（subshell） | 每条命令新开的 shell 进程；cd/环境变量不跨命令持久 |
| 哨兵命令 | 输出首行等于魔法字符串即视为提交，触发 `Submitted` |
| 进程组 | POSIX 下同一 session 的进程集合；`killpg` 一锅端 |
| action / output | 入参 `{"command": ...}`；出参 `{"output", "returncode", "exception_info", "extra"?}` |

## 三、全景：Environment 协议的三种实现

协议只有三个方法（`__init__.py:61-70`）：`execute`、`get_template_vars`、`serialize`。工厂映射表（`environments/__init__.py:8-16`）注册了七种环境：

| 环境 | 执行方式 | 典型场景 |
|---|---|---|
| `local` | 本地 `subprocess.Popen` | 日常 CLI（`mini` 默认） |
| `docker` | `docker run -d` + `docker exec` | SWE-bench 评测、隔离任务 |
| `singularity` | 先 build sandbox 目录再 `exec` | HPC 集群（无 Docker daemon 权限） |
| `bubblewrap` | `bwrap` 非特权沙箱 | Linux 轻量隔离（实验性） |
| `swerex_docker` / `swerex_modal` | 经 SWE-ReX 执行服务 | 远程/Modal 云执行 |
| `contree` | Contree SDK | 第三方沙箱服务 |

本章精读前两个，其余触类旁通。

## 四、LocalEnvironment：92 行的全部家当

### 4.1 配置与 execute 主干（13-43 行）

配置三字段（13-16 行）：`cwd`（默认空 = 进程当前目录）、`env`（追加的环境变量）、`timeout`（默认 30 秒）。

`execute`（24-43 行）主干：

```python
command = action.get("command", "")                                    # :26
cwd = cwd or self.config.cwd or os.getcwd()                            # :27
result = _run(command, cwd, os.environ | self.config.env, timeout or self.config.timeout)  # :29
output = {"output": result.stdout, "returncode": result.returncode, "exception_info": ""}  # :30
```

三个合并方向值得看清：cwd 是"参数 > 配置 > 进程目录"；环境变量是"宿主环境 | 配置覆盖"（配置优先）；stderr 并进 stdout（`_run` 里 `stderr=subprocess.STDOUT`，83 行）——模型只需要一份按时间序的混合输出。

**异常不出环境**（31-41 行）：超时等任何异常都被包成 `{"returncode": -1, "exception_info": "An error occurred...", "extra": {"exception_type": ...}}` 正常返回。命令失败是数据，不是事故——模型下一轮自己读到 `exception_info` 想办法。唯一的例外是哨兵触发的 `Submitted`（42 行），那是控制流，必须穿透。

### 4.2 _run：杀进程组的讲究（72-92 行）

这个模块级函数是本地环境唯一"有技术含量"的部分，docstring 说明意图（73 行）："kills the whole process group on timeout so no children are orphaned"。

```python
process = subprocess.Popen(
    command, shell=True, ...,
    start_new_session=os.name == "posix",        # :84  子进程自成会话/进程组
)
try:
    stdout, _ = process.communicate(timeout=timeout)   # :87
except subprocess.TimeoutExpired:
    os.killpg(process.pid, signal.SIGKILL) if os.name == "posix" else process.kill()  # :89
    stdout, _ = process.communicate()                  # :90  收尸，取回已产出输出
    raise subprocess.TimeoutExpired(command, timeout, output=stdout)  # :91
```

逐点拆解：

- **84 行 `start_new_session=True`**：让 shell 及其子孙成为独立进程组。没有它，89 行的 `killpg` 会误杀 agent 自己；
- **89 行 `killpg(..., SIGKILL)`**：`bash -c "sleep 100 & sleep 200"` 这种命令，只杀 shell 会留下两个孤儿 sleep。杀整组才干净。Windows 无进程组概念，退化为 `process.kill()`；
- **90-91 行**：杀完再 `communicate()` 收回已产生的输出，并把 output 塞进重新抛出的 `TimeoutExpired`——上层 execute 的 32 行 `getattr(e, "output", None)` 就是接它的。**超时命令的部分输出也能进轨迹**，模型能看到超时前打印了什么。

### 4.3 哨兵：_check_finished（45-56 行）

```python
lines = output.get("output", "").lstrip().splitlines(keepends=True)                 # :47
if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and output["returncode"] == 0:  # :48
    submission = "".join(lines[1:])                                                 # :49
    raise Submitted({"role": "exit", "content": submission,
                     "extra": {"exit_status": "Submitted", "submission": submission}})  # :50-56
```

判定条件三个，缺一不可：**首行**精确等于魔法字符串（前导空白容忍）、返回码为 0、其余行作为 submission。所以提示词反复强调 "Do not combine it with any other command"（`mini.yaml:18-19`）——`echo 哨兵 && ls` 会让首行不再是哨兵；`false; echo 哨兵` 会让返回码非零。

**为什么检查在环境侧？** 因为"命令输出"只有环境拿得到第一手；agent 看到的已是格式化后的观察。把宝鉴定放在产地，agent 保持纯粹。这也是 m12 里自定义环境必须复制的一段逻辑（Docker 版 140-151 行逐字相同——项目容忍这两份重复，换取每个类的独立可读）。

## 五、DockerEnvironment：容器生命周期

### 5.1 配置面（15-42 行）

| 字段 | 默认 | 说明 |
|---|---|---|
| `image` | 必填 | 镜像名 |
| `cwd` | `"/"` | 容器内工作目录（swebench 设为 `/testbed`） |
| `env` / `forward_env` | `{}` / `[]` | 显式变量 / 从宿主转发的变量名；冲突时 env 优先（21-25 行 docstring） |
| `timeout` | `30` | 单条命令超时 |
| `executable` | `docker` | 可换 podman（`MSWEA_DOCKER_EXECUTABLE`） |
| `run_args` | `["--rm"]` | 容器退出即删除 |
| `container_timeout` | `"2h"` | 容器最长存活（sleep 的参数） |
| `pull_timeout` | `120` | 拉镜像超时 |
| `interpreter` | `["bash", "-lc"]` | 命令解释器，可改 python 等 |

### 5.2 启动：一个 sleep 撑起的长容器（74-99 行）

```python
cmd = [executable, "run", "-d", "--name", container_name, "-w", cwd,
       *run_args, image, "sleep", container_timeout]     # :77-89
```

容器的主进程是 `sleep 2h`——一个什么都不干、只是活着的过程。之后每条命令都是 `docker exec` 进这个容器新开 shell（107-113 行）：

```python
cmd = [self.config.executable, "exec", "-w", cwd]        # :107
# forward_env 与 env 转成 -e KEY=VALUE                    :108-112
cmd.extend([self.container_id, *self.config.interpreter, command])  # :113
```

对照本地版：`Popen(command, shell=True)` 变成 `subprocess.run([docker, exec, ..., bash, -lc, command])`。**执行语义完全一致**（独立子壳、合并 stderr、超时包成 returncode -1），这就是"换环境不换 agent"的实证。116-124 行用 `subprocess.run` 而非 `_run`——容器内超时由 docker 自己收拾进程，宿主无需 killpg。

### 5.3 销毁：异步兜底（153-161 行）

```python
def cleanup(self):
    if getattr(self, "container_id", None) is not None:   # :155  init 早期失败时属性可能不存在
        cmd = f"(timeout 60 {executable} stop {id} || {executable} rm -f {id}) >/dev/null 2>&1 &"  # :156
        subprocess.Popen(cmd, shell=True)                 # :157

def __del__(self):
    self.cleanup()                                        # :159-161
```

`stop` 给 60 秒宽限，不停就 `rm -f` 强删；整个命令后台执行，不阻塞退出。`__del__` 挂钩保证对象回收时容器不会泄漏。加上 `--rm` 与 `sleep 2h`，容器有三重死法：正常清理、超时自杀、退出随删——评测集群里不会堆积僵尸容器。

## 六、其余环境：同一协议的变奏

- **Singularity**（`singularity.py`）：没有 daemon，先把镜像 build 成 sandbox 目录（46-65 行），失败重试 3 次（`sandbox_build_retries`，29-30 行），之后 `singularity exec` 进目录执行。默认 `--contain --cleanenv --fakeroot`（33 行）做隔离。HPC 集群的普通用户用不了 Docker，这是为他们准备的。
- **Bubblewrap**（`extra/bubblewrap.py`）：Linux 非特权沙箱，`wrapper_args` 默认只读绑定 `/usr /bin /lib /lib64 /etc`、tmpfs 挂 `/tmp`、`--unshare-user-try`（38-65 行）。文件头两个 warning：实验性、不支持 Windows（6-10 行）。
- **swerex_* / contree**：把 execute 委托给 SWE-ReX 执行服务或 Contree SDK，用于远程主机与云沙箱（Modal）。协议不变，只是 subprocess 换成了 RPC。

## 七、无状态子壳：一次回顾

现在可以把 m01 的支柱二落到代码了：本地版每条命令 `Popen(shell=True)` 新进程，Docker 版每条命令 `docker exec` 新 shell。**没有任何 shell 状态跨命令存活**。提示词把这个约束如实告知模型并教绕过技巧（`mini.yaml:39-40`），换来的是：

- 环境实现可互换（本章五个实现同协议）；
- 并发安全（无线程间共享 shell）；
- 超时干净（进程组/容器边界即清理边界）；
- 轨迹可重放（每条命令自包含，不依赖前序 shell 状态）。

## 八、小结

- execute 的契约：进 action dict，出 output dict；执行异常包成数据（returncode -1），只有 Submitted 穿透。
- 本地版精髓在 `_run`：新会话 + killpg 杀整组 + 超时保留部分输出。
- 哨兵三条件：首行精确匹配、返回码 0、余行即 submission；检查在环境侧，agent 无感。
- Docker 版 = sleep 长容器 + 逐条 exec + 三重销毁兜底；与本地版执行语义逐项对齐。
- 下一章把用户请进循环：human/confirm/yolo 三模式与中断恢复。
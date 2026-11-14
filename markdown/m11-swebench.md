# 基准测试 —— 批量跑 SWE-bench 的工程细节

> 本章源文件：`run/benchmarks/swebench.py`（275 行）、`swebench_single.py`（100 行）、`utils/batch_progress.py`（178 行）、`utils/common.py`（17 行）、`config/benchmarks/swebench.yaml`（183 行）。

## 一、引子：74% 是怎么跑出来的

m01 的成绩单背后是 `mini-extra swebench`：加载数据集、为每个 issue 起一个 Docker 容器、并发跑 agent、收集 patch、汇总死因。这一章讲把"跑一个任务"变成"跑五百个任务"要补哪些工程。读完你应该能回答三个问题：

1. 一个 SWE-bench instance 从数据集到 preds.json 的完整旅程是什么？
2. 跑到一半断了怎么办？为什么重跑同一命令不会重复花钱？
3. 多线程并发下，成本统计、进度条、结果文件各自怎么保证线程安全？

## 二、先把词对齐

| 术语 | 含义 |
|---|---|
| instance | 一个评测样本：真实 repo 的 issue（`problem_statement`）+ 专属 Docker 镜像 |
| preds.json | 汇总产出文件：`{instance_id: {model_name_or_path, instance_id, model_patch}}` |
| submission / model_patch | agent 提交的 git diff 文本，评测器拿它跑测试判对错 |
| exit_status | 每个 instance 的死因，汇总成 yaml 报告 |
| subset | 数据集变体：lite / verified / full / multimodal 等 |

## 三、全景：main 的流水线（202-271 行）

```text
mini-extra swebench --subset lite --slice 0:10 -w 4 -m ...
│
├─ 输出目录 + 文件日志                    :217-220
├─ load_dataset(subset, split)           :222-226  （DATASET_MAPPING 53-62）
├─ filter_instances：正则过滤/切片/洗牌   :228 → :180-197
├─ 跳过 preds.json 里已有的一切（含失败）  :229-232  ← 断点续跑
├─ 配置合并（YAML + CLI 覆盖）            :235-241
├─ RunBatchProgressManager               :243
└─ Live 进度界面 + ThreadPoolExecutor    :256-271
     └─ 每个 instance → process_instance :122-177
```

## 四、一个 instance 的旅程（process_instance，122-177 行）

### 4.1 开场先清场（129-133 行）

```python
remove_from_preds_file(output_dir / "preds.json", instance_id)      # :132
(instance_dir / f"{instance_id}.traj.json").unlink(missing_ok=True) # :133
```

注释说明意图（131 行）：避免上次失败的残留造成不一致状态。**先删旧结果再开跑**——上次跑到一半崩溃（没来得及写 preds）的 instance 因此不会被误判为已完成。注意 preds.json 的条目代表"跑完了"而非"跑成功了"：失败 instance 同样写入（patch 为空串，见 4.4）。

### 4.2 环境：镜像名里的魔法（68-94 行）

`get_swebench_docker_image_name`（68-76 行）：instance 自带镜像名就用，否则按约定构造 `docker.io/swebench/sweb.eval.x86_64.{id}:latest`。Docker 镜像名不允许双下划线，所以 instance_id 里的 `__` 替换成 `_1776_`（74 行，注释自称 magic token）。

`get_sb_environment`（79-94 行）：默认 docker；singularity/contree 要加 `docker://` 前缀（85-86 行）；支持 `env_startup_command`——用 instance 字段渲染后先跑一条初始化命令，非零返回码直接抛错（89-93 行）。

### 4.3 跑 agent：ProgressTrackingAgent（145-156 行）

批量跑用的不是 DefaultAgent 而是 `ProgressTrackingAgent`（`utils/common.py:7-17`）——覆写 `__init__` 收下 progress_manager 与 instance_id（10-13 行），再覆写 step 先报一句 `Step N ($cost)` 再调父类（15-17 行）。m03 说过 step 是子类的标准插头，这是最简实证。

### 4.4 收尾：finally 里的三件事（157-177 行）

```python
except Exception as e:
    exit_status, result = type(e).__name__, ""                       # :159
    extra_info = {"traceback": ..., "exception_str": ...}            # :160
finally:
    agent.save(traj_path, {"info": {"exit_status": ..., "submission": ..., **extra_info},
                           "instance_id": instance_id})              # :162-174
    update_preds_file(output_dir / "preds.json", instance_id, model_name, result)  # :176
    progress_manager.on_instance_end(instance_id, exit_status)       # :177
```

任何异常都被收成"异常类名当 exit_status"，**单个 instance 失败不拖垮整批**。save 的 extra_dicts 覆写轨迹元数据（m10 的合并顺序在此用上）；preds.json 里失败 instance 的 patch 是空串——评测时自然判错。

## 五、并发与线程安全

### 5.1 执行模型（256-271 行）

`ThreadPoolExecutor(max_workers=workers)` + rich `Live` 进度界面。线程而非进程：瓶颈在 API 与 Docker I/O，GIL 不是问题；且线程共享 `GLOBAL_MODEL_STATS`，成本汇总天然统一。

Ctrl-C 的处理（264-271 行）：第一次按下取消所有**未开始**的 future，等在跑的自然结束；再按一次才立即退出。批量评测的礼貌中断。

### 5.2 三处锁

| 共享资源 | 保护 | 位置 |
|---|---|---|
| preds.json 读改写 | `_OUTPUT_FILE_LOCK` | `swebench.py:65`、97-108、111-119 |
| 全局成本/调用数 | `GlobalModelStats._lock` | `models/__init__.py:19`、27-29 |
| 进度条与状态表 | `RunBatchProgressManager._lock` | `batch_progress.py:52` |

preds.json 的更新是"读全量 → 改一项 → 写全量"（100-108 行），无锁必丢数据；锁是模块级单例，所有 worker 线程共享。

### 5.3 进度界面（batch_progress.py）

三层渲染（82 行的 Group）：总进度条（含**实时总成本**，117-123 行从 GLOBAL_MODEL_STATS 读）、exit_status 汇总表（99-115 行，按数量排序、列最近 instance）、每个在跑 instance 一行状态。ETA 用已完成均速估算（89-97 行），且主进度条前 5 分钟不做速度估计（66 行注释）——避免初期抖动误导。

每有 instance 结束就把汇总写一份 `exit_statuses_<timestamp>.yaml`（155-156、175-178 行）——界面之外留持久报告。

## 六、断点续跑与筛选

### 6.1 续跑（229-232 行）

```python
if not redo_existing and (output_path / "preds.json").exists():
    existing_instances = list(json.loads(...).keys())
    instances = [i for i in instances if i["instance_id"] not in existing_instances]
```

**同一输出目录重跑同一命令 = 跳过 preds.json 里已有的一切**。注意"已有"包括失败 instance（4.4 里失败也写入、patch 为空串）——所以失败默认**不会**重试；想重跑失败或全部，加 `--redo-existing`，或手动从 preds.json 删掉对应键。配合 4.1 的"先清场"，中断重跑不会留下半旧半新的结果。

### 6.2 筛选（filter_instances，180-197 行）

三个操作按序：洗牌（`--shuffle`，先按 id 排序再 `random.seed(42)` 洗——**可复现的随机**，184-187 行）→ 正则过滤 instance_id（189 行）→ Python 切片语法 `--slice 0:5`（192-194 行）。冒烟测试常用 `--slice 0:3 --subset lite`。

## 七、swebench.yaml：评测专用的提示词工程

与 `mini.yaml` 的关键差异（`config/benchmarks/swebench.yaml`）：

| 配置 | 值 | 为什么 |
|---|---|---|
| `step_limit` | 250（112 行） | 评测要封顶，CLI 默认不限 |
| `environment.cwd` | `/testbed`（116 行） | SWE-bench 镜像的 repo 位置 |
| `environment.interpreter` | `["bash", "-c"]`（118 行） | 非登录 shell |
| `env.BASH_ENV` | `/root/.bashrc`（125-127 行） | 注释解释：`bash -c` 不读 bashrc，靠 BASH_ENV 让镜像里的 `conda activate testbed` 生效，否则命令跑在 base 环境 |
| `model_kwargs.parallel_tool_calls` | `true`（183 行） | 鼓励模型一次发多个独立命令 |
| 提交流程 | `git diff > patch.txt` → 检查 → `echo 哨兵 && cat patch.txt`（77-110 行） | submission 必须是 patch；哨兵与 cat 用 && 连接，cat 失败则返回码非零不提交 |

最后一条与 m07 的哨兵判定严丝合缝：`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt` 的首行是哨兵、返回码 0——两个判定条件全中，余下的 patch 文本就成了 submission。提示词还严禁提交测试文件/辅助脚本/二进制（84-92 行），这些都是评测有效性的护栏。

## 八、swebench_single：调试单例的入口

`swebench_single.py`（100 行）是批量版的单发变体：`-i` 给 instance_id 或排序后的索引（65-67 行），默认 interactive agent（94 行）——可以 confirm 模式围观模型解一个真实 issue，是理解评测行为的最佳实验场。环境构造复用 `get_sb_environment`（89 行），配置流水线与 mini.py 同构。

## 九、小结

- 批量跑 = 数据集加载 → 筛选/续跑 → 线程池 → 每实例独立容器与轨迹 → preds.json 汇总。
- 线程安全三处锁：结果文件、全局统计、进度管理；失败实例以异常类名记 exit_status，不拖垮批次。
- 断点续跑靠 preds.json 键集合 + 开跑先清场；失败实例也在键集合里，默认不重试（`--redo-existing` 重来）；洗牌固定 seed 42 可复现。
- 评测提示词的关键护栏：250 步封顶、/testbed、BASH_ENV 激活 conda、patch 提交三条件。
- 最后一章：不改源码，把这些组件换成你自己的。
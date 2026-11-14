<div align="center">

# mini-swe-agent 源码教程

**一百行代码，74% 的 SWE-bench —— 把最简单的 coding agent 逐行拆开**

[![章节](https://img.shields.io/badge/章节-12%20章-blue)](#目录)
[![源码版本](https://img.shields.io/badge/源码-v2.4.6-orange)](https://github.com/SWE-agent/mini-swe-agent)
[![源码协议](https://img.shields.io/badge/源码协议-MIT-green)](https://github.com/SWE-agent/mini-swe-agent/blob/main/LICENSE)
[![风格](https://img.shields.io/badge/风格-pi--agent--book-ff69b4)](https://github.com/Daniel-csvw/pi-agent-book)

[**在线阅读**](https://Daniel-csvw.github.io/mini-swe-agent-book/) · [Markdown 源文件](markdown/) · [原项目](https://github.com/SWE-agent/mini-swe-agent)

</div>

---

## 这是什么

[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) 用不到一百行的核心循环，在 SWE-bench Verified 上拿到 74% 的成绩。本书把它的源码（v2.4.6）逐行拆开：**异常驱动的控制流、三条动作解析路线、子壳与哨兵、重试与计费契约、配置递归合并、轨迹持久化、批量基准工程**——全部讲透。

不是 API 文档的复读，而是回答"**为什么这样写**"：

- 为什么"提交任务""超出限额""格式错误"都是异常，而不是返回码？
- 模型输出格式错误时 API 已经计费了，这笔钱记在哪里？
- 为什么上下文超了就让 API 报错，而不是悄悄修剪历史？

## 在线阅读

**<https://Daniel-csvw.github.io/mini-swe-agent-book/>**

无框架纯静态站点：侧栏目录、上一章/下一章、暗色模式（记住选择）、语法高亮、移动端适配。

## 目录

| # | 章节 | 一句话内容 |
|---|------|-----------|
| 01 | [概览 —— 一百行代码，74% 的 SWE-bench](markdown/m01-overview.md) | 成绩单、三根设计支柱与 v1→v2 沿革 |
| 02 | [包地图 —— 五个核心目录，三个协议](markdown/m02-package-map.md) | 五个核心目录、三个协议，以及一条 `mini` 命令从配置到 `agent.run` 的完整旅程 |
| 03 | [Agent 循环 —— 异常是唯一的控制流](markdown/m03-agent-loop.md) | run/step/query/execute_actions 逐段精读 |
| 04 | [消息与模板 —— 一切皆 dict，渲染靠 Jinja](markdown/m04-messages-templates.md) | extra 元数据袋、六层模板变量、观察截断 |
| 05 | [模型调用 —— 重试、计费与格式错误契约](markdown/m05-model-call.md) | 消息清洗、重试节奏、两级限额、FormatError 契约 |
| 06 | [动作解析 —— 一个 bash 工具打天下](markdown/m06-actions.md) | toolcall / textbased / Responses API 三条路线 |
| 07 | [环境 —— 子壳、哨兵与进程组](markdown/m07-environments.md) | 子壳执行、哨兵命令、进程组击杀、Docker 生命周期 |
| 08 | [交互模式 —— human/confirm/yolo 三态](markdown/m08-interactive.md) | 三态切换、Ctrl-C 插话、限额提额 |
| 09 | [配置系统 —— YAML、UNSET 与递归合并](markdown/m09-config.md) | YAML 查找、键值对解析、UNSET 哨兵与三个工厂 |
| 10 | [轨迹持久化 —— traj.json 与 inspector](markdown/m10-trajectory.md) | traj.json 解剖、每步落盘、inspector 回放 |
| 11 | [基准测试 —— 批量跑 SWE-bench 的工程细节](markdown/m11-swebench.md) | instance 旅程、并发三处锁、断点续跑 |
| 12 | [扩展指南 —— 写你自己的 Model/Environment/Agent](markdown/m12-extending.md) | 协议契约、确定性模型、元模型、不改源码接线 |

每章结构：**引子（读完能回答的三个问题）→ 术语对齐 → 全景 → 逐段精读 → 小结与下一章预告**。所有引用带 `文件:行号`，可直接对照源码核验。

## 本地阅读与构建

Markdown 源文件在 GitHub / 编辑器里直接可读。想生成同款静态站点：

```bash
pip install markdown pygments
python build.py        # 在本仓库根目录运行
python -m http.server 8000   # 打开 http://localhost:8000
```

产物：`index.html`、`modules/*.html`、`pygments.css`。Markdown 是唯一事实来源，改内容请改 `markdown/` 再重新构建。

## 仓库结构

```
├── index.html          # 站点首页（构建产物）
├── modules/            # 12 章页面（构建产物）
├── styles.css          # 站点样式（明暗双主题）
├── pygments.css        # 语法高亮（构建产物）
├── build.py            # 构建脚本：Markdown → HTML
└── markdown/           # 12 章教程源文件
    ├── m01-overview.md
    ├── ...
    └── m12-extending.md
```

## 致谢与版权

- 写作风格致敬 [pi-agent-book](https://github.com/Daniel-csvw/pi-agent-book)：中文写作、引子带问题、术语先对齐、全景后精读、行号可验证。
- 被解读的源码版权归 [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) 项目所有（MIT License）；本书为学习用途的源码解读，行号基于 v2.4.6，可能随版本漂移。
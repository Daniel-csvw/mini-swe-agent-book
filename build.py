"""Build the mini-swe-agent source tutorial: Markdown -> static HTML, no frameworks.

Usage:
    pip install markdown pygments
    python build.py
"""

import math
import re
from pathlib import Path

import markdown
from pygments.formatters import HtmlFormatter

book_dir = Path(__file__).resolve().parent

CHAPTERS = [
    ("m01-overview", "概览：一百行代码与 74% 的成绩单，三根设计支柱与 v1→v2 沿革。"),
    ("m02-package-map", "包地图：五个核心目录、三个协议、依赖清单与程序入口。"),
    ("m03-agent-loop", "Agent 循环：run/step/query/execute_actions，异常是唯一的控制流。"),
    ("m04-messages-templates", "消息与模板：extra 元数据袋、六层模板变量、观察截断的艺术。"),
    ("m05-model-call", "模型调用：消息清洗、重试节奏、两级限额、FormatError 契约。"),
    ("m06-actions", "动作解析：toolcall / textbased / Responses API 三条路线一个产物。"),
    ("m07-environments", "环境：子壳执行、哨兵命令、进程组击杀、Docker 生命周期。"),
    ("m08-interactive", "交互模式：human/confirm/yolo 三态、Ctrl-C 插话、限额提额。"),
    ("m09-config", "配置系统：YAML 查找、键值对解析、UNSET 哨兵与三个工厂。"),
    ("m10-trajectory", "轨迹持久化：traj.json 解剖、每步落盘、inspector 回放。"),
    ("m11-swebench", "基准测试：instance 旅程、并发三处锁、断点续跑、评测提示词。"),
    ("m12-extending", "扩展指南：协议契约、确定性模型、元模型、不改源码接线。"),
]

THEME_JS = """(function(){var t=localStorage.getItem("theme");
if(!t){t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}
document.documentElement.dataset.theme=t;})();"""

TOGGLE_JS = """function toggleTheme(){var h=document.documentElement;
var t=h.dataset.theme==="dark"?"light":"dark";h.dataset.theme=t;localStorage.setItem("theme",t);}"""


def render_page(*, title: str, body: str, css_prefix: str, nav_html: str, pager: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>{THEME_JS}</script>
<link rel="stylesheet" href="{css_prefix}styles.css">
<link rel="stylesheet" href="{css_prefix}pygments.css">
</head>
<body>
<div class="page">
<aside class="sidebar">
  <p class="book-title">mini-swe-agent<br>源码教程</p>
  <p class="book-sub">基于 v2.4.6 · 12 章</p>
  <nav>{nav_html}</nav>
</aside>
<div class="main">
  <div class="topbar"><button class="theme-toggle" onclick="toggleTheme()">切换明暗</button></div>
  <div class="content">
{body}
{pager}
  </div>
</div>
</div>
<script>{TOGGLE_JS}</script>
</body>
</html>
"""


def chapter_nav(active: str, prefix: str) -> str:
    links = [f'<a href="{prefix}index.html">首页</a>']
    for slug, _ in CHAPTERS:
        no = slug[:3].upper()
        cls = ' class="active"' if slug == active else ""
        title = TITLES[slug].split(" —— ")[0]
        links.append(f'<a href="{prefix}modules/{slug}.html"{cls}><span class="no">{no}</span>{title}</a>')
    return "\n".join(links)


def reading_minutes(text: str) -> int:
    return max(1, math.ceil(len(re.sub(r"\s", "", text)) / 300))


TITLES: dict[str, str] = {}
MINUTES: dict[str, int] = {}
BODIES: dict[str, str] = {}

md = markdown.Markdown(
    extensions=["tables", "fenced_code", "codehilite", "sane_lists"],
    extension_configs={"codehilite": {"guess_lang": False, "pygments_style": "monokai"}},
)

for slug, _ in CHAPTERS:
    source = (book_dir / "markdown" / f"{slug}.md").read_text(encoding="utf-8")
    TITLES[slug] = re.match(r"# (.+)", source).group(1)
    MINUTES[slug] = reading_minutes(source)
    md.reset()
    html = md.convert(source)
    html = html.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    html = html.replace("<blockquote>", f'<p class="meta">约 {MINUTES[slug]} 分钟读完</p><blockquote>', 1)
    BODIES[slug] = html

(book_dir / "modules").mkdir(exist_ok=True)
(book_dir / "pygments.css").write_text(
    HtmlFormatter(style="monokai").get_style_defs(".codehilite"), encoding="utf-8"
)

for i, (slug, _) in enumerate(CHAPTERS):
    pager = '<div class="pager">'
    if i > 0:
        prev = CHAPTERS[i - 1][0]
        pager += f'<a href="{prev}.html"><span class="dir">← 上一章</span>{TITLES[prev]}</a>'
    if i + 1 < len(CHAPTERS):
        nxt = CHAPTERS[i + 1][0]
        pager += f'<a class="next" href="{nxt}.html"><span class="dir">下一章 →</span>{TITLES[nxt]}</a>'
    pager += "</div>"
    (book_dir / "modules" / f"{slug}.html").write_text(
        render_page(
            title=f"{TITLES[slug]} | mini-swe-agent 源码教程",
            body=BODIES[slug],
            css_prefix="../",
            nav_html=chapter_nav(slug, "../"),
            pager=pager,
        ),
        encoding="utf-8",
    )

cards = "\n".join(
    f'<a class="card" href="modules/{slug}.html"><span class="no">{slug[:3].upper()}</span>'
    f"<h3>{TITLES[slug]}</h3><p>{desc}</p>"
    f'<span class="rt">约 {MINUTES[slug]} 分钟</span></a>'
    for slug, desc in CHAPTERS
)
total = sum(MINUTES.values())
index_body = f"""<div class="hero">
<h1>mini-swe-agent 源码教程</h1>
<p class="tagline">一百行代码，74% 的 SWE-bench —— 把最简单的 coding agent 逐行拆开。</p>
<p class="stats">基于 v2.4.6 · 12 章 · 共约 {total} 分钟 · 全部引用可核对行号</p>
</div>
<div class="cards">
{cards}
</div>
<div class="colophon">
<p>本书风格致敬 <a href="https://github.com/Daniel-csvw/pi-agent-book">pi-agent-book</a>：中文写作、引子带问题、术语先对齐、全景后精读、行号可验证。
源码版权归 mini-swe-agent 项目（MIT License）所有；本书为学习用途的源码解读。</p>
<p>Markdown 源文件在 <code>markdown/</code>，改动后运行 <code>python build.py</code> 重新生成本站。</p>
</div>"""

(book_dir / "index.html").write_text(
    render_page(
        title="mini-swe-agent 源码教程",
        body=index_body,
        css_prefix="",
        nav_html=chapter_nav("", ""),
    ),
    encoding="utf-8",
)

print(f"Built {len(CHAPTERS)} chapters + index.html into {book_dir}")
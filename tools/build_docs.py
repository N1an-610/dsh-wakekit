#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_docs.py — 把 注意事项.md 生成 注意事项.html（同一份源，不手抄第二遍）。

只支持这份文档用到的 Markdown 子集：
  # 标题 / 段落 / **粗体** / `代码` / 链接
  - 无序列表 / 1. 有序列表 / > 引用 / --- 分隔线 / ``` 代码块 / | 表格 |

用法：
  python tools/build_docs.py           # 生成
  python tools/build_docs.py --check   # 只检查 html 是否和 md 一致（不一致退出码 1）
"""

import html
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
MD = KIT / "注意事项.md"
HTML = KIT / "注意事项.html"
TITLE = "醒来箱 wakekit · 注意事项"

CSS = """
:root { color-scheme: light dark; }
body {
  max-width: 860px; margin: 40px auto; padding: 0 20px;
  font: 16px/1.75 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  color: #222; background: #fff;
}
h1 { font-size: 30px; margin: 0 0 8px; }
h2 { font-size: 22px; margin: 40px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #eee; }
h3 { font-size: 18px; margin: 26px 0 8px; }
p { margin: 10px 0; }
blockquote {
  margin: 16px 0; padding: 10px 16px; border-left: 4px solid #d8d8d8;
  background: #fafafa; color: #444;
}
code {
  font: 14px/1.5 "Cascadia Code", Consolas, monospace;
  background: #f2f2f2; padding: 1px 5px; border-radius: 4px;
}
pre { background: #f6f6f6; padding: 14px 16px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 16px 0; width: 100%; font-size: 15px; }
th, td { border: 1px solid #ddd; padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: #f6f6f6; }
hr { border: 0; border-top: 1px solid #eee; margin: 32px 0; }
li { margin: 4px 0; }
a { color: #0b62c4; }
@media (prefers-color-scheme: dark) {
  body { color: #ddd; background: #1b1b1b; }
  blockquote { background: #232323; border-left-color: #444; color: #bbb; }
  code, pre { background: #2a2a2a; }
  th { background: #262626; }
  th, td { border-color: #3a3a3a; }
  h2 { border-bottom-color: #333; }
  hr { border-top-color: #333; }
  a { color: #6cb0ff; }
}
"""


def inline(text):
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def join_lines(a, b):
    """中文行之间不插空格；英文单词之间插一个。"""
    if not a:
        return b
    if not b:
        return a
    if a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
        return a + " " + b
    return a + b


def is_table_sep(line):
    return bool(re.fullmatch(r"\|[\s:|-]+\|", line.strip())) and "-" in line


def render(md_text):
    lines = md_text.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # 代码块
        if line.startswith("```"):
            lang = line[3:].strip()
            buf = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            code = html.escape("\n".join(buf))
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{code}</code></pre>")
            continue

        # 分隔线
        if line.strip() == "---":
            out.append("<hr>")
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # 引用
        if line.startswith(">"):
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip())
                i += 1
            body = " ".join(x for x in buf if x)
            out.append(f"<blockquote><p>{inline(body)}</p></blockquote>")
            continue

        # 表格
        if line.strip().startswith("|") and i + 1 < n and is_table_sep(lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in header)
            trs = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
            continue

        # 无序列表
        if re.match(r"^-\s+", line):
            items = []
            while i < n and re.match(r"^-\s+", lines[i]):
                items.append(lines[i][2:].strip())
                i += 1
                # 续行（缩进）
                while i < n and lines[i].startswith("  ") and lines[i].strip():
                    items[-1] = join_lines(items[-1], lines[i].strip())
                    i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue

        # 有序列表
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i]).strip())
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 段落（合并连续非空行）
        buf = line.strip()
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|>|-\s|\d+\.\s|\||```|---$)", lines[i].strip()):
            buf = join_lines(buf, lines[i].strip())
            i += 1
        out.append(f"<p>{inline(buf)}</p>")

    return "\n".join(out)


def build():
    md = MD.read_text(encoding="utf-8")
    body = render(md)
    doc = (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(TITLE)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
    return doc


def main():
    doc = build()
    if "--check" in sys.argv:
        if not HTML.exists():
            print("还没有 注意事项.html，先跑一次 python tools/build_docs.py")
            return 1
        same = HTML.read_text(encoding="utf-8") == doc
        print("一致" if same else "不一致：md 改过但 html 没重新生成")
        return 0 if same else 1
    HTML.write_text(doc, encoding="utf-8")
    print(f"已生成 {HTML.name}（{len(doc)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

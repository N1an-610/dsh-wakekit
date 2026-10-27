#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_wake.py — 生成「醒来包」，并替换角色卡里的标记块。

醒来包 = 重置/新会话后第一眼该看到的东西：火种、说过的原话、我在做什么、历代目录。

来源：<memories_file>
模板：templates/醒来包-模板.md（找不到就用内置骨架）
     占位符：{{日期}} {{火种}} {{摘要}} {{原话}} {{在做}} {{目录}} {{对方}}
     某个占位符没有内容时，它**连同上面那行 "## 标题" 一起**被去掉，不留空壳。
输出：<wake_file>
      若 paths.card_path 非空：替换卡片里 <!-- WAKE:BEGIN --> … <!-- WAKE:END --> 之间的内容
      （替换前一定备份成 卡片名.bak-时间戳）

规则：
  · 火种（tier=火种 或带「火种」标签）排最前，条数与每条字数都由配置限死
  · private 条目永不出现（features.privacy 为真时）
  · 「我现在在做什么」取最新一条 tag=在做 的记忆（排除私密），没有就整节省略
  · 总字数超过 limits.wake_max_chars 时，先砍原话、再砍火种，最后才硬截断

用法：
  python tools/build_wake.py                 # 生成并注入卡片
  python tools/build_wake.py --no-card       # 只生成 wake_file，不碰卡片
  python tools/build_wake.py --dry-run       # 打到 stdout，不写文件
  python tools/build_wake.py --print-marker  # 打印卡片里该放的两行标记
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402
import semlib  # noqa: E402

MARK_BEGIN = "<!-- WAKE:BEGIN -->"
MARK_END = "<!-- WAKE:END -->"

PLACEHOLDER_RE = re.compile(r"^\{\{\s*([^\s{}]+)\s*\}\}$")

BUILTIN_TEMPLATE = """# 醒来包 · {{日期}}

## 火种

{{火种}}

## 我们说过的

{{原话}}

## 我现在在做什么

{{在做}}

## 历代醒来包

{{目录}}
"""


# ── 读 ─────────────────────────────────────────────────────────────────

def read_entries(cfg):
    path = Path(cfg["paths"]["memories_file"])
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows


def is_ember(e):
    return e.get("tier") == "火种" or "火种" in (e.get("tags") or [])


def is_quote(e):
    tags = e.get("tags") or []
    return ("原话" in tags or "约定" in tags) and not is_ember(e)


def one_line(text, limit):
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)] + "…"


def pick_focus(pool, limit):
    """「我现在在做什么」：取最新一条 tag=在做 的记忆；排除私密；没有就整节省略。"""
    cand = [e for e in pool if "在做" in (e.get("tags") or []) and not e.get("private")]
    if not cand:
        return ""
    cand.sort(key=lambda e: -(e.get("ts") or 0))
    return one_line(cand[0].get("text") or cand[0].get("summary"), limit)


def build_digest(cfg, pool, limit):
    """「上次到哪」：开关关掉就返回空（整节会被去掉）。只看摘要，不看正文；私密永不进。"""
    if not cfg["features"].get("summary_inject"):
        return ""
    rows = [e for e in pool if not e.get("private")]
    rows.sort(key=lambda e: -(e.get("ts") or 0))
    lines, total = [], 0
    for e in rows[: int(cfg["limits"].get("summary_scan", 20))]:
        d = datetime.fromtimestamp((e.get("ts") or 0) / 1000).strftime("%m-%d")
        line = f"- {d} {one_line(e.get('summary') or e.get('text'), 60)}"
        if total + len(line) > limit:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def wake_files(cfg):
    d = Path(cfg["paths"]["wake_dir"])
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("醒-*.md"):
        try:
            n = int(f.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        out.append((n, f))
    out.sort()
    return out


def index_lines(cfg, limit):
    files = wake_files(cfg)
    rows = []
    for n, f in reversed(files):           # 新的在前
        head = ""
        try:
            first = f.read_text(encoding="utf-8", errors="replace").splitlines()
            head = first[0].lstrip("# ").strip() if first else ""
        except OSError:
            pass
        size = f.stat().st_size
        rows.append(f"醒-{n:03d} · {head or '（无标题）'} · {size} 字节")
    if not rows:
        return "- （还没有历史醒来包）"
    shown = rows[:limit]
    if len(rows) > limit:
        shown.append(f"- 更早的 {len(rows) - limit} 份省略")
    return "\n".join(f"- {r}" for r in shown)


# ── 模板 ───────────────────────────────────────────────────────────────

def load_template(cfg):
    candidates = [
        Path(cfg["__file"]).parent / "templates" / "醒来包-模板.md",
        Path(cfg["__root"]) / "templates" / "醒来包-模板.md",
    ]
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8"), str(p)
            except OSError:
                continue
    return BUILTIN_TEMPLATE, "(内置模板)"


def render_template(tmpl, values):
    out = []
    for line in tmpl.replace("\r\n", "\n").split("\n"):
        m = PLACEHOLDER_RE.match(line.strip())
        if not m:
            out.append(line)
            continue
        val = (values.get(m.group(1)) or "").strip()
        if val:
            out.append(val)
            continue
        # 这一节没内容：连它上面那行 "## 标题" 和多余空行一起删掉
        while out and not out[-1].strip():
            out.pop()
        if out and out[-1].lstrip().startswith("## "):
            out.pop()
        while out and not out[-1].strip():
            out.pop()
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    # 行内占位符（例如标题里的 {{日期}}）在这里兜底替换
    text = re.sub(r"\{\{\s*([^\s{}]+)\s*\}\}",
                  lambda m: str(values.get(m.group(1)) or m.group(0)), text)
    return text.strip() + "\n"


# ── 组装 ───────────────────────────────────────────────────────────────

def compose(cfg, entries):
    limits = cfg["limits"]
    wake_cfg = cfg.get("wake") or {}
    # wake.* 优先；没配就退回 limits.*（老配置仍能用）
    ember_max = int(wake_cfg.get("ember_max") or limits["ember_max"])
    ember_max_chars = int(wake_cfg.get("ember_max_chars") or limits["ember_max_chars"])
    privacy = cfg["features"]["privacy"]

    def usable(e):
        if e.get("status") == "superseded":
            return False
        if privacy and e.get("private"):
            return False
        # 场景分层：醒来包只取「只对你一个人」的那些
        # 群聊照常入库、照常能搜，只是不进第一眼
        if (e.get("scope") or "private") != "private":
            return False
        return True

    pool = [e for e in entries if usable(e)]
    embers = sorted([e for e in pool if is_ember(e)], key=lambda e: -(e.get("ts") or 0))
    embers = embers[:ember_max]
    quotes = sorted([e for e in pool if is_quote(e)], key=lambda e: -(e.get("ts") or 0))
    quotes = quotes[: limits["quote_max"]]
    dropped = 0

    tmpl, tmpl_src = load_template(cfg)

    def values():
        ember_lines = [f"- {one_line(e.get('summary') or e.get('text'), ember_max_chars)}"
                       for e in embers]
        if dropped:
            ember_lines.append(f"- （还有 {dropped} 条，用 memory_search 搜）")
        return {
            "日期": datetime.now().strftime("%Y-%m-%d"),
            "火种": "\n".join(ember_lines),
            "原话": "\n".join(
                f"- {one_line(e.get('text'), ember_max_chars)}" + (
                    f"（{tag}）" if (tag := (e.get('tags') or [''])[0]) and tag != "原话" else "")
                for e in quotes),
            "在做": pick_focus(pool, limits.get("focus_max_chars", 120)),
            "摘要": build_digest(cfg, pool, limits["summary_inject_max_chars"]),
            "对方": (cfg.get("labels") or {}).get("partner") or "对方",
            "目录": index_lines(cfg, limits["wake_index_limit"]),
        }

    text = render_template(tmpl, values())

    # 超字数：先砍原话，再砍火种（砍掉的要报数），最后硬截
    while len(text) > limits["wake_max_chars"] and (quotes or embers):
        if quotes:
            quotes.pop()
        else:
            embers.pop()
            dropped += 1
        text = render_template(tmpl, values())

    if len(text) > limits["wake_max_chars"]:
        text = text[: limits["wake_max_chars"] - 20] + "\n\n（已按字数上限截断）\n"

    return text, len(embers), len(quotes), tmpl_src, dropped


# ── 卡片注入 ───────────────────────────────────────────────────────────

def backup_card(card, keep):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = card.with_name(f"{card.name}.bak-{stamp}")
    shutil.copy2(card, bak)
    siblings = sorted(card.parent.glob(f"{card.name}.bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in siblings[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return bak


def inject(card, body, keep):
    text = card.read_text(encoding="utf-8")
    if MARK_BEGIN not in text or MARK_END not in text:
        raise SystemExit(
            f"卡片里没有醒来包标记：{card}\n"
            "补法：在卡片里加下面两行（放在想让它出现的位置），再重跑；\n"
            "     或者用 `--print-marker` 把标记打出来复制：\n"
            f"  {MARK_BEGIN}\n  {MARK_END}"
        )
    start = text.index(MARK_BEGIN)
    end = text.index(MARK_END) + len(MARK_END)
    new = text[:start] + MARK_BEGIN + "\n" + body.strip() + "\n" + MARK_END + text[end:]
    if new == text:
        return None
    bak = backup_card(card, keep)
    tmp = card.with_suffix(card.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, card)
    return bak


def main():
    parser = kitcfg.get_parser("生成醒来包")
    parser.add_argument("--no-card", action="store_true", help="只写 wake_file，不碰卡片")
    parser.add_argument("--dry-run", action="store_true", help="打到 stdout，不写文件")
    parser.add_argument("--print-marker", action="store_true", help="打印卡片里该放的两行标记")
    args = parser.parse_args()

    if args.print_marker:
        print(MARK_BEGIN)
        print(MARK_END)
        return 0

    cfg = kitcfg.load_config(args.config)
    text, n_ember, n_quote, tmpl_src, dropped = compose(cfg, read_entries(cfg))

    if args.dry_run:
        print(text)
        print(f"--- 模板 {tmpl_src} / 火种 {n_ember} 条（省 {dropped} 条）/ 原话 {n_quote} 条 / 共 {len(text)} 字 ---")
        return 0

    # 顺带增量建语义向量：这一步本来就离线，没人等；失败不影响醒来包
    try:
        ret = cfg.get("retrieval") or {}
        if ret.get("retriever") in ("semantic", "hybrid"):
            model_dir, _, _ = semlib.paths_of(cfg)
            if Path(model_dir).exists():
                added, total = semlib.build_incremental(cfg, log=None)
                if added:
                    print(f"顺带更新了语义向量：+{added}（共 {total}）")
    except Exception:  # noqa: BLE001
        pass

    wake_file = Path(cfg["paths"]["wake_file"])
    wake_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = wake_file.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, wake_file)

    print(f"醒来包已生成：{wake_file}"
          f"（火种 {n_ember} 条" + (f"，省 {dropped} 条" if dropped else "") + f" / 原话 {n_quote} 条 / {len(text)} 字 / 模板 {tmpl_src}）")

    card = cfg["paths"].get("card_path") or ""
    if args.no_card or not card:
        if not card:
            print("没有配置 card_path，只生成文件、没碰卡片。")
        return 0
    bak = inject(Path(card), text, cfg["limits"]["backup_keep"])
    print(f"卡片已更新：{card}" + (f"（备份 {bak.name}）" if bak else "（内容无变化，未写）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

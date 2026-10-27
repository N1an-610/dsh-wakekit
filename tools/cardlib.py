#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cardlib.py — 往角色卡里写 MEMORY-KIT 块（安装向导和 --refresh 共用这一份）。

规矩：
  · 卡里必须有 <!-- WAKE:BEGIN --> / <!-- WAKE:END -->，没有就报错，不自动塞
  · 写之前一定备份 卡片名.bak-时间戳，只保留最近 N 份
  · 只替换 <!-- MEMORY-KIT:BEGIN --> … <!-- MEMORY-KIT:END --> 这一块，别的字不动
  · 能按当前配置裁段（dream=false 裁「做梦」「半夜梦醒」；self_wake=false 裁「自己醒来」）
"""

import shutil
import time
from pathlib import Path

import kitcfg

MARK_BEGIN = "<!-- MEMORY-KIT:BEGIN -->"
MARK_END = "<!-- MEMORY-KIT:END -->"
WAKE_BEGIN = "<!-- WAKE:BEGIN -->"
WAKE_END = "<!-- WAKE:END -->"


class CardError(Exception):
    pass


def load_tpl(root, name):
    for d in (Path(root) / "templates", kitcfg.KIT_ROOT / "templates"):
        f = d / name
        if f.exists():
            return f.read_text(encoding="utf-8")
    return ""


def strip_sections(text, titles):
    """把指定标题的整节（## 标题 到下一个 ##）裁掉，返回 (新文本, 被裁的标题列表)。"""
    lines = text.split("\n")
    out, skipping, removed, current = [], False, [], None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            skipping = any(t in current for t in titles)
            if skipping:
                removed.append(current)
        if not skipping:
            out.append(line)
    return "\n".join(out).strip() + "\n", removed


def headings(text):
    return [line[3:].strip() for line in text.split("\n") if line.startswith("## ")]


def _backup(card, keep):
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    bak = card.with_name(f"{card.name}.bak-{stamp}")
    shutil.copy2(card, bak)
    siblings = sorted(card.parent.glob(f"{card.name}.bak-*"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for old in siblings[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return bak


def apply_templates(card_path, partner, with_sub, sleep=None, root=None, backup_keep=30):
    """按当前配置把模板写进卡片。返回 dict：changed / backup / added / trimmed。"""
    card = Path(card_path)
    if not card.exists():
        raise CardError(f"角色卡不存在：{card}")
    text = card.read_text(encoding="utf-8")
    if WAKE_BEGIN not in text or WAKE_END not in text:
        raise CardError(
            "卡片里没有醒来包标记（" + WAKE_BEGIN + " / " + WAKE_END + "），不往卡里写任何东西。\n"
            "补法：python tools/build_wake.py --print-marker 把两行打出来，贴进卡片想让它出现的位置，再跑一次。"
        )

    sleep = sleep or {}
    added, trimmed = [], []

    mem_part = load_tpl(root, "人格卡片段-记忆.md")
    if mem_part.strip():
        added += headings(mem_part)

    if with_sub:
        sub = load_tpl(root, "潜意识-默认.md")
        titles = []
        if not sleep.get("dream", True):
            titles += ["做梦", "半夜梦醒"]
        if not sleep.get("self_wake", False):
            titles += ["自己醒来"]
        if titles:
            sub, removed = strip_sections(sub, titles)
            trimmed += removed
        if sub.strip():
            added += headings(sub)
    parts = [mem_part]
    if with_sub and sub.strip():
        parts.append(sub)

    body = "\n\n".join(x.strip() for x in parts if x.strip()).replace("{{对方}}", partner or "对方")
    block = f"{MARK_BEGIN}\n{body}\n{MARK_END}"

    if MARK_BEGIN in text and MARK_END in text:
        start = text.index(MARK_BEGIN)
        end = text.index(MARK_END) + len(MARK_END)
        new = text[:start] + block + text[end:]
    else:
        new = text.rstrip() + "\n\n" + block + "\n"

    if new == text:
        return {"changed": False, "backup": None, "added": added, "trimmed": trimmed}

    bak = _backup(card, backup_keep)
    tmp = card.with_suffix(card.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(card)
    return {"changed": True, "backup": bak, "added": added, "trimmed": trimmed}

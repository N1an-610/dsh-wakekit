#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_install.py — P8 安装向导自测。

覆盖：
  1. 完整走一遍（前提都答「有」）→ 开关写进配置、模板装进卡片、有备份
  2. 前提答「没有」→ 只装记忆片段，潜意识不写进卡片
  3. 中途拒绝某一项 → 配置里那一项确实是关的
  4. 卡片没有醒来包标记 → 不写卡片，并给出补法

跑法：python tests/selftest_install.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
INIT = KIT / "tools" / "init.py"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


def run_wizard(target, answers, card=None):
    cmd = [sys.executable, str(INIT), "--dir", str(target), "--wizard"]
    if card:
        cmd += ["--card", str(card)]
    return subprocess.run(cmd, input="\n".join(answers) + "\n", capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


def make_card(path, with_marker=True):
    body = "# 角色卡\n\n你是某某。\n"
    if with_marker:
        body += "\n<!-- WAKE:BEGIN -->\n（醒来包会出现在这）\n<!-- WAKE:END -->\n"
    path.write_text(body, encoding="utf-8")


def cfg_of(target):
    return json.loads((target / "wakekit.config.json").read_text(encoding="utf-8"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wk-install-"))

    # ── 1. 完整走一遍，前提都答「有」 ──────────────────────────────
    t1 = tmp / "one"
    card1 = tmp / "card1.md"
    make_card(card1)
    ans1 = ["y", "y"] + [""] * 8 + ["", "", "n", "1", "y", "", "", "n"]
    r = run_wizard(t1, ans1, card1)
    c1 = cfg_of(t1)
    text1 = card1.read_text(encoding="utf-8")
    baks = list(tmp.glob("card1.md.bak-*"))
    check("① 向导跑通且开关写进配置",
          r.returncode == 0 and c1["features"]["write_gate"] is True
          and c1["features"]["archive"] is False and c1["features"]["dream_scheduler"] is False,
          f"exit={r.returncode}")
    check("①b 模板装进卡片（记忆片段 + 潜意识）",
          "MEMORY-KIT:BEGIN" in text1 and "## 睡眠" in text1 and "## 翻旧话要带编号" in text1)
    check("①c {{对方}} 已被替换",
          "{{对方}}" not in text1 and "对方" in text1)
    check("①d 装卡片前有备份", len(baks) >= 1, f"{len(baks)} 份")

    # ── 2. 前提答「没有」→ 不装潜意识 ─────────────────────────────
    t2 = tmp / "two"
    card2 = tmp / "card2.md"
    make_card(card2)
    ans2 = ["n", "n"] + [""] * 8 + ["", "", "n", "3", "y", "", "", "n"]
    r = run_wizard(t2, ans2, card2)
    text2 = card2.read_text(encoding="utf-8")
    check("②a 前提问题默认是 N（一路回车不装潜意识）",
          "[y/N]" in r.stdout, "")
    check("② 前提答没有时潜意识不进卡片",
          "## 睡眠" not in text2 and "## 翻旧话要带编号" in text2 and "没有的话" in r.stdout)

    # ── 3. 中途拒绝某一项 ─────────────────────────────────────────
    t3 = tmp / "three"
    card3 = tmp / "card3.md"
    make_card(card3)
    # features 顺序：archive, write_gate, dedupe, health_check, privacy, observability, summary_inject, dream_scheduler
    ans3 = ["y", "y", "", "n", "", "y", "", "", "", ""] + ["", "", "n", "1", "n", "n"]
    r = run_wizard(t3, ans3, card3)
    c3 = cfg_of(t3)
    check("③ 拒绝的项在配置里确实是关的",
          c3["features"]["write_gate"] is False and c3["features"]["privacy"] is True,
          f"write_gate={c3['features']['write_gate']} privacy={c3['features']['privacy']}")
    check("③b 打印了开关清单", "安装完成" in r.stdout and "关 →" in r.stdout)

    # ── 4. 卡片没标记 → 不写卡片，给出补法 ────────────────────────
    t4 = tmp / "four"
    card4 = tmp / "card4.md"
    make_card(card4, with_marker=False)
    before = card4.read_text(encoding="utf-8")
    ans4 = ["y", "y"] + [""] * 8 + ["", "", "n", "1", "y", "", "", "n"]
    r = run_wizard(t4, ans4, card4)
    after = card4.read_text(encoding="utf-8")
    check("④ 卡片缺标记时不写卡片", before == after and "没装（卡片缺标记）" in r.stdout)
    check("④b 给出了补法", "print-marker" in r.stdout)

    # ── 5. 接数据源（jsonl） ───────────────────────────────────────
    t5 = tmp / "five"
    msgs = tmp / "msgs.jsonl"
    msgs.write_text('{"ts":1,"text":"hi","who":"a","key":"k"}\n', encoding="utf-8")
    ans5 = ["n", "n"] + [""] * 8 + ["", "", "n", "3", "n", "y", "jsonl", str(msgs), "none"]
    r = run_wizard(t5, ans5)
    c5 = cfg_of(t5)
    check("⑤ 向导能把数据源接上",
          c5["adapters"]["message_source"]["type"] == "jsonl"
          and c5["adapters"]["message_source"]["path"].endswith("msgs.jsonl"),
          f"{c5['adapters']['message_source']}")
    check("⑤b 不接时保持 none", c5["adapters"]["wake_hook"]["type"] == "none")

    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

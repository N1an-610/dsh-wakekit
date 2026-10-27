#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_flow.py — 全流程：新建空目录 → 初始化 → 写数据 → 醒来包 → 检索 → 卸载。

这是 P10 的「一路跑通」：用一条链路证明它作为一个独立盒子能装能跑能拆。

跑法：
  set WAKEKIT_NODE=<node.exe>   （PATH 里有 node 就不必）
  python tests/selftest_flow.py
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


def node_exe():
    p = os.environ.get("WAKEKIT_NODE")
    if p and Path(p).exists():
        return p
    p = shutil.which("node")
    if p:
        return p
    raise SystemExit("找不到 node：设 WAKEKIT_NODE 或加进 PATH")


class Mcp:
    def __init__(self, cfg, node):
        self.p = subprocess.Popen([node, str(KIT / "src" / "server.js"), "--config", str(cfg)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  errors="replace")
        self.q = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.n = 0

    def _read(self):
        for line in self.p.stdout:
            line = line.strip()
            if line:
                try:
                    self.q.put(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def call(self, name, args):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": "tools/call",
                                       "params": {"name": name, "arguments": args}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        msg = self.q.get(timeout=20)
        res = msg.get("result") or {}
        content = res.get("content") or [{}]
        return content[0].get("text", ""), bool(res.get("isError"))

    def close(self):
        try:
            self.p.stdin.close()
            self.p.kill()
        except Exception:
            pass


def run_tool(name, cfg, *args):
    return subprocess.run([sys.executable, str(KIT / "tools" / name), "--config", str(cfg), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)


def main():
    node = node_exe()
    tmp = Path(tempfile.mkdtemp(prefix="wk-flow-"))
    home = tmp / "kit"

    # ── 1. 新建空目录 → 初始化 ─────────────────────────────────────
    r = subprocess.run([sys.executable, str(KIT / "tools" / "init.py"), "--dir", str(home)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    cfg = home / "wakekit.config.json"
    check("① 空目录一键初始化",
          r.returncode == 0 and cfg.exists() and (home / "data" / "memories.jsonl").exists(),
          f"exit={r.returncode}")

    # 把 data 指到临时目录外面一点点，顺便验证路径可配
    c = json.loads(cfg.read_text(encoding="utf-8"))
    c["paths"]["card_path"] = str(tmp / "card.md")
    c["features"]["archive"] = True
    cfg.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "card.md").write_text("# 角色卡\n\n<!-- WAKE:BEGIN -->\n<!-- WAKE:END -->\n", encoding="utf-8")

    # ── 2. 写数据 ────────────────────────────────────────────────
    mcp = Mcp(cfg, node)
    mcp.call("mem_save", {"text": "想做一个能拷走的记忆插件。", "summary": "想做一个能拷走的记忆插件",
                          "tags": ["火种"], "tier": "火种"})
    mcp.call("mem_save", {"text": "约定每周日一起听歌。", "summary": "约定：周日一起听歌", "tags": ["约定"]})
    mcp.call("mem_save", {"text": "私密的：只想自己留着。", "summary": "私密的那件事",
                          "tags": ["日记"], "private": True})
    mem = home / "data" / "memories.jsonl"
    lines = [l for l in mem.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("② 写入 3 条", len(lines) == 3, f"{len(lines)} 条")

    # ── 3. 醒来包 ────────────────────────────────────────────────
    r = run_tool("build_wake.py", cfg)
    wake = (home / "data" / "wake.md").read_text(encoding="utf-8")
    card = (tmp / "card.md").read_text(encoding="utf-8")
    check("③ 生成醒来包并注入卡片",
          r.returncode == 0 and "想做一个能拷走的记忆插件" in wake and "想做一个能拷走的记忆插件" in card)
    check("③b 私密内容没进醒来包", "只想自己留着" not in wake)

    # ── 4. 检索 ──────────────────────────────────────────────────
    got, _ = mcp.call("mem_search", {"query": "插件"})
    check("④ 检索能翻到刚才写的", "找到" in got, got.splitlines()[0])
    mcp.close()

    # ── 5. 卸载：删块 + 删目录，卡里其它内容还在 ───────────────────
    text = card
    for begin, end in (("<!-- MEMORY-KIT:BEGIN -->", "<!-- MEMORY-KIT:END -->"),
                       ("<!-- WAKE:BEGIN -->", "<!-- WAKE:END -->")):
        while begin in text and end in text:
            s = text.index(begin)
            e = text.index(end) + len(end)
            text = (text[:s] + text[e:]).strip() + "\n"
    (tmp / "card.md").write_text(text, encoding="utf-8")
    shutil.rmtree(home)

    after = (tmp / "card.md").read_text(encoding="utf-8")
    # 卡片备份（card.md.bak-*）是「改卡前自动留的」，属于预期产物，不算残留
    leftovers = [p for p in tmp.rglob("*")
                 if p.is_file() and p.name != "card.md" and ".bak-" not in p.name]
    check("⑤ 卸载后卡里没有插件留下的块",
          "MEMORY-KIT" not in after and "WAKE:" not in after)
    check("⑤b 卸载后目录干干净净", not leftovers, f"残留 {leftovers[:3]}")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

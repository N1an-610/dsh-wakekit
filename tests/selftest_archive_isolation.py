#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_archive_isolation.py — 防止「归档被当成活库」这类回归。

背景：上一版在一个记忆服务里踩过这个坑——
归档目录里混着聊天原文，却用 *.jsonl 通配全读进来，再被 trim 当成记忆搬来搬去。
wakekit 现在是「只有活库一个文件」，这个自测就是把它钉死：

  1. archive/ 里放一份 memories-*.jsonl（伪造的归档记忆）→ mem_search 搜不到
  2. archive/ 里放 chat-*.jsonl（聊天原文）→ mem_search 搜不到（只有 chat_search 能搜）
  3. 两边都放「火种」→ 醒来包只出现活库那条
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
    return shutil.which("node")


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

    def call(self, name, args, timeout=60):
        self.n += 1
        rid = self.n
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                                       "params": {"name": name, "arguments": args}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        while True:
            msg = self.q.get(timeout=timeout)
            if msg.get("id") == rid:
                res = msg.get("result") or {}
                return (res.get("content") or [{}])[0].get("text", ""), bool(res.get("isError"))

    def close(self):
        try:
            self.p.stdin.close()
            self.p.kill()
        except Exception:
            pass


def main():
    node = node_exe()
    if not node:
        print("找不到 node，先设 WAKEKIT_NODE。")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="wk-iso-"))
    data = tmp / "data"
    (data / "archive").mkdir(parents=True)
    (data / "wake").mkdir(parents=True)

    (data / "memories.jsonl").write_text(json.dumps({
        "id": "live1", "ts": 1793000000000, "schema": 2, "scope": "private",
        "tier": "火种", "text": "活库里的火种", "summary": "活库火种",
        "tags": ["火种"]}, ensure_ascii=False) + "\n", encoding="utf-8")

    # 归档里放两份「不该被当记忆」的东西
    (data / "archive" / "memories-2026-10.jsonl").write_text(json.dumps({
        "id": "arch1", "ts": 1793000000001, "schema": 2, "scope": "private",
        "tier": "火种", "text": "ZZZONLYINARCHIVE 归档里的火种不该出现", "summary": "ZZZONLYINARCHIVE 归档火种",
        "tags": ["火种"]}, ensure_ascii=False) + "\n", encoding="utf-8")
    (data / "archive" / "chat-2026-10.jsonl").write_text(json.dumps({
        "ts": 1793000000002, "who": "甲", "text": "ZZZCHATONLY 聊天原文不该出现"},
        ensure_ascii=False) + "\n", encoding="utf-8")

    cfg = json.loads((KIT / "wakekit.config.json").read_text(encoding="utf-8"))
    cfg["paths"].update({
        "data_dir": str(data),
        "memories_file": str(data / "memories.jsonl"),
        "archive_dir": str(data / "archive"),
        "wake_dir": str(data / "wake"),
        "wake_file": str(data / "wake.md"),
        "log_file": str(data / "logs" / "x.log"),
        "card_path": "",
    })
    cf = tmp / "cfg.json"
    cf.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    mcp = Mcp(cf, node)
    out, _ = mcp.call("mem_search", {"query": "ZZZONLYINARCHIVE"})
    check("① 检索不读 archive 里的 memories-*.jsonl（伪造归档）", "没有" in out, out.splitlines()[0][:40])

    out, _ = mcp.call("mem_search", {"query": "ZZZCHATONLY"})
    check("② 检索不读 archive 里的 chat-*.jsonl", "没有" in out, out.splitlines()[0][:40])

    out, _ = mcp.call("chat_search", {"query": "ZZZCHATONLY"})
    check("②b chat_search 照常能翻到聊天原文（这是它该干的）", "找到 1 条" in out, out.splitlines()[0][:40])
    mcp.close()

    subprocess.run([sys.executable, str(KIT / "tools" / "build_wake.py"), "--config", str(cf),
                    "--no-card"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    wake = (data / "wake.md").read_text(encoding="utf-8")
    check("③ 醒来包只出现活库的火种", "活库火种" in wake and "ZZZONLYINARCHIVE" not in wake
          and "ZZZCHATONLY" not in wake,
          f"{len(wake)} 字")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

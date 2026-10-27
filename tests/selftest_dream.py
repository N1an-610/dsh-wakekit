#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_dream.py — P6 做梦调度的自测。

覆盖：
  1. 时间窗外不叫
  2. 有未读 → 叫一次，进度记一条
  3. 间隔没到 → 不叫
  4. 全部读完 → done=true，不再叫
  5. 换会话 → 先快照旧包、再重置进度
  6. 叫醒失败 → 进度不动、错误日志有一行

跑法：
  set WAKEKIT_NODE=<node.exe>          （本测试用不到 node，设不设都行）
  python tests/selftest_dream.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SCHED = KIT / "tools" / "read_scheduler.py"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


def run(cfg, *args, timeout=120):
    return subprocess.run([sys.executable, str(SCHED), "--config", str(cfg), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def set_progress(cfg, data, path):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wk-dream-"))
    data = tmp / "data"
    wake = data / "wake"
    wake.mkdir(parents=True)
    (data / "logs").mkdir(parents=True)

    (data / "wake.md").write_text("# 醒来包 · 2026-10-25\n\n## 历代醒来包\n\n- （空）\n", encoding="utf-8")
    (wake / "醒-001.md").write_text("# 醒-001 · 2026-10-23\n\n第一份\n", encoding="utf-8")
    (wake / "醒-002.md").write_text("# 醒-002 · 2026-10-24\n\n第二份\n", encoding="utf-8")

    cfg = json.loads((KIT / "wakekit.config.json").read_text(encoding="utf-8"))
    cfg["paths"]["data_dir"] = str(data)
    cfg["paths"]["wake_dir"] = str(wake)
    cfg["paths"]["wake_file"] = str(data / "wake.md")
    cfg["paths"]["log_file"] = str(data / "logs" / "wakekit.log")
    cfg["paths"]["memories_file"] = str(data / "memories.jsonl")
    cfg["paths"]["archive_dir"] = str(data / "archive")
    cfg["features"]["dream_scheduler"] = True
    cfg["dream"]["hours_start"] = 0
    cfg["dream"]["hours_end"] = 24
    cfg["dream"]["interval_min_minutes"] = 30
    cfg["dream"]["interval_max_minutes"] = 60
    cfg["dream"]["must_read_unread"] = 1
    cfg["dream"]["progress_file"] = str(wake / "read-progress.json")
    cf = tmp / "cfg.json"
    cf.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    prog = wake / "read-progress.json"

    # ── 1. 时间窗外不叫 ────────────────────────────────────────────
    cfg_off = json.loads(json.dumps(cfg))
    now_h = datetime.now().hour
    cfg_off["dream"]["hours_start"] = (now_h + 2) % 24
    cfg_off["dream"]["hours_end"] = ((now_h + 2) % 24) + 1
    if cfg_off["dream"]["hours_start"] >= cfg_off["dream"]["hours_end"]:
        cfg_off["dream"]["hours_start"] = 0
        cfg_off["dream"]["hours_end"] = 1
    if datetime.now().hour == 0:
        cfg_off["dream"]["hours_start"], cfg_off["dream"]["hours_end"] = 1, 2
    cf_off = tmp / "cfg-off.json"
    cf_off.write_text(json.dumps(cfg_off, ensure_ascii=False), encoding="utf-8")
    r = run(cf_off)
    check("① 时间窗外不叫", "不在叫醒时间窗" in r.stdout,
          r.stdout.strip().splitlines()[-1][:60] if r.stdout.strip() else "")

    # ── 1b. session 已经是它的话，窗口外应纯只读 ────────────────────
    m1 = prog.stat().st_mtime_ns
    run(cf_off)
    m2 = prog.stat().st_mtime_ns
    check("①b session 相同时窗口外不写盘", m1 == m2, f"mtime {m1} → {m2}")

    # ── 2. 有未读 → 叫一次 ─────────────────────────────────────────
    r = run(cf)
    p = json.loads(prog.read_text(encoding="utf-8")) if prog.exists() else {}
    check("② 有未读时叫醒一次", "[该叫醒了]" in r.stdout and p.get("read") == ["醒-001"],
          (r.stdout.strip().splitlines()[0] if r.stdout.strip() else "")[:70])

    # ── 3. 间隔没到不叫 ────────────────────────────────────────────
    r = run(cf)
    p2 = json.loads(prog.read_text(encoding="utf-8"))
    check("③ 间隔没到不叫", "还不够久" in r.stdout and p2.get("read") == ["醒-001"],
          r.stdout.strip().splitlines()[-1][:60] if r.stdout.strip() else "")

    # ── 4. 全读完 → done ──────────────────────────────────────────
    p2["lastWakeAt"] = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
    set_progress(cfg, p2, prog)
    r = run(cf)
    p3 = json.loads(prog.read_text(encoding="utf-8"))
    check("④ 最后一份读完置 done", p3.get("read") == ["醒-001", "醒-002"] and p3.get("done") is True,
          f"read={p3.get('read')} done={p3.get('done')}")
    r = run(cf)
    check("④b 全读完后不再叫", "不再叫醒" in r.stdout, r.stdout.strip().splitlines()[-1][:50])

    # ── 5. 换会话：先快照、再重置 ──────────────────────────────────
    (data / "wake.md").write_text("# 醒来包 · 2026-10-26\n\n第二版内容\n", encoding="utf-8")
    r = run(cf, "--session", "sess-B")
    p4 = json.loads(prog.read_text(encoding="utf-8"))
    files = sorted(f.name for f in wake.glob("醒-*.md"))
    check("⑤ 换会话先快照旧包", "醒-003.md" in files and p4.get("session") == "sess-B",
          f"files={files} session={p4.get('session')}")
    check("⑤b 换会话后进度重新计", len(p4.get("read") or []) <= 1,
          f"read={p4.get('read')}（重置后最多只读了 1 份）")

    # ── 6. 叫醒失败：进度不动 + 错误日志 ───────────────────────────
    set_progress(cfg, {"session": "sess-C", "read": [], "lastWakeAt": None, "done": False}, prog)
    cfg_fail = json.loads(json.dumps(cfg))
    cfg_fail["adapters"]["wake_hook"] = {"type": "command", "command": "exit 1"}
    cf_fail = tmp / "cfg-fail.json"
    cf_fail.write_text(json.dumps(cfg_fail, ensure_ascii=False), encoding="utf-8")
    before = json.loads(prog.read_text(encoding="utf-8"))
    r = run(cf_fail, "--session", "sess-C")
    after = json.loads(prog.read_text(encoding="utf-8"))
    log = (data / "logs" / "wakekit.log").read_text(encoding="utf-8") if (data / "logs" / "wakekit.log").exists() else ""
    check("⑥ 叫醒失败时进度不动",
          after.get("read") == before.get("read") and after.get("lastWakeAt") is None,
          f"read={after.get('read')} exit={r.returncode}")
    check("⑥b 失败写一行错误日志", "wake_error" in log, log.strip().splitlines()[-1][:70] if log.strip() else "无日志")

    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

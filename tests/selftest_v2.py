#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_v2.py — 第二版改动（火种容量 / 场景分层 / 睡眠开关）的自测。

对应施工单第五节七条：
  1. 火种调到 20 条：醒来包不超总字数上限，超出的写「还有 M 条」
  2. ember_max=5：只出 5 条
  3. 群聊记忆不进醒来包，但按 scope 能搜到
  4. 老数据没有 scope：不报错，按私聊处理
  5. sleep.enabled=false：潜意识不装、定时任务不注册、醒来包照常
  6. self_wake=false：没有主动唤醒那一段，被叫醒正常
  7. 原有自测仍全绿（由 tools/run_tests.py 统一跑，这里不重复）
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

    def call(self, name, args):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": "tools/call",
                                       "params": {"name": name, "arguments": args}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        msg = self.q.get(timeout=20)
        res = msg.get("result") or {}
        return (res.get("content") or [{}])[0].get("text", ""), bool(res.get("isError"))

    def close(self):
        try:
            self.p.stdin.close()
            self.p.kill()
        except Exception:
            pass


def run(kind, script, cfg, *args, node=None):
    cmd = [node if kind == "node" else sys.executable, str(KIT / script), "--config", str(cfg), *args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def write_entries(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def base_cfg(tmp, **over):
    cfg = json.loads((KIT / "wakekit.config.json").read_text(encoding="utf-8"))
    data = tmp / "data"
    cfg["paths"].update({
        "data_dir": str(data),
        "memories_file": str(data / "memories.jsonl"),
        "archive_dir": str(data / "archive"),
        "wake_dir": str(data / "wake"),
        "wake_file": str(data / "wake.md"),
        "log_file": str(data / "logs" / "wakekit.log"),
        "card_path": str(tmp / "card.md"),
    })
    for k, v in over.items():
        if isinstance(v, dict):
            cfg.setdefault(k, {}).update(v)
        else:
            cfg[k] = v
    data.mkdir(parents=True, exist_ok=True)
    (data / "wake").mkdir(parents=True, exist_ok=True)
    (tmp / "card.md").write_text("# 卡\n\n<!-- WAKE:BEGIN -->\n<!-- WAKE:END -->\n", encoding="utf-8")
    f = tmp / "cfg.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg, f


def ember_count(wake_text):
    seg = wake_text.split("## 火种")
    if len(seg) < 2:
        return 0
    body = seg[1].split("##")[0]
    return len([ln for ln in body.splitlines() if ln.strip().startswith("- ") and "还有" not in ln])


def main():
    node = node_exe()
    tmp = Path(tempfile.mkdtemp(prefix="wk-v2-"))

    # ── 1 / 2：火种条数与字数上限 ────────────────────────────────
    rows = []
    for i in range(24):
        rows.append({"id": f"e{i}", "ts": 1793000000000 + i, "schema": 2,
                     "text": f"第{i}条火种的内容，写得长一点好把字数顶上去。" * 2,
                     "summary": f"火种{i}：这是第{i}件不能忘的事，写长一点好把字数顶上去", "tags": ["火种"], "tier": "火种"})
    _, cf = base_cfg(tmp, wake={"ember_max": 20, "ember_max_chars": 120},
                     limits={"wake_max_chars": 300, "ember_max": 8, "ember_max_chars": 120})
    write_entries(tmp / "data" / "memories.jsonl", rows)
    r = run("python", "tools/build_wake.py", cf, "--dry-run")
    out = r.stdout
    body = out.split("---")[0]
    check("① 火种 20 条时字数不超上限", 0 < len(body) <= 300, f"{len(body)} 字 / 上限 300")
    check("①b 超出的写了「还有 M 条，用 memory_search 搜」",
          "还有" in body and "memory_search" in body,
          [ln for ln in body.splitlines() if "还有" in ln][:1])

    cfg2, cf2 = base_cfg(tmp / "b", wake={"ember_max": 5, "ember_max_chars": 120})
    write_entries(tmp / "b" / "data" / "memories.jsonl", rows)
    r = run("python", "tools/build_wake.py", cf2, "--dry-run")
    check("② ember_max=5 只出 5 条", ember_count(r.stdout) == 5, f"{ember_count(r.stdout)} 条")

    # ── 3 / 4：场景分层与老数据 ──────────────────────────────────
    tmp3 = tmp / "c"
    cfg3, cf3 = base_cfg(tmp3, limits={"wake_max_chars": 1000})
    write_entries(tmp3 / "data" / "memories.jsonl", [
        {"id": "p1", "ts": 1793000000000, "text": "私聊里说的悄悄话", "summary": "私聊：悄悄话",
         "tags": ["火种"], "tier": "火种", "scope": "private", "schema": 2},
        {"id": "g1", "ts": 1793000001000, "text": "群里说的热闹事", "summary": "群聊：热闹事",
         "tags": ["火种"], "tier": "火种", "scope": "group", "scene": "某个群", "schema": 2},
        {"id": "o1", "ts": 1793000002000, "text": "老数据，没有 scope 字段", "summary": "老数据不含scope",
         "tags": ["火种"], "tier": "火种", "schema": 2},
    ])
    r = run("python", "tools/build_wake.py", cf3, "--dry-run")
    body = r.stdout
    check("③ 群聊记忆不进醒来包", "热闹事" not in body and "悄悄话" in body)
    check("④ 老数据（没 scope）按私聊处理", "老数据不含scope" in body)

    mcp = Mcp(cf3, node)
    got_all, _ = mcp.call("mem_search", {"query": "热闹事"})
    got_g, _ = mcp.call("mem_search", {"query": "热闹事", "scope": "group"})
    got_p, _ = mcp.call("mem_search", {"query": "热闹事", "scope": "private"})
    check("③b 群聊记忆能搜到（默认不限）", "找到" in got_all, got_all.splitlines()[0])
    check("③c --scope group 能精确筛到", "找到 1 条" in got_g and "群聊" in got_g,
          got_g.splitlines()[0])
    check("③d --scope private 筛不到群聊", "没有" in got_p, got_p.splitlines()[0])
    mcp.close()

    # ── 5：sleep.enabled=false ───────────────────────────────────
    tmp5 = tmp / "d"
    cfg5, cf5 = base_cfg(tmp5, sleep={"enabled": False, "dream": False, "self_wake": False})
    card5 = tmp5 / "card.md"
    answers = ["y", "y"] + [""] * 9 + ["8", "n", "3", "y", "", "", "n"]
    r = subprocess.run([sys.executable, str(KIT / "tools" / "init.py"), "--dir", str(tmp5 / "kit"),
                        "--wizard", "--card", str(card5)],
                       input="\n".join(answers) + "\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    card_text = card5.read_text(encoding="utf-8") if card5.exists() else ""
    check("⑤ sleep.enabled=false 时不装潜意识模板",
          "## 睡眠" not in card_text and "## 翻旧话要带编号" in card_text,
          "卡片里有记忆片段" if "翻旧话" in card_text else "卡片没写进去")
    cfg5b = json.loads((tmp5 / "kit" / "wakekit.config.json").read_text(encoding="utf-8"))
    check("⑤b 配置里 sleep.enabled 是 false", cfg5b["sleep"]["enabled"] is False)

    # 定时任务不注册：scheduler 直接退出且不写进度
    (tmp5 / "data" / "wake" / "醒-001.md").write_text("# 醒-001\n", encoding="utf-8")
    r = run("python", "tools/read_scheduler.py", cf5)
    check("⑤c 睡眠关掉后调度器不叫醒",
          "sleep.enabled 是关的" in r.stdout and not (tmp5 / "data" / "wake" / "read-progress.json").exists(),
          r.stdout.strip().splitlines()[0][:50])

    # 醒来包照常生成
    write_entries(tmp5 / "data" / "memories.jsonl", [
        {"id": "x1", "ts": 1793000000000, "text": "照样要出醒来包", "summary": "照样要出醒来包",
         "tags": ["火种"], "tier": "火种", "schema": 2}])
    r = run("python", "tools/build_wake.py", cf5)
    check("⑤d 醒来包照常生成", r.returncode == 0 and "照样要出醒来包" in
          (tmp5 / "data" / "wake.md").read_text(encoding="utf-8"))

    # ── 6：self_wake=false ──────────────────────────────────────
    tmp6 = tmp / "e"
    cfg6, cf6 = base_cfg(tmp6)
    card6 = tmp6 / "card.md"
    answers2 = ["y", "y"] + [""] * 9 + ["8", "n", "2", "y", "", "", "n"]
    subprocess.run([sys.executable, str(KIT / "tools" / "init.py"), "--dir", str(tmp6 / "kit"),
                    "--wizard", "--card", str(card6)],
                   input="\n".join(answers2) + "\n", capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=120)
    c6 = card6.read_text(encoding="utf-8") if card6.exists() else ""
    check("⑥ self_wake=false 时没有「自己醒来」那一段",
          "## 自己醒来" not in c6 and "## 睡眠" in c6)
    check("⑥b 做梦那一段还在（被叫醒照常）", "## 做梦" in c6)

    # ── 7：--refresh 按当前配置重装模板 ─────────────────────────
    tmp7 = tmp / "f"
    (tmp7 / "data" / "wake").mkdir(parents=True)
    card7 = tmp7 / "card.md"
    card7.write_text("# 卡\n\n<!-- WAKE:BEGIN -->\n<!-- WAKE:END -->\n", encoding="utf-8")
    cfg7 = json.loads((KIT / "wakekit.config.json").read_text(encoding="utf-8"))
    cfg7["paths"].update({
        "data_dir": str(tmp7 / "data"),
        "memories_file": str(tmp7 / "data" / "memories.jsonl"),
        "archive_dir": str(tmp7 / "data" / "archive"),
        "wake_dir": str(tmp7 / "data" / "wake"),
        "wake_file": str(tmp7 / "data" / "wake.md"),
        "log_file": str(tmp7 / "data" / "logs" / "x.log"),
        "card_path": str(card7),
    })
    cfg7["card"]["subconscious"] = True
    cfg7["sleep"] = {"enabled": True, "dream": True, "self_wake": False}
    cf7 = tmp7 / "cfg.json"

    def refresh():
        cf7.write_text(json.dumps(cfg7, ensure_ascii=False), encoding="utf-8")
        return run("python", "tools/install_card.py", cf7, "--refresh")

    r = refresh()
    t = card7.read_text(encoding="utf-8")
    check("⑦ dream=true 时装进「做梦」「半夜梦醒」",
          "## 做梦" in t and "## 半夜梦醒" in t and "自己醒来" not in t,
          r.stdout.strip().splitlines()[-1][:40])

    cfg7["sleep"]["dream"] = False
    r = refresh()
    t = card7.read_text(encoding="utf-8")
    check("⑦b dream=false 后两段消失，且打印了裁掉了什么",
          "## 做梦" not in t and "## 半夜梦醒" not in t and "裁掉了这些段" in r.stdout,
          [ln for ln in r.stdout.splitlines() if "裁掉" in ln][:1])

    cfg7["sleep"]["dream"] = True
    refresh()
    t = card7.read_text(encoding="utf-8")
    check("⑦c 改回 true 后两段回来（有备份）",
          "## 做梦" in t and "## 半夜梦醒" in t and len(list(tmp7.glob("card.md.bak-*"))) >= 1,
          f"备份 {len(list(tmp7.glob('card.md.bak-*')))} 份")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

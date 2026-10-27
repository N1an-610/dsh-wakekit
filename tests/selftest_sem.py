#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_sem.py — 语义检索（按意思找）的验收。

对应施工单第七节：
  1. 不装模型、retriever=bm25：一切照旧（由其它测试套覆盖）
  2. semantic：拿「她其实困了」能搜到「眼皮已经打架」那条
  3. hybrid：原词（编号）仍然命中
  4. 删掉模型目录 + retriever=hybrid：退回 bm25，加一行提示，不报错
  5. sem_build 增量：第二次只算新增
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

ROWS = [
    {"id": "e1", "ts": 1793000000001, "schema": 2, "scope": "private",
     "text": "她说不想睡，其实眼皮已经睁不开了。", "summary": "她困得厉害", "tags": ["印象"]},
    {"id": "e2", "ts": 1793000000002, "schema": 2, "scope": "private",
     "text": "她说想有个自己的抽屉，钥匙只有一把。", "summary": "她想要一个自己的抽屉", "tags": ["约定"]},
    {"id": "e3", "ts": 1793000000003, "schema": 2, "scope": "private",
     "text": "约定每周日晚上一起听一首歌。", "summary": "周日一起听歌", "tags": ["约定"]},
    {"id": "e4", "ts": 1793000000004, "schema": 2, "scope": "private",
     "text": "编号 A-20261024-0113 那条原话。", "summary": "按编号存的原话", "tags": ["原话"]},
]


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

    def call(self, name, args, timeout=240):
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


def make_cfg(tmp, model_dir):
    data = tmp / "data"
    (data / "wake").mkdir(parents=True)
    (data / "memories.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ROWS) + "\n", encoding="utf-8")
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
    cfg["retrieval"]["sem_model_dir"] = str(model_dir)
    cfg["retrieval"]["sem_index"] = str(data / "sem" / "sem_vectors.npy")
    f = tmp / "cfg.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg, f


def main():
    node = node_exe()
    if not node:
        print("找不到 node，先设 WAKEKIT_NODE。")
        return 2
    model = KIT / "models" / "bge-small-zh-v1.5"
    if not model.exists():
        print(f"没有模型目录：{model}")
        print("跳过原因：缺少语义模型，未安装（先跑 python tools/fetch_model.py）")
        return 3

    tmp = Path(tempfile.mkdtemp(prefix="wk-sem-"))
    cfg, cf = make_cfg(tmp, model)

    # ── 建向量 ─────────────────────────────────────────────────
    r = subprocess.run([sys.executable, str(KIT / "tools" / "sem_build.py"), "--config", str(cf)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    built = (tmp / "data" / "sem" / "sem_vectors.npy").exists()
    check("① 建向量成功", r.returncode == 0 and built,
          [ln for ln in (r.stdout or "").splitlines() if "完成" in ln][:1])

    mcp = Mcp(cf, node)

    # ── 2. semantic：按意思搜 ───────────────────────────────────
    out, err = mcp.call("mem_search", {"query": "她其实困了", "mode": "semantic", "limit": 2})
    check("② semantic 能按意思命中（她其实困了 → 眼皮已经睁不开）",
          not err and "(e1)" in out and out.index("(e1)") < 200,
          out.splitlines()[-1][:50] if out.strip() else "")

    # ── 3. hybrid：原词/编号仍然命中 ────────────────────────────
    out, err = mcp.call("mem_search", {"query": "A-20261024-0113", "mode": "hybrid", "limit": 2})
    check("③ hybrid 仍能命中编号原词", not err and "(e4)" in out,
          out.splitlines()[-1][:50] if out.strip() else "")

    # ── 5. 增量 ────────────────────────────────────────────────
    r2 = subprocess.run([sys.executable, str(KIT / "tools" / "sem_build.py"), "--config", str(cf)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("⑤ 第二次跑不重算", "没有新条目" in (r2.stdout or ""),
          (r2.stdout or "").strip().splitlines()[0])
    with open(tmp / "data" / "memories.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": "e5", "ts": 1793000000005, "schema": 2, "scope": "private",
                            "text": "新加的一条。", "summary": "新加的一条", "tags": ["测试"]},
                           ensure_ascii=False) + "\n")
    r3 = subprocess.run([sys.executable, str(KIT / "tools" / "sem_build.py"), "--config", str(cf)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("⑤b 有新增时只算新增那 1 条", "新增 1 条" in (r3.stdout or ""),
          (r3.stdout or "").strip().splitlines()[0])
    # ── 6. 懒补：写 3 条新记忆 → 直接搜，能搜到 ────────────────
    with open(tmp / "data" / "memories.jsonl", "a", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({"id": f"n{i}", "ts": 1793000001000 + i, "schema": 2,
                                "scope": "private", "text": f"刚写下的第{i}条，说的是下雨和旧伞。",
                                "summary": f"刚写的第{i}条",
                                "tags": ["测试"]}, ensure_ascii=False) + "\n")
    out, err = mcp.call("mem_search", {"query": "下雨天那把旧伞", "mode": "semantic", "limit": 3})
    check("⑥ 写 3 条新记忆后直接搜能搜到（懒补生效）",
          not err and "(n0)" in out and "落后" not in out,
          out.splitlines()[-1][:50] if out.strip() else "")

    # ── 6b. 落后太多 → 只提示，不硬补 ──────────────────────────
    with open(tmp / "data" / "memories.jsonl", "a", encoding="utf-8") as f:
        for i in range(25):
            f.write(json.dumps({"id": f"m{i}", "ts": 1793000002000 + i, "schema": 2,
                                "scope": "private", "text": f"批量写的第{i}条，讲的是火车。",
                                "summary": f"批量第{i}条",
                                "tags": ["测试"]}, ensure_ascii=False) + "\n")
    out, err = mcp.call("mem_search", {"query": "坐火车", "mode": "semantic", "limit": 3})
    check("⑥b 落后超过 sem_auto_max 时只提示不硬补",
          not err and "落后 25 条" in out, out.splitlines()[0][:60] if out.strip() else "")

    # ── 7. 生成醒来包时顺带增量建向量 ──────────────────────────
    cfg["retrieval"]["retriever"] = "hybrid"
    cf.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    before = len(json.loads((tmp / "data" / "sem" / "sem_ids.json").read_text(encoding="utf-8")))
    with open(tmp / "data" / "memories.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": "w1", "ts": 1793000009000, "schema": 2, "scope": "private",
                            "text": "醒来包顺手补的那条。", "summary": "顺手补的一条",
                            "tags": ["测试"]}, ensure_ascii=False) + "\n")
    r4 = subprocess.run([sys.executable, str(KIT / "tools" / "build_wake.py"), "--config", str(cf),
                         "--no-card"], capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
    after = len(json.loads((tmp / "data" / "sem" / "sem_ids.json").read_text(encoding="utf-8")))
    check("⑦ 生成醒来包时顺带补了向量",
          after > before and "顺带更新了语义向量" in (r4.stdout or ""),
          f"{before} → {after}")
    mcp.close()

    # ── 4. 没模型 → 退回 bm25 + 提示 ───────────────────────────
    tmp2 = Path(tempfile.mkdtemp(prefix="wk-sem-nomodel-"))
    cfg2, cf2 = make_cfg(tmp2, tmp2 / "nope-model")
    cfg2["retrieval"]["retriever"] = "hybrid"
    cf2.write_text(json.dumps(cfg2, ensure_ascii=False, indent=2), encoding="utf-8")
    mcp2 = Mcp(cf2, node)
    out, err = mcp2.call("mem_search", {"query": "困了", "limit": 2})
    mcp2.close()
    check("④ 没模型时退回关键词并给提示，不报错",
          not err and "语义检索未装" in out and "关键词" in out,
          out.splitlines()[0] if out.strip() else "")

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)
    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

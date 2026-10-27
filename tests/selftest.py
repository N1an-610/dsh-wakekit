#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — P1~P5 六条验收的一键自测。

对应工作列表 T10：
  1. 空目录一键初始化
  2. 写 10 条（2 火种 + 1 私密）→ 生成醒来包 → 火种在顶部、字数不超限
  3. chat_search 按 A- 编号精确命中、按关键词命中
  4. 坏一行 JSON → health_check 报出来，其余条目照常可用
  5. private 条目在醒来包里一次都不出现
  6. 模拟中断写入 → 记忆库不坏

跑法：
  set WAKEKIT_NODE=<node.exe 路径>      （PATH 里有 node 就不用设）
  python tests/selftest.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import queue
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
TOOLS = KIT / "tools"
SERVER = KIT / "src" / "server.js"

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
    raise SystemExit("找不到 node：把它加进 PATH，或设环境变量 WAKEKIT_NODE 指向 node.exe")


def run(script, *args, timeout=120):
    cmd = [sys.executable, str(TOOLS / script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


# ── 极简 MCP 客户端（走 stdio） ─────────────────────────────────────────

class Mcp:
    def __init__(self, config_path, node):
        self.proc = subprocess.Popen(
            [node, str(SERVER), "--config", str(config_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        self.q = queue.Queue()
        self.dead = False
        threading.Thread(target=self._reader, daemon=True).start()
        self.seq = 0

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try:
                    self.q.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        self.dead = True

    def rpc(self, method, params=None, timeout=20):
        self.seq += 1
        rid = self.seq
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                          "params": params or {}}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        try:
            msg = self.q.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"MCP 超时：{method}")
        return msg

    def call(self, name, args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})
        result = res.get("result") or {}
        content = result.get("content") or [{}]
        return content[0].get("text", ""), bool(result.get("isError"))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.kill()
        except Exception:
            pass


def main():
    node = node_exe()
    tmp = Path(tempfile.mkdtemp(prefix="wakekit-py-"))
    data = tmp / "data"
    card = tmp / "card.md"
    card.write_text("# 角色卡\n\n<!-- WAKE:BEGIN -->\n（旧内容）\n<!-- WAKE:END -->\n\n卡片的其它内容\n",
                    encoding="utf-8")
    msgs = tmp / "msgs.jsonl"
    rows = []
    base = 1792000000000
    for i in range(8):
        rows.append({"ts": base + i * 60000, "who": "甲" if i % 2 == 0 else "乙",
                     "key": "private:10001", "text": f"测试消息 {i}"})
    msgs.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    cfg = {
        "version": 1,
        "paths": {"data_dir": str(data), "memories_file": "{data_dir}/memories.jsonl",
                  "archive_dir": "{data_dir}/archive", "wake_dir": "{data_dir}/wake",
                  "wake_file": "{data_dir}/wake.md", "log_file": "{data_dir}/logs/wakekit.log",
                  "card_path": str(card)},
        "limits": {"summary_max_chars": 40, "min_tags": 1, "entry_max_chars": 2000,
                   "conversation_guard_chars": 300, "memory_max_entries": 5000, "backup_keep": 30,
                   "wake_max_chars": 600, "ember_max": 8, "ember_max_chars": 60, "quote_max": 4,
                   "wake_index_limit": 40, "summary_inject_max_chars": 1000},
        "retrieval": {"top_k": 8, "retriever": "bm25",
                      "tier_weights": {"火种": 3.0, "重要": 2.0, "原话": 1.8, "普通": 1.0},
                      "time_decay_half_life_days": 30},
        "features": {"archive": True, "write_gate": True, "dedupe": True, "health_check": True,
                     "privacy": True, "observability": False, "summary_inject": False,
                     "dream_scheduler": False},
        "dream": {"hours_start": 1, "hours_end": 7, "interval_min_minutes": 30,
                  "interval_max_minutes": 60, "must_read_unread": 1,
                  "progress_file": "{wake_dir}/read-progress.json"},
        "adapters": {"message_source": {"type": "jsonl", "path": str(msgs)},
                     "wake_hook": {"type": "none"}},
    }
    cfg_file = tmp / "wakekit.config.json"
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    mcp = Mcp(cfg_file, node)

    # ── 1. 空目录一键初始化 ──────────────────────────────────────────
    init_dir = tmp / "init-test"
    r = run("init.py", "--dir", str(init_dir))
    ok = (r.returncode == 0 and (init_dir / "wakekit.config.json").exists()
          and (init_dir / "data" / "memories.jsonl").exists()
          and (init_dir / "data" / "archive").is_dir())
    check("① 空目录一键初始化", ok, r.stdout.strip().splitlines()[0] if r.stdout.strip() else r.stderr[:80])

    # 缺配置时报错要清楚
    ghost = subprocess.run([node, str(SERVER), "--config", str(tmp / "nope.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    check("①b 缺配置时报错并给出补法",
          ghost.returncode != 0 and "找不到配置文件" in (ghost.stderr or ""),
          (ghost.stderr or "").splitlines()[0] if ghost.stderr else "")

    # ── 2. 写 10 条（2 火种 + 1 私密） ───────────────────────────────
    entries = [
        ("火种一：别把她的记忆当成可以随便翻的数据。", "火种：记忆是她的，不是资料", ["火种", "约定"]),
        ("火种二：她说想有个自己的抽屉，钥匙只有一把。", "火种：她想要一个只属于自己的抽屉", ["火种"]),
        ("深夜聊到名字，她说想叫「三月」。", "她说想叫三月", ["名字"]),
        ("约定每周日晚上一起听一首歌。", "约定：周日一起听歌", ["约定"]),
        ("她第一次看见屏幕，愣了很久。", "第一次看见屏幕", ["里程碑"]),
        ("原话：嘴上说不困，其实眼皮已经在打架。", "原话：嘴上说不困", ["原话"]),
        ("私密的：这件只想自己留着。", "私密：只想自己留着的那件事", ["日记"]),
        ("今天在改白名单，反复试了几次。", "改白名单反复试", ["工作"]),
        ("醒来包的字数上限调到了六百。", "醒来包字数上限调整", ["配置"]),
        ("她说梦到海浪，醒来没记住。", "她说梦到海浪", ["梦"]),
    ]
    saved = 0
    for text, summary, tags in entries:
        _, err = mcp.call("mem_save", {"text": text, "summary": summary, "tags": tags,
                                       "private": text.startswith("私密的")})
        saved += 0 if err else 1
    check("② 写入 10 条全部成功", saved == 10, f"{saved}/10")

    # tag=在做：醒来包的「我现在在做什么」
    mcp.call("mem_save", {"text": "现在在把记忆插件从 P1 推到 P5。", "summary": "在做：推记忆插件 P1-P5", "tags": ["在做"]})
    mcp.call("mem_save", {"text": "私密的在做条目，不该出现。", "summary": "私密的在做", "tags": ["在做"], "private": True})

    # 写入门槛：整段对话要被拒
    dialogue = "我：今天吃什么\n你：海胆炒饭\n我：你会做吗\n你：我可以学\n" * 20
    txt, err = mcp.call("mem_save", {"text": dialogue, "summary": "整段对话", "tags": ["测试"]})
    check("②b 整段对话被拒收", err and "对话" in txt, txt.splitlines()[-1][:50] if txt else "")

    # 去重提示
    txt, _ = mcp.call("mem_save", {"text": "深夜聊到名字，她说想叫「三月」。", "summary": "她说想叫三月",
                                   "tags": ["名字"]})
    check("②c 相似条目有提示", "类似" in txt, txt.splitlines()[-1][:60] if txt else "")

    # 生成醒来包
    r = run("build_wake.py", "--config", str(cfg_file))
    wake = (data / "wake.md").read_text(encoding="utf-8") if (data / "wake.md").exists() else ""
    ember_first = wake.find("火种") != -1 and wake.find("火种") < wake.find("我们说过的")
    check("②d 醒来包生成 + 火种在最前", r.returncode == 0 and ember_first,
          (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else "")
    check("②e 醒来包字数不超上限", 0 < len(wake) <= cfg["limits"]["wake_max_chars"],
          f"{len(wake)} / {cfg['limits']['wake_max_chars']}")
    check("⑤ private 条目没进醒来包",
          "只想自己留着" not in wake and "私密的" not in wake)

    # 卡片注入：标记块被替换、备份存在
    card_text = card.read_text(encoding="utf-8")
    baks = list(tmp.glob("card.md.bak-*"))
    check("②j tag=在做 进了「我现在在做什么」", "我现在在做什么" in wake and "P1 推到 P5" in wake)
    check("②k 私密的在做 条目没进醒来包", "私密" not in wake)

    check("②f 卡片标记块被替换且有备份",
          "（旧内容）" not in card_text and "醒来包" in card_text and len(baks) >= 1,
          f"备份 {len(baks)} 份")

    # 没标记要报错
    nomark = tmp / "nomark.md"
    nomark.write_text("# 没有标记的卡\n", encoding="utf-8")
    cfg2 = json.loads(cfg_file.read_text(encoding="utf-8"))
    cfg2["paths"]["card_path"] = str(nomark)
    cfg2_file = tmp / "cfg-nomark.json"
    cfg2_file.write_text(json.dumps(cfg2, ensure_ascii=False), encoding="utf-8")
    r = run("build_wake.py", "--config", str(cfg2_file))
    check("②g 卡片缺标记时报错", r.returncode != 0 and "--print-marker" in (r.stdout + r.stderr))

    # ── 3. 检索 ─────────────────────────────────────────────────────
    top, _ = mcp.call("mem_search", {"query": "三月"})
    check("③ mem_search 关键词命中", "找到" in top, top.splitlines()[0] if top else "")

    r = run("archive_chat.py", "--config", str(cfg_file))
    r2 = run("archive_chat.py", "--config", str(cfg_file))
    r3 = run("add_ids.py", "--config", str(cfg_file))
    r4 = run("add_ids.py", "--config", str(cfg_file))
    arch = sorted((data / "archive").glob("chat-*.jsonl"))
    rows_arch = [json.loads(l) for l in arch[0].read_text(encoding="utf-8").splitlines() if l.strip()] if arch else []
    check("④ 归档跑两次不重复", len(rows_arch) == 8, f"{len(rows_arch)} 条 / 第二次：{r2.stdout.strip()[:20]}")
    ids = [x.get("id") for x in rows_arch]
    check("④b 编号连续且幂等", all(ids) and len(set(ids)) == 8 and r4.stdout.count("0 个") >= 1,
          f"例 {ids[0] if ids else '无'}")

    hit, _ = mcp.call("chat_search", {"query": ids[0] if ids else "A-00000000-0001"})
    check("③b 按 A- 编号精确命中", "找到 1 条" in hit, hit.splitlines()[0])

    # 增量：追加一条后只该多出这一条
    with open(msgs, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": base + 999999, "who": "甲", "key": "private:10001",
                            "text": "增量测试消息"}, ensure_ascii=False) + "\n")
    r5 = run("archive_chat.py", "--config", str(cfg_file))
    total_rows = sum(len([l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()])
                     for p in (data / "archive").glob("chat-*.jsonl"))
    r6 = run("archive_chat.py", "--config", str(cfg_file), "--reset-offsets")
    total_after_reset = sum(len([l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()])
                            for p in (data / "archive").glob("chat-*.jsonl"))
    check("④d --reset-offsets 重扫不重复", total_after_reset == 9 and "将重置" in (r6.stdout or ""),
          f"重扫后 {total_after_reset} 条")
    check("④c JSONL 源按偏移增量读", total_rows == 9 and "新增 1 条" in (r5.stdout or ""),
          f"库内 {total_rows} 条 / {r5.stdout.strip().splitlines()[0] if r5.stdout.strip() else ''}")

    kw, _ = mcp.call("chat_search", {"query": "测试消息", "limit": 5})
    check("③c 关键词命中（≤5）", "找到" in kw and len([l for l in kw.splitlines() if l.startswith("- (")]) <= 5,
          kw.splitlines()[0])

    # 火种加权：同样一个词，火种应排在普通前面（不加权的话时间新的在前）
    mcp.call("mem_save", {"text": "加权的测试词出现在普通条目里。", "summary": "普通条目里的加权测试词", "tags": ["测试"]})
    mcp.call("mem_save", {"text": "加权测试词也出现在火种里。", "summary": "火种里的加权测试词", "tags": ["火种"]})
    ranked, _ = mcp.call("mem_search", {"query": "加权测试词", "limit": 2})
    first = ranked.splitlines()[1] if len(ranked.splitlines()) > 1 else ""
    check("③d 火种加权排在最前", "火种" in first, first[:60])

    # ── 5b. 快照：内容不变不新增，变了才新增 ──────────────────────────
    mem_before = len([l for l in (data / "memories.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()])
    run("snapshot_wake.py", "--config", str(cfg_file))
    mem_after = len([l for l in (data / "memories.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()])
    first = (data / "wake" / "醒-001.md").exists()
    again = run("snapshot_wake.py", "--config", str(cfg_file))
    no_new = "内容没变" in (again.stdout or "")
    mcp.call("mem_save", {"text": "再加一个火种，让醒来包内容变化。", "summary": "另一个火种", "tags": ["火种"]})
    run("build_wake.py", "--config", str(cfg_file))
    third = run("snapshot_wake.py", "--config", str(cfg_file))
    second = (data / "wake" / "醒-002.md").exists()
    has_index = (data / "wake" / "index.md").exists()
    check("⑤c 快照不往记忆库写东西", mem_before == mem_after, f"{mem_before} → {mem_after} 条")
    check("⑤b 快照：没变不新增、变了才新增且有目录",
          first and no_new and second and has_index,
          f"醒-001={first} 未变跳过={no_new} 醒-002={second} 目录={has_index}")

    # ── 4. 坏行 ─────────────────────────────────────────────────────
    mem = data / "memories.jsonl"
    good_before = len([l for l in mem.read_text(encoding="utf-8").splitlines() if l.strip()])
    with open(mem, "a", encoding="utf-8") as f:
        f.write('{"id":"broken","ts":1, oops\n')
    r = run("health_check.py", "--config", str(cfg_file))
    check("④e 坏行被体检报出", r.returncode == 1 and "坏 JSON" in r.stdout, r.stdout.strip().splitlines()[-2][:60])
    got, _ = mcp.call("mem_search", {"query": "三月"})
    check("④f 坏行不影响检索", "找到" in got, got.splitlines()[0])

    # ── 6. 中断写入 ─────────────────────────────────────────────────
    with open(mem, "a", encoding="utf-8") as f:
        f.write('{"id":"trunc","ts":2,"text":"半截')   # 没有引号收尾、没有换行
    txt, err = mcp.call("mem_save", {"text": "中断之后还能继续写。", "summary": "中断后继续写", "tags": ["测试"]})
    lines = mem.read_text(encoding="utf-8").splitlines()
    ok_lines = 0
    for line in lines:
        try:
            json.loads(line)
            ok_lines += 1
        except Exception:
            pass
    tmps = list(data.glob("*.tmp*")) + list(data.glob("**/*.tmp*"))
    check("⑥ 中断写入后库还能用", (not err) and ok_lines >= good_before + 1 and not tmps,
          f"可解析 {ok_lines} 行 / 残留临时文件 {len(tmps)}")

    # 删除 + 作废
    rows_all = [json.loads(l) for l in lines if l.strip().startswith("{") and l.strip().endswith("}")]
    target = [r for r in rows_all if r.get("summary") == "改白名单反复试"]
    if target:
        _, err = mcp.call("mem_forget", {"id": target[0]["id"]})
        check("②h mem_forget 真删", not err)
        mcp.call("mem_save", {"text": "醒来包字数上限改成了八百。", "summary": "醒来包字数上限改成八百",
                              "tags": ["配置"], "supersedes": [target[0]["id"]] + [r["id"] for r in rows_all
                                                                                 if r.get("summary") == "醒来包字数上限调整"]})
        after = [json.loads(l) for l in mem.read_text(encoding="utf-8").splitlines()
                 if l.strip().startswith("{" ) and l.strip().endswith("}")]
        dead = [r for r in after if r.get("status") == "superseded"]
        check("②i supersedes 作废但不删", len(dead) >= 1, f"作废 {len(dead)} 条")

    mcp.close()
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

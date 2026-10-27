#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""archive_chat.py — 把聊天原文按月存下来（给「重置后还能翻原话」用）。

消息源由配置决定，代码里没有任何指向具体项目的默认值：
  adapters.message_source.type = none     什么都不做（默认）
                               = jsonl    读一个 JSONL 文件，按字节偏移增量读
                               = json    读一个 JSON 文件里的数组（每次全量读，靠去重）
                               = command  跑一条命令，把它的 stdout 当 JSONL

输出：<archive_dir>/chat-YYYY-MM.jsonl，一条一行

去重（幂等的根本）：
  每次运行先把**已有归档**整个读一遍，按 (ts, who, text) 的哈希建集合，
  再拿它去重。所以：
    · 跑多少次都不会重复写
    · --reset-offsets 重扫也不会写重（不依赖状态文件）

用法：
  python tools/archive_chat.py
  python tools/archive_chat.py --dry-run          # 只统计，不落盘
  python tools/archive_chat.py --reset-offsets    # 先把偏移清零再重扫（会先打印重置了什么）
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402


def state_file(cfg):
    return Path(cfg["paths"]["data_dir"]) / ".archive-state.json"


def load_state(cfg):
    p = state_file(cfg)
    if not p.exists():
        return {"offsets": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("offsets", {})
        data.pop("seen", None)   # 旧版本留下的字段，现在用不上了
        return data
    except Exception:
        return {"offsets": {}}


def save_state(cfg, state):
    p = state_file(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def rec_hash(rec):
    """去重口径：(ts, who, text)。不含 key——同一句话换会话也不该重复存。"""
    raw = f"{rec.get('ts', 0)}|{rec.get('who', '')}|{rec.get('text', '')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def existing_hashes(archive_dir):
    """把已有归档全读一遍，建哈希集合。这是幂等的依据，不靠状态文件。"""
    seen = set()
    d = Path(archive_dir)
    if not d.is_dir():
        return seen
    for path in sorted(d.glob("chat-*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add(rec_hash(obj))
    return seen


def month_of(ts_ms):
    d = datetime.fromtimestamp(float(ts_ms) / 1000)
    return d.strftime("%Y-%m")


def normalize(rec):
    ts = rec.get("ts") or rec.get("time") or rec.get("timestamp") or 0
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        ts = 0
    return {
        "ts": ts,
        "text": str(rec.get("text") or rec.get("content") or ""),
        "who": str(rec.get("who") or rec.get("sender") or rec.get("name") or ""),
        "key": str(rec.get("key") or rec.get("conversation") or rec.get("chat") or ""),
    }


# ── 三种消息源 ─────────────────────────────────────────────────────────

def source_none(cfg, state):
    print("消息源是 none（默认），没有可归档的内容。要开就在配置里设 adapters.message_source。")
    return []


def source_jsonl(cfg, state):
    src = cfg["adapters"]["message_source"]
    path = Path(src["path"])
    key = str(path)
    offsets = state.setdefault("offsets", {})
    off = int(offsets.get(key, 0))
    size = path.stat().st_size
    if size < off:
        off = 0  # 文件被截断/换过，从头来

    with open(path, "rb") as f:
        f.seek(off)
        raw = f.read()
    cut = raw.rfind(b"\n")
    if cut < 0:
        return []
    chunk = raw[: cut + 1]
    offsets[key] = off + len(chunk)

    out = []
    for line in chunk.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(normalize(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return out


def _dig(obj, pointer):
    cur = obj
    for part in str(pointer).split("."):
        if not part:
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def source_json(cfg, state):
    src = cfg["adapters"]["message_source"]
    data = json.loads(Path(src["path"]).read_text(encoding="utf-8"))
    arr = _dig(data, src.get("pointer"))
    if not isinstance(arr, list):
        raise SystemExit(
            f"adapters.message_source.pointer={src.get('pointer')!r} 没有指到一个数组，"
            "请检查配置（例如 recentMessages）。"
        )
    fields = src.get("fields") or {}
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        out.append(normalize({
            "ts": item.get(fields.get("ts", "ts")),
            "text": item.get(fields.get("text", "text")),
            "who": item.get(fields.get("who", "who")),
            "key": item.get(fields.get("key", "key")),
        }))
    return out


def source_command(cfg, state):
    src = cfg["adapters"]["message_source"]
    cmd = src["command"]
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"消息源命令退出码 {proc.returncode}：{cmd}\n{proc.stderr[:400]}")
    out = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(normalize(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return out


SOURCES = {"none": source_none, "jsonl": source_jsonl, "json": source_json, "command": source_command}


def show_offsets(cfg, state):
    """--reset-offsets 之前，把「要重置什么、现在是多少」打清楚。"""
    src = cfg["adapters"]["message_source"]
    offsets = state.get("offsets", {})
    print("将重置消息源读取偏移：")
    if not offsets:
        print("  （还没有记录过任何偏移，等于本来就是从头读）")
    for path, off in sorted(offsets.items()):
        print(f"  - {path}  当前 offset = {off} 字节")
    print(f"  源类型：{src.get('type')}")
    print("  重置不删已有归档；重新扫的时候按 (ts, who, text) 去重，重复的不会写第二遍。")


def main():
    parser = kitcfg.get_parser("把聊天原文按月归档")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    parser.add_argument("--reset-offsets", action="store_true",
                        help="把各个消息源的读取偏移清零后重新扫一遍（已有归档不删，靠去重兜底）")
    args = parser.parse_args()

    cfg = kitcfg.load_config(args.config)
    if not cfg["features"]["archive"]:
        print("features.archive 是关的，跳过（要开就把它改成 true）。")
        return 0

    state = load_state(cfg)

    if args.reset_offsets:
        show_offsets(cfg, state)
        state["offsets"] = {}
        if not args.dry_run:
            save_state(cfg, state)
        # 继续往下走，让这次调用顺便把内容重扫一遍

    archive_dir = Path(cfg["paths"]["archive_dir"])
    archive_dir.mkdir(parents=True, exist_ok=True)
    seen = existing_hashes(archive_dir)

    src = cfg["adapters"]["message_source"]
    records = SOURCES[src["type"]](cfg, state)

    new_count = 0
    skipped = 0
    by_month = {}
    for rec in records:
        if not rec["text"].strip():
            skipped += 1
            continue
        h = rec_hash(rec)
        if h in seen:
            skipped += 1
            continue
        seen.add(h)
        by_month.setdefault(month_of(rec["ts"]), []).append(
            {"ts": rec["ts"], "who": rec["who"], "key": rec["key"], "text": rec["text"]}
        )
        new_count += 1

    for month, rows in sorted(by_month.items()):
        target = archive_dir / f"chat-{month}.jsonl"
        if args.dry_run:
            print(f"[dry-run] 会往 {target.name} 写 {len(rows)} 条")
            continue
        with open(target, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    if not args.dry_run:
        save_state(cfg, state)
        print(f"归档完成：新增 {new_count} 条，跳过 {skipped} 条（重复/空），写入 {len(by_month)} 个月份文件")
    else:
        print(f"[dry-run] 新增 {new_count} 条，跳过 {skipped} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())

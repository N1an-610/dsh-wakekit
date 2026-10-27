#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""add_ids.py — 给原文归档的每条消息补一个可引用的编号。

编号格式：A-YYYYMMDD-0001（A=archive，日期，当天第几条）
幂等：已有 id 的不动；只给缺的补，序号接着当天已有的最大值往后排。
只增不改：不删除、不重排、不改正文。

用法：
  python tools/add_ids.py
  python tools/add_ids.py --dry-run
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402


def day_of(ts_ms):
    return datetime.fromtimestamp(float(ts_ms) / 1000).strftime("%Y%m%d")


def main():
    parser = kitcfg.get_parser("给归档消息补 A- 编号")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = kitcfg.load_config(args.config)
    archive = Path(cfg["paths"]["archive_dir"])
    files = sorted(archive.glob("chat-*.jsonl"))
    if not files:
        print(f"没有找到归档文件：{archive}\\chat-*.jsonl（先跑 archive_chat.py）")
        return 0

    total = filled = 0
    for path in files:
        rows = []
        bad = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    bad += 1
        if bad:
            print(f"  [注意] {path.name} 里有 {bad} 行坏 JSON，已跳过（不修改它们）")

        # 不排序：归档「只增不改」，行序保持原样（排序也会改变文件内容）。
        # 已有的编号先扫一遍，算出每天的最大序号
        max_seq = {}
        for r in rows:
            rid = str(r.get("id") or "")
            parts = rid.split("-")
            if len(parts) == 3 and parts[0].upper() == "A":
                try:
                    max_seq[parts[1]] = max(max_seq.get(parts[1], 0), int(parts[2]))
                except ValueError:
                    pass

        changed = False
        for r in rows:
            total += 1
            if str(r.get("id") or "").strip():
                continue
            day = day_of(r.get("ts", 0))
            if day not in max_seq:
                # 当天可能已经有编号但格式不规范，这里从 0 起
                max_seq[day] = 0
            max_seq[day] += 1
            r["id"] = f"A-{day}-{max_seq[day]:04d}"
            filled += 1
            changed = True

        if not changed or args.dry_run:
            continue
        # 改文件前先留一份备份（.bak-时间戳），保留最近 10 份
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, bak)
        for old_bak in sorted(path.parent.glob(f"{path.name}.bak-*"), key=lambda x: x.stat().st_mtime,
                              reverse=True)[10:]:
            try:
                old_bak.unlink()
            except OSError:
                pass

        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    if args.dry_run:
        print(f"[dry-run] 共 {total} 条，会补 {filled} 个编号")
    else:
        print(f"补编号完成：共 {total} 条，本次新补 {filled} 个（已有的没动）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

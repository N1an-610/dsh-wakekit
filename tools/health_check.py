#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""health_check.py — 记忆库体检。

查这些：
  · 总条数 / 有效条数 / 作废条数
  · 坏 JSON 行（报行号，不影响其它行）
  · 缺字段（id / ts / text / summary / tags）
  · 超长条目（正文 > limits.entry_max_chars）
  · 重复 id
  · 醒来包目录孤儿（index.md 里列了但文件不存在；有文件但没进目录）
  · 条数超过 limits.memory_max_entries

退出码：0 没问题 / 1 有问题（方便放进定时任务看红灯）

用法：python tools/health_check.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402


def main():
    parser = kitcfg.get_parser("记忆库体检")
    args = parser.parse_args()
    cfg = kitcfg.load_config(args.config)

    problems = []
    info = []

    mem = Path(cfg["paths"]["memories_file"])
    if not mem.exists():
        print(f"记忆库还不存在：{mem}（空库算正常）")
        return 0

    rows = []
    bad = []
    for n, line in enumerate(mem.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("不是对象")
            rows.append(obj)
        except Exception as exc:  # noqa: BLE001
            bad.append((n, str(exc)))

    live = [r for r in rows if r.get("status") != "superseded"]
    dead = [r for r in rows if r.get("status") == "superseded"]
    info.append(f"总 {len(rows)} 条（有效 {len(live)} / 作废 {len(dead)}）")

    if bad:
        problems.append(f"坏 JSON {len(bad)} 行：" + "、".join(f"第{n}行({e})" for n, e in bad[:10])
                        + ("…" if len(bad) > 10 else ""))

    ids = {}
    for r in rows:
        rid = str(r.get("id") or "")
        if not rid:
            problems.append(f"有条目缺 id（ts={r.get('ts')}, summary={str(r.get('summary'))[:20]}）")
            continue
        ids[rid] = ids.get(rid, 0) + 1
    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        problems.append(f"重复 id {len(dup)} 个：{'、'.join(dup[:5])}")

    missing = [r for r in live if not str(r.get("summary") or "").strip() or not (r.get("tags") or [])]
    if missing:
        problems.append(f"有效条目里有 {len(missing)} 条缺摘要或标签（例：{str(missing[0].get('id'))}）")

    too_long = [r for r in rows if len(str(r.get("text") or "")) > cfg["limits"]["entry_max_chars"]]
    if too_long:
        problems.append(f"超长条目 {len(too_long)} 条（> {cfg['limits']['entry_max_chars']} 字），例：{too_long[0].get('id')}")

    if len(rows) > cfg["limits"]["memory_max_entries"]:
        problems.append(f"条数 {len(rows)} 超过上限 {cfg['limits']['memory_max_entries']}，建议归档")

    # 醒来包目录
    wake_dir = Path(cfg["paths"]["wake_dir"])
    if wake_dir.is_dir():
        files = {f.name for f in wake_dir.glob("醒-*.md")}
        idx = wake_dir / "index.md"
        listed = set()
        if idx.exists():
            for line in idx.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("- 醒-"):
                    listed.add(line[2:].split("·")[0].strip() + ".md")
        orphan_files = files - listed
        orphan_rows = listed - files
        if orphan_files:
            problems.append(f"有 {len(orphan_files)} 份醒来包没进目录：{'、'.join(sorted(orphan_files)[:5])}")
        if orphan_rows:
            problems.append(f"目录里列了不存在的醒来包：{'、'.join(sorted(orphan_rows)[:5])}")
        info.append(f"醒来包 {len(files)} 份")
    else:
        info.append("还没有醒来包目录")

    print("记忆库体检")
    for line in info:
        print(f"  · {line}")
    if problems:
        print(f"  ✗ 发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"    - {p}")
        return 1
    print("  ✓ 没有发现问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())

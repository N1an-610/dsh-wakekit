#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""snapshot_wake.py — 把当前醒来包存成一份带编号的「历代醒来包」，并刷新目录。

规则：
  · 内容和上一次一样就不新增（按内容哈希判断）
  · 新增时编号接着最大编号往后排：wake/醒-NNN.md
  · 同时重写 wake/index.md（新的在前）
  · **不往记忆库写任何东西**：记忆库只放"经历的事"，
    「第 N 份醒来包已生成」这种关于文件的事件由 index.md 负责（早先踩过坑：
    醒来包把自己写进记忆，结果醒来包里出现"第 N 份醒来包"的自指条目）。

用法：
  python tools/snapshot_wake.py
  python tools/snapshot_wake.py --dry-run
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402


def next_number(wake_dir):
    n = 0
    for f in Path(wake_dir).glob("醒-*.md"):
        try:
            n = max(n, int(f.stem.split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return n + 1


def rebuild_index(wake_dir):
    rows = []
    for f in sorted(Path(wake_dir).glob("醒-*.md")):
        try:
            n = int(f.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        head = ""
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            head = lines[0].lstrip("# ").strip() if lines else ""
        except OSError:
            pass
        rows.append((n, head, f.stat().st_size))
    rows.sort(reverse=True)
    lines = ["# 历代醒来包", ""]
    for n, head, size in rows:
        lines.append(f"- 醒-{n:03d} · {head or '（无标题）'} · {size} 字节")
    (Path(wake_dir) / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = kitcfg.get_parser("把当前醒来包存成一份历史存档")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = kitcfg.load_config(args.config)
    wake_file = Path(cfg["paths"]["wake_file"])
    wake_dir = Path(cfg["paths"]["wake_dir"])

    if not wake_file.exists():
        print(f"还没有醒来包：{wake_file}（先跑 build_wake.py）")
        return 1

    body = wake_file.read_text(encoding="utf-8")
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()

    state_file = wake_dir / ".snapshot-state.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    if state.get("hash") == digest:
        print(f"内容没变（{digest[:8]}），不新增存档。")
        return 0

    n = next_number(wake_dir)
    head = body.splitlines()[0].lstrip("# ").strip() if body.strip() else ""
    dated = head if "·" in head else f"{head} · {datetime.now().strftime('%Y-%m-%d')}"
    stamped = body.replace(body.splitlines()[0], f"# {dated}", 1) if body.strip() else f"# {dated}\n"

    target = wake_dir / f"醒-{n:03d}.md"
    if args.dry_run:
        print(f"[dry-run] 会新增 {target.name}（{len(stamped)} 字）")
        return 0

    wake_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(stamped, encoding="utf-8")
    os.replace(tmp, target)
    rebuild_index(wake_dir)

    state_file.write_text(json.dumps({"hash": digest, "last": n, "at": int(time.time() * 1000)}),
                          encoding="utf-8")
    print(f"已存档：{target.name}（{len(stamped)} 字），目录已刷新（没写记忆库）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

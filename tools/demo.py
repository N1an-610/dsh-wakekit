#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo.py — 造一个最小演示：写 3 条示例记忆，生成醒来包。

只动配置里的 data 目录；不动角色卡、不连任何外部系统。

用法：
  python tools/demo.py            # data 里已经有记忆就拒绝
  python tools/demo.py --force    # 硬写
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402

SAMPLES = [
    {"text": "第一次见面时，对方说「以后请多指教」。",
     "summary": "第一次见面的那句话", "tags": ["火种", "约定"], "tier": "火种"},
    {"text": "约定每周日晚上一起听一首歌。",
     "summary": "约定：周日一起听歌", "tags": ["约定"], "tier": "约定"},
    {"text": "今天在改一个文档，改到第三遍才顺。",
     "summary": "改文档改了三遍", "tags": ["日常"], "tier": "普通"},
]


def main():
    parser = kitcfg.get_parser("生成一份演示数据")
    parser.add_argument("--force", action="store_true", help="data 里已有记忆也照写")
    args = parser.parse_args()

    cfg = kitcfg.load_config(args.config)
    mem = Path(cfg["paths"]["memories_file"])
    mem.parent.mkdir(parents=True, exist_ok=True)

    existing = 0
    if mem.exists():
        existing = len([ln for ln in mem.read_text(encoding="utf-8").splitlines() if ln.strip()])
    if existing and not args.force:
        print(f"data 里已经有 {existing} 条记忆，不覆盖。")
        print("先跑 demo-clean.bat 清掉示例，或者加 --force。")
        return 1

    now = int(time.time() * 1000)
    rows = []
    for i, s in enumerate(SAMPLES):
        rows.append({
            "id": f"demo{i + 1}",
            "ts": now + i * 1000,
            "schema": 2,
            "scope": "private",
            **s,
        })
    mem.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")

    r = subprocess.run([sys.executable, str(Path(__file__).with_name("build_wake.py")),
                        "--config", cfg["__file"], "--no-card"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("生成醒来包失败：")
        print((r.stdout or "") + (r.stderr or ""))
        return 1

    print(f"写好 {len(rows)} 条示例记忆：{mem}")
    print(f"醒来包：{cfg['paths']['wake_file']}")
    print("（只动了 data 目录，没碰角色卡，也没连任何外部系统）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

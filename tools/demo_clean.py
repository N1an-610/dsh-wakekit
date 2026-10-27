#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_clean.py — 把演示痕迹清掉，只删配置里 data 目录内部的东西。

安全限制：
  · 只删 data_dir 里面的内容，不碰外面
  · data_dir 必须在插件目录里、且名字是 data（防止配置写歪了把别处清空）
  · 删完打印删了什么
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402

TARGETS = ["memories.jsonl", "wake.md", ".archive-state.json", "wake", "archive", "logs", "backups"]


def main():
    parser = kitcfg.get_parser("清掉演示数据")
    args = parser.parse_args()
    cfg = kitcfg.load_config(args.config)

    data = Path(cfg["paths"]["data_dir"]).resolve()
    kit = Path(cfg["__root"]).resolve()

    if data.name != "data" or kit not in data.parents:
        print(f"拒绝执行：data 目录看起来不对（{data}）。")
        print("这个命令只删插件目录下的 data 文件夹里的东西。")
        return 2

    removed = []
    for name in TARGETS:
        p = data / name
        if not p.exists():
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(name)
        except OSError as exc:
            print(f"删不掉 {name}：{exc}")

    if removed:
        print("已清掉：" + "、".join(removed))
    else:
        print("没有要清的（data 里已经是干净的）。")
    print(f"目录还在：{data}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

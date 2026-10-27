#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_card.py — 按当前配置重新渲染角色卡里的 MEMORY-KIT 块。

为什么需要它：改了 sleep.dream / sleep.self_wake 之后，卡里的模板不会自己变。
不重装一次，开关就是假的。

用法：
  python tools/install_card.py --refresh      # 重新装一遍（先备份）
  python tools/install_card.py --print-marker # 打印卡片里该放的两行标记
  python tools/install_card.py --dry-run      # 只打印会装哪些段、裁哪些段
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402
import cardlib  # noqa: E402


def main():
    parser = kitcfg.get_parser("按当前配置重装角色卡里的模板")
    parser.add_argument("--refresh", action="store_true", help="重新渲染并替换 MEMORY-KIT 块")
    parser.add_argument("--print-marker", action="store_true", help="打印卡片里该放的两行标记")
    parser.add_argument("--dry-run", action="store_true", help="只看会装什么，不写卡片")
    args = parser.parse_args()

    if args.print_marker:
        print(cardlib.MARK_BEGIN)
        print(cardlib.MARK_END)
        return 0

    cfg = kitcfg.load_config(args.config)
    card = (cfg.get("paths") or {}).get("card_path") or ""
    if not card:
        print("配置里没有 card_path，没法装卡片。")
        print("补法：在 wakekit.config.json 的 paths.card_path 填角色卡路径，再跑一次。")
        return 2

    sleep = cfg.get("sleep") or {}
    with_sub = bool((cfg.get("card") or {}).get("subconscious")) and bool(sleep.get("enabled", True))

    if args.dry_run:
        print(f"卡片：{card}")
        print(f"潜意识：{'装' if with_sub else '不装'}")
        print(f"睡眠：enabled={sleep.get('enabled')} dream={sleep.get('dream')} "
              f"self_wake={sleep.get('self_wake')}")
        print("（dry-run，没写卡片）")
        return 0

    if not args.refresh:
        print("这个命令只做一件事：按当前配置重装模板。加 --refresh 才会写卡片。")
        return 2

    try:
        res = cardlib.apply_templates(
            card,
            (cfg.get("labels") or {}).get("partner") or "对方",
            with_sub,
            sleep,
            root=cfg.get("__file", "") and str(Path(cfg["__file"]).parent) or None,
            backup_keep=int((cfg.get("limits") or {}).get("backup_keep", 30)),
        )
    except cardlib.CardError as exc:
        print(str(exc))
        return 1

    if not res["changed"]:
        print("卡片内容没有变化（和现在一样），没写。")
    else:
        print(f"卡片已更新：{card}")
        if res["backup"]:
            print(f"  备份：{res['backup'].name}")

    print("装进了这些段：" + ("、".join(res["added"]) if res["added"] else "（无）"))
    print("裁掉了这些段：" + ("、".join(res["trimmed"]) if res["trimmed"] else "（无）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

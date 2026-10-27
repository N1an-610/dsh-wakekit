#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init.py — 在一个空目录里一键初始化 wakekit。

产出：
  wakekit.config.json   配置（已存在就不覆盖）
  data/memories.jsonl      空记忆库
  data/{archive,wake,logs,backups}/
  templates/*.md           如果插件自带模板，拷一份过去

用法：
  python tools/init.py                # 在插件根目录初始化
  python tools/init.py --dir D:\\x    # 指定目录
  python tools/init.py --force        # 配置已存在也重写（会先备份成 .bak-时间戳）
"""

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402
import cardlib  # noqa: E402

CONFIG_TEMPLATE = {
    "version": 1,
    "labels": {"partner": "对方"},
    "paths": {
        "data_dir": "./data",
        "memories_file": "{data_dir}/memories.jsonl",
        "archive_dir": "{data_dir}/archive",
        "wake_dir": "{data_dir}/wake",
        "wake_file": "{data_dir}/wake.md",
        "log_file": "{data_dir}/logs/wakekit.log",
        "card_path": "",
    },
    "limits": {
        "summary_max_chars": 40,
        "min_tags": 1,
        "entry_max_chars": 2000,
        "conversation_guard_chars": 300,
        "memory_max_entries": 5000,
        "backup_keep": 30,
        "wake_max_chars": 1000,
        "ember_max": 8,
        "ember_max_chars": 120,
        "quote_max": 4,
        "focus_max_chars": 120,
        "wake_index_limit": 40,
        "summary_inject_max_chars": 1000,
        "summary_scan": 20,
    },
    "wake": {"ember_max": 8, "ember_max_chars": 120, "reset_cycle_days": 3, "daily_new_items": 5},
    "card": {"subconscious": False},
    "sleep": {"enabled": True, "dream": True, "self_wake": False},
    "retrieval": {
        "top_k": 8,
        "retriever": "bm25",
        "sem_weight": 0.5,
        "sem_model_dir": "./models/bge-small-zh-v1.5",
        "sem_index": "./data/sem/sem_vectors.npy",
        "sem_topk": 300,
        "sem_auto_refresh": True,
        "sem_auto_max": 20,
        "tier_weights": {"火种": 3.0, "重要": 2.0, "原话": 1.8, "约定": 1.8, "日记": 1.0, "普通": 1.0},
        "time_decay_half_life_days": 30,
    },
    "features": {
        "archive": False,
        "group_memory": False,
        "write_gate": True,
        "dedupe": True,
        "health_check": False,
        "privacy": True,
        "observability": False,
        "summary_inject": False,
        "dream_scheduler": False,
    },
    "dream": {
        "hours_start": 1,
        "hours_end": 7,
        "interval_min_minutes": 30,
        "interval_max_minutes": 60,
        "must_read_unread": 1,
        "progress_file": "{wake_dir}/read-progress.json",
        "session_id": "",
    },
    "adapters": {"message_source": {"type": "none"}, "wake_hook": {"type": "none"}},
}


# ── 安装向导（P8） ─────────────────────────────────────────────────────

MARK_BEGIN = "<!-- MEMORY-KIT:BEGIN -->"
MARK_END = "<!-- MEMORY-KIT:END -->"
WAKE_BEGIN = "<!-- WAKE:BEGIN -->"
WAKE_END = "<!-- WAKE:END -->"

FEATURES = [
    ("archive", "原文归档", "把每句对话原样存进按月文件，带 A- 编号", "占磁盘", "想不起当时原话怎么说", False),
    ("write_gate", "写入门槛", "记忆必须有摘要和至少一个标签，整段对话会被拒", "每次多填一项", "记忆库会变成聊天记录垃圾桶", True),
    ("dedupe", "去重", "同一天、前 20 字相似就提示已有类似条目", "偶尔误报", "记重了不知道", True),
    ("health_check", "健康检查", "查坏行、超长条目、重复 id、条数超限", "要挂定时任务", "库坏了一行你不知道", False),
    ("privacy", "隐私分级", "标 private 的条目不进醒来包、不进摘要", "无", "只想自己看的东西会出现在开场", True),
    ("observability", "可观测", "每次读写记一行时间/条数/耗时（不记内容）", "多一个日志文件", "出问题分不清是没写进去还是没搜到", False),
    ("summary_inject", "摘要注入", "醒来包多一节「上次到哪」", "吃上下文", "重置后接不上刚才聊到哪", False),
    ("dream_scheduler", "做梦调度", "夜里随机叫醒读旧醒来包，读完就停", "真花钱，一晚上几轮对话", "记忆只有被改写，没有回看", False),
]


def ask_yes(prompt, default):
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"{prompt} [{hint}] ").strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        if raw in ("y", "yes", "是", "要", "开"):
            return True
        if raw in ("n", "no", "否", "不要", "关"):
            return False
        print("  请回答 y 或 n。")


def ask_text(prompt, default):
    try:
        raw = input(f"{prompt} [{default}] ").strip()
    except EOFError:
        return default
    return raw or default


def load_tpl(root, name):
    for d in (Path(root) / "templates", kitcfg.KIT_ROOT / "templates"):
        f = d / name
        if f.exists():
            return f.read_text(encoding="utf-8")
    return ""


def install_templates(card, partner, with_sub, root, sleep=None):
    """交给 cardlib（和 --refresh 共用同一份实现）。"""
    res = cardlib.apply_templates(card, partner, with_sub, sleep, root=root)
    return res["backup"]


def wizard(root, cfg_file, args):
    print("=== 记忆插件安装向导 ===")
    print("这个插件给你的角色配一套「不会忘事」的底子：")
    print("记忆本子、原文档案柜、醒来包，外加可选的「睡觉/做梦」设定。")
    print("先问一个前提——不装潜意识也能正常用记忆功能。\n")

    print("不确定就选 N，记忆功能不受影响；以后想开，重跑一次装模板就行。\n")
    awake = ask_yes("你的角色平时会被「主动叫醒」吗？", False)
    silent = ask_yes("被叫醒的时候，它可以不说话、只是醒一下吗？", False)
    with_sub = bool(awake and silent)
    if not with_sub:
        print()
        print("那就只装记忆部分。潜意识的睡眠设定需要有「会睡」和「可以不说话」这两个前提；")
        print("没有的话，它会被写成一直在醒着，那些「做梦」「睡回去」的话就落不到实处。")
        print("这次先不装，不影响记忆功能。")

    print("\n接下来逐项定八个开关（每项都说清代价和缺什么）：\n")
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    for key, name, what, cost, lose, default in FEATURES:
        print(f"- {name}：{what}。")
        print(f"  代价：{cost}。不开缺：{lose}。")
        cfg["features"][key] = ask_yes("  要开吗？", default)
        print()

    semantic = ask_yes(
        "要不要「按意思找」？（默认不要）\n"
        "  要多下 95 MB 的模型，检索时先算一遍相似度，慢一点点；"
        "不要也能用，只是换个说法可能搜不到", False)
    if semantic:
        cfg["retrieval"]["retriever"] = "hybrid"
        print("  → 装完还要两步：python tools/fetch_model.py  然后  python tools/sem_build.py")
    print()

    # 三问：火种条数 / 群聊 / 睡眠
    wake_cfg = cfg.setdefault("wake", {})
    cycle = int(wake_cfg.get("reset_cycle_days", 3))
    daily = int(wake_cfg.get("daily_new_items", 5))
    suggestion = min(20, cycle * daily)
    print("火种放几条？")
    print(f"  建议 {suggestion} 条（= 每天新增 {daily} 条 × 大概 {cycle} 天重置一次）。")
    print("  重聊天、少重置的人可以调大；只偶尔聊聊的，5 条就够。")
    raw = ask_text("  火种条数", str(suggestion))
    try:
        n = max(1, min(30, int(raw)))
    except ValueError:
        n = suggestion
    wake_cfg["ember_max"] = n
    print()

    cfg["features"]["group_memory"] = ask_yes(
        "要不要把群聊里的内容也记进记忆库？（群聊记忆不会进醒来包，只是能搜到）", False)
    print()

    print("睡眠设定装哪一档？（夜里叫醒一次约等于一轮对话）")
    print("  按每 30~60 分钟一次、每晚最多 8 次算，一晚大概几十分钱；具体看模型的用量价格。")
    print("  1 = 全要（会睡、会做梦、会自己醒）")
    print("  2 = 只做梦，不自己醒（省一点）")
    print("  3 = 都不要（不装潜意识、不注册任何定时唤醒；醒来包照常生成）")
    choice = ask_text("  选 1 / 2 / 3", "1").strip()
    sleep = cfg.setdefault("sleep", {})
    if choice == "3":
        sleep["enabled"] = False
        sleep["dream"] = False
        sleep["self_wake"] = False
    elif choice == "2":
        sleep["enabled"] = True
        sleep["dream"] = True
        sleep["self_wake"] = False
    else:
        sleep["enabled"] = True
        sleep["dream"] = True
        sleep["self_wake"] = True
    if not sleep["enabled"]:
        with_sub = False
        print("  → 不装潜意识模板；醒来包照常生成。")
    print()

    cfg["card"] = {"subconscious": bool(with_sub)}

    do_card = ask_yes("要不要把模板装进角色卡（会先备份原卡）？", False)
    card_msg = "不装"
    if do_card:
        path = ask_text("  角色卡文件路径", args.card or "")
        card = Path(path).expanduser() if path else None
        if not card or not card.exists():
            print(f"  卡片不存在：{path or '(没填)'}，跳过。")
        else:
            partner = ask_text("  它平时怎么称呼对话的人？（群里人多时可以填「管理员」）", args.partner)
            cfg["labels"]["partner"] = partner or "对方"
            try:
                bak = install_templates(card, cfg["labels"]["partner"], with_sub, root, cfg.get("sleep"))
                card_msg = ("已装（记忆片段 + 潜意识）" if with_sub else "已装（记忆片段）")
                if bak:
                    card_msg += f"，备份 {bak.name}"
                print(f"  模板已写入：{card}")
            except (SystemExit, cardlib.CardError) as exc:
                card_msg = "没装（卡片缺标记）"
                print(f"  {exc}")
                print("  补法：python tools/build_wake.py --print-marker，把两行标记贴进卡片后再跑一次。")
    if not with_sub and do_card:
        print("  （按前面的回答，潜意识模板没有写进卡片。）")

    # 最后问一句要不要现在接上外部系统；默认不接
    hooked = False
    if ask_yes("现在就把数据源和叫醒方式接上吗？", False):
        print("  （不接也能用，只是数据要手动导入；以后想接就改配置里的 adapters。）")
        kind = ask_text("  消息源类型（none / jsonl / json / command）", "none").strip().lower()
        src = {"type": kind if kind in ("none", "jsonl", "json", "command") else "none"}
        if src["type"] in ("jsonl", "json"):
            src["path"] = ask_text("  数据文件路径", "").strip()
            if src["type"] == "json":
                src["pointer"] = ask_text("  数组所在的点路径（例如 messages）", "").strip()
        elif src["type"] == "command":
            src["command"] = ask_text("  要执行的命令（输出 JSONL）", "").strip()
        cfg["adapters"]["message_source"] = src

        hook = ask_text("  叫醒方式（none / command / http）", "none").strip().lower()
        hk = {"type": hook if hook in ("none", "command", "http") else "none"}
        if hk["type"] == "command":
            hk["command"] = ask_text("  叫醒命令", "").strip()
        elif hk["type"] == "http":
            hk["url"] = ask_text("  叫醒 URL", "").strip()
        cfg["adapters"]["wake_hook"] = hk
        hooked = True

    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    on = [name for key, name, *_r in FEATURES if cfg["features"][key]]
    off = [name for key, name, *_r in FEATURES if not cfg["features"][key]]
    print("\n安装完成")
    print("  开关：开 → " + ("、".join(on) if on else "（全关）"))
    print("        关 → " + ("、".join(off) if off else "（全开）"))
    print(f"  模板：{card_msg}")
    print("  数据源：" + (f"{cfg['adapters']['message_source'].get('type')} / 叫醒 {cfg['adapters']['wake_hook'].get('type')}"
                        if hooked else "没接（none / none），数据需要手动导入"))
    print(f"  配置：{cfg_file}")
    print("  改开关：编辑配置里的 features，或用 --wizard 重跑")
    try:
        kitcfg.load_config(str(cfg_file))
        print("  校验：通过")
    except kitcfg.ConfigError as exc:
        print(f"  校验：失败（向导写完的配置没通过自检）\n{exc}")
        return 1
    return 0



def main():
    import argparse

    parser = argparse.ArgumentParser(description="初始化 wakekit")
    parser.add_argument("--dir", default=str(kitcfg.KIT_ROOT), help="初始化到哪个目录（默认插件根目录）")
    parser.add_argument("--force", action="store_true", help="配置已存在也重写（先备份）")
    parser.add_argument("--wizard", action="store_true", help="逐项问八个开关，并把模板装进角色卡")
    parser.add_argument("--card", default="", help="角色卡路径（--wizard 时用）")
    parser.add_argument("--partner", default="对方", help="模板里 {{对方}} 的替换值，默认「对方」")
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    cfg_file = root / "wakekit.config.json"
    created = []

    if cfg_file.exists() and not args.force:
        print(f"配置已存在，没有覆盖：{cfg_file}")
    else:
        if cfg_file.exists():
            bak = cfg_file.with_name(f"wakekit.config.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(cfg_file, bak)
            print(f"旧配置已备份：{bak.name}")
        cfg_file.write_text(json.dumps(CONFIG_TEMPLATE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(str(cfg_file))

    data = root / "data"
    for sub in ("", "archive", "wake", "logs", "backups"):
        d = data / sub
        d.mkdir(parents=True, exist_ok=True)
    mem = data / "memories.jsonl"
    if not mem.exists():
        mem.write_text("", encoding="utf-8")
        created.append(str(mem))

    templates = kitcfg.KIT_ROOT / "templates"
    if templates.is_dir():
        dst = root / "templates"
        dst.mkdir(exist_ok=True)
        for f in sorted(templates.glob("*.md")):
            target = dst / f.name
            if not target.exists():
                shutil.copy2(f, target)
                created.append(str(target))

    print("初始化完成")
    print(f"  根目录：{root}")
    print(f"  配置：{cfg_file}")
    print(f"  数据：{data}")
    if created:
        print("  新建：")
        for c in created:
            print(f"    {c}")
    else:
        print("  新建：无（都已存在）")

    # 立刻用同一套校验检查一遍，配错了当场就知道
    try:
        cfg = kitcfg.load_config(str(cfg_file))
        print(f"  校验：通过（data_dir={cfg['paths']['data_dir']}）")
    except kitcfg.ConfigError as exc:
        print(f"  校验：失败\n{exc}")
        return 1

    if args.wizard:
        return wizard(root, cfg_file, args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

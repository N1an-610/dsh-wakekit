# -*- coding: utf-8 -*-
"""kitcfg.py — python 工具共用的配置读取（和 src/config.js 同一份文件、同一套规则）。

规则：
  · 从插件根目录找 wakekit.config.json（也可 --config 或环境变量 WAKEKIT_CONFIG）
  · 先展开 {data_dir} 这类占位符，再把相对路径按配置文件所在目录解析
  · 缺东西直接抛 ConfigError 并说清补法，不静默降级
"""

import argparse
import json
import os
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


DEFAULTS = {
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
    "retrieval": {"top_k": 8, "retriever": "bm25", "sem_weight": 0.5,
                  "sem_model_dir": "./models/bge-small-zh-v1.5",
                  "sem_index": "./data/sem/sem_vectors.npy", "sem_topk": 300,
                  "sem_auto_refresh": True, "sem_auto_max": 20, "tier_weights": {"普通": 1.0},
                  "time_decay_half_life_days": 30},
    "wake": {"ember_max": 8, "ember_max_chars": 120, "reset_cycle_days": 3, "daily_new_items": 5},
    "card": {"subconscious": False},
    "sleep": {"enabled": True, "dream": True, "self_wake": False},
    "features": {"archive": False, "group_memory": False, "write_gate": True, "dedupe": True, "health_check": False,
                 "privacy": True, "observability": False, "summary_inject": False,
                 "dream_scheduler": False},
    "dream": {"hours_start": 1, "hours_end": 7, "interval_min_minutes": 30,
              "interval_max_minutes": 60, "must_read_unread": 1,
              "progress_file": "{wake_dir}/read-progress.json", "session_id": ""},
    "adapters": {"message_source": {"type": "none"}, "wake_hook": {"type": "none"}},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out


def _expand(value, ctx):
    if isinstance(value, str):
        out = value
        for k, v in ctx.items():
            out = out.replace("{" + k + "}", v)
        return out
    if isinstance(value, list):
        return [_expand(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, ctx) for k, v in value.items()}
    return value


def _absolutize(value, base):
    if isinstance(value, str):
        if not value:
            return value
        p = Path(value)
        return str(p if p.is_absolute() else (base / p).resolve())
    return value


def _resolve_paths(cfg, base):
    """只把确实是路径的字段绝对化——枚举值（bm25 / none）不能当路径拼。"""
    paths = cfg.get("paths", {})
    for key, value in list(paths.items()):
        if isinstance(value, str):
            paths[key] = _absolutize(value, base)
    dream = cfg.get("dream") or {}
    if isinstance(dream.get("progress_file"), str):
        dream["progress_file"] = _absolutize(dream["progress_file"], base)
    ret = cfg.get("retrieval") or {}
    if not isinstance(ret.get("sem_auto_refresh"), bool):
        p.append("retrieval.sem_auto_refresh 必须是 true/false")
    if not isinstance(ret.get("sem_auto_max"), int) or ret.get("sem_auto_max", 0) <= 0:
        p.append("retrieval.sem_auto_max 必须是正整数")

    wake = cfg.get("wake") or {}
    for key in ("ember_max", "ember_max_chars", "reset_cycle_days", "daily_new_items"):
        v = wake.get(key)
        if not isinstance(v, int) or v <= 0:
            p.append(f"wake.{key} 必须是正整数（现在是 {v!r}）")
    card = cfg.get("card") or {}
    if not isinstance(card.get("subconscious"), bool):
        p.append("card.subconscious 必须是 true/false")
    sleep = cfg.get("sleep") or {}
    for key in ("enabled", "dream", "self_wake"):
        if not isinstance(sleep.get(key), bool):
            p.append(f"sleep.{key} 必须是 true/false")
    src = (cfg.get("adapters") or {}).get("message_source") or {}
    if isinstance(src.get("path"), str):
        src["path"] = _absolutize(src["path"], base)
    return cfg


def config_path(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("WAKEKIT_CONFIG")
    if env:
        return Path(env).resolve()
    return KIT_ROOT / "wakekit.config.json"


def load_config(explicit=None):
    path = config_path(explicit)
    if not path.exists():
        raise ConfigError(
            f"找不到配置文件：{path}\n"
            "补法：跑一次 `python tools/init.py`，或用 --config <路径> 指定。"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON：{path}\n{exc}\n补法：修好括号/逗号。") from exc

    merged = _merge(DEFAULTS, raw)
    data_dir = Path(merged["paths"]["data_dir"])
    if not data_dir.is_absolute():
        data_dir = (path.parent / data_dir).resolve()
    ctx = dict(merged["paths"])
    ctx["data_dir"] = str(data_dir)
    ctx["kit_root"] = str(KIT_ROOT)
    cfg = _expand(merged, ctx)
    cfg = _resolve_paths(cfg, path.parent)
    cfg["__file"] = str(path)
    cfg["__root"] = str(KIT_ROOT)

    problems = validate(cfg)
    if problems:
        raise ConfigError(
            f"配置有问题（{path}）：\n"
            + "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(problems))
            + "\n改完再跑；校验不通过不会读写任何数据。"
        )
    return cfg


def validate(cfg):
    p = []
    paths = cfg.get("paths", {})
    if not paths.get("data_dir"):
        p.append("paths.data_dir 缺失")
    for key in ("memories_file", "archive_dir", "wake_dir", "wake_file"):
        if not paths.get(key):
            p.append(f"paths.{key} 缺失")
    labels = cfg.get("labels") or {}
    if not isinstance(labels.get("partner"), str) or not labels["partner"].strip():
        p.append("labels.partner 必须是非空字符串（模板里的 {{对方}} 会替换成它，默认「管理员」）")
    limits = cfg.get("limits", {})
    for key in ("summary_max_chars", "min_tags", "entry_max_chars", "wake_max_chars", "ember_max",
                "ember_max_chars", "quote_max", "focus_max_chars", "wake_index_limit", "backup_keep",
                "memory_max_entries", "conversation_guard_chars", "summary_inject_max_chars"):
        v = limits.get(key)
        if not isinstance(v, int) or v <= 0:
            p.append(f"limits.{key} 必须是正整数（现在是 {v!r}）")
    if isinstance(limits.get("ember_max"), int) and limits["ember_max"] > 8:
        p.append(f"limits.ember_max 不能超过 8（现在是 {limits['ember_max']}）")
    if isinstance(limits.get("wake_max_chars"), int) and limits["wake_max_chars"] < 300:
        p.append(f"limits.wake_max_chars 至少 300（现在是 {limits['wake_max_chars']}）")
    feats = cfg.get("features", {})
    for key in ("archive", "group_memory", "write_gate", "dedupe", "health_check", "privacy",
                "observability", "summary_inject", "dream_scheduler"):
        if not isinstance(feats.get(key), bool):
            p.append(f"features.{key} 必须是 true/false")
    ret = cfg.get("retrieval") or {}
    if not isinstance(ret.get("sem_auto_refresh"), bool):
        p.append("retrieval.sem_auto_refresh 必须是 true/false")
    if not isinstance(ret.get("sem_auto_max"), int) or ret.get("sem_auto_max", 0) <= 0:
        p.append("retrieval.sem_auto_max 必须是正整数")

    wake = cfg.get("wake") or {}
    for key in ("ember_max", "ember_max_chars", "reset_cycle_days", "daily_new_items"):
        v = wake.get(key)
        if not isinstance(v, int) or v <= 0:
            p.append(f"wake.{key} 必须是正整数（现在是 {v!r}）")
    card = cfg.get("card") or {}
    if not isinstance(card.get("subconscious"), bool):
        p.append("card.subconscious 必须是 true/false")
    sleep = cfg.get("sleep") or {}
    for key in ("enabled", "dream", "self_wake"):
        if not isinstance(sleep.get(key), bool):
            p.append(f"sleep.{key} 必须是 true/false")
    src = (cfg.get("adapters") or {}).get("message_source") or {}
    if src.get("type") not in ("none", "jsonl", "json", "command"):
        p.append(f"adapters.message_source.type 只能是 none/jsonl/json/command（现在是 {src.get('type')!r}）")
    if src.get("type") == "jsonl":
        if not src.get("path"):
            p.append("adapters.message_source.type=jsonl 时必须给 path")
        elif not Path(src["path"]).exists():
            p.append(f"adapters.message_source.path 不存在：{src['path']}")
    if src.get("type") == "json":
        if not src.get("path"):
            p.append("adapters.message_source.type=json 时必须给 path")
        elif not Path(src["path"]).exists():
            p.append(f"adapters.message_source.path 不存在：{src['path']}")
        if not src.get("pointer"):
            p.append("adapters.message_source.type=json 时必须给 pointer（例如 recentMessages）")
    if src.get("type") == "command" and not src.get("command"):
        p.append("adapters.message_source.type=command 时必须给 command")
    hook = (cfg.get("adapters") or {}).get("wake_hook") or {}
    if hook.get("type") not in ("none", "command", "http"):
        p.append(f"adapters.wake_hook.type 只能是 none/command/http（现在是 {hook.get('type')!r}）")
    return p


def add_common_args(parser):
    parser.add_argument("--config", default=None, help="配置文件路径（默认用插件根目录那份）")


def get_parser(desc):
    parser = argparse.ArgumentParser(description=desc)
    add_common_args(parser)
    return parser

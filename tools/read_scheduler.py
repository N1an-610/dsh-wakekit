#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_scheduler.py — 夜里的「做梦」调度：隔一段时间叫醒一次，让她读一份旧醒来包。

规则（全部走配置 dream.*）：
  · 时间窗：只在 dream.hours_start ~ dream.hours_end 之间（默认 01:00–06:59）
  · 间隔：min~max 分钟之间；具体值由「上次叫醒时间」算出来（同一轮里稳定，不会每次重抽）
  · 每次叫醒：必须读 must_read_unread 份「未读」
  · 全部读过一遍 → done=true，不再叫
  · 新会话（进度里的 session 变了）：先把旧包快照一份，再重置进度
  · 叫醒方式走 adapters.wake_hook：none（只打印）/ command / http
  · **叫醒失败时进度不动**，只往错误日志里写一行（时间、原因）

进度文件（dream.progress_file，默认 wake/read-progress.json）：
  {
    "session": "当前会话标识",
    "read": ["醒-001", "醒-002"],
    "lastWakeAt": "2026-10-25T02:13:00+08:00",
    "done": false
  }

用法：
  python tools/read_scheduler.py                    # 到点就叫
  python tools/read_scheduler.py --dry-run          # 只看会不会叫
  python tools/read_scheduler.py --session abc123   # 显式告诉它当前是哪个会话
  python tools/read_scheduler.py --status           # 打印进度摘要
"""

import json
import os
import random
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402

DEFAULT_SESSION = "default"


# ── 进度 ───────────────────────────────────────────────────────────────

def progress_path(cfg):
    return Path(cfg["dream"].get("progress_file") or (Path(cfg["paths"]["wake_dir"]) / "read-progress.json"))


def load_progress(cfg):
    p = progress_path(cfg)
    empty = {"session": "", "read": [], "lastWakeAt": None, "done": False}
    if not p.exists():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return empty
        data.setdefault("session", "")
        data.setdefault("read", [])
        data.setdefault("lastWakeAt", None)
        data.setdefault("done", False)
        data["read"] = [str(x) for x in (data.get("read") or [])]
        return data
    except Exception:
        return empty


def save_progress(cfg, data):
    p = progress_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


# ── 醒来包 ─────────────────────────────────────────────────────────────

def wake_files(cfg):
    d = Path(cfg["paths"]["wake_dir"])
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("醒-*.md"):
        try:
            n = int(f.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        out.append((n, f))
    out.sort()
    return out


def wid(n):
    return f"醒-{n:03d}"


# ── 时间 ───────────────────────────────────────────────────────────────

def in_window(cfg, now):
    d = cfg["dream"]
    return d["hours_start"] <= now.hour < d["hours_end"]


def next_at(cfg, last_wake):
    """由上次叫醒时间算出这一轮的目标时间：同一轮里稳定，不会每次调用都重抽。

    这样进度文件只需要存 lastWakeAt，不用再多存一个 nextAt。
    """
    d = cfg["dream"]
    lo, hi = int(d["interval_min_minutes"]), int(d["interval_max_minutes"])
    if last_wake is None:
        return None
    # 用 lastWakeAt 的秒数当种子：同一轮固定，下一轮自然不同
    seed = int(last_wake.timestamp())
    span = max(1, hi - lo + 1)
    return last_wake + timedelta(minutes=lo + (seed % span))


def parse_last(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# ── 叫醒 ───────────────────────────────────────────────────────────────

def wake_line(now, n, read_count, total):
    return (f"[该叫醒了] {now.strftime('%H:%M')} · 该读第 {n} 份（{wid(n)}）"
            f"· 已读 {read_count}/{total} · 也可以翻一份看过的")


def error_log(cfg, message):
    """叫醒失败：一行错误日志（时间 + 原因），不写内容。"""
    path = Path(cfg["paths"].get("log_file")
                or (Path(cfg["paths"]["data_dir"]) / "logs" / "wakekit.log"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} wake_error {message}\n")
    except OSError:
        pass


def do_wake(cfg, session, unread, read_count, total):
    """执行叫醒。返回 (ok, detail)。任何异常都算失败。"""
    hook = (cfg["adapters"] or {}).get("wake_hook") or {"type": "none"}
    kind = hook.get("type", "none")
    now = datetime.now()
    n = unread[0]
    line = wake_line(now, n, read_count, total)

    if kind == "none":
        print(line)
        return True, "none"

    if kind == "command":
        cmd = (hook.get("command") or "").format(session=session, unread=wid(n), read=read_count, total=total)
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120)
        except Exception as exc:  # noqa: BLE001
            return False, f"command 抛异常：{type(exc).__name__}: {exc}"
        if proc.returncode != 0:
            return False, f"command 退出码 {proc.returncode}：{(proc.stderr or '')[:200]}"
        print(line)
        return True, "command"

    if kind == "http":
        url = hook.get("url") or ""
        body = json.dumps({"session": session, "unread": wid(n), "read": read_count, "total": total},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        for k, v in (hook.get("headers") or {}).items():
            req.add_header(k, str(v))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if not (200 <= resp.status < 300):
                    return False, f"http 状态码 {resp.status}"
        except urllib.error.HTTPError as exc:
            return False, f"http 状态码 {exc.code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"http 失败：{type(exc).__name__}: {exc}"
        print(line)
        return True, "http"

    return False, f"未知的 wake_hook.type：{kind!r}"


# ── 主流程 ─────────────────────────────────────────────────────────────

def main():
    parser = kitcfg.get_parser("夜里的做梦调度")
    parser.add_argument("--session", default=None, help="当前会话标识（不给就用配置里的 dream.session_id）")
    parser.add_argument("--dry-run", action="store_true", help="只判断会不会叫，不改进度")
    parser.add_argument("--status", action="store_true", help="只打印进度摘要")
    args = parser.parse_args()

    cfg = kitcfg.load_config(args.config)
    session = args.session or cfg["dream"].get("session_id") or DEFAULT_SESSION
    progress = load_progress(cfg)
    files = wake_files(cfg)
    total = len(files)

    if args.status:
        read = set(progress.get("read") or [])
        print(f"会话：{progress.get('session') or '(未开始)'}")
        print(f"已读：{len(read & {wid(n) for n, _ in files})}/{total}")
        print(f"上次叫醒：{progress.get('lastWakeAt') or '无'}")
        print(f"完成：{'是' if progress.get('done') else '否'}")
        return 0

    sleep = cfg.get("sleep") or {}
    if not sleep.get("enabled", True):
        print("sleep.enabled 是关的（睡眠设定整体关闭）：不装潜意识、不注册任何定时唤醒；"
              "醒来包照常生成，不受影响。")
        return 0
    if not sleep.get("dream", True):
        print("sleep.dream 是关的（不做梦、不夜读旧包），不叫醒。")
        return 0

    if not cfg["features"].get("dream_scheduler"):
        print("features.dream_scheduler 是关的，跳过（要开就改成 true）。")
        return 0

    now = datetime.now()

    # ① 会话变了：先快照旧包，再重置进度。顺序不能反。
    if progress.get("session") != session:
        if progress.get("session"):
            snap = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("snapshot_wake.py")),
                 "--config", cfg["__file"]],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if snap.returncode != 0:
                msg = f"会话切换前快照失败，进度未重置：{(snap.stdout or snap.stderr or '').strip()[:200]}"
                error_log(cfg, msg)
                print(msg)
                return 1
            print(f"（会话切换，旧包已快照：{(snap.stdout or '').strip().splitlines()[-1] if snap.stdout.strip() else '无变化'}）")
        # 只有到这一步才写文件：session 对不上 → 记下见过它，之后就纯只读。
        # session 相同的情况下，非窗口时间、间隔没到、dry-run 都不会写盘。
        progress = {"session": session, "read": [], "lastWakeAt": None, "done": False}
        if not args.dry_run:
            save_progress(cfg, progress)
        files = wake_files(cfg)
        total = len(files)

    read = set(progress.get("read") or [])
    unread = [(n, f) for n, f in files if wid(n) not in read]

    if not total:
        print("还没有任何醒来包，没什么可读的。")
        return 0

    if not unread:
        if not progress.get("done"):
            progress["done"] = True
            if not args.dry_run:
                save_progress(cfg, progress)
        print(f"全部读过了（{len(read)}/{total}），不再叫醒。")
        return 0

    if not in_window(cfg, now):
        d = cfg["dream"]
        print(f"现在 {now.strftime('%H:%M')} 不在叫醒时间窗（{d['hours_start']:02d}:00–{d['hours_end']:02d}:00），不叫。")
        return 0

    last = parse_last(progress.get("lastWakeAt"))
    target = next_at(cfg, last)
    if target and now < target:
        print(f"离上次叫醒还不够久（上次 {last.strftime('%H:%M')}，下次 {target.strftime('%H:%M')}），不叫。")
        return 0

    need = min(int(cfg["dream"]["must_read_unread"]), len(unread))
    picked = unread[:need]

    if args.dry_run:
        print(wake_line(now, picked[0][0], len(read), total) + "  [dry-run]")
        return 0

    ok, detail = do_wake(cfg, session, [n for n, _ in picked], len(read), total)
    if not ok:
        # 失败：进度一点不动，只记错误
        error_log(cfg, detail)
        print(f"叫醒失败：{detail}（进度没动）")
        return 1

    for n, _ in picked:
        read.add(wid(n))
    progress["read"] = sorted(read)
    progress["lastWakeAt"] = now.isoformat(timespec="seconds")
    progress["done"] = len(unread) <= need
    save_progress(cfg, progress)
    return 0


if __name__ == "__main__":
    sys.exit(main())

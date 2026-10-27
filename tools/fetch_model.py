#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_model.py — 下载 bge-small-zh-v1.5（约 95 MB）到 models/bge-small-zh-v1.5。

要点：
  · 走 hf-mirror.com（国内直连 huggingface 多半不通）
  · 断点续传：带 Range 头，已下多少接着下
  · 单文件失败重试 5 次
  · 校验 model.safetensors 的字节数（95,827,648）
  · 下不下来就**明确说语义检索不可用**，不假装成功

用法：
  python tools/fetch_model.py
  python tools/fetch_model.py --local D:\\某处\\model   # 从本地已有的模型目录拷（不联网）
"""

import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402

REPO = "BAAI/bge-small-zh-v1.5"
BASE = f"https://hf-mirror.com/{REPO}/resolve/main/"
FILES = [
    ("config.json", None),
    ("model.safetensors", 95827648),
    ("tokenizer.json", None),
    ("tokenizer_config.json", None),
    ("vocab.txt", None),
    ("special_tokens_map.json", None),
]
RETRIES = 5


def human(n):
    return f"{n / 1024 / 1024:.1f} MB"


def download_one(name, expect_size, dest):
    target = dest / name
    for attempt in range(1, RETRIES + 1):
        have = target.stat().st_size if target.exists() else 0
        if expect_size and have == expect_size:
            print(f"  {name}: 已完整（{human(have)}），跳过")
            return True
        req = urllib.request.Request(BASE + name)
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = resp.headers.get("Content-Length")
                mode = "ab" if have and resp.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                with open(target, mode) as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if expect_size:
                            pct = min(100, int(have * 100 / expect_size))
                            print(f"\r  {name}: {human(have)} / {human(expect_size)} ({pct}%)",
                                  end="", flush=True)
            if expect_size:
                print()
            got = target.stat().st_size if target.exists() else 0
            if expect_size and got != expect_size:
                raise RuntimeError(f"大小不对：{got} != {expect_size}")
            print(f"  {name}: 完成（{human(got)}）")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"\n  {name}: 第 {attempt}/{RETRIES} 次失败 —— {type(exc).__name__}: {exc}")
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    return False


def copy_local(src):
    src = Path(src)
    if not src.exists():
        print(f"本地模型目录不存在：{src}")
        return False
    dst = Path(target_dir())
    dst.mkdir(parents=True, exist_ok=True)
    for name, _ in FILES:
        s = src / name
        if s.exists():
            shutil.copy2(s, dst / name)
            print(f"  拷入 {name}（{human(s.stat().st_size)}）")
    return True


def target_dir():
    cfg = kitcfg.load_config()
    return cfg["retrieval"]["sem_model_dir"]


def main():
    local = None
    if "--local" in sys.argv:
        i = sys.argv.index("--local")
        if i + 1 < len(sys.argv):
            local = sys.argv[i + 1]

    cfg = kitcfg.load_config()
    dest = Path(cfg["retrieval"]["sem_model_dir"])
    dest.mkdir(parents=True, exist_ok=True)

    print(f"模型目录：{dest}")
    if local:
        ok = copy_local(local)
    else:
        ok = True
        for name, size in FILES:
            if not download_one(name, size, dest):
                ok = False
                break

    if not ok:
        print("\n下载失败：**语义检索不可用**。")
        print("其它功能不受影响——把 retrieval.retriever 留在 bm25 就能正常用。")
        return 1

    # 再校验一次关键文件
    st = dest / "model.safetensors"
    if not st.exists() or st.stat().st_size != 95827648:
        print("\n模型文件不完整：语义检索不可用。")
        return 1
    print(f"\n模型就绪：{dest}")
    print("下一步：python tools/sem_build.py（建向量）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

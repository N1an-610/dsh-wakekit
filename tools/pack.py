#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack.py — 把 wakekit 打成一个能直接给别人的压缩包。

产物：dist/wakekit-<version>.zip

包里放：README / LICENSE / package.json / config.schema.json / wakekit.config.json（占位）
        src/ tools/ templates/ docs/ tests/ / .gitignore

不放：data/（真实记忆）、dist/、*.bak-*、__pycache__/、*.tmp

打完会自证：解压到临时目录，在里面跑一遍协议自测（node tests/mcp_selftest.js）。
node 找不到时会明确提示，并跳过这一步（不算失败，但会说出来）。

用法：
  python tools/pack.py
  python tools/pack.py --no-verify     # 不跑解压自证
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
DIST = KIT / "dist"

INCLUDE_DIRS = ["src", "tools", "templates", "docs", "tests", "shell"]
INCLUDE_FILES = ["README.md", "LICENSE", "package.json", "config.schema.json",
                 "wakekit.config.json", ".gitignore", "run-tests.bat", "使用事项-必看.txt",
                 "注意事项.md", "注意事项.html", "CHANGELOG.md",
                 "demo.bat", "demo-clean.bat"]

EXCLUDE_PARTS = {"data", "dist", "__pycache__", ".git", "models"}
EXCLUDE_SUFFIX = (".pyc", ".tmp")
EXCLUDE_PREFIX = ("*.bak-",)


def should_skip(path: Path) -> bool:
    rel = path.relative_to(KIT)
    if any(p in EXCLUDE_PARTS for p in rel.parts):
        return True
    if path.name.endswith(EXCLUDE_SUFFIX):
        return True
    if ".bak-" in path.name or ".tmp-" in path.name:
        return True
    return False


def collect():
    files = []
    for name in INCLUDE_FILES:
        p = KIT / name
        if p.exists():
            files.append(p)
    for d in INCLUDE_DIRS:
        base = KIT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and not should_skip(p):
                files.append(p)
    return files


def node_exe():
    p = os.environ.get("WAKEKIT_NODE")
    if p and Path(p).exists():
        return p
    return shutil.which("node")


def main():
    no_verify = "--no-verify" in sys.argv
    if not (KIT / "LICENSE").exists():
        print("缺 LICENSE，先补上再打包。")
        return 1

    version = json.loads((KIT / "package.json").read_text(encoding="utf-8"))["version"]
    DIST.mkdir(exist_ok=True)
    target = DIST / f"dsh-wakekit-{version}.zip"
    if target.exists():
        target.unlink()

    files = collect()
    total = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            arc = Path("dsh-wakekit") / p.relative_to(KIT)
            z.write(p, arc.as_posix())
            total += p.stat().st_size

    size_kb = target.stat().st_size / 1024
    print(f"打包完成：{target}")
    print(f"  文件 {len(files)} 个，原始 {total/1024:.1f} KB，压缩后 {size_kb:.1f} KB")
    print("  内容：")
    for p in files:
        print(f"    {p.relative_to(KIT)}")

    if no_verify:
        print("\n--no-verify：跳过解压自证。")
        return 0

    node = node_exe()
    if not node:
        print("\n找不到 node（设 WAKEKIT_NODE 或加进 PATH），跳过解压自证。")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="wk-pack-"))
    try:
        with zipfile.ZipFile(target) as z:
            z.extractall(tmp)
        root = tmp / "dsh-wakekit"
        env = dict(os.environ)
        env["WAKEKIT_NODE"] = node
        r = subprocess.run([node, str(root / "tests" / "mcp_selftest.js")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, cwd=str(root), env=env)
        ok = "通过 9 / 9" in (r.stdout or "")
        print("\n解压自证：", "通过（协议自测 9/9）" if ok else "失败")
        if not ok:
            print((r.stdout or "")[-800:])
            print((r.stderr or "")[-400:])
            return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

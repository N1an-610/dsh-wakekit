#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tests.py — 一次跑完六套自测，给一行总账。

给 run-tests.bat 用；也可以直接：python tools/run_tests.py

退出码：0 全绿 / 1 有失败 / 2 环境缺东西
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
# (标题, 解释器, 脚本, 这套有几条)
SUITES = [
    ("协议（MCP 四方法）", "node", "tests/mcp_selftest.js", 9),
    ("验收六条 + 补充", "python", "tests/selftest.py", 27),
    ("做梦调度", "python", "tests/selftest_dream.py", 10),
    ("安装向导", "python", "tests/selftest_install.py", 12),
    ("全流程（装→用→卸）", "python", "tests/selftest_flow.py", 7),
    ("可选外壳（实验性）", "node", "tests/selftest_shell.js", 13),
    ("容量 / 场景 / 睡眠", "python", "tests/selftest_v2.py", 17),
    ("语义检索（按意思找）", "python", "tests/selftest_sem.py", 9),
    ("归档隔离（防串库）", "python", "tests/selftest_archive_isolation.py", 4),
]
TOTAL_EXPECTED = sum(x[3] for x in SUITES)
SKIP_CODE = 3

COUNT_RE = re.compile(r"通过\s+(\d+)\s*/\s*(\d+)")


def find_node():
    p = os.environ.get("WAKEKIT_NODE")
    if p and Path(p).exists():
        return p
    return shutil.which("node")


def main():
    node = find_node()
    if not node:
        print("找不到 node。")
        print("  · 装一个 Node.js 18+，或者")
        print("  · 设置环境变量 WAKEKIT_NODE 指向 node.exe")
        return 2

    env = dict(os.environ)
    env["WAKEKIT_NODE"] = node
    env["PYTHONIOENCODING"] = "utf-8"

    print("wakekit 自测")
    print(f"  python：{sys.executable}")
    print(f"  node：  {node}")
    print()

    total_pass = 0
    total_all = 0
    failed = []

    skipped = []
    for title, kind, script, expect in SUITES:
        cmd = [node if kind == "node" else sys.executable, str(KIT / script)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=300, cwd=str(KIT), env=env)
        except subprocess.TimeoutExpired:
            print(f"❌ {title}：超时（300 秒）")
            failed.append(title)
            continue

        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == SKIP_CODE:
            why = ""
            for ln in out.splitlines():
                if ln.strip().startswith("跳过原因："):
                    why = ln.strip().split("：", 1)[1].strip()
                    break
            if not why:
                why = "条件不满足（该套件返回了跳过）"
            print(f"⏭  {title}：跳过（{why}）")
            skipped.append((title, why, expect))
            continue
        m = None
        for m in COUNT_RE.finditer(out):
            pass
        if m:
            got, alln = int(m.group(1)), int(m.group(2))
        else:
            got, alln = 0, 0

        total_pass += got
        total_all += alln
        ok = r.returncode == 0 and got == alln and alln == expect
        print(f"{'✅' if ok else '❌'} {title}：{got}/{alln}")
        if not ok:
            failed.append(title)
            tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
            for ln in tail:
                print(f"     {ln}")

    skip_pass = sum(x[2] for x in skipped)
    print()
    print(f"总结：通过 {total_pass} / 共 {total_all}（全套 {TOTAL_EXPECTED} 条）")
    if skipped:
        print(f"跳过 {len(skipped)} 套（{skip_pass} 条）："
              + "、".join(f"{t}（{n} 条）" for t, _why, n in skipped))
        reasons = "、".join(dict.fromkeys(w for _t, w, _n in skipped))
        print(f"（有 {skip_pass} 条测试跳过：{reasons}。这不是失败。）")

    if failed:
        print(f"没过的：{'、'.join(failed)}")
        return 1
    if total_all + skip_pass != TOTAL_EXPECTED:
        print("注意：条数和预期对不上，可能有的测试没跑全。")
        return 1
    if skipped:
        print("跑到的部分全部通过。")
    else:
        print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

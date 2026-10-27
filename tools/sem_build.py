#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sem_build.py — 给记忆库建/更新语义向量。

读 <memories_file> 里所有有效条目（跳过 superseded），存：
  <sem_index>         向量矩阵 .npy（每行一条，L2 归一化）
  同目录 sem_ids.json  每行对应的记忆 id

增量：只给「id 还不在 sem_ids.json 里」的条目算向量；没有新条目就都不算，
也不加载模型（所以可以随手调）。

用法：
  python tools/sem_build.py
  python tools/sem_build.py --rebuild    # 全部重算
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402
import semlib  # noqa: E402


def main():
    parser = kitcfg.get_parser("建/更新语义向量")
    parser.add_argument("--rebuild", action="store_true", help="丢掉旧向量全部重算")
    args = parser.parse_args()

    cfg = kitcfg.load_config(args.config)
    model_dir, vec_path, _ = semlib.paths_of(cfg)
    if not Path(model_dir).exists():
        print(f"模型目录不存在：{model_dir}")
        print("先跑 python tools/fetch_model.py 下载（约 95 MB），语义检索才可用。")
        return 2

    entries = semlib.read_entries(cfg["paths"]["memories_file"])
    if not entries:
        print("记忆库是空的，没有要向量化的条目。")
        return 0

    t0 = time.time()
    try:
        added, total = semlib.build_incremental(cfg, rebuild=args.rebuild)
    except Exception as exc:  # noqa: BLE001
        print(f"建向量失败：{type(exc).__name__}: {exc}")
        return 1

    if added == 0:
        print(f"没有新条目（共 {total} 条已在索引里），不用重算。")
        return 0

    size_mb = vec_path.stat().st_size / 1024 / 1024
    print(f"完成：新增 {added} 条，共 {total} 条向量，{size_mb:.2f} MB，"
          f"耗时 {time.time() - t0:.1f} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())

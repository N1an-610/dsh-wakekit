#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sem_search.py — 语义检索的 Python 侧（由 node 的 MCP 服务按需拉起）。

两种用法：
  1) 常驻（推荐）：--serve
     启动时加载模型和向量，然后从 stdin 读 JSON 行 {"query": "...", "topk": 300}，
     往 stdout 回一行 {"scores": {"<id>": <cos>}}；出任何错回 {"error": "..."}，不退出。

  2) 一次性：--query "..." [--topk N]
     打一行 JSON 就退出，给测试和手工排查用。

退出码：模型/向量缺失 → 2（调用方会退回关键词检索）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitcfg  # noqa: E402


def load_all(cfg):
    import numpy as np
    import semlib

    r = cfg["retrieval"]
    model_dir = Path(r["sem_model_dir"])
    vec_path = Path(r["sem_index"])
    ids_path = vec_path.parent / "sem_ids.json"

    if not model_dir.exists():
        raise SystemExit(f"模型目录不存在：{model_dir}")
    if not vec_path.exists() or not ids_path.exists():
        raise SystemExit(f"还没有语义索引：{vec_path}（先跑 python tools/sem_build.py）")

    vecs = np.load(vec_path)
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    if len(ids) != len(vecs):
        raise SystemExit("向量和 id 数量对不上，重跑 sem_build.py")
    state = semlib.load_model(model_dir)
    return {"vecs": vecs, "ids": ids, "state": state, "np": np}


def scores_for(ctx, query, topk):
    import semlib
    np = ctx["np"]
    v = semlib.embed(ctx["state"], [str(query)], batch=1, log=None)[0]
    sims = ctx["vecs"] @ v
    idx = np.argsort(-sims)[:max(1, int(topk))]
    return {ctx["ids"][i]: float(sims[i]) for i in idx}


def main():
    parser = kitcfg.get_parser("语义检索（按意思找）")
    parser.add_argument("--serve", action="store_true", help="常驻模式：stdin 一行一个查询")
    parser.add_argument("--query", default=None, help="一次性查询")
    parser.add_argument("--topk", type=int, default=None, help="候选数（默认用配置里的 sem_topk）")
    args = parser.parse_args()

    cfg = kitcfg.load_config(args.config)
    topk = args.topk or int(cfg["retrieval"].get("sem_topk", 300))

    try:
        ctx = load_all(cfg)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.query is not None:
        try:
            print(json.dumps({"scores": scores_for(ctx, args.query, topk)}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            return 1
        return 0

    # 常驻模式
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"error": "bad json"}), flush=True)
            continue
        try:
            if req.get("refresh"):
                # 模型已经在内存里，补小差量很快（零点几秒）
                import semlib
                added, total = semlib.build_incremental(cfg, model_state=ctx["state"], log=None)
                ids, vecs = semlib.load_vectors(cfg)
                if vecs is not None:
                    ctx["ids"], ctx["vecs"] = ids, vecs
                print(json.dumps({"refreshed": added, "total": total}, ensure_ascii=False), flush=True)
                continue
            print(json.dumps({"scores": scores_for(ctx, req.get("query", ""),
                                                   req.get("topk", topk))}, ensure_ascii=False),
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

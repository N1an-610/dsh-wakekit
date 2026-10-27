# -*- coding: utf-8 -*-
"""semlib.py — 语义检索共用：读条目、加载模型、向量化、增量建库。

模型：BAAI/bge-small-zh-v1.5（约 95 MB，CPU 能跑，有 GPU 会用 GPU）。
取 [CLS] 向量并 L2 归一化，所以余弦相似度 = 点积。

增量建库（build_incremental）被三个地方用：
  · tools/sem_build.py       手工跑
  · build_wake.py            每天生成醒来包时顺手补
  · sem_search.py --serve    检索时发现落后，现场补小差量（模型已加载，很快）
"""

import json
import sys
from pathlib import Path

BATCH = 32
MAX_LEN = 256


def read_entries(memories_file):
    """读有效条目（跳过 superseded）。坏行跳过。"""
    rows = []
    p = Path(memories_file)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("status") != "superseded":
            rows.append(obj)
    return rows


def load_model(model_dir):
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModel.from_pretrained(str(model_dir))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()
    if device == "cuda":
        model.half()
    return {"tok": tok, "model": model, "device": device, "torch": torch}


def _emit(log, msg):
    if log is None:
        return
    if callable(log):
        log(msg)
    else:
        print(msg, file=log, flush=True)


def embed(state, texts, batch=BATCH, log=None):
    import numpy as np

    torch = state["torch"]
    tok, model, device = state["tok"], state["model"], state["device"]
    out = []
    total = len(texts)
    for i in range(0, total, batch):
        chunk = texts[i:i + batch]
        with torch.no_grad():
            enc = tok(chunk, padding=True, truncation=True, max_length=MAX_LEN,
                      return_tensors="pt").to(device)
            v = model(**enc).last_hidden_state[:, 0]
            v = torch.nn.functional.normalize(v.float(), dim=-1).cpu().numpy()
        out.append(v)
        _emit(log, f"  向量化 {min(i + batch, total)}/{total}")
    if not out:
        return np.zeros((0, 512), dtype="float32")
    return np.vstack(out).astype("float32")


def entry_text(e):
    """一条记忆拿去算向量时用的文本：摘要 + 正文 + 标签。"""
    parts = [str(e.get("summary") or ""), str(e.get("text") or ""),
             " ".join(str(t) for t in (e.get("tags") or []))]
    return "\n".join(p for p in parts if p.strip())


def paths_of(cfg):
    vec = Path(cfg["retrieval"]["sem_index"])
    return Path(cfg["retrieval"]["sem_model_dir"]), vec, vec.parent / "sem_ids.json"


def load_vectors(cfg):
    import numpy as np
    _, vec_path, ids_path = paths_of(cfg)
    if not vec_path.exists() or not ids_path.exists():
        return [], None
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    vecs = np.load(vec_path)
    if len(ids) != len(vecs):
        return [], None
    return ids, vecs


def build_incremental(cfg, rebuild=False, model_state=None, log=print):
    """只给新条目算向量。返回 (新增条数, 总条数)。

    没有新条目时**不加载模型**，所以调用方可以放心随手调。
    """
    import numpy as np

    entries = read_entries(cfg["paths"]["memories_file"])
    ids, vecs = ([], None) if rebuild else load_vectors(cfg)
    known = set(ids)
    todo = [e for e in entries if str(e.get("id")) not in known]
    if not todo:
        return 0, len(ids)

    model_dir, vec_path, ids_path = paths_of(cfg)
    if not Path(model_dir).exists():
        raise RuntimeError(f"模型目录不存在：{model_dir}")

    state = model_state or load_model(model_dir)
    new_vecs = embed(state, [entry_text(e) for e in todo], log=log)
    new_ids = [str(e.get("id")) for e in todo]

    if vecs is not None and len(ids):
        vecs = np.vstack([vecs, new_vecs]).astype("float32")
        ids = list(ids) + new_ids
    else:
        vecs = new_vecs
        ids = new_ids

    vec_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(vec_path, vecs)
    ids_path.write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
    return len(todo), len(ids)

// src/search.js — BM25（中文按字、英文按词）+ tier 加权 + 时间衰减。
//
// 零依赖；条目量级是几千条，直接暴力算，不需要倒排索引。
// 打分口径固定，不随调用方变化：
//   score = bm25(query, doc) × tier_weight[tier] × 0.5^(ageDays / halfLife)

const CJK_RE = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]/;

// 中文一个字一个 token；英文/数字连成一个词；标点当分隔符。
export function tokenize(text) {
  const out = [];
  let buf = '';
  for (const ch of String(text || '')) {
    if (CJK_RE.test(ch)) {
      if (buf) { out.push(buf.toLowerCase()); buf = ''; }
      out.push(ch);
    } else if (/[A-Za-z0-9_\-.]/.test(ch)) {
      buf += ch;
    } else if (buf) {
      out.push(buf.toLowerCase());
      buf = '';
    }
  }
  if (buf) out.push(buf.toLowerCase());
  return out;
}

const K1 = 1.2;
const B = 0.75;

export function ageDays(ts, now = Date.now()) {
  const t = Number(ts);
  if (!Number.isFinite(t) || t <= 0) return 0;
  return Math.max(0, (now - t) / 86400000);
}

export function decayFactor(cfg, ts, now = Date.now()) {
  const half = cfg?.retrieval?.time_decay_half_life_days ?? 0;
  if (!half) return 1;
  return Math.pow(0.5, ageDays(ts, now) / half);
}

export function tierWeight(cfg, entry) {
  const weights = cfg?.retrieval?.tier_weights || {};
  const key = entry?.tier && weights[entry.tier] !== undefined ? entry.tier : '普通';
  return Number(weights[key] ?? 1);
}

// 传进来的 entries 应该是「有效条目」（调用方负责过滤作废的）。
export function searchEntries(entries, query, cfg, { now = Date.now() } = {}) {
  const qTokens = [...new Set(tokenize(query))];
  if (!qTokens.length || !entries.length) return [];

  const docs = entries.map((e) => {
    const toks = tokenize(`${e.summary || ''} ${e.text || ''} ${(e.tags || []).join(' ')}`);
    const tf = new Map();
    for (const t of toks) tf.set(t, (tf.get(t) || 0) + 1);
    return { entry: e, tf, dl: toks.length };
  });

  const N = docs.length;
  const avgdl = docs.reduce((s, d) => s + d.dl, 0) / N || 1;

  const df = new Map();
  for (const q of qTokens) {
    let c = 0;
    for (const d of docs) if (d.tf.has(q)) c += 1;
    df.set(q, c);
  }

  const scored = [];
  for (const d of docs) {
    let base = 0;
    for (const q of qTokens) {
      const tf = d.tf.get(q) || 0;
      if (!tf) continue;
      const n = df.get(q) || 0;
      const idf = Math.log(1 + (N - n + 0.5) / (n + 0.5));
      base += idf * ((tf * (K1 + 1)) / (tf + K1 * (1 - B + (B * d.dl) / avgdl)));
    }
    if (base <= 0) continue;
    const score = base * tierWeight(cfg, d.entry) * decayFactor(cfg, d.entry.ts, now);
    scored.push({ entry: d.entry, score });
  }

  scored.sort((a, b) => b.score - a.score || (Number(b.entry.ts) || 0) - (Number(a.entry.ts) || 0));
  return scored;
}

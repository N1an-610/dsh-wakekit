#!/usr/bin/env node
// src/server.js — wakekit 的 MCP stdio 服务。
//
// 工具：
//   mem_save     写一条记忆（带写入门槛 + 去重提示 + supersedes 覆盖）
//   mem_search   按关键词查（BM25 + tier 加权 + 时间衰减）
//   mem_list     最近条目
//   mem_get      按 id 取全文
//   mem_forget   按 id 真删（覆盖请用 mem_save 的 supersedes，别删）
//   chat_search  翻原文归档（关键词 / A-编号精确命中）
//   wake_get     翻某一份历史醒来包
//
// 约定：stdout 只写 MCP 协议消息；一切人类可读的日志走 stderr。

import fs from 'node:fs';
import path from 'node:path';
import { loadConfig, ConfigError, summarize } from './config.js';
import {
  ensureDirs, readEntries, appendEntry, rewriteEntries, findById, newId, logLine, todayStamp,
} from './store.js';
import { searchEntries } from './search.js';
import { checkEntry } from './gate.js';
import { createSemantic } from './semantic.js';
import { serve } from './rpc.js';

function argValue(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

let cfg;
try {
  cfg = loadConfig(argValue('--config'));
} catch (error) {
  if (error instanceof ConfigError) {
    process.stderr.write(`${error.message}\n`);
    process.exit(1);
  }
  throw error;
}

if (process.argv.includes('--print-config')) {
  process.stdout.write(`${JSON.stringify(summarize(cfg), null, 2)}\n`);
  process.exit(0);
}

ensureDirs(cfg);
const sem = createSemantic(cfg);



// ── 小工具 ─────────────────────────────────────────────────────────────

const pad = (n) => String(n).padStart(2, '0');

function fmtDay(ts) {
  const d = new Date(Number(ts) || Date.now());
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtTime(ts) {
  const d = new Date(Number(ts) || Date.now());
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function live(entries) {
  return entries.filter((e) => e && e.status !== 'superseded');
}

function card(e) {
  const tags = (e.tags || []).length ? ` [${(e.tags || []).join(',')}]` : '';
  const sc = e.scope && e.scope !== 'private' ? ` <${e.scope}${e.scene ? ':' + e.scene : ''}>` : '';
  const priv = e.private ? ' (私密)' : '';
  return `- (${e.id}) ${fmtDay(e.ts)}${tags}${priv}${sc} ${e.summary || String(e.text || '').slice(0, 40)}`;
}

// ── 工具实现 ───────────────────────────────────────────────────────────

function toolMemSave(args) {
  const started = Date.now();
  const { entries } = readEntries(null, cfg);
  const now = Date.now();

  if (!cfg.features.write_gate && !String(args.summary ?? '').trim()) {
    // 门槛关掉时也要给检索留个抓手：没摘要就从正文截
    args.summary = String(args.text ?? '').trim().slice(0, cfg.limits.summary_max_chars);
  }

  const { errors, warnings, similar } = checkEntry(args, cfg, entries, { now });
  if (errors.length) {
    return { text: `没有写进去：\n- ${errors.join('\n- ')}`, isError: true };
  }

  const tags = (Array.isArray(args.tags) ? args.tags : []).map((t) => String(t).trim()).filter(Boolean);
  const tier = String(args.tier ?? '').trim()
    || (tags.includes('火种') ? '火种' : tags.includes('原话') ? '原话' : '普通');

  const scope = String(args.scope ?? 'private').trim() || 'private';
  const scene = String(args.scene ?? '').trim().slice(0, 40);
  const entry = {
    id: newId(now),
    ts: now,
    scope,
    ...(scene ? { scene } : {}),
    text: String(args.text).trim(),
    summary: String(args.summary).trim().slice(0, cfg.limits.summary_max_chars),
    tags,
    tier,
    private: Boolean(args.private),
    schema: 2,
  };

  const supersedes = (Array.isArray(args.supersedes) ? args.supersedes : [])
    .map((x) => String(x).trim()).filter(Boolean).slice(0, 10);

  let replaced = [];
  if (supersedes.length) {
    const next = entries.map((e) => ({ ...e }));
    for (const e of next) {
      if (supersedes.includes(String(e.id)) && e.status !== 'superseded') {
        e.status = 'superseded';
        e.supersededBy = entry.id;
        replaced.push(e.id);
      }
    }
    if (replaced.length) entry.replaces = replaced;
    rewriteEntries(cfg, next);
  }

  appendEntry(cfg, entry);

  // 条目数上限：超了只提示，不自动删（删是人的决定）
  const total = entries.length + 1;
  const notes = [];
  if (warnings.length) notes.push(...warnings);
  if (total > cfg.limits.memory_max_entries) {
    notes.push(`记忆库已有 ${total} 条，超过上限 ${cfg.limits.memory_max_entries}；建议跑 health_check.py 看看哪些可以归档。`);
  }
  if (replaced.length) notes.push(`已作废旧条目：${replaced.join('、')}（文件里还留着，不再参与检索）。`);

  logLine(cfg, 'mem_save', { entries: total, ms: Date.now() - started });

  const head = `已记住 (${entry.id}) ${fmtDay(entry.ts)}${entry.private ? ' (私密)' : ''}`;
  return { text: [head, ...notes].join('\n') };
}

async function toolMemSearch(args) {
  const started = Date.now();
  const q = String(args.query ?? '').trim();
  if (!q) return { text: 'query 不能为空。', isError: true };
  const limit = Math.min(50, Math.max(1, Number(args.limit) || cfg.retrieval.top_k));
  const scope = String(args.scope ?? '').trim();
  const mode = String(args.mode ?? cfg.retrieval.retriever ?? 'bm25').trim() || 'bm25';

  const { entries } = readEntries(null, cfg);
  let pool = live(entries);
  if (scope) pool = pool.filter((e) => (e.scope || 'private') === scope);
  const byId = new Map(pool.map((e) => [String(e.id), e]));

  const kw = searchEntries(pool, q, cfg);

  let semScores = null;
  let notice = '';
  if (mode === 'semantic' || mode === 'hybrid') {
    // 先看向量落不落后：小差量现场补（模型已在子进程里，很快），大差量只提示
    if (cfg.retrieval.sem_auto_refresh !== false) {
      const ids = sem.idsCount();
      const liveCount = live(entries).length;
      if (ids >= 0 && liveCount > ids) {
        const lag = liveCount - ids;
        if (lag <= Number(cfg.retrieval.sem_auto_max ?? 20)) {
          const rr = await sem.refresh();
          if (!rr.ok) notice = rr.notice || '（语义索引没补上，本次只搜到已建索引的部分）';
        } else {
          notice = `（语义索引落后 ${lag} 条，跑一次 python tools/sem_build.py；`
            + '本次只搜到已建索引的部分）';
        }
      }
    }
    const res = await sem.query(q, cfg.retrieval.sem_topk);
    if (res.ok) semScores = res.scores;
    else notice = notice || res.notice;
  }

  let ranked;
  if ((mode === 'semantic' || mode === 'hybrid') && semScores) {
    if (mode === 'semantic') {
      ranked = Object.entries(semScores)
        .filter(([id]) => byId.has(id))
        .map(([id, sc]) => ({ entry: byId.get(id), score: Math.max(0, Number(sc) || 0) }))
        .sort((a, b) => b.score - a.score);
    } else {
      const w = Math.min(1, Math.max(0, Number(cfg.retrieval.sem_weight ?? 0.5)));
      const maxKw = kw.length ? kw[0].score : 1;
      const merged = new Map();
      for (const { entry, score } of kw) {
        merged.set(String(entry.id), { entry, kw: score / (maxKw || 1), sem: 0 });
      }
      for (const [id, sc] of Object.entries(semScores)) {
        if (!byId.has(id)) continue;
        const cur = merged.get(id) || { entry: byId.get(id), kw: 0, sem: 0 };
        cur.sem = Math.max(0, Number(sc) || 0);
        merged.set(id, cur);
      }
      ranked = [...merged.values()]
        .map(({ entry, kw: k, sem: s }) => ({
          entry,
          score: w * s + (1 - w) * k
            + 0.0005 * Math.min(String(entry.text || '').length, 300),
        }))
        .sort((a, b) => b.score - a.score);
    }
  } else {
    ranked = kw;
  }

  const scored = ranked.slice(0, limit);
  logLine(cfg, 'mem_search', { hits: scored.length, ms: Date.now() - started, mode });

  const head = notice ? `${notice}\n` : '';
  if (!scored.length) return { text: head + `记忆库里没有和「${q}」对得上的条目。` };
  return {
    text: head + `找到 ${scored.length} 条：\n${scored.map(({ entry }) => card(entry)).join('\n')}`,
  };
}

function toolMemList(args) {
  const limit = Math.min(100, Math.max(1, Number(args.limit) || 20));
  const { entries } = readEntries(null, cfg);
  const rows = live(entries).sort((a, b) => (b.ts || 0) - (a.ts || 0)).slice(0, limit);
  if (!rows.length) return { text: '记忆库还是空的。' };
  return { text: `最近 ${rows.length} 条：\n${rows.map(card).join('\n')}` };
}

function toolMemGet(args) {
  const id = String(args.id ?? '').trim();
  const { entries } = readEntries(null, cfg);
  const e = findById(entries, id);
  if (!e) return { text: `没有 id 为 ${id} 的条目。`, isError: true };
  const lines = [
    `(${e.id}) ${fmtDay(e.ts)}${e.tier ? ` · ${e.tier}` : ''}${e.private ? ' · 私密' : ''}`
      + `${e.scope && e.scope !== 'private' ? ` · ${e.scope}${e.scene ? ':' + e.scene : ''}` : ''}`,
    e.summary || '',
    (e.tags || []).length ? `标签：${e.tags.join('、')}` : '',
    e.status === 'superseded' ? `状态：已作废（被 ${e.supersededBy || '?'} 取代）` : '',
    '',
    e.text || '',
  ];
  return { text: lines.join('\n').replace(/\n{3,}/g, '\n\n').trim() };
}

function toolMemForget(args) {
  const id = String(args.id ?? '').trim();
  if (!id) return { text: 'id 不能为空。', isError: true };
  const { entries } = readEntries(null, cfg);
  const next = entries.filter((e) => String(e.id) !== id);
  if (next.length === entries.length) return { text: `没有 id 为 ${id} 的条目。`, isError: true };
  rewriteEntries(cfg, next);
  return { text: `已删除 ${id}（这是真删；以后想"覆盖"请用 supersedes，旧条会保留但作废）。` };
}

// 归档检索：先看是不是 A- 编号，再按关键词子串
function chatFiles() {
  try {
    return fs.readdirSync(cfg.paths.archive_dir)
      .filter((f) => /^chat-\d{4}-\d{2}\.jsonl$/.test(f))
      .sort()
      .map((f) => path.join(cfg.paths.archive_dir, f));
  } catch {
    return [];
  }
}

function toolChatSearch(args) {
  const started = Date.now();
  const raw = String(args.query ?? '').trim();
  if (!raw) return { text: 'query 不能为空。', isError: true };
  const q = raw.replace(/^#/, '');
  const byId = /^A-\d{8}-\d{4}$/i.test(q);
  const limit = Math.min(40, Math.max(1, Number(args.limit) || 10));

  const hits = [];
  for (const file of chatFiles()) {
    let text;
    try { text = fs.readFileSync(file, 'utf8'); } catch { continue; }
    for (const line of text.split('\n')) {
      if (!line.trim()) continue;
      let m;
      try { m = JSON.parse(line); } catch { continue; }
      const id = String(m.id || '');
      const body = String(m.text || '');
      if (byId ? id.toUpperCase() === q.toUpperCase() : body.includes(raw)) hits.push(m);
    }
  }

  hits.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const picked = hits.slice(0, limit);
  logLine(cfg, 'chat_search', { hits: picked.length, ms: Date.now() - started });

  if (!picked.length) return { text: `原文归档里没找到「${raw}」。` };
  const lines = picked.map((m) => {
    const d = new Date(Number(m.ts) || Date.now());
    const when = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const who = m.who ? `${m.who}：` : '';
    const body = String(m.text || '').replace(/\n/g, ' ').slice(0, 120);
    return `- (${m.id || '无编号'}) ${when} ${who}${body}`;
  });
  return { text: `原文里找到 ${picked.length} 条：\n${lines.join('\n')}` };
}

function toolWakeGet(args) {
  const n = Math.max(1, Math.min(999, Number(args.n) || 0));
  if (!n) return { text: 'n 必须是 1~999 的整数。', isError: true };
  const file = path.join(cfg.paths.wake_dir, `醒-${String(n).padStart(3, '0')}.md`);
  try {
    return { text: fs.readFileSync(file, 'utf8') };
  } catch {
    return { text: `没有第 ${n} 份醒来包。`, isError: true };
  }
}

// ── 工具清单 ───────────────────────────────────────────────────────────

const TOOLS = [
  {
    name: 'mem_save',
    description: '写一条记忆。必须有 summary（≤40字，一句"这是啥"）和至少一个 tag；'
      + '整段对话原文会被拒（那走 chat_search / 归档）。今天有类似条目会提示，'
      + '要覆盖就把旧 id 放进 supersedes（旧条作废但不删）。',
    inputSchema: {
      type: 'object',
      properties: {
        text: { type: 'string', description: '正文。一两句话，写清谁/什么/时间，人名写全名' },
        summary: { type: 'string', description: '一句话摘要，≤40 字，检索只显示这句' },
        tags: { type: 'array', items: { type: 'string' }, description: '标签，至少一个，如 ["约定","名字"]' },
        tier: { type: 'string', description: '重要度：火种/重要/原话/约定/普通/日记。不填按标签推断' },
        private: { type: 'boolean', description: '私密：永不进醒来包、不进摘要' },
        supersedes: { type: 'array', items: { type: 'string' }, description: '要覆盖的旧条目 id' },
        scope: { type: 'string', enum: ['private', 'group', 'other'],
          description: '这条记忆属于哪类场子：private=只对你一个人（默认）、group=群聊、other=其它' },
        scene: { type: 'string', description: '可选：群名或人名，group 时建议填' },
      },
      required: ['text', 'summary', 'tags'],
    },
  },
  {
    name: 'mem_search',
    description: '按关键词搜记忆库（重要度加权、旧条目会衰减）。拿不准以前说过什么先查这里。',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '关键词，可以是一句话' },
        limit: { type: 'number', description: '最多几条，默认用配置里的 top_k' },
        scope: { type: 'string', enum: ['private', 'group', 'other'],
          description: '只看某一类场子的记忆；不填=不限' },
        mode: { type: 'string', enum: ['bm25', 'semantic', 'hybrid'],
          description: '检索方式：bm25=关键词（默认）、semantic=按意思、hybrid=两边加权；不填用配置里的' },
      },
      required: ['query'],
    },
  },
  {
    name: 'mem_list',
    description: '列出最近的记忆条目（摘要卡片）。',
    inputSchema: {
      type: 'object',
      properties: { limit: { type: 'number', description: '最多几条，默认 20，最大 100' } },
    },
  },
  {
    name: 'mem_get',
    description: '按 id 取一条记忆的全文。',
    inputSchema: {
      type: 'object',
      properties: { id: { type: 'string', description: '条目 id' } },
      required: ['id'],
    },
  },
  {
    name: 'mem_forget',
    description: '按 id 真删一条记忆。只是想更正内容，请用 mem_save 的 supersedes，不要删。',
    inputSchema: {
      type: 'object',
      properties: { id: { type: 'string', description: '条目 id' } },
      required: ['id'],
    },
  },
  {
    name: 'chat_search',
    description: '翻原文归档。可以直接甩 `A-20261024-0113` 这种编号精确定位，也可以给关键词。'
      + '搜的是原始对话，不是记忆摘要。',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '关键词，或 A-YYYYMMDD-NNNN 编号' },
        limit: { type: 'number', description: '最多几条，默认 10，最大 40' },
      },
      required: ['query'],
    },
  },
  {
    name: 'wake_get',
    description: '翻某一份历史醒来包（wake_get(3) 看第 3 份）。醒来包末尾的目录里列着编号。',
    inputSchema: {
      type: 'object',
      properties: { n: { type: 'number', description: '第几份，从 1 开始' } },
      required: ['n'],
    },
  },
];

const HANDLERS = {
  mem_save: toolMemSave,
  mem_search: toolMemSearch,
  mem_list: toolMemList,
  mem_get: toolMemGet,
  mem_forget: toolMemForget,
  chat_search: toolChatSearch,
  wake_get: toolWakeGet,
};

serve({
  serverInfo: { name: 'wakekit', version: '0.2.1' },
  tools: TOOLS,
  callTool: async (name, args) => {
    const fn = HANDLERS[name];
    if (!fn) return { text: `未知工具：${name}`, isError: true };
    try {
      return fn(args || {});
    } catch (error) {
      return { text: `执行出错：${error?.message || error}`, isError: true };
    }
  },
});

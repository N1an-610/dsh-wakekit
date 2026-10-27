// src/gate.js — 写入门槛 + 去重提示。这两件事都在「落盘之前」做完。
//
// 门槛（可关，features.write_gate）：
//   · summary 必填，≤ limits.summary_max_chars
//   · 至少 limits.min_tags 个标签
//   · 正文 ≤ limits.entry_max_chars
//   · 拒收整段对话原文（那是归档的活）
// 去重（可关，features.dedupe）：
//   · 同一天 + 摘要开头 20 字（去空白/标点后）相同或互相包含 → 提示已有类似条目

import { tokenize } from './search.js';

const DIALOGUE_RE = /^\s*(我|你|他|她|user|assistant|human|bot|ai)\s*[：:]/i;

function norm(s) {
  return String(s || '').replace(/[\s\p{P}\p{S}]/gu, '').toLowerCase();
}

function sameDay(a, b) {
  const da = new Date(Number(a) || 0);
  const db = new Date(Number(b) || 0);
  return da.getFullYear() === db.getFullYear()
    && da.getMonth() === db.getMonth()
    && da.getDate() === db.getDate();
}

export function checkEntry(input, cfg, entries, { now = Date.now() } = {}) {
  const errors = [];
  const warnings = [];

  const text = String(input.text ?? '').trim();
  const summary = String(input.summary ?? '').trim();
  const tags = Array.isArray(input.tags)
    ? input.tags.map((t) => String(t).trim()).filter(Boolean)
    : [];

  if (!text) errors.push('正文不能为空。');
  if (text.length > cfg.limits.entry_max_chars) {
    errors.push(`正文 ${text.length} 字，超过上限 ${cfg.limits.entry_max_chars} 字；太长的东西请走原文归档。`);
  }

  if (cfg.features.write_gate) {
    if (!summary) {
      errors.push('缺少 summary：写一条记忆必须有一句「这是啥」（≤ '
        + `${cfg.limits.summary_max_chars} 字），以后检索只看这句。`);
    } else if (summary.length > cfg.limits.summary_max_chars) {
      errors.push(`summary ${summary.length} 字，超过上限 ${cfg.limits.summary_max_chars} 字。`);
    }
    if (tags.length < cfg.limits.min_tags) {
      errors.push(`至少要有 ${cfg.limits.min_tags} 个标签（比如 ["约定","名字"]）。`);
    }
    const dialogueLines = text.split('\n').filter((l) => DIALOGUE_RE.test(l)).length;
    if (dialogueLines >= 2 && text.length > cfg.limits.conversation_guard_chars) {
      errors.push('这看起来是整段对话原文（多行「谁：」开头）。记忆库存要点，原文请走 archive_chat.py。');
    }
  }

  const scope = String(input.scope ?? 'private').trim() || 'private';
  if (!['private', 'group', 'other'].includes(scope)) {
    errors.push(`scope 只能是 private / group / other（现在是 ${JSON.stringify(scope)}）。`);
  }
  if (scope === 'group' && !String(input.scene ?? '').trim()) {
    warnings.push('群聊记忆建议填上 scene（群名或人名），以后分得清是哪个场子里的。');
  }

  let similar = [];
  if (cfg.features.dedupe && summary) {
    const head = norm(summary).slice(0, 20);
    const headText = norm(text).slice(0, 20);
    for (const e of entries) {
      if (!e || e.status === 'superseded') continue;
      if (!sameDay(e.ts, now)) continue;
      const es = norm(e.summary);
      const et = norm(e.text);
      const hit = (head && (es.startsWith(head) || head.startsWith(es.slice(0, 20))))
        || (headText && (et.startsWith(headText) || et.slice(0, 20) === headText));
      if (hit) {
        similar.push({ id: e.id, summary: e.summary, ts: e.ts });
      }
    }
    if (similar.length) {
      warnings.push('今天已经有类似的条目：'
        + similar.map((s) => `(${s.id}) ${s.summary}`).join('；')
        + '。要覆盖就在 supersedes 里填它们的 id。');
    }
  }

  // 关键词覆盖率极低时提示（不是错误，只是提醒她可能搜不到）
  if (summary) {
    const q = new Set(tokenize(summary));
    const d = new Set(tokenize(`${summary} ${text} ${tags.join(' ')}`));
    let cover = 0;
    for (const t of q) if (d.has(t)) cover += 1;
    if (cover / (q.size || 1) < 1) {
      // 摘要必然覆盖自己，这里只是留个钩子，不做处理
    }
  }

  return { errors, warnings, similar };
}

// src/store.js — 记忆库的读写。JSONL，一行一条；坏行不拖垮其它行。
//
// 写策略：
//   · 新增 = 直接 append 一行（单行小写入，崩溃最多丢这一行，不会破坏前面）
//   · 改写 = 先写临时文件、fsync 前 rename 覆盖（原子替换），并留一份 .bak
//   · 任何读取都跳过坏行，坏行号记下来给 health_check 用

import fs from 'node:fs';
import path from 'node:path';

export function ensureDirs(cfg) {
  for (const dir of [
    cfg.paths.data_dir,
    path.dirname(cfg.paths.memories_file),
    cfg.paths.archive_dir,
    cfg.paths.wake_dir,
    path.dirname(cfg.paths.log_file),
    path.join(cfg.paths.data_dir, 'backups'),
  ]) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

export function todayStamp(ts = Date.now()) {
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function newId(ts = Date.now()) {
  return `${ts.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

// 读全部条目。返回 {entries, badLines:[{line, error}]}
export function readEntries(file = null, cfg = null) {
  const target = file || cfg.paths.memories_file;
  if (!fs.existsSync(target)) return { entries: [], badLines: [] };
  const raw = fs.readFileSync(target, 'utf8');
  const entries = [];
  const badLines = [];
  const lines = raw.split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (!line) continue;
    try {
      const obj = JSON.parse(line);
      if (!obj || typeof obj !== 'object') throw new Error('不是对象');
      entries.push(obj);
    } catch (error) {
      badLines.push({ line: i + 1, error: error.message });
    }
  }
  return { entries, badLines };
}

function endsWithNewline(file) {
  try {
    const st = fs.statSync(file);
    if (st.size === 0) return true;
    const fd = fs.openSync(file, 'r');
    try {
      const buf = Buffer.alloc(1);
      fs.readSync(fd, buf, 0, 1, st.size - 1);
      return buf[0] === 0x0a;
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return true;
  }
}

export function appendEntry(cfg, entry) {
  const file = cfg.paths.memories_file;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (!fs.existsSync(file)) fs.writeFileSync(file, '', 'utf8');
  const prefix = endsWithNewline(file) ? '' : '\n';
  fs.appendFileSync(file, `${prefix}${JSON.stringify(entry)}\n`, 'utf8');
}

// 原子替换：临时文件写完再 rename，最后留一份 .bak
export function rewriteEntries(cfg, entries, { backup = true } = {}) {
  const file = cfg.paths.memories_file;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp-${process.pid}-${Date.now()}`;
  const body = entries.map((e) => JSON.stringify(e)).join('\n');
  const fd = fs.openSync(tmp, 'w');
  try {
    fs.writeSync(fd, body ? `${body}\n` : '');
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  if (backup && fs.existsSync(file)) {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    fs.copyFileSync(file, path.join(path.dirname(file), `memories.jsonl.bak-${stamp}`));
  }
  fs.renameSync(tmp, file);
  pruneBackups(cfg);
}

export function pruneBackups(cfg) {
  const dir = path.dirname(cfg.paths.memories_file);
  const keep = cfg.limits.backup_keep;
  let files = [];
  try {
    files = fs.readdirSync(dir)
      .filter((f) => /^memories\.jsonl\.bak-/.test(f))
      .map((f) => ({ f, t: fs.statSync(path.join(dir, f)).mtimeMs }))
      .sort((a, b) => b.t - a.t);
  } catch {
    return;
  }
  for (const { f } of files.slice(keep)) {
    try { fs.unlinkSync(path.join(dir, f)); } catch { /* 删不掉就算了 */ }
  }
}

export function findById(entries, id) {
  return entries.find((e) => e && String(e.id) === String(id)) || null;
}

// 只记「时间 / 条数 / 耗时」，永远不写内容。
export function logLine(cfg, kind, fields = {}) {
  if (!cfg.features.observability) return;
  const file = cfg.paths.log_file;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const extra = Object.entries(fields)
      .map(([k, v]) => `${k}=${typeof v === 'number' ? Math.round(v) : v}`)
      .join(' ');
    fs.appendFileSync(file, `${new Date().toISOString()} ${kind} ${extra}\n`, 'utf8');
  } catch {
    // 日志写不进去不影响主流程
  }
}

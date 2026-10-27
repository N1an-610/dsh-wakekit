#!/usr/bin/env node
// tests/mcp_selftest.js — 验证手写的 MCP stdio 实现三个核心方法都通：
//   initialize / tools/list / tools/call
//
// 跑法：node tests/mcp_selftest.js
// 它会在临时目录里造一份配置和数据，不碰插件里的真实数据。

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const KIT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SERVER = path.join(KIT_ROOT, 'src', 'server.js');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wakekit-selftest-'));
const dataDir = path.join(tmp, 'data');
fs.mkdirSync(dataDir, { recursive: true });

const config = {
  version: 1,
  paths: {
    data_dir: dataDir,
    memories_file: '{data_dir}/memories.jsonl',
    archive_dir: '{data_dir}/archive',
    wake_dir: '{data_dir}/wake',
    wake_file: '{data_dir}/wake.md',
    log_file: '{data_dir}/logs/wakekit.log',
    card_path: '',
  },
  limits: { summary_max_chars: 40, min_tags: 1, entry_max_chars: 2000, conversation_guard_chars: 300,
    memory_max_entries: 5000, backup_keep: 30, wake_max_chars: 1000, ember_max: 8, ember_max_chars: 120,
    quote_max: 4, wake_index_limit: 40, summary_inject_max_chars: 1000 },
  retrieval: { top_k: 8, retriever: 'bm25', tier_weights: { 火种: 3, 普通: 1 }, time_decay_half_life_days: 30 },
  features: { archive: false, write_gate: true, dedupe: true, health_check: false, privacy: true,
    observability: false, summary_inject: false, dream_scheduler: false },
  dream: { hours_start: 1, hours_end: 7, interval_min_minutes: 30, interval_max_minutes: 60,
    must_read_unread: 1, progress_file: '{wake_dir}/read-progress.json' },
  adapters: { message_source: { type: 'none' }, wake_hook: { type: 'none' } },
};
const cfgFile = path.join(tmp, 'wakekit.config.json');
fs.writeFileSync(cfgFile, JSON.stringify(config, null, 2), 'utf8');

const child = spawn(process.execPath, [SERVER, '--config', cfgFile], { stdio: ['pipe', 'pipe', 'pipe'] });

let buf = '';
const pending = new Map();
let nextId = 1;

child.stdout.setEncoding('utf8');
child.stdout.on('data', (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { console.error('收到不是 JSON 的行：', line.slice(0, 80)); continue; }
    const p = pending.get(msg.id);
    if (p) { pending.delete(msg.id); p(msg); }
  }
});

const stderrChunks = [];
child.stderr.on('data', (c) => stderrChunks.push(String(c)));

function rpc(method, params) {
  const id = nextId++;
  const msg = { jsonrpc: '2.0', id, method, params };
  child.stdin.write(`${JSON.stringify(msg)}\n`);
  return new Promise((resolve, reject) => {
    pending.set(id, resolve);
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); reject(new Error(`超时：${method}`)); }
    }, 5000);
  });
}

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? `  — ${detail}` : ''}`);
}

function textOf(res) {
  return res?.result?.content?.[0]?.text ?? '';
}

async function main() {
  const init = await rpc('initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'selftest', version: '0' },
  });
  check('initialize 返回 serverInfo',
    init?.result?.serverInfo?.name === 'wakekit',
    JSON.stringify(init?.result?.serverInfo));
  check('initialize 返回 protocolVersion', typeof init?.result?.protocolVersion === 'string');

  child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' })}\n`);

  const list = await rpc('tools/list', {});
  const names = (list?.result?.tools || []).map((t) => t.name).sort();
  const expected = ['chat_search', 'mem_forget', 'mem_get', 'mem_list', 'mem_save', 'mem_search', 'wake_get'];
  check('tools/list 返回 7 个工具', JSON.stringify(names) === JSON.stringify(expected), names.join(','));

  const ping = await rpc('ping', {});
  check('ping 有响应', !!ping?.result);

  const save = await rpc('tools/call', {
    name: 'mem_save',
    arguments: { text: '对方说过要把记忆做成能单独拷走的插件。', summary: '记忆插件要能单独拷走', tags: ['约定'] },
  });
  check('tools/call mem_save 成功', /已记住/.test(textOf(save)), textOf(save).split('\n')[0]);

  const bad = await rpc('tools/call', {
    name: 'mem_save',
    arguments: { text: '没有摘要的一条', tags: ['测试'] },
  });
  check('tools/call 缺 summary 被拒', bad?.result?.isError === true, textOf(bad).split('\n')[0]);

  const found = await rpc('tools/call', { name: 'mem_search', arguments: { query: '插件' } });
  check('tools/call mem_search 命中', /找到 1 条/.test(textOf(found)), textOf(found).split('\n')[0]);

  const unknown = await rpc('tools/call', { name: 'nope', arguments: {} });
  check('未知工具返回 error', !!unknown?.error, unknown?.error?.message || '');

  const dup = fs.readFileSync(path.join(dataDir, 'memories.jsonl'), 'utf8').trim().split('\n');
  check('记忆库只落了 1 行（被拒那条没写进去）', dup.length === 1, `${dup.length} 行`);

  child.kill();
  fs.rmSync(tmp, { recursive: true, force: true });

  const failed = results.filter((r) => !r.ok);
  console.log(`\n通过 ${results.length - failed.length} / ${results.length}`);
  if (failed.length && stderrChunks.length) {
    console.log('\n服务端 stderr（最后几行）：');
    console.log(stderrChunks.join('').split('\n').slice(-8).join('\n'));
  }
  process.exit(failed.length ? 1 : 0);
}

main().catch((error) => {
  console.error('自测崩了：', error);
  child.kill();
  process.exit(1);
});

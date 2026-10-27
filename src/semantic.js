// src/semantic.js — 语义检索的 node 侧：按需拉起 python 常驻进程，问一句答一句。
//
// 为什么要常驻：加载模型要八九秒，每查一次就 spawn 一次根本没法用。
// 第一次用到语义时才拉起，之后复用；它挂了就标记不可用，调用方退回关键词。
//
// 两个动作：
//   query(text, topk)  查相似度
//   refresh()          让子进程用已加载的模型补建新条目的向量（零点几秒）

import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import readline from 'node:readline';

const REQUEST_TIMEOUT_MS = 120000;

export function createSemantic(cfg) {
  const base = path.dirname(cfg.__file);
  const modelDir = path.resolve(base, cfg.retrieval.sem_model_dir || './models/bge-small-zh-v1.5');
  const vecPath = path.resolve(base, cfg.retrieval.sem_index || './data/sem/sem_vectors.npy');
  const idsPath = path.join(path.dirname(vecPath), 'sem_ids.json');
  // 脚本在插件目录里（不是配置文件所在目录）
  const script = path.join(cfg.__root || path.dirname(cfg.__file), 'tools', 'sem_search.py');
  const py = (cfg.shell && cfg.shell.python) || process.env.WAKEKIT_PYTHON || 'python';

  let child = null;
  let rl = null;
  let pending = null;
  let dead = false;

  function unavailable() {
    if (!fs.existsSync(modelDir)) {
      return { ok: false, notice: '（语义检索未装，用的是关键词）' };
    }
    if (!fs.existsSync(vecPath) || !fs.existsSync(idsPath)) {
      return { ok: false, notice: '（还没建语义索引，先跑 python tools/sem_build.py；本次用的是关键词）' };
    }
    if (dead) return { ok: false, notice: '（语义检索进程没起来，本次用的是关键词）' };
    return null;
  }

  function start() {
    child = spawn(py, [script, '--config', cfg.__file, '--serve'],
      { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    rl = readline.createInterface({ input: child.stdout });
    rl.on('line', (line) => {
      const p = pending;
      pending = null;
      if (!p) return;
      try {
        p.resolve(JSON.parse(line));
      } catch {
        p.resolve({ error: '返回了看不懂的东西' });
      }
    });
    child.on('exit', () => {
      dead = true;
      child = null;
      if (pending) { pending.resolve({ error: '进程退出' }); pending = null; }
    });
    child.stderr.on('data', () => { /* 模型日志不往用户那边倒 */ });
  }

  function request(payload) {
    const why = unavailable();
    if (why) return Promise.resolve(why);
    if (!child) {
      try {
        start();
      } catch {
        dead = true;
        return Promise.resolve({ ok: false, notice: '（语义检索起不来，本次用的是关键词）' });
      }
    }
    return new Promise((resolve) => {
      if (pending) {
        resolve({ ok: false, notice: '（语义检索正忙，本次用的是关键词）' });
        return;
      }
      pending = { resolve };
      const timer = setTimeout(() => {
        if (pending) {
          pending = null;
          resolve({ ok: false, notice: '（语义检索超时，本次用的是关键词）' });
        }
      }, REQUEST_TIMEOUT_MS);
      if (timer.unref) timer.unref();
      try {
        child.stdin.write(`${JSON.stringify(payload)}\n`);
      } catch {
        pending = null;
        resolve({ ok: false, notice: '（语义检索写不进去，本次用的是关键词）' });
      }
    });
  }

  return {
    async query(text, topk) {
      const res = await request({ query: text, topk });
      if (res.ok === false) return res;
      if (res.error) return { ok: false, notice: `（语义检索出错：${res.error}，本次用的是关键词）` };
      if (res.refreshed !== undefined) return { ok: false, notice: '（语义检索状态不对，本次用的是关键词）' };
      return { ok: true, scores: res.scores || {} };
    },
    async refresh() {
      const res = await request({ refresh: true });
      if (res.error) return { ok: false, notice: `（语义索引自动补建失败：${res.error}）` };
      return { ok: true, added: res.refreshed || 0, total: res.total || 0 };
    },
    idsCount() {
      try {
        return JSON.parse(fs.readFileSync(idsPath, 'utf8')).length;
      } catch {
        return -1;
      }
    },
    close() { try { child?.kill(); } catch { /* 忽略 */ } },
  };
}

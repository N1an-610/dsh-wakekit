// shell/dsh-plugin/index.js — wakekit 的可选外壳。
//
// 只做两件事：
//   1. 定时跑 archive_chat.py（自动建索引）
//   2. 尝试把「上次到哪」挂进系统提示（有接口就挂，没有就只打一行日志）
//
// 三条硬规矩：
//   · 版本对不上 → 拒绝加载（不许带病运行）
//   · 任何异常都吃掉，绝不往外抛（外壳坏了，主体照常可用）
//   · 不引入任何依赖

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'wakekit-shell';

// 插件目录：<kit>/shell/dsh-plugin → kit 根在往上两级
const SHELL_DIR = path.dirname(fileURLToPath(import.meta.url));
const KIT_ROOT = path.resolve(SHELL_DIR, '..', '..');

// 支持的 DSH 版本区间（package.json 里也写了一份，两处保持一致）
const DSH_SUPPORT = { min: '0.2.0', maxExclusive: '0.3.0' };

const DEFAULT_INTERVAL_MINUTES = 30;

// ── 版本 ───────────────────────────────────────────────────────────────

function parseVersion(v) {
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(String(v || ''));
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

export function versionInRange(version) {
  const a = parseVersion(version);
  const lo = parseVersion(DSH_SUPPORT.min);
  const hi = parseVersion(DSH_SUPPORT.maxExclusive);
  if (!a || !lo || !hi) return false;
  const ge = a[0] > lo[0] || (a[0] === lo[0] && (a[1] > lo[1]
    || (a[1] === lo[1] && a[2] >= lo[2])));
  const lt = a[0] < hi[0] || (a[0] === hi[0] && (a[1] < hi[1]
    || (a[1] === hi[1] && a[2] < hi[2])));
  return ge && lt;
}

function detectDshVersion(ctx) {
  const tries = [
    () => process.env.WAKEKIT_DSH_VERSION,
    () => ctx?.version,
    () => ctx?.get?.('version'),
    () => ctx?.get?.('host')?.version,
    () => ctx?.get?.('runtime')?.version,
  ];
  for (const f of tries) {
    try {
      const v = f();
      if (v) return String(v);
    } catch { /* 读不到就算了 */ }
  }
  return '';
}

// ── 配置 ───────────────────────────────────────────────────────────────

function readKitConfig() {
  const p = path.join(KIT_ROOT, 'wakekit.config.json');
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function pythonExe(cfg) {
  return (cfg?.shell?.python) || process.env.WAKEKIT_PYTHON || 'python';
}

function runTool(ctx, cfg, script, args = []) {
  return new Promise((resolve) => {
    try {
      const py = pythonExe(cfg);
      const file = path.join(KIT_ROOT, 'tools', script);
      const child = spawn(py, [file, '--config', path.join(KIT_ROOT, 'wakekit.config.json'), ...args],
        { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
      let out = '';
      child.stdout?.on('data', (c) => { out += String(c); });
      child.stderr?.on('data', (c) => { out += String(c); });
      child.on('error', (e) => resolve({ ok: false, out: `spawn 失败：${e.message}` }));
      child.on('close', (code) => resolve({ ok: code === 0, out }));
    } catch (e) {
      resolve({ ok: false, out: `启动失败：${e.message}` });
    }
  });
}

// ── 插件入口 ───────────────────────────────────────────────────────────

export function apply(ctx, config = {}) {
  const version = detectDshVersion(ctx);
  const allowUnknown = process.env.WAKEKIT_SHELL_ALLOW_UNKNOWN === '1';

  if (!version) {
    if (!allowUnknown) {
      throw new Error(
        'wakekit-shell 拒绝加载：读不到 DSH 版本。'
        + `支持范围 ${DSH_SUPPORT.min} ~ <${DSH_SUPPORT.maxExclusive}。`
        + '确认版本后设置环境变量 WAKEKIT_DSH_VERSION；'
        + '确实要跳过检查，设 WAKEKIT_SHELL_ALLOW_UNKNOWN=1。',
      );
    }
  } else if (!versionInRange(version)) {
    throw new Error(
      `wakekit-shell 拒绝加载：当前 DSH ${version} 不在支持范围`
      + ` ${DSH_SUPPORT.min} ~ <${DSH_SUPPORT.maxExclusive}。`
      + '外壳和 DSH 内部接口绑定，版本不对可能带病运行；请先升级/降级，或不用外壳（主体照常可用）。',
    );
  }

  const cfg = readKitConfig();
  const sleep = cfg?.sleep || {};
  const sleepOff = sleep.enabled === false;
  const minutes = Number(config.intervalMinutes
    || cfg?.shell?.interval_minutes
    || DEFAULT_INTERVAL_MINUTES);
  const ms = Math.max(60_000, minutes * 60_000);

  // 1) 自动建索引：定时跑 archive_chat.py，失败只记日志
  try {
    ctx.effect?.(() => {
    const timer = setInterval(async () => {
      try {
        const r = await runTool(ctx, cfg, 'archive_chat.py');
        if (!r.ok) ctx.logger?.warn?.(`[wakekit-shell] 自动索引失败：${r.out.slice(-200)}`);
      } catch (e) {
        ctx.logger?.warn?.(`[wakekit-shell] 自动索引异常：${e?.message || e}`);
      }
    }, ms);
    if (timer.unref) timer.unref();
    return () => clearInterval(timer);
    }, 'wakekit-shell: auto index');
  } catch (e) {
    ctx.logger?.warn?.(`[wakekit-shell] 自动索引挂载失败：${e?.message || e}`);
  }

  // 2) 尝试自动注入摘要：有系统提示接口就挂，没有就只打一行日志（不报错）
  if (sleepOff) {
    ctx.logger?.info?.('[wakekit-shell] sleep.enabled=false，跳过摘要注入（醒来包不受影响）');
    return;
  }
  try {
    const hook = resolvePromptHook(ctx);
    if (hook) {
      ctx.effect?.(() => {
        try {
          const r = runTool(ctx, cfg, 'build_wake.py', ['--no-card']);
          if (!r.ok) return () => {};
          const text = r.out || '';
          const m = /## 上次到哪\n\n([\s\S]*?)\n\n##/.exec(text);
          const digest = (m?.[1] || '').trim();
          if (digest) hook(`上次到哪：\n${digest}`);
        } catch (e) {
          ctx.logger?.warn?.(`[wakekit-shell] 摘要注入失败：${e?.message || e}`);
        }
        return () => {};
      }, 'wakekit-shell: digest');
    } else {
      ctx.logger?.info?.('[wakekit-shell] 当前 DSH 没有可用的系统提示接口，跳过自动摘要（不影响主体）');
    }
  } catch (e) {
    ctx.logger?.warn?.(`[wakekit-shell] 摘要挂载异常：${e?.message || e}`);
  }
}

// 不同 DSH 版本的注入接口不一样，这里按可能性从宽到窄试一遍。
function resolvePromptHook(ctx) {
  try {
    if (typeof ctx?.systemPrompt?.append === 'function') return (t) => ctx.systemPrompt.append(t);
    if (typeof ctx?.systemPrompt?.register === 'function') return (t) => ctx.systemPrompt.register(t);
    if (typeof ctx?.hooks?.systemPrompt === 'function') return (t) => ctx.hooks.systemPrompt(t);
  } catch { /* 忽略 */ }
  return null;
}

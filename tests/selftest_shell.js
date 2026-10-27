#!/usr/bin/env node
// tests/selftest_shell.js — 外壳自测（不需要真的装进 DSH）。
//
// 验四件事：
//   1. 导出的形状对（name / apply / versionInRange）
//   2. 版本区间判断正确
//   3. 版本对不上 → 拒绝加载并说清原因
//   4. 语言层一切正常、但运行时依赖坏掉（python 不存在）→ 不抛异常

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const KIT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const mod = await import(new URL(`../shell/dsh-plugin/index.js?t=${Date.now()}`, import.meta.url).href);

let pass = 0;
let fail = 0;
function check(name, ok, detail = '') {
  if (ok) pass += 1; else fail += 1;
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? `  — ${detail}` : ''}`);
}

check('导出 name', mod.name === 'wakekit-shell', mod.name);
check('导出 apply', typeof mod.apply === 'function');
check('导出 versionInRange', typeof mod.versionInRange === 'function');

check('区间内 0.2.5', mod.versionInRange('0.2.5') === true);
check('下界 0.2.0', mod.versionInRange('0.2.0') === true);
check('上界外 0.3.0', mod.versionInRange('0.3.0') === false);
check('太低 0.1.9', mod.versionInRange('0.1.9') === false);
check('乱写的版本', mod.versionInRange('abc') === false);

function fakeCtx(extra = {}) {
  return {
    effects: 0,
    logger: { info() {}, warn() {} },
    get: () => undefined,
    effect(fn) { this.effects += 1; const d = fn(); return typeof d === 'function' ? d : () => {}; },
    ...extra,
  };
}

// 版本不对 → 拒绝加载
process.env.WAKEKIT_DSH_VERSION = '0.1.0';
let refused = '';
try {
  mod.apply(fakeCtx());
} catch (e) {
  refused = String(e.message || e);
}
check('版本不匹配时拒绝加载', /拒绝加载/.test(refused), refused.slice(0, 60));

// 版本读不到且没开开关 → 也拒绝
delete process.env.WAKEKIT_DSH_VERSION;
delete process.env.WAKEKIT_SHELL_ALLOW_UNKNOWN;
let refusedUnknown = '';
try {
  mod.apply(fakeCtx());
} catch (e) {
  refusedUnknown = String(e.message || e);
}
check('读不到版本时默认拒绝', /拒绝加载/.test(refusedUnknown), refusedUnknown.slice(0, 60));

// 版本对、但 python 不存在 → 不许抛，只能记日志
process.env.WAKEKIT_DSH_VERSION = '0.2.5';
process.env.WAKEKIT_PYTHON = '__definitely_not_a_python__';
let threw = '';
let ctx = fakeCtx();
try {
  mod.apply(ctx, { intervalMinutes: 60 });
} catch (e) {
  threw = String(e.message || e);
}
check('依赖坏掉时不抛异常', threw === '', threw.slice(0, 60));
check('注册了 effect（定时器挂着）', ctx.effects >= 1, `effects=${ctx.effects}`);

// 允许跳过检查时，未知版本也能加载
delete process.env.WAKEKIT_DSH_VERSION;
process.env.WAKEKIT_SHELL_ALLOW_UNKNOWN = '1';
let threw2 = '';
try {
  mod.apply(fakeCtx());
} catch (e) {
  threw2 = String(e.message || e);
}
check('ALLOW_UNKNOWN=1 时可加载', threw2 === '', threw2.slice(0, 60));

console.log(`\n通过 ${pass} / ${pass + fail}`);
process.exit(fail ? 1 : 0);

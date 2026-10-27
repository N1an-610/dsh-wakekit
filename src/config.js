// src/config.js — 读配置、展开路径占位符、启动校验。
//
// 铁律：配置有问题就带着「缺什么 / 怎么补」退出，不许静默降级。
// 这里没有任何指向外部项目的默认值——所有路径都相对本插件目录。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const KIT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export const DEFAULTS = {
  version: 1,
  labels: { partner: '对方' },
  paths: {
    data_dir: './data',
    memories_file: '{data_dir}/memories.jsonl',
    archive_dir: '{data_dir}/archive',
    wake_dir: '{data_dir}/wake',
    wake_file: '{data_dir}/wake.md',
    log_file: '{data_dir}/logs/wakekit.log',
    card_path: '',
  },
  limits: {
    summary_max_chars: 40,
    min_tags: 1,
    entry_max_chars: 2000,
    conversation_guard_chars: 300,
    memory_max_entries: 5000,
    backup_keep: 30,
    wake_max_chars: 1000,
    ember_max: 8,
    ember_max_chars: 120,
    quote_max: 4,
    focus_max_chars: 120,
    wake_index_limit: 40,
    summary_inject_max_chars: 1000,
    summary_scan: 20,
  },
  retrieval: {
    top_k: 8,
    retriever: 'bm25',
    sem_weight: 0.5,
    sem_model_dir: './models/bge-small-zh-v1.5',
    sem_index: './data/sem/sem_vectors.npy',
    sem_topk: 300,
    sem_auto_refresh: true,
    sem_auto_max: 20,
    tier_weights: { 火种: 3.0, 重要: 2.0, 原话: 1.8, 约定: 1.8, 日记: 1.0, 普通: 1.0 },
    time_decay_half_life_days: 30,
  },
  wake: {
    ember_max: 8,
    ember_max_chars: 120,
    reset_cycle_days: 3,
    daily_new_items: 5,
  },
  card: {
    subconscious: false,
  },
  sleep: {
    enabled: true,
    dream: true,
    self_wake: false,
  },
  features: {
    archive: false,
    group_memory: false,
    write_gate: true,
    dedupe: true,
    health_check: false,
    privacy: true,
    observability: false,
    summary_inject: false,
    dream_scheduler: false,
  },
  dream: {
    hours_start: 1,
    hours_end: 7,
    interval_min_minutes: 30,
    interval_max_minutes: 60,
    must_read_unread: 1,
    progress_file: '{wake_dir}/read-progress.json',
    session_id: '',
  },
  adapters: {
    message_source: { type: 'none' },
    wake_hook: { type: 'none' },
  },
};

function isPlainObject(v) {
  return v && typeof v === 'object' && !Array.isArray(v);
}

// 深合并：文件里的值覆盖默认值；数组整体替换。
export function mergeConfig(base, override) {
  if (!isPlainObject(override)) return base;
  const out = Array.isArray(base) ? base.slice() : { ...base };
  for (const [k, v] of Object.entries(override)) {
    out[k] = isPlainObject(v) && isPlainObject(base?.[k]) ? mergeConfig(base[k], v) : v;
  }
  return out;
}

// 递归展开 {data_dir} 这类占位符，并把相对路径按插件根目录解析。
function expand(value, ctx) {
  if (typeof value === 'string') {
    return value.replace(/\{([a-z_]+)\}/g, (m, key) => (key in ctx ? ctx[key] : m));
  }
  if (Array.isArray(value)) return value.map((v) => expand(v, ctx));
  if (isPlainObject(value)) {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = expand(v, ctx);
    return out;
  }
  return value;
}

function absolutize(value, baseDir) {
  if (typeof value === 'string') {
    if (!value) return value;
    return path.isAbsolute(value) ? path.normalize(value) : path.resolve(baseDir, value);
  }
  return value;
}

// 只把「确实是路径」的字段绝对化。以前这里是递归地绝对化所有字符串，
// 结果 "bm25"、"none" 这种枚举值也被拼成了 D:\...\bm25，校验直接炸。
function resolvePaths(cfg, baseDir) {
  cfg.paths.data_dir = absolutize(cfg.paths.data_dir, baseDir);
  for (const k of Object.keys(cfg.paths)) {
    if (k === 'data_dir') continue;
    cfg.paths[k] = absolutize(cfg.paths[k], baseDir);
  }
  if (cfg.dream && typeof cfg.dream.progress_file === 'string') {
    cfg.dream.progress_file = absolutize(cfg.dream.progress_file, baseDir);
  }
  const src = cfg.adapters && cfg.adapters.message_source;
  if (src && typeof src.path === 'string') src.path = absolutize(src.path, baseDir);
  return cfg;
}

export function configPath(explicit) {
  if (explicit) return path.resolve(explicit);
  if (process.env.WAKEKIT_CONFIG) return path.resolve(process.env.WAKEKIT_CONFIG);
  return path.join(KIT_ROOT, 'wakekit.config.json');
}

export function loadConfig(explicitPath) {
  const file = configPath(explicitPath);
  if (!fs.existsSync(file)) {
    throw new ConfigError(
      `找不到配置文件：${file}\n`
        + '补法：跑一次 `python tools/init.py`，或把 wakekit.config.json 放回插件根目录。\n'
        + '（也可以用 --config <路径> 或环境变量 WAKEKIT_CONFIG 指定位置。）',
    );
  }

  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    throw new ConfigError(`配置文件不是合法 JSON：${file}\n${error.message}\n补法：修好括号/逗号，或用备份恢复。`);
  }

  const baseDir = path.dirname(file);
  const merged = mergeConfig(DEFAULTS, raw);
  // 先展开占位符（{data_dir} 本身也要先解析成绝对路径）
  const dataDir = path.isAbsolute(merged.paths?.data_dir || '')
    ? merged.paths.data_dir
    : path.resolve(baseDir, merged.paths?.data_dir || './data');
  const expanded = expand(merged, { ...merged.paths, data_dir: dataDir, kit_root: KIT_ROOT });
  const cfg = resolvePaths(expanded, baseDir);
  cfg.__file = file;
  cfg.__root = KIT_ROOT;

  const problems = validate(cfg);
  if (problems.length) {
    throw new ConfigError(
      `配置有问题（${file}）：\n`
        + problems.map((p, i) => `  ${i + 1}. ${p}`).join('\n')
        + '\n改完再启动；配置校验通过之前不会读写任何数据。',
    );
  }
  return cfg;
}

export class ConfigError extends Error {}

// 校验返回问题清单（空数组 = 通过）。不做任何"自动修复"。
export function validate(cfg) {
  const p = [];
  const need = (cond, msg) => { if (!cond) p.push(msg); };
  const posInt = (v) => Number.isInteger(v) && v > 0;
  const bool = (v) => typeof v === 'boolean';

  const paths = cfg.paths || {};
  need(!!paths.data_dir, 'paths.data_dir 缺失');
  need(typeof paths.memories_file === 'string' && paths.memories_file, 'paths.memories_file 缺失');
  need(typeof paths.archive_dir === 'string' && paths.archive_dir, 'paths.archive_dir 缺失');
  need(typeof paths.wake_dir === 'string' && paths.wake_dir, 'paths.wake_dir 缺失');
  need(typeof paths.wake_file === 'string' && paths.wake_file, 'paths.wake_file 缺失');
  if (paths.card_path) {
    need(fs.existsSync(paths.card_path), `paths.card_path 指向的卡片不存在：${paths.card_path}（不需要注入卡片就留空）`);
  }

  const LAB = cfg.labels || {};
  need(typeof LAB.partner === 'string' && LAB.partner.trim().length > 0,
    'labels.partner 必须是非空字符串（模板里的 {{对方}} 会替换成它，默认「对方」）');

  const L = cfg.limits || {};
  for (const k of ['summary_max_chars', 'min_tags', 'entry_max_chars', 'conversation_guard_chars',
    'memory_max_entries', 'backup_keep', 'wake_max_chars', 'ember_max', 'ember_max_chars',
    'quote_max', 'focus_max_chars', 'wake_index_limit', 'summary_inject_max_chars', 'summary_scan']) {
    need(posInt(L[k]), `limits.${k} 必须是正整数（现在是 ${JSON.stringify(L[k])}）`);
  }
  need(L.ember_max <= 8, `limits.ember_max 不能超过 8（现在是 ${L.ember_max}）`);
  need(L.wake_max_chars >= 300, `limits.wake_max_chars 至少 300（现在是 ${L.wake_max_chars}）`);
  need(L.ember_max_chars <= L.wake_max_chars, 'limits.ember_max_chars 不能大于 wake_max_chars');

  const R = cfg.retrieval || {};
  need(posInt(R.top_k), `retrieval.top_k 必须是正整数（现在是 ${JSON.stringify(R.top_k)}）`);
  need(['bm25', 'semantic', 'hybrid'].includes(R.retriever),
    `retrieval.retriever 只能是 bm25 / semantic / hybrid（现在是 ${JSON.stringify(R.retriever)}）`);
  need(typeof R.sem_weight === 'number' && R.sem_weight >= 0 && R.sem_weight <= 1,
    'retrieval.sem_weight 必须是 0~1 的数字');
  need(posInt(R.sem_topk), `retrieval.sem_topk 必须是正整数（现在是 ${JSON.stringify(R.sem_topk)}）`);
  need(typeof R.sem_model_dir === 'string' && R.sem_model_dir, 'retrieval.sem_model_dir 缺失');
  need(typeof R.sem_index === 'string' && R.sem_index, 'retrieval.sem_index 缺失');
  need(typeof R.sem_auto_refresh === 'boolean', 'retrieval.sem_auto_refresh 必须是 true/false');
  need(posInt(R.sem_auto_max), `retrieval.sem_auto_max 必须是正整数（现在是 ${JSON.stringify(R.sem_auto_max)}）`);
  need(isPlainObject(R.tier_weights), 'retrieval.tier_weights 必须是对象');
  if (isPlainObject(R.tier_weights)) {
    for (const [k, v] of Object.entries(R.tier_weights)) {
      need(typeof v === 'number' && v >= 0, `retrieval.tier_weights.${k} 必须是非负数字`);
    }
  }
  need(typeof R.time_decay_half_life_days === 'number' && R.time_decay_half_life_days >= 0,
    'retrieval.time_decay_half_life_days 必须是 ≥0 的数字（0 = 不衰减）');

  const W = cfg.wake || {};
  for (const k of ['ember_max', 'ember_max_chars', 'reset_cycle_days', 'daily_new_items']) {
    need(posInt(W[k]), `wake.${k} 必须是正整数（现在是 ${JSON.stringify(W[k])}）`);
  }
  need(W.ember_max <= 30, `wake.ember_max 不要超过 30（现在是 ${W.ember_max}）`);

  const C = cfg.card || {};
  need(bool(C.subconscious), `card.subconscious 必须是 true/false（现在是 ${JSON.stringify(C.subconscious)}）`);

  const SL = cfg.sleep || {};
  for (const k of ['enabled', 'dream', 'self_wake']) {
    need(bool(SL[k]), `sleep.${k} 必须是 true/false（现在是 ${JSON.stringify(SL[k])}）`);
  }

  const F = cfg.features || {};
  for (const k of ['archive', 'group_memory', 'write_gate', 'dedupe', 'health_check', 'privacy',
    'observability', 'summary_inject', 'dream_scheduler']) {
    need(bool(F[k]), `features.${k} 必须是 true/false（现在是 ${JSON.stringify(F[k])}）`);
  }

  const D = cfg.dream || {};
  need(Number.isInteger(D.hours_start) && D.hours_start >= 0 && D.hours_start <= 23, 'dream.hours_start 必须是 0~23 的整数');
  need(Number.isInteger(D.hours_end) && D.hours_end >= 1 && D.hours_end <= 24, 'dream.hours_end 必须是 1~24 的整数');
  need(D.hours_start < D.hours_end, `dream.hours_start（${D.hours_start}）必须小于 hours_end（${D.hours_end}）`);
  need(posInt(D.interval_min_minutes), 'dream.interval_min_minutes 必须是正整数');
  need(posInt(D.interval_max_minutes), 'dream.interval_max_minutes 必须是正整数');
  need(D.interval_min_minutes <= D.interval_max_minutes, 'dream.interval_min_minutes 不能大于 interval_max_minutes');
  need(posInt(D.must_read_unread), 'dream.must_read_unread 必须是正整数');

  const A = cfg.adapters || {};
  const src = A.message_source || {};
  need(['none', 'jsonl', 'json', 'command'].includes(src.type),
    `adapters.message_source.type 只能是 none/jsonl/json/command（现在是 ${JSON.stringify(src.type)}）`);
  if (src.type === 'jsonl') {
    need(!!src.path, 'adapters.message_source.type=jsonl 时必须给 path');
    need(!!src.path && fs.existsSync(src.path), `adapters.message_source.path 不存在：${src.path}`);
  }
  if (src.type === 'json') {
    need(!!src.path, 'adapters.message_source.type=json 时必须给 path');
    need(!!src.path && fs.existsSync(src.path), `adapters.message_source.path 不存在：${src.path}`);
    need(!!src.pointer, 'adapters.message_source.type=json 时必须给 pointer（数组所在的点路径，例如 recentMessages）');
  }
  if (src.type === 'command') {
    need(!!src.command, 'adapters.message_source.type=command 时必须给 command');
  }

  const hook = A.wake_hook || {};
  need(['none', 'command', 'http'].includes(hook.type),
    `adapters.wake_hook.type 只能是 none/command/http（现在是 ${JSON.stringify(hook.type)}）`);
  if (hook.type === 'command') need(!!hook.command, 'adapters.wake_hook.type=command 时必须给 command');
  if (hook.type === 'http') need(!!hook.url, 'adapters.wake_hook.type=http 时必须给 url');

  return p;
}

// 给 --print-config / 排错用：只打印会影响行为的部分，不打印卡片全文。
export function summarize(cfg) {
  return {
    config: cfg.__file,
    data_dir: cfg.paths.data_dir,
    memories_file: cfg.paths.memories_file,
    archive_dir: cfg.paths.archive_dir,
    wake_dir: cfg.paths.wake_dir,
    card_path: cfg.paths.card_path || '(不注入卡片)',
    features: cfg.features,
    message_source: cfg.adapters.message_source.type,
    wake_hook: cfg.adapters.wake_hook.type,
  };
}

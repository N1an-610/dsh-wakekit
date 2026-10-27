// src/rpc.js — 手写的最小 MCP stdio 传输（JSON-RPC 2.0，一行一条消息）。
//
// 为什么手写：分发时「零依赖」最值钱。这里只实现 MCP 用得到的几个方法：
//   initialize / notifications/initialized / tools/list / tools/call / ping
// 其它方法一律 -32601。stdout 只写协议消息，日志一律走 stderr。

import readline from 'node:readline';

const PROTOCOL_VERSION = '2024-11-05';

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function reply(id, result) {
  write({ jsonrpc: '2.0', id, result });
}

function fail(id, code, message, data) {
  const error = { code, message };
  if (data !== undefined) error.data = data;
  write({ jsonrpc: '2.0', id, error });
}

// serverInfo: {name, version}；tools: 数组；callTool(name, args) → {text, isError?}
export function serve({ serverInfo, tools, callTool }) {
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });

  rl.on('line', async (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch (error) {
      fail(null, -32700, `JSON 解析失败：${error.message}`);
      return;
    }

    const { id, method, params } = msg || {};
    const isNotification = id === undefined || id === null;

    try {
      if (method === 'initialize') {
        reply(id, {
          protocolVersion: typeof params?.protocolVersion === 'string' ? params.protocolVersion : PROTOCOL_VERSION,
          capabilities: { tools: { listChanged: false } },
          serverInfo,
        });
        return;
      }

      if (method === 'notifications/initialized' || method === 'initialized') {
        return; // 通知：不回
      }

      if (method === 'ping') {
        reply(id, {});
        return;
      }

      if (method === 'tools/list') {
        reply(id, { tools });
        return;
      }

      if (method === 'tools/call') {
        const name = params?.name;
        const args = params?.arguments || {};
        const tool = tools.find((t) => t.name === name);
        if (!tool) {
          fail(id, -32602, `未知工具：${name}`);
          return;
        }
        const result = await callTool(name, args);
        reply(id, {
          content: [{ type: 'text', text: String(result.text ?? '') }],
          isError: Boolean(result.isError),
        });
        return;
      }

      if (method === 'shutdown') {
        reply(id, null);
        return;
      }

      if (isNotification) return;
      fail(id, -32601, `不支持的方法：${method}`);
    } catch (error) {
      process.stderr.write(`[wakekit] ${method} 出错：${error?.stack || error}\n`);
      if (!isNotification) fail(id, -32603, `内部错误：${error?.message || error}`);
    }
  });

  rl.on('close', () => process.exit(0));
}

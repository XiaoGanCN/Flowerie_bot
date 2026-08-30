#!/usr/bin/env node
// Plugin Node Runner v1: Flowerie <-> Node.js 插件（独立子进程，stdin/stdout JSON Lines）
//
// 用法（由 PluginRuntime 启动）: node node_runner.js --dir <plugin_dir> --entry index.js --plugin-id xxx
//
// 协议与 python_runner.py 完全一致：
//   Flowerie -> runner: {"id":1,"method":"initialize|event|health|shutdown","params":{...}}
//   runner -> Flowerie: {"id":1,"result":{...}} | {"id":N,"error":"..."}
//   插件动作（阻塞等待响应）: {"id":99,"method":"action","params":{"action":...,"payload":{...}}}
//
// 插件契约：module.exports = { on_startup(ctx, api), on_message(event, api), ... }
// 钩子可以是同步或 async；返回 None / {type,...} / [{type,...}, ...] 或 Promise 包裹的以上值。
// 插件无法 require Flowerie 内部模块（独立进程 + 只注入插件目录 require 路径）。

'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');

function parseArgs(argv) {
  const out = { dir: null, entry: 'plugin.js', pluginId: 'unknown' };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--dir') out.dir = argv[++i];
    else if (argv[i] === '--entry') out.entry = argv[++i];
    else if (argv[i] === '--plugin-id') out.pluginId = argv[++i];
  }
  return out;
}

class PluginApi {
  constructor(sendAction, pluginId) {
    this._sendAction = sendAction;
    this.pluginId = pluginId;
  }
  send_message(payload) { return this._sendAction('send_message', payload); }
  send_private_message(payload) { return this._sendAction('send_private_message', payload); }
  get_group(payload) { return this._sendAction('get_group', payload); }
  get_user(payload) { return this._sendAction('get_user', payload); }
  get_memory(payload) { return this._sendAction('get_memory', payload); }
  write_memory(payload) { return this._sendAction('write_memory', payload); }
  http_request(payload) { return this._sendAction('http_request', payload); }
  log(level, message) { return this._sendAction('log', { level, message }); }
}

class PluginRunner {
  constructor(args) {
    this.pluginDir = path.resolve(args.dir || '.');
    this.entry = args.entry || 'plugin.js';
    this.pluginId = args.pluginId || 'unknown';
    this.module = null;
    this._reqId = 0;
    this._pending = new Map(); // id -> {resolve}
    this.rl = null;
    this.api = new PluginApi((a, p) => this.sendAction(a, p), this.pluginId);
  }

  emit(obj) {
    process.stdout.write(JSON.stringify(obj) + '\n');
  }

  error(reqId, message) {
    this.emit({ id: reqId, error: String(message).slice(0, 800) });
  }

  // 插件 API：发 action 请求等待响应（Promise 由行分发器 resolve）
  sendAction(action, payload) {
    return new Promise((resolve, reject) => {
      this._reqId += 1;
      const myId = this._reqId;
      this._pending.set(myId, { resolve, reject });
      this.emit({ id: myId, method: 'action', params: { action, payload: payload || {} } });
      setTimeout(() => {
        if (this._pending.has(myId)) {
          this._pending.delete(myId);
          resolve({ ok: false, error: 'action timeout' });
        }
      }, 30000);
    });
  }

  async sendActionSafe(action, payload) {
    try {
      return await this.sendAction(action, payload);
    } catch (e) {
      return { ok: false, error: String(e && e.message ? e.message : e) };
    }
  }

  loadModule() {
    const entryPath = path.join(this.pluginDir, this.entry);
    if (!fs.existsSync(entryPath)) return `入口文件不存在: ${this.entry}`;
    try {
      const stat = fs.lstatSync(entryPath);
      if (stat.isSymbolicLink()) return '入口文件不能是符号链接';
      if (path.resolve(entryPath).indexOf(this.pluginDir + path.sep) !== 0) return '入口路径越界';
      // 清空 require 缓存（每次 initialize 重新加载）
      delete require.cache[require.resolve(entryPath)];
      this.module = require(entryPath);
      return null;
    } catch (e) {
      return `插件加载失败: ${e && e.message ? e.message : e}`;
    }
  }

  async callHook(name, ...args) {
    if (!this.module) return null;
    const hook = this.module[name];
    if (typeof hook !== 'function') return null;
    try {
      let result = await hook(...args);
      return result;
    } catch (e) {
      return { __error__: String(e && e.message ? e.message : e) };
    }
  }

  normalizeActions(result) {
    if (!result) return [];
    if (Array.isArray(result)) {
      return result.filter((x) => x && typeof x === 'object' && !x.__error__)
        .map((x) => Object.assign({}, x));
    }
    if (typeof result === 'object') {
      if (result.__error__) return [];
      return [Object.assign({}, result)];
    }
    return [];
  }

  async dispatchEvent(event, payload) {
    const eventObj = Object.assign({ event, plugin_id: this.pluginId }, payload || {});
    let hookName = null;
    if (event === 'message') hookName = 'on_message';
    else if (event === 'group_message') hookName = 'on_group_message';
    else if (event === 'command') hookName = 'on_command';
    if (!hookName) return [];
    const result = await this.callHook(hookName, eventObj, this.api);
    return this.normalizeActions(result);
  }

  // 行分发：处理 Flowerie 请求 / 响应插件 action
  onLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch (e) {
      return;
    }
    if (msg && typeof msg.id === 'number' && this._pending.has(msg.id)) {
      const p = this._pending.get(msg.id);
      this._pending.delete(msg.id);
      p.resolve(msg.result || { ok: false, error: 'empty result' });
      return;
    }
    if (msg && typeof msg.id === 'number' && msg.method) {
      // 串行处理（await 保证事件间不交错）
      return this.handle(msg).catch((e) => this.error(msg.id, `runner 异常: ${e && e.message ? e.message : e}`));
    }
  }

  async handle(msg) {
    const reqId = msg.id;
    const method = msg.method;
    const params = msg.params || {};
    if (method === 'initialize') {
      const err = this.loadModule();
      if (err) { this.error(reqId, err); return; }
      const ctx = Object.assign({ plugin_id: this.pluginId, plugin_dir: this.pluginDir, api_version: '1' }, params.context || {});
      const hookErr = await this.callHook('on_startup', ctx, this.api);
      if (hookErr && hookErr.__error__) { this.error(reqId, `on_startup 异常: ${hookErr.__error__}`); return; }
      this.emit({ id: reqId, result: { ok: true, api_version: '1' } });
    } else if (method === 'event') {
      const event = params.event || '';
      const actions = await this.dispatchEvent(event, params.payload || {});
      this.emit({ id: reqId, result: { actions } });
    } else if (method === 'health') {
      const hookResult = await this.callHook('health_check', { plugin_id: this.pluginId }, this.api);
      if (hookResult && hookResult.__error__) {
        this.emit({ id: reqId, result: { ok: false, error: hookResult.__error__ } });
      } else {
        this.emit({ id: reqId, result: { ok: true } });
      }
    } else if (method === 'shutdown') {
      await this.callHook('on_shutdown', { plugin_id: this.pluginId }, this.api);
      this.emit({ id: reqId, result: { ok: true } });
    } else {
      this.error(reqId, `未知方法: ${method}`);
    }
  }

  async run() {
    this.rl = readline.createInterface({ input: process.stdin, terminal: false });
    try {
      for await (const line of this.rl) {
        if (!line || !line.trim()) continue;
        const result = this.onLine(line.trim());
        if (result && typeof result.then === 'function') {
          await result.catch(() => {});
        }
      }
    } finally {
      this.rl.close();
    }
    return 0;
  }
}

if (require.main === module) {
  const args = parseArgs(process.argv.slice(2));
  const runner = new PluginRunner(args);
  runner.run().then((code) => process.exit(code));
}

module.exports = { PluginRunner, PluginApi };

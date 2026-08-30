// 最小可执行 Node.js 插件（Flowerie ↔ Node Plugin 端到端验证用）。
// 契约：on_message(event, api) 返回单个 action（Flowerie 捕获后经 PermissionManager 验证）。
'use strict';

exports.on_message = function onMessage(event, api) {
  return { type: 'test', message: 'node-ok', event: (event && event.event) || '' };
};

# 插件开发者指南（Plugin Developer Guide）

> Flowerie Plugin API **v1**（版本 `1.2.0` 引入）
>
> 本指南只描述 **实际支持** 的接口与行为。未列出的接口一概不存在；
> 「保留权限」等标注为 v1 未实现的能力请勿依赖。

## 1. Plugin API 概览

插件是**独立子进程**（Python / Node）或**进程内声明式规则**（JSON），通过统一协议与
Flowerie 通信。插件**不能** `import Flowerie` 内部模块（进程隔离 + Python `-I` 隔离模式），
一切能力都来自本 API：

```
Flowerie Plugin Manager
        │  (stdin/stdout JSON-Lines 协议)
        ├── Python 插件进程（python3 -I python_runner.py --dir <dir> --entry plugin.py）
        ├── Node 插件进程（node node_runner.js --dir <dir> --entry index.js）
        └── JSON 声明式插件（进程内规则匹配，无代码执行）
```

**核心原则**：插件返回「动作（action）」而不是直接执行副作用；动作由 Flowerie 的
PermissionManager 检查后执行。插件永远无法绕过权限。

## 2. Plugin Manifest

`manifest.json` 是每个插件的唯一元数据来源（严格 schema 校验，未知字段拒绝）：

```json
{
  "id": "example_plugin",
  "name": "Example Plugin",
  "version": "1.0.0",
  "author": "author",
  "description": "Example",
  "runtime": "python",
  "entry": "plugin.py",
  "api_version": "1",
  "permissions": ["read_message", "send_message"],
  "config": {}
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 小写字母开头，`[a-z0-9_-]`，≤32 字符 |
| `name` | ✅ | 1~64 字符 |
| `version` | ✅ | `x.y.z`（semver 三字段） |
| `runtime` | ✅ | `python` / `node` / `json` |
| `entry` | ✅ | 相对路径文件名（禁止绝对路径/`..`/反斜杠）；`json` 可留空 |
| `api_version` | ✅ | 仅支持 `"1"` |
| `permissions` | ✅ | 数组，见 §9；未知键会被拒绝 |
| `author` / `description` / `config` | ❌ | 元数据（config 为 JSON 对象，≤16KB） |
| `declarations` | ❌ | 仅 `runtime=json` 允许，见 §5 |

Node 插件示例：

```json
{ "runtime": "node", "entry": "index.js" }
```

## 3. Python 插件

目录结构：

```
my_plugin/
├── manifest.json
└── plugin.py
```

`plugin.py` 导出钩子函数（全部可省略，只写用到的）：

```python
def on_startup(context, api):
    """插件被加载时调用一次。return None。"""

def on_message(event, api):
    """收到消息事件（权限 read_message 已批准时投递）。
    返回 None | 单个 action dict | action list。"""

def on_command(event, api):
    """(预留) 命令事件。v1 不主动投递。"""

def on_group_message(event, api):
    """群消息事件（与 on_message 二选一实现；v1 的 message 事件投递给 on_message）。"""

def health_check(context=None, api=None):
    """健康检查；返回 None=健康。"""

def on_shutdown(context, api):
    """插件进程被优雅关闭时调用。"""
```

`event` 为 dict（最小字段集）：

```python
{"event": "message", "plugin_id": "...", "group_id": 123, "user_id": 456,
 "message_id": 789, "time": 1700000000, "text": "用户发言文本"}
```

`api` 为同步辅助对象（每个方法发送动作请求并等待响应，见 §7）。

最小示例（等价于仓库 `tests/plugins/minimal_plugin/`）：

```python
def on_message(event, api=None):
    return {"type": "test", "message": "plugin-ok"}
```

## 4. Node.js 插件

目录结构：

```
my_node_plugin/
├── manifest.json
├── package.json     # {"name": "...", "version": "1.0.0", "main": "index.js"}
└── index.js
```

`index.js` 导出与 Python 相同的钩子（支持 `async`）：

```js
'use strict';
exports.on_startup = async function (context, api) {};
exports.on_message = async function (event, api) {
  // 返回 action 或 action 数组（Promise 也支持）
  return { type: 'test', message: 'node-ok', event: event.event };
};
exports.on_shutdown = async function (context, api) {};
```

运行方式由 Flowerie 统一处理（`node node_runner.js --dir <dir> --entry index.js`），
插件只需要导出钩子。

## 5. JSON 声明式插件（Declarative Plugin）

`runtime=json`：**无代码执行**。声明式规则在进程内做模板匹配与动作转发，
行为受同样的权限检查约束：

```json
{
  "id": "greet_plugin",
  "name": "Greet",
  "version": "1.0.0",
  "runtime": "json",
  "entry": "",
  "api_version": "1",
  "permissions": ["read_message", "send_message"],
  "declarations": [
    {
      "event": "message",
      "match": {"text_prefix": "hello"},
      "actions": [
        {"type": "send_message",
         "payload": {"group_id": "${group_id}", "message": "你好 ${user_id}"}}
      ]
    }
  ]
}
```

- `match` 支持：`text_contains` / `text_prefix`（字符串）、`user_id` / `group_id`（整数）
- `payload` 支持模板字段：`${group_id}` `${user_id}` `${text}` `${message}` `${message_name}`
- 每条规则最多 4 个 action；插件最多 64 条规则；**没有 eval/exec，没有任意表达式**

## 6. 生命周期

```
安装（上传 ZIP / URL / 目录扫描）  →  状态 discovered（默认禁用）
管理员「启用 + 批准权限」          →  启动子进程 initialize → running
事件到达（read_message 已批准）    →  on_message(event) → actions → 权限检查 → 执行
管理员「禁用」                     →  shutdown → 进程退出 → disabled
进程崩溃 / 执行超时                →  标记 crashed（Flowerie 继续运行；管理员可重新启用）
卸载                               →  停止进程 + 删除插件目录 + 清除注册
```

发现 ≠ 执行：放入插件目录（`PLUGIN_DIR`，默认 `./plugins`）的插件在刷新扫描后
注册为**禁用**；必须由管理员明确启用。

## 7. Event API

| 事件 | 投递条件 | 钩子 |
| --- | --- | --- |
| `message`（群消息） | `read_message` 已批准 | `on_message(event, api)` |
| `notice`（群上传/戳戳等） | `read_message` 已批准 | （v1 无专用钩子，事件不投递） |
| `command` | — | 预留，v1 不投递 |

事件负载只包含最小字段（group_id / user_id / message_id / time / text ≤2000 字符），
不透传原始段数组。

## 8. Action API

钩子返回值 = `{"type": <action>, "payload": {...}}` 或 `[{...}, ...]`；
插件也可在钩子内调用 `api.<method>(payload)` 同步执行并拿到结果。

| action / api 方法 | 所需权限 | payload | 返回 |
| --- | --- | --- | --- |
| `send_message` / `api.send_message` | `send_message` | `{group_id, message}` | `{ok, group_id}` |
| `send_private_message` / `api.send_private_message` | `send_message` | `{user_id, message}` | `{ok, user_id}` |
| `get_group` / `api.get_group` | `read_group_info` | `{group_id}` | `{ok, group_id, info}` |
| `get_user` / `api.get_user` | `read_user_info` | `{user_id}` | `{ok, user_id, info}` |
| `get_memory` / `api.get_memory` | `read_memory` | `{user_id, group_id}` | `{ok, memory}` |
| `write_memory` / `api.write_memory` | `write_memory` | `{user_id, group_id, content}` | `{ok}` |
| `http_request` / `api.http_request` | `http_request` | `{url, method?, headers?, body?}` | `{ok, status, body}` |
| `file_read` | `filesystem_read` | `{path}`（插件目录内相对路径） | `{ok, content}` |
| `file_write` | `filesystem_write` | `{path, data}` | `{ok, bytes}` |
| `log` / `api.log(level, message)` | 无（内建安全） | `{level, message}` | `{ok}` |
| `test` | 无（内建安全） | 任意 | `{ok}` |

未实现（保留定义，批准权限也会拒绝）：`execute_process`、`webhook`。

### http_request 限制

- 仅 `GET` / `POST`；仅 `http://` / `https://`
- SSRF 防线：拒绝回环/私网/链路本地/组播/保留地址、`.local` 主机、userinfo、
  DNS 解析结果命中内网（防 rebinding）；**不跟随重定向**
- 请求体 ≤256KB；响应体 ≤256KB（超限截断返回）；超时 10s
- `Host` / `Authorization` / `Cookie` 头一律剥离

## 9. Permission API

权限（manifest 声明 → 管理员启用时批准，可批准子集；插件默认 0 权限）：

| 权限 | 对应能力 |
| --- | --- |
| `send_message` | 发送群/私聊消息 |
| `read_message` | 接收消息事件 |
| `read_group_info` | 读取群信息 |
| `read_user_info` | 读取用户信息 |
| `read_memory` | 读取记忆 |
| `write_memory` | 写入记忆 |
| `http_request` | 受限 HTTP 请求 |
| `filesystem_read` | 插件目录内读文件 |
| `filesystem_write` | 插件目录内写文件 |
| `execute_process` | **保留**：v1 无实现 |
| `webhook` | **保留**：v1 无实现 |

运行时强制：任何 action 在真正执行前都会过 PermissionManager；
未批准 → 拒绝并记录 `plugin_permission_denied` 日志。**关闭插件保护也不会绕过**。

## 10. 配置 API

插件自己的配置写在 `manifest.json` 的 `config` 字段（≤16KB JSON 对象），
通过 `context["config"]` 在 `on_startup` 时读取：

```python
def on_startup(context, api):
    default_msg = context["config"].get("message", "hi")
```

v1 不提供运行时修改插件配置的接口（改动 manifest 后重新扫描/启用）。

## 11. Memory API

见 §8 `get_memory` / `write_memory`。写入内容会经过与模型写入相同的记忆安全闸门
`validate_memory_content`（长度 ≤100 字、不含 QQ 号、不含记忆指令句式），
被拒绝时返回 `{ok: False, error: "记忆内容被安全策略拒绝（防注入）"}`。

## 12. HTTP API

插件调用外部服务统一走 `http_request`（§8），不提供其他网络能力；
Flowerie 的 HTTP API（send_group_msg 等 OneBot 接口）不是插件接口——请用 `send_message`。

## 13. Message API / 14. Group API / 15. User API

| API | 说明 |
| --- | --- |
| `send_message` / `send_private_message` | 消息发送（§8） |
| `get_group` | 群信息（上下文长度/最近活动时间等） |
| `get_user` | 用户信息（v1 返回 `{}`，视状态注入而定） |

## 16. Logging API

`api.log(level, message)` 或动作 `{"type":"log"}`：
写入 Flowerie 日志（等级 `info`），带 `plugin_id` 与 `plugin_log` 事件标记。
**插件无法读取 Flowerie 日志**；Flowerie 日志也不会记录任何插件 token/secret
（URL 下载与 WS 日志一律脱敏查询串）。

## 17. 错误处理

- 钩子抛异常 → 该事件被丢弃并记录 `plugin_event_error`（进程继续）
- 钩子返回 `{"__error__": ...}` → 同样丢弃
- action 执行失败 → 返回 `{ok: False, error: ...}` 给插件

## 18. 超时

| 场景 | 默认（与保护级别相关） |
| --- | --- |
| initialize 握手 | 10s（normal）/ 20s（relaxed）/ 30s（unsafe） |
| 单事件处理 | 15s / 60s / 120s |
| 插件 API 同步调用响应 | 30s |

超时 → 进程被终止、插件标记 `crashed`，Flowerie 继续运行。

## 19. 资源限制

- 单插件输出累计 ≥256KB（normal）/ 1MB / 4MB → 进程被终止
- 单事件动作数上限 8 / 16 / 32（超出截断）
- 插件目录文件/大小：安装时 ZIP 解压总大小 ≤50MB、文件数 ≤200、深度 ≤16、
  入口文件 ≤1MB；manifest ≤64KB
- 注册表插件总数 ≤100（`PLUGIN_MAX_COUNT`）

## 20. 安全规范

- 插件运行在**独立子进程**（`python3 -I` 隔离模式 / `node` 子进程），
  环境变量白名单不含任何 API Key / Token
- **边界诚实声明**：子进程隔离是"代码级"隔离（无共享状态、无 API Key、无 Flowerie
  内部模块），**不是 OS 级沙箱**——插件进程与 Flowerie 同用户运行，仍可读同用户的
  文件（如 `../.env` 配置）或执行任意系统调用。插件权限系统只约束**经管理器路由的
  动作**，不约束原始 OS 调用。因此：只安装你自己编写/审查过的插件；保护级别
  `unsafe` 只放宽资源限制，不改变上述边界；生产环境建议将 bot 运行在容器/独立
  用户内，并把 `.env` / 数据目录权限收紧
- 插件不能 `import Flowerie`，不能访问 Flowerie 数据库（没有也没有路径）
- 事件负载最小化：不传原始段数组、不传他人隐私
- `http_request` / 文件访问受路径与 SSRF 限制（§8）
- 安装（上传/URL）经 ZIP Slip / Zip Bomb / Symlink / 路径穿越 / manifest 注入防护（§22）
- **不要**在插件中硬编码 API Key；插件日志不外泄（Flowerie 侧已脱敏）

## 21. Plugin Packaging（打包）

ZIP 结构（也支持整体包一层目录 `pkg/`，自动剥离）：

```
plugin.zip
├── manifest.json
└── plugin.py          # 或 index.js + package.json（node）
```

限制：`.zip` 或 `.json`（单个 manifest，仅 `runtime=json`）；ZIP ≤5MB；
不允许符号链接；不允许绝对路径/`..`。

## 22. 本地插件目录 / 23. Web UI 安装 / 24. URL 安装

| 方式 | 入口 | 说明 |
| --- | --- | --- |
| 本地目录 | `<PLUGIN_DIR>/<id>/manifest.json` | Web UI「插件」页点「刷新扫描」即可发现；安装到注册表（默认禁用）后自动发现 |
| Web UI 上传 | Web UI「插件」页 → 导入插件 → 选择本地文件 → 上传并安装 | 文件类型/大小/内容均受控 |
| URL | Web UI「插件」页 → URL → 下载并安装 | SSRF 防护（内网/回环/私网/重定向全部拒绝）+ Content-Length 预检 + 流式大小中止 + 超时 + Content-Type/扩展名检查 |

三种方式安装后插件一律处于 **disabled**，由管理员启用并批准权限。

### 保护级别（插件保护措施开关）

Web UI「插件」页提供 Normal / Relaxed / Unsafe 三档（`PLUGIN_PROTECTION`）：

- **Normal（推荐）**：完整限制（默认）
- **Relaxed**：放宽非必要限制（更大的超时/输出/动作数）
- **Unsafe（仅可信插件，作者概不负责）**：进一步放宽限制

**任何级别都不豁免**：manifest 校验、管理员权限（Web UI 认证）、进程隔离、
日志、崩溃保护、资源限制、**权限强制（PermissionManager）**。
关闭保护≠无安全边界：普通 QQ 用户永远不能安装/启用插件或修改权限。

## 25. 最小插件示例（Python）

仓库自带可执行的最小插件（端到端测试用）：

```
tests/plugins/minimal_plugin/
├── manifest.json
└── plugin.py         # on_message → {"type": "test", "message": "plugin-ok"}
```

启用流程会真实启动子进程、发送事件、捕获 action → 见 `tests/test_plugin_runtime.py`。

## 26. Node.js 示例

```
tests/plugins/minimal_node_plugin/
├── manifest.json
├── package.json
└── index.js          # on_message → {"type": "test", "message": "node-ok"}
```

## 27. Python 示例（完整带权限）

```python
def on_message(event, api):
    if event.get("text") and event.get("text").startswith("!ping"):
        return {"type": "send_message",
                "payload": {"group_id": event["group_id"], "message": "pong"}}
    return None
```

（manifest 需声明 `"permissions": ["read_message", "send_message"]`）
（请配合 `tests/plugins/` 命名规范重命名 id 避免冲突）

## 28. Manifest 示例

见 §2 / §5。

## 29. 测试方法

插件开发者自测（无需真实 QQ）：

1. Manifest 校验：`PluginManifest.from_dict(...)`（单元测试，`tests/test_plugin_manifest.py`）
2. 端到端（推荐）：启动 `PluginRuntime` → `dispatch_event` → 断言返回 actions
   （参考 `tests/test_plugin_runtime.py`；Node 插件在 CI 有 Node 20 可执行）
3. 在线测试：启用后从群内发消息触发（事件经 `read_message` 投递）

## 30. API version compatibility

- `api_version` 当前仅支持 `"1"`；未来版本升级会保留 v1 兼容
- 破坏性变更会提升 `api_version` 并发布新指南；旧插件在升级后需更新 manifest
- 保留字段（`execute_process` / `webhook`）在 v1 中**不会**被实现；
  出现时只允许作为 manifest 权限声明（启用即被拒绝），不要作为功能使用

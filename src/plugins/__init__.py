"""Plugin System v1：受控插件运行时（Plugin Manager → Plugin Runtime → 独立进程）。

架构（与 Flowerie 现有边界对齐，不引入新的全局状态）：
- PluginManifest    统一插件契约（manifest.json 严格 schema 校验）
- PermissionManager 权限系统（运行时强制，不依赖插件自觉）
- PluginInstaller   安全安装（ZIP Slip / Bomb / Symlink / 路径穿越 / manifest 注入防护；
                    URL 下载继承 MCP 同级 SSRF 防线）
- PluginRuntime     子进程隔离的 Python / Node 运行时（stdin/stdout JSON-Lines 协议）
- PluginManager     注册表（settings.db）/ 自动发现 / 启停 / 事件分发 / Action 执行 / 日志

安全不变式（任何保护级别都不豁免）：
- 插件不能 import Flowerie 内部类（独立进程 + 仅 IPC）
- 插件不能绕过 PermissionManager（action 一律经其检查）
- 插件崩溃/超时被隔离，Flowerie 继续运行
- 插件安装/启用/权限修改只允许管理员（Web UI 认证）
- 插件 token / secret 不进日志
"""

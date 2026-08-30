#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plugin Python Runner v1：插件 API（Flowerie ↔ Python 插件，子进程隔离）。

用法（由 PluginRuntime 启动，不直接调用）::

    python3 -I python_runner.py --dir <plugin_dir> --entry plugin.py

协议（stdin/stdout JSON Lines，一行一条）：
- Flowerie → runner:
    {"id":1,"method":"initialize","params":{...}}
    {"id":2,"method":"event","params":{"event":"message","payload":{...}}}
    {"id":3,"method":"health"}
    {"id":4,"method":"shutdown"}
- runner → Flowerie:
    {"id":1,"result":{"ok":true}}
    {"id":2,"result":{"actions":[...]}}      # event 处理结果（插件返回的动作）
    {"id":N,"error":"..."}
- 插件 → Flowerie（运行期请求，同步等待响应）:
    {"id":99,"method":"action","params":{"action":"send_message","payload":{...}}}
    Flowerie 回复: {"id":99,"result":{...}}

插件契约（entry 导出的钩子，全部可省略，只在定义时调用）：
- on_startup(context) / on_shutdown(context)
- on_message(event) / on_group_message(event) / on_command(event)
- health_check(event=None)
钩子返回：None（无动作）| {"type": ..., ...}（单个动作）| [ {..}, .. ]（动作列表）
事件参数：event 为 dict；同时传第二个参数 api（可选）：api.send_message(payload) 等。

安全边界：本进程只读 stdin/stdout 与自己的插件目录；没有任何 Flowerie 内部
类可导入（独立进程 + python -I 隔离模式）。
"""
import argparse
import importlib.util
import inspect
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional


class PluginApi:
    """同步插件 API：每个方法向 Flowerie 发 action 请求并等待响应（阻塞读取 stdin）。"""

    def __init__(self, send_action, plugin_id: str):
        self._send_action = send_action
        self.plugin_id = plugin_id

    def send_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._send_action("send_message", payload)

    def send_private_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._send_action("send_private_message", payload)

    def get_group(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._send_action("get_group", payload)

    def get_user(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._send_action("get_user", payload)

    def get_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._send_action("get_memory", payload)

    def write_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._send_action("write_memory", payload)

    def http_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._send_action("http_request", payload)

    def log(self, level: str, message: str) -> Dict[str, Any]:
        return self._send_action("log", {"level": level, "message": message})


class PluginRunner:
    """协议主体：初始化模块 → 分发事件 → 处理 action 请求（请求-响应嵌套循环）。"""

    def __init__(self, plugin_dir: str, entry: str, plugin_id: str):
        self.plugin_dir = os.path.abspath(plugin_dir)
        self.entry = entry
        self.plugin_id = plugin_id
        self.module = None
        self._req_id = 0
        self.api = PluginApi(self._send_action_inner, plugin_id)

    # ---------- 基础 ----------
    def _emit(self, obj: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _error(self, req_id, message: str) -> None:
        self._emit({"id": req_id, "error": str(message)[:800]})

    def _readline(self) -> Optional[str]:
        line = sys.stdin.readline()
        if not line:
            return None
        return line.strip()

    def _send_action_inner(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """发 action 请求并阻塞等待对应响应（同步 API 的底层实现）。"""
        self._req_id += 1
        my_id = self._req_id
        self._emit({"id": my_id, "method": "action",
                    "params": {"action": action, "payload": payload or {}}})
        while True:
            line = self._readline()
            if line is None:
                return {"ok": False, "error": "connection closed"}
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == my_id:
                return msg.get("result") or {"ok": False, "error": "empty result"}

    def _send_action_safe(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._send_action_inner(action, payload)
        except Exception as e:  # noqa: BLE001 - 插件 API 异常不得拖死 runner
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---------- 模块加载 ----------
    def _load_module(self) -> Optional[str]:
        entry_path = os.path.join(self.plugin_dir, self.entry)
        if not os.path.isfile(entry_path):
            return f"入口文件不存在: {self.entry}"
        if os.path.islink(entry_path):
            return "入口文件不能是符号链接"
        try:
            spec = importlib.util.spec_from_file_location(
                f"flowerie_plugin_{self.plugin_id}", entry_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.module = module
            return None
        except Exception as e:  # noqa: BLE001
            return f"插件加载失败: {type(e).__name__}: {e}"

    def _call_hook(self, name: str, *args):
        if self.module is None:
            return None
        hook = getattr(self.module, name, None)
        if hook is None:
            return None
        # 按签名决定传参个数（避免 TypeError 重试歧义：插件内部 TypeError 不会误判为签名问题）
        try:
            sig = inspect.signature(hook)
            n_args = len([p for p in sig.parameters.values()
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
            call_args = args[:n_args] if n_args < len(args) else args
        except (TypeError, ValueError):  # 内置函数等无签名：按 2 参调用
            call_args = args
        try:
            return hook(*call_args)
        except Exception as e:  # noqa: BLE001
            return {"__error__": f"{type(e).__name__}: {e}"}

    # ---------- 请求处理 ----------
    def handle(self, msg: Dict[str, Any]) -> None:
        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                err = self._load_module()
                if err:
                    self._error(req_id, err)
                    return
                ctx = {"plugin_id": self.plugin_id, "plugin_dir": self.plugin_dir,
                       "api_version": "1", **params.get("context", {})}
                hook_err = self._call_hook("on_startup", ctx, self.api)
                if isinstance(hook_err, dict) and "__error__" in hook_err:
                    self._error(req_id, f"on_startup 异常: {hook_err['__error__']}")
                    return
                self._emit({"id": req_id, "result": {"ok": True, "api_version": "1"}})
            elif method == "event":
                event = params.get("event", "")
                payload = params.get("payload", {})
                self._emit({"id": req_id, "result": {"actions": self._dispatch_event(event, payload)}})
            elif method == "health":
                hook = self._call_hook("health_check", {"plugin_id": self.plugin_id}, self.api)
                if isinstance(hook, dict) and "__error__" in hook:
                    self._emit({"id": req_id, "result": {"ok": False, "error": hook["__error__"]}})
                else:
                    self._emit({"id": req_id, "result": {"ok": True}})
            elif method == "shutdown":
                self._call_hook("on_shutdown", {"plugin_id": self.plugin_id}, self.api)
                self._emit({"id": req_id, "result": {"ok": True}})
            else:
                self._error(req_id, f"未知方法: {method!r}")
        except Exception as e:  # noqa: BLE001 - runner 自身异常按请求级别报告
            self._error(req_id, f"runner 异常: {type(e).__name__}: {e}")

    def _dispatch_event(self, event: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        event_obj = {"event": event, "plugin_id": self.plugin_id, **payload}
        hook_name = None
        if event == "message":
            hook_name = "on_message"
        elif event == "group_message":
            hook_name = "on_group_message"
        elif event == "command":
            hook_name = "on_command"
        if hook_name is None:
            return []
        result = self._call_hook(hook_name, event_obj, self.api)
        return self._normalize_actions(result)

    @staticmethod
    def _normalize_actions(result) -> List[Dict[str, Any]]:
        if result is None:
            return []
        if isinstance(result, dict):
            if "__error__" in result:
                traceback.print_exc(file=sys.stderr)
                return []
            return [dict(result)]
        if isinstance(result, list):
            actions = []
            for item in result:
                if isinstance(item, dict) and not ("__error__" in item):
                    actions.append(dict(item))
            return actions
        return []

    # ---------- 主循环 ----------
    def run(self) -> int:
        while True:
            line = self._readline()
            if line is None:
                return 0
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            try:
                self.handle(msg)
            except SystemExit:  # noqa: BLE001
                return 0
            except Exception:  # noqa: BLE001 - 顶级兜底：单条消息失败不退出进程
                self._error(msg.get("id"), "runner 未处理异常")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="插件目录")
    parser.add_argument("--entry", default="plugin.py", help="入口文件")
    parser.add_argument("--plugin-id", default="unknown", help="插件 id（日志/API 透传）")
    args = parser.parse_args()
    runner = PluginRunner(args.dir, args.entry, args.plugin_id)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())

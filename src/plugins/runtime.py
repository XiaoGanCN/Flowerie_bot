"""PluginRuntime：插件的子进程运行时（隔离 + 超时 + 崩溃保护）。

管理一个插件子进程（Python：`python3 -I python_runner.py`；Node：`node node_runner.js`），
通过 stdin/stdout JSON-Lines 完成 Plugin API v1 协议交互。

关键不变式：
- 子进程独立：插件无法 import Flowerie 内部类（python -I 隔离模式 + 最小化环境变量，
  环境变量白名单不含任何 API Key / Secret）
- 单次请求有超时（保护级别决定）；超时则杀进程 → 标记 crashed（Flowerie 继续运行）
- 进程异常退出时通过 on_exit 回调通知 PluginManager（标记 unhealthy）
- 输出累计字节超限即杀（资源限制真实生效，不只是文档）
- 读写均加锁串行化（请求与插件 action 响应互不交错）

超出请求数上限时只透露统一错误信息，不泄露额外细节。
"""
import asyncio
import json
import os
import shutil
import sys
from typing import Any, Callable, Dict, List, Optional

from src.plugins.manifest import PluginManifest
from src.plugins.permissions import PermissionManager
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# 环境变量白名单：插件进程不继承任何 Secret（API Key / Token 全部不注入）。
# LD_LIBRARY_PATH 仅含系统/解释器库目录（不含源码路径），共享库构建的解释器需要它。
_ENV_WHITELIST = ("PATH", "HOME", "LANG", "TMPDIR", "TEMP", "TMP", "NODE_PATH",
                  "LD_LIBRARY_PATH")


class PluginTimeoutError(Exception):
    """插件请求超时（导致插件进程被杀）。"""


class PluginRuntime:
    """单个插件的子进程运行时。"""

    def __init__(self, plugin_id: str, manifest: PluginManifest, plugin_dir: str,
                 protection: str = "normal", on_exit: Optional[Callable[[str, int], None]] = None,
                 timeout_override: Optional[float] = None):
        self.plugin_id = plugin_id
        self.manifest = manifest
        self.plugin_dir = plugin_dir
        self.permissions = PermissionManager([], protection)
        self._limits = PermissionManager.limits(protection)
        if timeout_override is not None and protection != "normal":
            self._limits["event_timeout"] = float(timeout_override)
        self._on_exit = on_exit
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._writer_lock = asyncio.Lock()
        self._req_lock = asyncio.Lock()
        self._pending: Dict[int, asyncio.Future] = {}
        self._req_id = 0
        self._shutting_down = False
        self._output_bytes = 0
        self.status = "stopped"  # stopped | starting | running | crashed | unhealthy
        self._action_handler: Optional[Callable[[str, str, Dict[str, Any]], Any]] = None

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        """启动子进程并完成 initialize 握手；失败抛异常（由 Manager 记录状态）。"""
        if self.proc is not None:
            raise RuntimeError(f"plugin {self.plugin_id} already running")
        self.status = "starting"
        self._output_bytes = 0
        cmd, env = self._build_command()
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,   # 捕获尾 4KB（诊断 + 日志），不落盘
            env=env,
            cwd=self.plugin_dir,
        )
        self._stderr_tail = b""
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        self._reader_task = asyncio.create_task(self._reader_loop())
        try:
            await asyncio.wait_for(
                self.request("initialize", {"context": {"plugin_dir": self.plugin_dir}}),
                timeout=self._limits["startup_timeout"],
            )
        except asyncio.TimeoutError:
            self.status = "crashed"
            await self._kill("initialize timeout")
            raise PluginTimeoutError(
                f"plugin {self.plugin_id} initialize 超过 {self._limits['startup_timeout']}s") from None
        except Exception:
            self.status = "crashed"
            await self._kill("initialize failed")
            raise
        self.status = "running"
        logger.info("plugin_started id=%s runtime=%s", self.plugin_id, self.manifest.runtime,
                    extra={"event": "plugin_lifecycle"})

    async def shutdown(self, timeout: float = 5.0) -> None:
        """优雅停止：发 shutdown 请求 → 等待进程退出 → 强制清理。"""
        self._shutting_down = True
        if self.proc is None:
            return
        try:
            await asyncio.wait_for(self.request("shutdown", {}, timeout=min(timeout, 3.0)),
                                   timeout=timeout)
        except Exception:  # noqa: BLE001 - 关闭路径不抛
            pass
        await self._kill("shutdown")
        self._shutting_down = False

    async def _kill(self, reason: str) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self.proc.kill()
                await asyncio.wait_for(self.proc.wait(), timeout=3.0)
        except Exception:  # noqa: BLE001
            pass
        self._cleanup()
        logger.info("plugin_stopped id=%s reason=%s", self.plugin_id, reason,
                    extra={"event": "plugin_lifecycle"})

    async def _stderr_loop(self) -> None:
        """收集插件 stderr 尾 4KB（超出丢弃；供崩溃诊断，不落盘）。"""
        try:
            while self.proc is not None and self.proc.stderr is not None:
                chunk = await self.proc.stderr.read(4096)
                if not chunk:
                    break
                self._stderr_tail = (self._stderr_tail + chunk)[-4096:]
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            return

    def _stderr_diagnostic(self) -> str:
        tail = str(getattr(self, "_stderr_tail", b"") or b"")[-4096:]
        try:
            return tail.decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _cleanup(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        for task, attr in ((getattr(self, "_reader_task", None), "_reader_task"),
                           (getattr(self, "_stderr_task", None), "_stderr_task")):
            if task is not None and not task.done():
                task.cancel()
            setattr(self, attr, None)
        self.proc = None

    # ---------- 命令构造 ----------
    def _build_command(self) -> tuple:
        runner_dir = os.path.dirname(os.path.abspath(__file__))
        env = {k: os.environ.get(k, "") for k in _ENV_WHITELIST if k in os.environ}
        if not env.get("PATH"):
            env["PATH"] = "/usr/bin:/bin"
        if self.manifest.runtime == "node":
            node_bin = shutil.which("node") or "node"
            cmd = [node_bin, os.path.join(runner_dir, "node_runner.js"),
                   "--dir", self.plugin_dir, "--entry", self.manifest.entry,
                   "--plugin-id", self.plugin_id]
        elif self.manifest.runtime == "python":
            cmd = [sys.executable, "-I",  # -I：隔离模式（忽略 PYTHONPATH / 用户 site）
                   os.path.join(runner_dir, "python_runner.py"),
                   "--dir", self.plugin_dir, "--entry", self.manifest.entry,
                   "--plugin-id", self.plugin_id]
        else:
            raise RuntimeError(f"runtime {self.manifest.runtime} 不适用于子进程")
        return cmd, env

    # ---------- 协议 ----------
    async def _write(self, obj: Dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        data = json.dumps(obj, ensure_ascii=False) + "\n"
        async with self._writer_lock:
            try:
                self.proc.stdin.write(data.encode("utf-8"))
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                logging_runtime_conn_lost(self.plugin_id)

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                      timeout: Optional[float] = None) -> Dict[str, Any]:
        """发送请求并等待响应（默认用事件超时；超时杀进程）。"""
        if self.proc is None:
            raise RuntimeError(f"plugin {self.plugin_id} 未启动")
        timeout = timeout if timeout is not None else self._limits["event_timeout"]
        async with self._req_lock:
            self._req_id += 1
            req_id = self._req_id
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[req_id] = fut
            try:
                await self._write({"id": req_id, "method": method, "params": params or {}})
                try:
                    result = await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError:
                    await self._kill("timeout")
                    self.status = "crashed"
                    raise PluginTimeoutError(
                        f"plugin {self.plugin_id} {method} 超过 {timeout}s（已终止）") from None
            finally:
                self._pending.pop(req_id, None)
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(f"plugin {self.plugin_id} {method} 返回错误: {result['error']}")
            return result if isinstance(result, dict) else {}

    async def dispatch_event(self, event: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """投递事件，返回插件动作列表（已截断到上限）。"""
        try:
            result = await self.request("event", {"event": event, "payload": payload})
        except (PluginTimeoutError, RuntimeError, ConnectionError):
            self.status = "crashed"
            raise
        actions = result.get("actions") or []
        max_actions = int(self._limits["max_actions"])
        if isinstance(actions, list) and len(actions) > max_actions:
            logger.warning("plugin_actions_truncated id=%s got=%d max=%d",
                           self.plugin_id, len(actions), max_actions,
                           extra={"event": "plugin_truncated"})
            actions = actions[:max_actions]
        return actions if isinstance(actions, list) else []

    # ---------- 插件运行期请求（action）路由 ----------
    def set_action_handler(self, handler: Callable[[str, str, Dict[str, Any]], Any]) -> None:
        """注入 action 处理器（由 PluginManager 实现：权限检查 + 真实副作用）。"""
        self._action_handler = handler

    async def _reader_loop(self) -> None:
        """读 stdout：解析响应行 / 插件 action 请求。异常退出 → on_exit 通知。"""
        try:
            while self.proc is not None and self.proc.stdout is not None:
                raw = await self.proc.stdout.readline()
                if not raw:
                    break
                self._output_bytes += len(raw)
                if self._output_bytes > self._limits["max_output_bytes"]:
                    logger.error("plugin_output_overflow id=%s bytes=%d（已终止）",
                                 self.plugin_id, self._output_bytes,
                                 extra={"event": "plugin_crash"})
                    await self._kill("output overflow")
                    self.status = "crashed"
                    self._notify_exit("output_overflow", 0)
                    return
                try:
                    msg = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("id")
                if isinstance(msg_id, int) and msg_id in self._pending:
                    fut = self._pending[msg_id]
                    if not fut.done():
                        fut.set_result(msg)
                    continue
                if msg.get("method") == "action" and self._action_handler is not None:
                    asyncio.create_task(self._handle_action_line(msg))
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - 读循环自身异常：视为连接断开
            pass
        finally:
            if not self._shutting_down and self.proc is not None:
                self.status = "crashed"
                self._notify_exit("process_exit", 0)

    async def _handle_action_line(self, msg: Dict[str, Any]) -> None:
        """执行插件 action 请求并把结果写回插件（响应与请求互不阻塞）。"""
        req_id = msg.get("id")
        params = msg.get("params") or {}
        action = str(params.get("action") or "")
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        try:
            if self._action_handler is None:
                result = {"ok": False, "error": "no action handler"}
            else:
                result = await self._action_handler(self.plugin_id, action, payload)
                if not isinstance(result, dict):
                    result = {"ok": False, "error": "invalid action result"}
        except Exception as e:  # noqa: BLE001 - 动作异常反馈插件，不杀进程
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        await self._write({"id": req_id, "result": result})

    def _notify_exit(self, reason: str, code: int) -> None:
        diag = self._stderr_diagnostic()
        if diag:
            logger.warning("plugin_stderr id=%s reason=%s code=%s stderr=%s",
                           self.plugin_id, reason, code, diag[:500],
                           extra={"event": "plugin_crash"})
        if self._on_exit is not None:
            try:
                self._on_exit(self.plugin_id, reason, code)
            except Exception:  # noqa: BLE001
                pass

    @property
    def healthy(self) -> bool:
        return self.status == "running"

    async def health_check(self) -> Dict[str, Any]:
        try:
            result = await self.request("health", {}, timeout=5.0)
            return {"ok": bool(result.get("result", {}).get("ok", True)), "status": self.status}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": self.status, "error": type(e).__name__}


def logging_runtime_conn_lost(plugin_id: str) -> None:
    logger.warning("plugin_conn_lost id=%s", plugin_id, extra={"event": "plugin_crash"})

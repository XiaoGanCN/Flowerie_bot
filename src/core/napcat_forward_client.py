"""NapCatForwardClient：正向 WebSocket 客户端（Flowerie 连接 NapCat 的 WS server）。

与 WebSocketServer（反向 = NapCat 连接 Flowerie）二选一，由 NAPCAT_WS_MODE 决定：
- reverse：src/core/websocket_server.py（原有行为）
- forward：本客户端（Flowerie 作为 client 连接 NAPCAT_WS_URL）

安全与健壮性（requirement 7.2）：
- 仅 ws:// / wss://（启动校验）；wss 由 websockets 自动 TLS
- 鉴权：NAPCAT_ACCESS_TOKEN 通过 Authorization 头 + URL access_token 参数（OneBot11 约定）；
  **任何日志不包含带 token 的 URL**
- 超时：连接超时 + 单条事件处理超时（复用 EVENT_PROCESS_TIMEOUT）
- 重连：逐档递增退避 5→10→20→40→60 封顶
- 心跳：websockets 内建 ping（30s interval / 20s timeout）
- 事件处理隔离：trace_id 独立 + 并发信号量 + 单条超时（一条慢消息不卡整群）
- shutdown：优雅断开 + 取消在途任务
"""
import asyncio
import json
from typing import Optional
from urllib.parse import urlparse, urlunparse

import websockets

from src.utils.logging_setup import get_logger
from src.utils.metrics import registry
from src.utils.trace import trace_context

logger = get_logger(__name__)

_M_RECEIVED = registry.counter("received_messages_total", "收到的事件总数（按 post_type）", ["post_type"])
_M_RECONNECT = registry.counter("websocket_reconnect_total", "WebSocket 服务重连次数")

_RECONNECT_DELAYS = [5, 10, 20, 40, 60]


def redact_ws_url(url: str) -> str:
    """去除 URL 查询串（access_token 等敏感参数绝不进日志/UI）。"""
    try:
        parts = urlparse(url)
        return urlunparse((parts.scheme, parts.netloc, parts.path, "", parts.fragment))
    except ValueError:
        return "<invalid-url>"


class NapCatForwardClient:
    """正向 WS 客户端（NapCat 作为 server，Flowerie 作为 client）。"""

    def __init__(self, config, message_router, reconnect_delays=None):
        self.config = config
        self.message_router = message_router
        self._delays = list(reconnect_delays or _RECONNECT_DELAYS)
        self._running = True
        self._ws: Optional["websockets.WebSocketClientProtocol"] = None
        self._task: Optional[asyncio.Task] = None
        self._connect_timeout = 10.0
        self._url = str(getattr(config, "NAPCAT_WS_URL", "") or "").strip()
        self._token = str(getattr(config, "NAPCAT_ACCESS_TOKEN", "") or "").strip()

    # ---------- 生命周期 ----------
    async def run(self):
        """保持运行：连接 → 收消息 → 断开（自动重连退避）。"""
        self._task = asyncio.current_task()
        while self._running:
            if not self._url:
                logger.error("napcat_ws_url_missing: NAPCAT_WS_URL 未配置", extra={"event": "ws_error"})
                self._running = False
                break
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                logger.info("NapCat forward client cancelled")
                break
            except Exception as e:  # noqa: BLE001 - 连接失败/断开 → 退避重连
                logger.warning("napcat_ws_disconnected reason=%s", type(e).__name__,
                               extra={"event": "ws_disconnected"})
                self._mark_disconnected()
            if not self._running:
                break
            for delay in self._delays:
                if not self._running:
                    break
                logger.warning("NapCat 正向 WS 将在 %ss 后重连", delay)
                _M_RECONNECT.inc()
                await asyncio.sleep(delay)
        await self._cleanup()

    async def shutdown(self) -> None:
        self._running = False
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close(code=1000, reason="shutdown")
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None and self._task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._mark_disconnected()

    # ---------- 连接与消息循环 ----------
    async def _connect_and_listen(self) -> None:
        headers = None
        connect_url = self._url
        if self._token:
            # 鉴权：Authorization 头 + URL access_token 参数（OneBot11 约定；日志只记脱敏 URL）
            headers = {"Authorization": f"Bearer {self._token}"}
            parsed = urlparse(self._url)
            connect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                                      "access_token=" + self._token, parsed.fragment))
        logger.info("napcat_ws_connecting url=%s", redact_ws_url(self._url),
                    extra={"event": "ws_connecting"})
        connect_kwargs = {
            "ping_interval": 30,
            "ping_timeout": 20,
            "max_size": 8 * 1024 * 1024,      # 单帧上限（防异常大包）
            "max_queue": 64,                   # 背压上限（防缓慢消费打爆内存）
            "open_timeout": self._connect_timeout,
            "close_timeout": 10,
        }
        if headers is not None:
            # websockets 14+ 用 additional_headers；12/13 用 extra_headers（兼容处理）
            try:
                connect_kwargs["additional_headers"] = headers
                ws = await websockets.connect(connect_url, **connect_kwargs)
            except TypeError:
                connect_kwargs.pop("additional_headers", None)
                connect_kwargs["extra_headers"] = headers
                ws = await websockets.connect(connect_url, **connect_kwargs)
        else:
            ws = await websockets.connect(connect_url, **connect_kwargs)
        async with ws:
            self._ws = ws
            self.message_router.global_state.ws_connected = True
            logger.info("napcat_ws_connected url=%s", redact_ws_url(self._url),
                        extra={"event": "ws_connected"})
            async for message in ws:
                if not self._running:
                    break
                await self._process_message(message)

    async def _process_message(self, message) -> None:
        try:
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            if isinstance(message, str):
                data = json.loads(message)
            else:
                data = message
            with trace_context() as tid:
                logger.info(
                    "ws_event post_type=%s type=%s",
                    data.get("post_type"), data.get("message_type") or data.get("notice_type") or "-",
                    extra={"event": "ws_event_received"},
                )
                _M_RECEIVED.inc({"post_type": str(data.get("post_type", "unknown"))})
                async with self.message_router.process_semaphore:
                    await asyncio.wait_for(
                        self.message_router.process_event(data),
                        timeout=self.config.EVENT_PROCESS_TIMEOUT,
                    )
                logger.debug("ws_event processed trace=%s", tid)
        except asyncio.TimeoutError:
            logger.error("Event processing timeout (>=%ss), skipped: %s",
                         self.config.EVENT_PROCESS_TIMEOUT, str(message)[:100])
        except json.JSONDecodeError as e:
            logger.error("JSON decode error: %s", e)
        except Exception as e:  # noqa: BLE001 - 单条事件异常不中断循环
            logger.exception("Event processing error: %s", e)

    def _mark_disconnected(self) -> None:
        if self._ws is not None:
            self._ws = None
        if self.message_router is not None:
            self.message_router.global_state.ws_connected = False

    async def _cleanup(self) -> None:
        self._mark_disconnected()
        logger.info("NapCat forward client stopped", extra={"event": "ws_shutdown_finished"})

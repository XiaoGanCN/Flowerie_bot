import asyncio
import json
import websockets
from typing import Optional
from loguru import logger

from src.config import Settings
from src.core.message_router import MessageRouter


class WebSocketServer:
    """NapCat 反向 WebSocket 服务。

    业务场景是单连接（花璃只对接一个 NapCat 实例），因此：
    - 已有连接时拒绝新连接（1008），避免 self.ws 被覆盖导致状态错乱
    - shutdown() 提供优雅停机：停重连、关连接、释放任务
    - 重连采用逐档递增退避（5→10→20→40→60 封顶，倍增接近指数）
    """

    def __init__(self, config: Settings, message_router: MessageRouter):
        self.config = config
        self.message_router = message_router
        self.ws: Optional[websockets.WebSocketServerProtocol] = None
        self._running = True
        self._server_task: Optional[asyncio.Task] = None
        self._server: Optional[websockets.Server] = None

    async def run(self):
        """启动 WebSocket 服务器（带自动重连与逐档递增退避）。"""
        while self._running:
            try:
                logger.info(f"Starting WebSocket server on {self.config.WS_HOST}:{self.config.WS_PORT}")
                self._server = await websockets.serve(
                    self._handler,
                    self.config.WS_HOST,
                    self.config.WS_PORT,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=10,
                )
                logger.info("WebSocket server started, waiting for connections...")
                self._server_task = asyncio.current_task()
                # 保持运行：被 shutdown() 置 _running=False 或任务被取消时退出
                while self._running:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                logger.info("WebSocket server task cancelled")
                break
            except Exception as e:
                logger.exception(f"WebSocket server error: {e}")
                await self._close_server()
                # 逐档递增退避（倍增封顶，接近指数退避）：5→10→20→40→60 秒
                for delay in [5, 10, 20, 40, 60]:
                    if not self._running:
                        break
                    logger.info(f"Reconnecting in {delay}s...")
                    await asyncio.sleep(delay)
                    if not self._running:
                        break
        await self._close_server()

    async def shutdown(self) -> None:
        """优雅停机：停止重连循环、关闭当前 NapCat 连接并释放服务。"""
        self._running = False
        if self.ws is not None:
            try:
                await self.ws.close(code=1000, reason="shutdown")
                await self.ws.wait_closed()
            except Exception as e:
                logger.debug(f"关闭连接异常: {e}")
            self.ws = None
            self.message_router.global_state.ws_connected = False
        if self._server_task is not None and self._server_task is not asyncio.current_task():
            self._server_task.cancel()
            try:
                await self._server_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_server()
        logger.info("WebSocket server 已优雅关闭")

    async def _close_server(self) -> None:
        if self._server is not None:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception as e:
                logger.debug(f"关闭 server 异常: {e}")
            self._server = None

    async def _handler(self, ws: websockets.WebSocketServerProtocol):
        # 可选鉴权（WS_TOKEN）：配置后，NapCat 握手需携带
        # Authorization: Bearer <token> 头，或 URL 带 ?access_token=<token>
        # （OneBot11 规范约定；默认空=不鉴权，保持向后兼容）
        token = getattr(self.config, "WS_TOKEN", "") or ""
        if token:
            auth_ok = False
            try:
                if ws.request is not None:
                    auth_header = ws.request.headers.get("Authorization", "")
                    auth_ok = auth_header == f"Bearer {token}"
            except Exception:
                auth_ok = False
            if not auth_ok:
                try:
                    from urllib.parse import urlparse, parse_qs
                    query = parse_qs(urlparse(ws.path).query)
                    auth_ok = (query.get("access_token") or [""])[0] == token
                except Exception:
                    auth_ok = False
            if not auth_ok:
                logger.warning("WS 鉴权失败，拒绝连接")
                try:
                    await ws.close(code=1008, reason="unauthorized")
                except Exception:
                    pass
                return

        # 单连接守卫：已有连接时拒绝新的 NapCat 连接，防止 self.ws 被覆盖
        if self.ws is not None:
            logger.warning("仅允许单连接，拒绝新的 NapCat 连接")
            try:
                await ws.close(code=1008, reason="仅允许单连接")
            except Exception:
                pass
            return
        logger.info("NapCat WebSocket connected")
        self.ws = ws
        self.message_router.global_state.ws_connected = True
        try:
            async for message in ws:
                logger.debug(f"WS raw: {message[:200]}")
                try:
                    if isinstance(message, bytes):
                        message = message.decode('utf-8')
                    if isinstance(message, str):
                        data = json.loads(message)
                    else:
                        data = message
                    # 并发上限 + 单条超时：防止一条慢消息卡死整个群 / 突发消息打爆 API
                    async with self.message_router.process_semaphore:
                        await asyncio.wait_for(
                            self.message_router.process_event(data),
                            timeout=self.config.EVENT_PROCESS_TIMEOUT,
                        )
                except asyncio.TimeoutError:
                    logger.error(f"Event processing timeout (>={self.config.EVENT_PROCESS_TIMEOUT}s), skipped: {str(message)[:100]}")
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error: {e}")
                except Exception as e:
                    logger.exception(f"Event processing error: {e}")
        except websockets.ConnectionClosed:
            logger.warning("NapCat WebSocket disconnected")
        finally:
            # 只有当前连接自己断开才清状态（避免把新连接的状态误清）
            if self.ws is ws:
                self.ws = None
                self.message_router.global_state.ws_connected = False

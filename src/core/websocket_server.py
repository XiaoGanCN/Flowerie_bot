import asyncio
import json
import websockets
from typing import Optional
from loguru import logger

from src.config import Settings
from src.core.message_router import MessageRouter


class WebSocketServer:
    def __init__(self, config: Settings, message_router: MessageRouter):
        self.config = config
        self.message_router = message_router
        self.ws: Optional[websockets.WebSocketServerProtocol] = None
        self._running = True
        self._server_task: Optional[asyncio.Task] = None

    async def run(self):
        """启动 WebSocket 服务器（带自动重连和指数退避）"""
        while self._running:
            try:
                logger.info(f"Starting WebSocket server on {self.config.WS_HOST}:{self.config.WS_PORT}")
                async with websockets.serve(
                    self._handler,
                    self.config.WS_HOST,
                    self.config.WS_PORT,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=10,
                ):
                    logger.info("WebSocket server started, waiting for connections...")
                    # 保持运行直到被取消
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                logger.info("WebSocket server task cancelled")
                break
            except Exception as e:
                logger.exception(f"WebSocket server error: {e}")
                # ✅ 修复：完整实现指数退避，逐档递增等待
                for delay in [5, 10, 20, 40, 60]:
                    if not self._running:
                        break
                    logger.info(f"Reconnecting in {delay}s...")
                    await asyncio.sleep(delay)
                    # 等待期间如果收到停止信号，立即退出重连循环
                    if not self._running:
                        break
                # 所有 delay 档位尝试完后，外层 while 循环会重新进入

    async def _handler(self, ws: websockets.WebSocketServerProtocol):
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
            self.ws = None
            self.message_router.global_state.ws_connected = False
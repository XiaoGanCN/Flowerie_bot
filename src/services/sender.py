import asyncio

import aiohttp

from src.config import Settings
from src.utils.logging_setup import get_logger
from src.utils.metrics import registry

logger = get_logger(__name__)

_M_SEND_FAIL = registry.counter("message_send_failure_total", "消息发送失败次数（按目标类型）", ["target"])


class Sender:
    def __init__(self, config: Settings):
        self.config = config
        self.session: aiohttp.ClientSession = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def close(self):
        if self.session:
            await self.session.close()

    async def send_group_message_with_image(self, group_id: int, text: str, image_path: str,
                                            retries: int = 2) -> bool:
        """发送文字 + 本地图片（段数组消息，OneBot11 image 段用 file:// 绝对路径）。"""
        if not image_path:
            return False
        segments = []
        if text and text.strip():
            segments.append({"type": "text", "data": {"text": text[: self.config.MAX_REPLY_LENGTH]}})
        segments.append({"type": "image", "data": {"file": f"file://{image_path}"}})
        url = f"{self.config.HTTP_API_BASE}/send_group_msg"
        payload = {"group_id": group_id, "message": segments}
        logger.info("message_send_started group=%s image=%s", group_id, image_path,
                    extra={"event": "message_send_started"})
        for attempt in range(max(1, retries + 1)):
            try:
                async with self.session.post(url, json=payload, headers=self._headers(),
                                             timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.error("message_send_failed group=%s http=%s", group_id, resp.status,
                                     extra={"event": "message_send_failed"})
                    else:
                        data = await resp.json()
                        if data.get("retcode") == 0:
                            logger.info("message_send_finished group=%s image=%s", group_id, image_path,
                                        extra={"event": "message_send_finished"})
                            return True
                        logger.error("message_send_failed group=%s retcode=%s", group_id, data.get("retcode"),
                                     extra={"event": "message_send_failed"})
            except Exception as e:
                logger.error("message_send_failed group=%s err=%s", group_id, e,
                             extra={"event": "message_send_failed"})
            _M_SEND_FAIL.inc({"target": "group"})
            if attempt < retries:
                await asyncio.sleep(2)
        return False

    async def send_group_message(self, group_id: int, message: str, retries: int = 2) -> bool:
        if not message:
            return False
        if len(message) > self.config.MAX_REPLY_LENGTH:
            message = message[:self.config.MAX_REPLY_LENGTH] + "..."
        url = f"{self.config.HTTP_API_BASE}/send_group_msg"
        payload = {"group_id": group_id, "message": message}
        logger.info("message_send_started group=%s", group_id, extra={"event": "message_send_started"})
        for attempt in range(max(1, retries + 1)):
            try:
                async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.error("message_send_failed group=%s http=%s", group_id, resp.status,
                                     extra={"event": "message_send_failed"})
                    else:
                        data = await resp.json()
                        if data.get("retcode") == 0:
                            logger.info("message_send_finished group=%s", group_id,
                                        extra={"event": "message_send_finished"})
                            return True
                        else:
                            logger.error("message_send_failed group=%s retcode=%s", group_id, data.get("retcode"),
                                         extra={"event": "message_send_failed"})
            except Exception as e:
                logger.error("message_send_failed group=%s err=%s", group_id, e,
                             extra={"event": "message_send_failed"})
            _M_SEND_FAIL.inc({"target": "group"})
            if attempt < retries:
                logger.info("Send retry in 2s... (%s/%s)", attempt + 1, retries)
                await asyncio.sleep(2)
        return False

    async def send_private_message(self, user_id: int, message: str) -> bool:
        if not message:
            return False
        url = f"{self.config.HTTP_API_BASE}/send_private_msg"
        payload = {"user_id": user_id, "message": message}
        try:
            async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _M_SEND_FAIL.inc({"target": "private"})
                    return False
                data = await resp.json()
                ok = data.get("retcode") == 0
                if not ok:
                    _M_SEND_FAIL.inc({"target": "private"})
                return ok
        except Exception:
            _M_SEND_FAIL.inc({"target": "private"})
            return False

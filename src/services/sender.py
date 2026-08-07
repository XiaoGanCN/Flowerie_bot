import aiohttp
from loguru import logger
from src.config import Settings

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

    async def send_group_message(self, group_id: int, message: str) -> bool:
        if not message:
            return False
        if len(message) > self.config.MAX_REPLY_LENGTH:
            message = message[:self.config.MAX_REPLY_LENGTH] + "..."
        url = f"{self.config.HTTP_API_BASE}/send_group_msg"
        payload = {"group_id": group_id, "message": message}
        logger.info(f"Send to group {group_id}: {message[:30]}...")
        try:
            async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Send failed HTTP {resp.status}: {text}")
                    return False
                data = await resp.json()
                if data.get("retcode") == 0:
                    logger.info("Send success")
                    return True
                else:
                    logger.error(f"Send failed retcode {data.get('retcode')}: {data}")
                    return False
        except Exception as e:
            logger.error(f"Send exception: {e}")
            return False

    async def send_private_message(self, user_id: int, message: str) -> bool:
        if not message:
            return False
        url = f"{self.config.HTTP_API_BASE}/send_private_msg"
        payload = {"user_id": user_id, "message": message}
        try:
            async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                return data.get("retcode") == 0
        except Exception:
            return False
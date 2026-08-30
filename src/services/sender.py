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

    async def send_msg_raw(self, target: str, target_id: int, message,
                           reply_id=None, retries: int = 2) -> dict:
        """通用发送（OneBot11 段数组 / CQ 码字符串；回复自动加 reply 段）。

        - target: group / private
        - message: str（含 [CQ:...] 由 NapCat 解析）或 list（OneBot 段数组，
          如 [{"type":"image","data":{"file": ...}}]）——插件由此获得图片/视频/语音/文件/at 等能力
        - reply_id: 若提供，自动在最前插入 reply 段（引用回复）
        返回 {"ok": bool, "message_id": int|None}（message_id 供 delete_message 撤回）。
        """
        if not message:
            return {"ok": False, "message_id": None}
        if isinstance(message, str):
            message = message[:4000]
        segments = []
        if reply_id is not None:
            segments.append({"type": "reply", "data": {"id": int(reply_id)}})
        if isinstance(message, list):
            segments.extend(message[:40])
        else:
            segments.append({"type": "text", "data": {"text": str(message)}})
        url = f"{self.config.HTTP_API_BASE}/send_group_msg" if target == "group" \
            else f"{self.config.HTTP_API_BASE}/send_private_msg"
        payload = {"group_id": target_id, "message": segments} if target == "group" \
            else {"user_id": target_id, "message": segments}
        for attempt in range(max(1, retries + 1)):
            try:
                async with self.session.post(url, json=payload,
                                             timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.error("message_send_failed target=%s http=%s", target, resp.status,
                                     extra={"event": "message_send_failed"})
                    else:
                        data = await resp.json()
                        if data.get("retcode") == 0:
                            mid = data.get("data", {}).get("message_id")
                            logger.info("message_send_finished target=%s id=%s", target, mid,
                                        extra={"event": "message_send_finished"})
                            return {"ok": True, "message_id": mid}
                        logger.error("message_send_failed target=%s retcode=%s", target, data.get("retcode"),
                                     extra={"event": "message_send_failed"})
            except Exception as e:
                logger.error("message_send_failed target=%s err=%s", target, e,
                             extra={"event": "message_send_failed"})
            if attempt < retries:
                await asyncio.sleep(2)
        return {"ok": False, "message_id": None}

    async def delete_msg(self, message_id: int) -> bool:
        """撤回消息（OneBot11 /delete_msg）。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/delete_msg",
                    json={"message_id": int(message_id)},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ok = resp.status == 200
                if not ok:
                    logger.error("message_delete_failed id=%s http=%s", message_id, resp.status,
                                 extra={"event": "message_delete_failed"})
                return ok
        except Exception as e:
            logger.error("message_delete_failed id=%s err=%s", message_id, e,
                         extra={"event": "message_delete_failed"})
            return False

    async def get_msg(self, message_id: int) -> dict:
        """消息详情（OneBot11 /get_msg）。裁剪为最小字段：text/user/time/segments 摘要。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/get_msg",
                    json={"message_id": int(message_id)},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                d = data.get("data", {}) if data.get("retcode") == 0 else {}
                return {"ok": bool(d), "message_id": int(message_id),
                        "user_id": d.get("user_id"), "time": d.get("time"),
                        "text": str(d.get("raw_message") or "")[:2000]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_group_msg_history(self, group_id: int, count: int = 15) -> dict:
        """群最近消息（NapCat 扩展 /get_group_msg_history）。裁剪为最小字段，最多 count 条。"""
        count = max(1, min(int(count or 15), 20))
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/get_group_msg_history",
                    json={"group_id": int(group_id), "count": count},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                items = data.get("data", {}).get("messages") if data.get("retcode") == 0 else []
                if not isinstance(items, list):
                    return {"ok": False, "error": "服务端不支持 get_group_msg_history"}
                out = [{"message_id": m.get("message_id"), "user_id": m.get("user_id"),
                        "time": m.get("time"),
                        "text": str(m.get("raw_message") or "")[:2000]} for m in items]
                return {"ok": True, "messages": out[:count]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_group_member_info(self, group_id: int, user_id: int) -> dict:
        """群成员详情（OneBot11 /get_group_member_info），裁剪为角色/名片/昵称/QQ。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/get_group_member_info",
                    json={"group_id": int(group_id), "user_id": int(user_id)},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                d = data.get("data", {}) if data.get("retcode") == 0 else {}
                if not d:
                    return {"ok": False, "error": "成员信息不可用"}
                return {"ok": True, "group_id": int(group_id), "user_id": int(user_id),
                        "role": d.get("role", "member"),            # owner/admin/member
                        "card": str(d.get("card") or "")[:50],
                        "nickname": str(d.get("nickname") or "")[:50],
                        "join_time": d.get("join_time"), "last_sent_time": d.get("last_sent_time"),
                        "title": str(d.get("title") or "")[:30]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def get_group_member_list(self, group_id: int) -> dict:
        """群成员列表（OneBot11 /get_group_member_list），裁剪为最小字段。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/get_group_member_list",
                    json={"group_id": int(group_id)},
                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                items = data.get("data", []) if data.get("retcode") == 0 else []
                if not isinstance(items, list) or not items:
                    return {"ok": False, "error": "成员列表不可用（检查权限/平台支持）"}
                out = [{"user_id": m.get("user_id"), "role": m.get("role", "member"),
                        "card": str(m.get("card") or "")[:50], "nickname": str(m.get("nickname") or "")[:50]}
                       for m in items]
                return {"ok": True, "group_id": int(group_id), "members": out}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def set_group_ban(self, group_id: int, user_id: int, duration_seconds: int) -> bool:
        """群禁言（OneBot11 /set_group_ban；duration=0 解除）。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/set_group_ban",
                    json={"group_id": int(group_id), "user_id": int(user_id),
                          "duration": max(0, int(duration_seconds))},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def set_group_kick(self, group_id: int, user_id: int, reject_add: bool = False) -> bool:
        """移出群成员（OneBot11 /set_group_kick）。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/set_group_kick",
                    json={"group_id": int(group_id), "user_id": int(user_id), "reject_add_request": bool(reject_add)},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def set_group_admin(self, group_id: int, user_id: int, enable: bool) -> bool:
        """设为/取消管理员（OneBot11 /set_group_admin）。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/set_group_admin",
                    json={"group_id": int(group_id), "user_id": int(user_id), "enable": bool(enable)},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def set_friend_add_request(self, flag: str, approve: bool, remark: str = "") -> bool:
        """处理好友请求（OneBot11 /set_friend_add_request）。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/set_friend_add_request",
                    json={"flag": str(flag), "approve": bool(approve), "remark": str(remark)[:30]},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def set_group_add_request(self, flag: str, approve: bool, reason: str = "") -> bool:
        """处理加群请求（OneBot11 /set_group_add_request）。"""
        try:
            async with self.session.post(
                    f"{self.config.HTTP_API_BASE}/set_group_add_request",
                    json={"flag": str(flag), "approve": bool(approve),
                          "reason": str(reason)[:30]},
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except Exception:
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

import os
import time
from typing import Dict, Any, List, Tuple
from datetime import datetime
from loguru import logger

from src.config import Settings
from src.models import GlobalState
from src.services.ai_client import AIClient
from src.services.file_parser import FileParser


class MessageAssembler:
    """消息组装：把原始 message_array 拼成 AI 能看懂的完整文本。

    负责：文本提取、图片识图、回复/@ 扫描、转发解析（含转发内图片）、
    卡片解析、待解析文件配对、消息存档。Router 只负责流程决策。
    注意：辅助方法返回"要追加的文本块"（字符串不可变，不能原地改外层变量）。
    """

    def __init__(self, config: Settings, ai_client: AIClient, file_parser: FileParser, global_state: GlobalState):
        self.config = config
        self.ai_client = ai_client
        self.file_parser = file_parser
        self.global_state = global_state

    async def assemble(
        self,
        message_array: List[Dict],
        user_id: int,
        group_id: int,
        raw_time: int,
    ) -> Tuple[str, List[str], bool, bool, bool]:
        """组装消息，返回 (full_text, 顶层图片描述列表, is_reply_to_bot, has_reply_to_other, has_at_others)。"""
        # 提取纯文本与是否@机器人
        clean_text, is_mentioned = self.file_parser.extract_mention_and_text(message_array, self.config.BOT_QQ)
        full_text = clean_text

        # 顶层图片/表情包识图（OneBot11 image 段带 url）
        image_descriptions = await self._describe_images(message_array)
        if image_descriptions:
            full_text += f"\n[用户发送了一张图片，内容如下：]\n{'; '.join(image_descriptions)}\n[图片内容结束]"
            logger.debug(f"Image descriptions: {image_descriptions}")

        # 扫描回复与@
        is_reply_to_bot, has_reply_to_other, has_at_others = self._scan_reply_and_at(message_array)

        # 合并转发（含转发内图片，由 VISION_FORWARD_IMAGES 控制）
        full_text += await self._assemble_forward(message_array)

        # JSON 卡片
        full_text += self._assemble_card(message_array)

        # 待解析文件配对（NapCat 先发上传通知，消息过来后再取内容）
        full_text += await self._assemble_pending_file(user_id, group_id)

        # 存档
        self._archive(group_id, user_id, full_text, raw_time)
        return full_text, image_descriptions, is_reply_to_bot, has_reply_to_other, has_at_others

    # ---------- 顶层图片识图 ----------
    async def _describe_images(self, message_array: List[Dict]) -> List[str]:
        descriptions = []
        for seg in message_array:
            if seg.get("type") == "image":
                seg_data = seg.get("data") or {}
                url = seg_data.get("url", "")
                if url:
                    desc = await self.ai_client.describe_image(url)
                    if desc:
                        descriptions.append(desc)
                    else:
                        logger.warning(f"Vision describe failed for image url: {url[:80]}")
        return descriptions

    # ---------- 回复/@ 扫描 ----------
    def _scan_reply_and_at(self, message_array: List[Dict]) -> Tuple[bool, bool, bool]:
        is_reply_to_bot = False
        has_reply_to_other = False
        has_at_others = False
        for seg in message_array:
            if seg.get("type") == "reply":
                reply_data = seg.get("data", {})
                replied_qq = str(reply_data.get("qq", ""))
                if replied_qq == str(self.config.BOT_QQ):
                    is_reply_to_bot = True
                else:
                    has_reply_to_other = True
            elif seg.get("type") == "at":
                qq = str(seg.get("data", {}).get("qq", ""))
                if qq != str(self.config.BOT_QQ):
                    has_at_others = True
        return is_reply_to_bot, has_reply_to_other, has_at_others

    # ---------- 合并转发 ----------
    async def _assemble_forward(self, message_array: List[Dict]) -> str:
        forward_text, forward_image_urls, has_forward = await self.file_parser.extract_forward_messages(message_array)
        if not has_forward:
            return ""
        block = ""
        if forward_text:
            block += f"\n[用户转发了多条消息，内容如下：]\n{forward_text}\n[转发内容结束]"
        # 转发里的图片：由 VISION_FORWARD_IMAGES 开关控制（默认关，省视觉 token）
        if forward_image_urls and self.config.VISION_FORWARD_IMAGES:
            forward_image_descriptions = []
            for fwd_url in forward_image_urls:
                fwd_desc = await self.ai_client.describe_image(fwd_url)
                if fwd_desc:
                    forward_image_descriptions.append(fwd_desc)
                else:
                    logger.warning(f"Vision describe failed for forward image url: {fwd_url[:80]}")
            if forward_image_descriptions:
                block += f"\n[用户转发的消息中包含图片，内容如下：]\n{'; '.join(forward_image_descriptions)}\n[图片内容结束]"
                logger.debug(f"Forward image descriptions: {forward_image_descriptions}")
        return block

    # ---------- JSON 卡片 ----------
    def _assemble_card(self, message_array: List[Dict]) -> str:
        card_text, has_card = self.file_parser.extract_json_card_content(message_array)
        if has_card and card_text:
            return f"\n[用户分享了一个卡片，内容如下：]\n{card_text}\n[卡片内容结束]"
        return ""

    # ---------- 待解析文件 ----------
    async def _assemble_pending_file(self, user_id: int, group_id: int) -> str:
        pending_key = f"{user_id}_{group_id}"
        if pending_key not in self.global_state.pending_files:
            return ""
        file_info = self.global_state.pending_files.pop(pending_key)
        file_id = file_info.get("file_id")
        file_name = file_info.get("file_name", "未命名文件")
        file_size = file_info.get("file_size", 0)

        if file_id and file_size <= 1 * 1024 * 1024:
            file_content, success = await self.file_parser.fetch_and_parse_file(file_id, file_name)
            if success and file_content:
                logger.debug(f"File parsed: {file_name} ({len(file_content)} chars)")
                return f"\n[用户上传了一个文件，内容如下：]\n{file_content}\n[文件内容结束]"
            logger.warning(f"Failed to parse file: {file_name}")
        elif file_size > 1 * 1024 * 1024:
            logger.warning(f"File too large, skipped: {file_name} ({file_size} bytes)")
        else:
            logger.debug(f"No file_id for pending file: {file_name}")
        return ""

    # ---------- 存档 ----------
    def _archive(self, group_id: int, user_id: int, text: str, raw_time: int) -> None:
        if not text:
            return
        try:
            base = self.config.ARCHIVE_BASE_DIR
            if not os.path.exists(base):
                os.makedirs(base, exist_ok=True)
            group_dir = os.path.join(base, str(group_id))
            if not os.path.exists(group_dir):
                os.makedirs(group_dir, exist_ok=True)
            filename = os.path.join(group_dir, f"{datetime.now().strftime('%Y-%m-%d')}.txt")
            time_str = datetime.fromtimestamp(raw_time).strftime("%H:%M:%S")
            line = f"[{time_str}] 用户{user_id}：{text}\n"
            with open(filename, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"Archive error: {e}")

"""StickerManager：表情包目录扫描、Vision 索引、模型选择辅助、发送防刷屏。

核心机制：
- SHA-256(file bytes) 作为图片身份：hash 相同 → 复用缓存描述（重启不重复 Vision）；
  文件被替换（同名不同 hash）→ 重新 Vision
- Vision 失败：记录 failed 状态，不阻塞启动；超过 24h 允许自动重试，绝不每次消息重试
- 选择：聊天时把"可用表情包描述列表"（文字）交给模型，模型通过回复标记
  [STICKER:<filename>] 选择；只传描述，不传图片本体
"""
import hashlib
import os
import re
from typing import Optional

from src.config import Settings
from src.repositories.sticker_repository import StickerRepository
from src.services.ai_client import AIClient
from src.utils.expiring_map import ExpiringMap
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# 图片扩展名白名单
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
# 模型回复中的表情包选择标记：[STICKER:filename]
_STICKER_PATTERN = re.compile(r"\[STICKER:([^\]]+?)\]")
_FAILED_RETRY_AFTER = 86400  # 失败状态超过 24h 允许重新 Vision


class StickerManager:
    def __init__(self, config: Settings, repository: StickerRepository, ai_client: AIClient):
        self.config = config
        self.repository = repository
        self.ai_client = ai_client
        # 每群表情包冷却（ExpiringMap 自治 TTL）
        self._cooldown = ExpiringMap(ttl_seconds=max(1, config.STICKER_COOLDOWN))

    # ---------- 扫描与索引 ----------
    def is_enabled(self) -> bool:
        return bool(self.config.STICKER_ENABLED and self.config.STICKER_DIR)

    async def scan_and_index(self) -> int:
        """扫描 STICKER_DIR：新图片 Vision 索引；hash 缓存复用；失败记录待重试。

        返回本次新索引的数量。任何单文件失败不影响整体。
        """
        if not self.is_enabled():
            return 0
        base = self.config.STICKER_DIR
        if not os.path.isdir(base):
            logger.warning("STICKER_DIR 不存在: %s", base)
            return 0
        indexed = 0
        seen_paths = set()
        for filename in sorted(os.listdir(base)):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in _IMAGE_EXTS:
                continue
            path = os.path.join(base, filename)
            seen_paths.add(path)
            try:
                indexed += await self._index_file(path, filename)
            except Exception as e:  # noqa: BLE001 - 单文件失败不阻塞扫描
                logger.error("sticker_analysis_failed file=%s err=%s", filename, e,
                             extra={"event": "sticker_analysis_failed"})
        # 清理已被删除文件的索引
        for row in self.repository.list_ok(limit=10000):
            if row["file_path"] not in seen_paths and not os.path.exists(row["file_path"]):
                self.repository.delete(row["file_path"])
        if indexed:
            logger.info("sticker_indexed count=%d", indexed, extra={"event": "sticker_indexed"})
        return indexed

    async def _index_file(self, path: str, filename: str) -> int:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return 0
        file_hash = hashlib.sha256(data).hexdigest()
        existing = self.repository.get_by_hash(file_hash)
        if existing and existing["status"] == "ok":
            return 0  # 已索引，重启不重复 Vision
        if existing and existing["status"] == "failed":
            # 失败状态：超过 24h 才允许重试，否则跳过
            import time
            if time.time() - existing["updated_at"] < _FAILED_RETRY_AFTER:
                return 0
        description = await self.ai_client.describe_image_file(path)
        if not description:
            self.repository.upsert(file_hash, filename, path, status="failed", error="vision_failed")
            logger.warning("sticker_analysis_failed file=%s", filename,
                           extra={"event": "sticker_analysis_failed"})
            return 0
        self.repository.upsert(file_hash, filename, path, description=description.strip(),
                               status="ok")
        logger.info("sticker_indexed file=%s desc=%s", filename, description[:40],
                    extra={"event": "sticker_indexed"})
        return 1

    # ---------- 模型选择辅助 ----------
    def build_sticker_context(self) -> str:
        """构造提供给模型的可用表情包文字描述（不传图片本体）。"""
        if not self.is_enabled():
            return ""
        stickers = self.repository.list_ok(limit=max(1, self.config.STICKER_MAX_LIST))
        if not stickers:
            return ""
        lines = ["可用表情包（如语境合适，可在回复中附加 [STICKER:文件名] 选择，最多一张）："]
        for s in stickers:
            lines.append(f"[{s['filename']}] {s['description'][:50]}")
        return "\n".join(lines)

    # ---------- 回复解析与发送 ----------
    def extract_sticker(self, reply: str) -> Optional[str]:
        """从模型回复中提取 [STICKER:filename] 标记，返回文件路径；无则 None。"""
        if not self.is_enabled():
            return None
        m = _STICKER_PATTERN.search(reply)
        if not m:
            return None
        filename = m.group(1).strip()
        base = self.config.STICKER_DIR
        path = os.path.join(base, filename)
        # 路径安全：只允许目录内文件
        if os.path.dirname(path) != base or not os.path.isfile(path):
            logger.warning("sticker path rejected: %s", filename)
            return None
        return path

    def strip_sticker_marker(self, reply: str) -> str:
        return re.sub(r"\s+", " ", _STICKER_PATTERN.sub("", reply)).strip()

    def can_send(self, group_id: int) -> bool:
        """每群冷却检查（防刷屏）。"""
        return self._cooldown.get(group_id) is None

    def mark_sent(self, group_id: int) -> None:
        self._cooldown.set(group_id, 1.0)

    def close(self) -> None:
        self.repository.close()

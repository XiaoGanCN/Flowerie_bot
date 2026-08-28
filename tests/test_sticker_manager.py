"""Sticker + Vision 索引测试（第四轮）：扫描 / hash 去重 / 缓存复用 / 失败重试 / 选择 / 冷却。"""
import os
import tempfile
import time

import pytest

from src.repositories.sticker_repository import StickerRepository
from src.services.sticker_manager import StickerManager
from src.utils.expiring_map import ExpiringMap
from tests.test_router_regression import make_config


def _png_bytes(seed: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + bytes([seed % 256]) * 64


class FakeVisionAI:
    """fake ai_client：记录 describe_image_file 调用，可配置失败。"""

    def __init__(self):
        self.calls = []
        self.fail = False

    async def describe_image_file(self, path):
        self.calls.append(path)
        if self.fail:
            return None
        return f"花璃开心地比耶 {os.path.basename(path)}"


@pytest.fixture()
def env():
    tmp = tempfile.TemporaryDirectory()
    sticker_dir = os.path.join(tmp.name, "stickers")
    os.makedirs(sticker_dir)
    # 3 张假图片
    for i in range(3):
        with open(os.path.join(sticker_dir, f"{i:03d}.png"), "wb") as f:
            f.write(_png_bytes(i))
    config = make_config(
        STICKER_DIR=sticker_dir,
        STICKER_ENABLED=True,
        STICKER_COOLDOWN=60,
        STICKER_MAX_LIST=30,
    )
    repo = StickerRepository(os.path.join(tmp.name, "stickers.db"))
    vision = FakeVisionAI()
    mgr = StickerManager(config, repo, vision)
    yield tmp, sticker_dir, config, repo, vision, mgr
    mgr.close()
    tmp.cleanup()


async def test_scan_indexes_new_files(env):
    """首次扫描：3 张新图全部 Vision 索引。"""
    tmp, d, config, repo, vision, mgr = env
    n = await mgr.scan_and_index()
    assert n == 3
    assert repo.count() == 3
    assert len(vision.calls) == 3
    ok = repo.list_ok()
    assert len(ok) == 3
    assert all("比耶" in s["description"] for s in ok)


async def test_restart_reuses_cache(env):
    """重启（新 manager 同库）：hash 已存在，不重复调用 Vision。"""
    tmp, d, config, repo, vision, mgr = env
    await mgr.scan_and_index()
    # 模拟重启
    mgr2 = StickerManager(config, StickerRepository(os.path.join(tmp.name, "stickers.db")), vision)
    n = await mgr2.scan_and_index()
    assert n == 0  # 全部命中缓存
    assert len(vision.calls) == 3  # 没有新增调用
    mgr2.close()


async def test_changed_file_reanalyzed(env):
    """同名文件内容被替换（hash 不同）→ 重新 Vision。"""
    tmp, d, config, repo, vision, mgr = env
    await mgr.scan_and_index()
    # 替换 000.png 内容
    with open(os.path.join(d, "000.png"), "wb") as f:
        f.write(_png_bytes(99))
    n = await mgr.scan_and_index()
    assert n == 1  # 只有替换的重新分析
    assert len(vision.calls) == 4


async def test_vision_failure_does_not_block(env):
    """Vision 失败：记录 failed 状态，不阻塞扫描，不每次重试。"""
    tmp, d, config, repo, vision, mgr = env
    vision.fail = True
    n = await mgr.scan_and_index()
    assert n == 0
    assert repo.count() == 3
    assert len(repo.list_ok()) == 0
    # 立即重扫：failed 未超 24h，不重试
    await mgr.scan_and_index()
    assert len(vision.calls) == 3  # 没有新增调用
    # 超 24h 后重扫：允许 retry
    row = repo.list_failed(older_than=0)  # 直接取全部 failed
    assert len(row) == 3
    vision.fail = False
    # 手动把 updated_at 改旧，模拟超时
    import sqlite3
    conn = sqlite3.connect(os.path.join(tmp.name, "stickers.db"))
    conn.execute("UPDATE sticker_index SET updated_at=?", (time.time() - 90000,))
    conn.commit()
    conn.close()
    n = await mgr.scan_and_index()
    assert n == 3
    assert len(repo.list_ok()) == 3


async def test_sticker_context_text_only(env):
    """提供给模型的是文字描述，不传图片本体。"""
    tmp, d, config, repo, vision, mgr = env
    await mgr.scan_and_index()
    ctx = mgr.build_sticker_context()
    assert "可用表情包" in ctx
    assert "000.png" in ctx and "比耶" in ctx
    # 不包含文件路径/二进制内容
    assert d not in ctx


def test_extract_sticker_and_path_safety(env):
    tmp, d, config, repo, vision, mgr = env
    # 合法选择
    path = mgr.extract_sticker("哈哈 [STICKER:000.png] 笑死")
    assert path == os.path.join(d, "000.png")
    # 路径穿越拒绝
    assert mgr.extract_sticker("[STICKER:../evil.png]") is None
    assert mgr.extract_sticker("[STICKER:/etc/passwd]") is None
    # 不存在的文件
    assert mgr.extract_sticker("[STICKER:nope.png]") is None
    # 无标记
    assert mgr.extract_sticker("普通回复") is None
    # strip
    assert mgr.strip_sticker_marker("哈哈 [STICKER:000.png] 笑死") == "哈哈 笑死"


def test_sticker_cooldown(env):
    tmp, d, config, repo, vision, mgr = env
    assert mgr.can_send(123) is True
    mgr.mark_sent(123)
    assert mgr.can_send(123) is False  # 冷却中
    assert mgr.can_send(456) is True   # 其他群不受影响


async def test_router_sends_sticker(env):
    """路由集成：模型回复含 [STICKER:] → 发送图片；无标记 → 正常文本。"""
    from tests.test_router_regression import FakeAIClient, FakeFileParser, FakeSender, build_router

    tmp, d, config, repo, vision, mgr = env
    await mgr.scan_and_index()
    router, config, ai, sender, mm = build_router(config)
    router.sticker_manager = mgr

    class FP(FakeFileParser):
        def extract_mention_and_text(self, message_array, bot_qq):
            return "发个表情", True  # 识别 @

    router.file_parser = FP()
    router.policy_engine.should_reply_by_context = lambda gid: False

    class StickerAI(FakeAIClient):
        async def chat_once(self, **kwargs):
            self.calls += 1
            return "哈哈笑死 [STICKER:000.png]", None

    router.ai_client = StickerAI()

    class ImgSender(FakeSender):
        def __init__(self):
            super().__init__()
            self.images = []

        async def send_group_message_with_image(self, group_id, text, image_path):
            self.images.append((group_id, text, image_path))
            return True

    img_sender = ImgSender()
    router.sender = img_sender

    event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123,
        "user_id": 456,
        "message_id": 9001,
        "time": 1700000000,
        "message": [
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": "发个表情"}},
        ],
    }
    await router.process_event(event)
    assert len(img_sender.images) == 1
    gid, text, path = img_sender.images[0]
    assert text == "哈哈笑死"  # 标记已剥离
    assert path == os.path.join(d, "000.png")


def test_expiring_map_cooldown_ttl():
    """冷却 TTL 自治：过期后恢复可发。"""
    class Clock:
        def __init__(self):
            self.now = 1000.0

        def __call__(self):
            return self.now

    clock = Clock()
    m = ExpiringMap(ttl_seconds=60, clock=clock)
    m.set(1, 1.0)
    assert m.get(1) is not None
    clock.now = 1061
    assert m.get(1) is None  # 过期自动失效

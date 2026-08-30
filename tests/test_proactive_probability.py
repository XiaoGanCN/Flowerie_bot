"""主动发言概率配置化测试：默认值保持原行为、热更新读取、非法配置拒绝、min<=max。"""
import random

import pytest

from src.config import validate_config
from src.core.active_chat_manager import ActiveChatManager
from src.core.context_manager import ContextManager
from src.models import GlobalState
from src.services.config_service import ConfigService
from tests.test_config_service import FakeSettings


class _Cfg(FakeSettings):
    """FakeSettings + 主动发言概率字段（默认值 = 原硬编码）。"""

    PROACTIVE_MESSAGE_MIN_PROBABILITY = 0.01
    PROACTIVE_MESSAGE_MAX_PROBABILITY = 0.05
    PROACTIVE_MESSAGE_BASE_PROBABILITY = 0.03
    PROACTIVE_MESSAGE_USER_BOOST = 0.01
    PROACTIVE_MESSAGE_SINGLE_USER_PROBABILITY = 0.02
    PROACTIVE_MESSAGE_SHORT_MESSAGE_PROBABILITY = 0.02
    PROACTIVE_MESSAGE_EMPTY_CONTEXT_PROBABILITY = 0.02
    PROACTIVE_MESSAGE_BOT_MULTIPLIER = 0.3
    ACTIVE_CHAT_PROBABILITY = 0.10
    ACTIVE_CHAT_INTERVAL_MIN_SECONDS = 5
    ACTIVE_CHAT_INTERVAL_MAX_SECONDS = 10
    ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS = 1800
    ACTIVE_CHAT_COOLDOWN = 300
    NIGHT_SILENCE_START = 0
    NIGHT_SILENCE_END = 8


def _context_cfg(monkeypatch, **over):
    cfg = _Cfg()
    for k, v in over.items():
        setattr(cfg, k, v)
    cm = ContextManager(cfg, {}, GlobalState())
    return cfg, cm


# ---------- 逻辑保持（概率可注入/用 rng 断言边界） ----------
def test_default_probability_values_preserved():
    cfg = _Cfg()
    assert cfg.PROACTIVE_MESSAGE_BASE_PROBABILITY == 0.03
    assert cfg.PROACTIVE_MESSAGE_USER_BOOST == 0.01
    assert cfg.PROACTIVE_MESSAGE_SINGLE_USER_PROBABILITY == 0.02
    assert cfg.PROACTIVE_MESSAGE_SHORT_MESSAGE_PROBABILITY == 0.02
    assert cfg.PROACTIVE_MESSAGE_EMPTY_CONTEXT_PROBABILITY == 0.02
    assert cfg.PROACTIVE_MESSAGE_BOT_MULTIPLIER == 0.3
    assert (cfg.PROACTIVE_MESSAGE_MIN_PROBABILITY, cfg.PROACTIVE_MESSAGE_MAX_PROBABILITY) == (0.01, 0.05)
    assert cfg.ACTIVE_CHAT_PROBABILITY == 0.10


def test_should_reply_by_context_uses_config(monkeypatch):
    cfg, cm = _context_cfg(monkeypatch)
    cm.add_context(1, 100, "第一条消息")
    cm.add_context(1, 101, "第二条消息")
    # 强制 roll=0：任何概率都小于 1 → False
    monkeypatch.setattr(random, "random", lambda: 0.99999)
    assert cm.should_reply_by_context(1) is False
    # 强制 roll=0 → True（基础概率 3%+boost）
    monkeypatch.setattr(random, "random", lambda: 0.0)
    assert cm.should_reply_by_context(1) is True


def test_config_changes_reflected_immediately(monkeypatch):
    """读 self.config（热更新）：修改配置后立即生效，无需重建 RNG/配置。"""
    cfg, cm = _context_cfg(monkeypatch)
    cm.add_context(1, 100, "x")
    roll = 0.5
    monkeypatch.setattr(random, "random", lambda: roll)
    # 基础 0.03 + boost 0.01 = 0.04；roll=0.5 → False
    assert cm.should_reply_by_context(1) is False
    # 提升概率到 1.0 → True
    cfg.PROACTIVE_MESSAGE_BASE_PROBABILITY = 1.0
    cfg.PROACTIVE_MESSAGE_USER_BOOST = 0.0
    cfg.PROACTIVE_MESSAGE_MIN_PROBABILITY = 1.0
    cfg.PROACTIVE_MESSAGE_MAX_PROBABILITY = 1.0
    assert cm.should_reply_by_context(1) is True


def test_empty_context_probability(monkeypatch):
    cfg, cm = _context_cfg(monkeypatch)
    monkeypatch.setattr(random, "random", lambda: 0.0199)
    assert cm.should_reply_by_context(1) is True   # 空上下文概率 0.02


def test_bot_multiplier_applied(monkeypatch):
    cfg, cm = _context_cfg(monkeypatch)
    cm.add_context(1, 0, "bot 1", is_bot=True)
    cm.add_context(1, 0, "bot 2", is_bot=True)
    cm.add_context(1, 0, "bot 3", is_bot=True)
    monkeypatch.setattr(random, "random", lambda: 0.9999)
    assert cm.should_reply_by_context(1) is False


# ---------- ActiveChatManager（只配置化，逻辑不变） ----------
def test_active_chat_probability_config(monkeypatch):
    cfg = _Cfg()
    acm = ActiveChatManager(cfg, {}, GlobalState(), cooldown=_DummyCooldown())
    monkeypatch.setattr(random, "random", lambda: 0.99)
    assert acm.should_active_chat(1) is False
    cfg.ACTIVE_CHAT_PROBABILITY = 1.0
    monkeypatch.setattr(random, "random", lambda: 0.99)
    assert acm.should_active_chat(1) is True
    # 夜间静默与冷却逻辑不变
    cfg.ACTIVE_CHAT_PROBABILITY = 1.0
    cfg.NIGHT_SILENCE_START, cfg.NIGHT_SILENCE_END = 0, 24
    assert acm.should_active_chat(1) is False


class _DummyCooldown:
    def can_bot_reply(self, group_id):
        return True


# ---------- 配置校验（启动 / Web UI 保存） ----------
def test_validate_config_rejects_invalid():
    cfg = _Cfg()
    cfg.DEEPSEEK_API_KEY = "sk-x"
    cfg.BOT_QQ = 1
    cfg.WS_PORT = 3001
    cfg.NIGHT_SILENCE_START, cfg.NIGHT_SILENCE_END = 0, 8
    cfg.PROACTIVE_MESSAGE_MIN_PROBABILITY = 0.9
    cfg.PROACTIVE_MESSAGE_MAX_PROBABILITY = 0.1  # min > max
    with pytest.raises(ValueError, match="min <= max"):
        validate_config(cfg)


def test_validate_config_rejects_nan_and_out_of_range():
    cfg = _Cfg()
    cfg.DEEPSEEK_API_KEY = "sk-x"
    cfg.BOT_QQ = 1
    cfg.WS_PORT = 3001
    cfg.PROACTIVE_MESSAGE_MAX_PROBABILITY = 1.5
    with pytest.raises(ValueError, match="0.0~1.0"):
        validate_config(cfg)
    cfg.PROACTIVE_MESSAGE_MAX_PROBABILITY = float("nan")
    with pytest.raises(ValueError, match="NaN"):
        validate_config(cfg)
    cfg.PROACTIVE_MESSAGE_MAX_PROBABILITY = float("inf")
    with pytest.raises(ValueError, match="NaN|Infinity"):
        validate_config(cfg)


# ---------- Web UI 保存校验（ConfigService.update） ----------
def _svc(cfg):
    import tempfile

    from src.repositories.settings_repository import SettingsRepository
    tmp = tempfile.TemporaryDirectory()
    repo = SettingsRepository(tmp.name + "/s.db")
    return ConfigService(cfg, repo), repo, tmp


def test_config_service_rejects_invalid_probability():
    cfg = _Cfg()
    svc, _repo, tmp = _svc(cfg)
    ok, msg = svc.update("PROACTIVE_MESSAGE_MAX_PROBABILITY", "abc")
    assert not ok
    ok, msg = svc.update("PROACTIVE_MESSAGE_MAX_PROBABILITY", "-1")
    assert not ok
    ok, msg = svc.update("PROACTIVE_MESSAGE_MAX_PROBABILITY", "2")
    assert not ok
    ok, msg = svc.update("PROACTIVE_MESSAGE_MAX_PROBABILITY", "NaN")
    assert not ok
    ok, msg = svc.update("PROACTIVE_MESSAGE_MAX_PROBABILITY", "Infinity")
    assert not ok
    ok, msg = svc.update("PROACTIVE_MESSAGE_MAX_PROBABILITY", "0.05")
    assert ok
    ok, msg = svc.update("PROACTIVE_MESSAGE_MIN_PROBABILITY", "0.5")
    assert not ok and "不能大于" in msg  # min(0.5) > max(0.05) → 拒绝
    tmp.cleanup()


def test_config_service_validate_range_uses_ranges():
    cfg = _Cfg()
    svc, _repo, tmp = _svc(cfg)
    ok, msg = svc.update("PROACTIVE_MESSAGE_BASE_PROBABILITY", "0.3")
    assert ok
    ok, msg = svc.update("PROACTIVE_MESSAGE_BASE_PROBABILITY", "-0.1")
    assert not ok
    tmp.cleanup()


# ---------- 概率分布黑盒：统计频率与配置一致（±30% 容差，避免 flaky） ----------
def test_probability_distribution_matches_config(monkeypatch):
    """5000 次采样：BASE 0.03 时回复频率应在 0.021~0.039（±30%）。"""
    cfg, cm = _context_cfg(monkeypatch)
    cm.add_context(1, 100, "第一条消息")
    cm.add_context(1, 101, "第二条消息")
    hits = sum(1 for _ in range(5000) if cm.should_reply_by_context(1))
    rate = hits / 5000
    # BASE 0.03 + USER_BOOST 0.01 = 0.04（上下文含用户消息）
    assert 0.04 * 0.7 <= rate <= 0.04 * 1.3, f"频率 {rate} 偏离 0.04"


def test_probability_zero_never_replies(monkeypatch):
    cfg, cm = _context_cfg(monkeypatch)
    cfg.PROACTIVE_MESSAGE_BASE_PROBABILITY = 0.0
    cfg.PROACTIVE_MESSAGE_USER_BOOST = 0.0
    cfg.PROACTIVE_MESSAGE_SHORT_MESSAGE_PROBABILITY = 0.0
    cfg.PROACTIVE_MESSAGE_EMPTY_CONTEXT_PROBABILITY = 0.0
    cfg.PROACTIVE_MESSAGE_SINGLE_USER_PROBABILITY = 0.0
    cfg.PROACTIVE_MESSAGE_MIN_PROBABILITY = 0.0
    cfg.PROACTIVE_MESSAGE_MAX_PROBABILITY = 0.0
    cm.add_context(1, 100, "x")
    cm.add_context(1, 101, "y")
    assert all(cm.should_reply_by_context(1) is False for _ in range(200))

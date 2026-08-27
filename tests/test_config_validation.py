"""配置校验测试：启动阶段错误配置必须直接报错（不打印密钥值）。"""
from types import SimpleNamespace

import pytest

from src.config import validate_config


def _cfg(**overrides):
    base = dict(
        DEEPSEEK_API_KEY="sk-real-key-123456",
        BOT_QQ=10001,
        WS_PORT=3001,
        MAX_CONCURRENT_AI=3,
        AI_MAX_RETRIES=3,
        USER_COOLDOWN=5,
        BOT_COOLDOWN=2,
        NIGHT_SILENCE_START=0,
        NIGHT_SILENCE_END=8,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_missing_api_key_rejected():
    with pytest.raises(ValueError):
        validate_config(_cfg(DEEPSEEK_API_KEY="sk-your-key-here"))
    with pytest.raises(ValueError):
        validate_config(_cfg(DEEPSEEK_API_KEY=""))


def test_valid_config_passes():
    validate_config(_cfg())  # 不抛异常


def test_bot_qq_invalid():
    with pytest.raises(ValueError):
        validate_config(_cfg(BOT_QQ=0))


def test_ws_port_invalid():
    with pytest.raises(ValueError):
        validate_config(_cfg(WS_PORT=0))
    with pytest.raises(ValueError):
        validate_config(_cfg(WS_PORT=70000))


def test_negative_limits_rejected():
    with pytest.raises(ValueError):
        validate_config(_cfg(MAX_CONCURRENT_AI=0))
    with pytest.raises(ValueError):
        validate_config(_cfg(AI_MAX_RETRIES=-1))


def test_night_silence_invalid():
    with pytest.raises(ValueError):
        validate_config(_cfg(NIGHT_SILENCE_START=10, NIGHT_SILENCE_END=8))
    with pytest.raises(ValueError):
        validate_config(_cfg(NIGHT_SILENCE_END=25))


def test_error_message_does_not_contain_key():
    """任何校验报错信息里都不得出现 API Key 本身（敏感保护）。"""
    secret = "sk-super-secret-abc-123456"
    with pytest.raises(ValueError) as exc:
        validate_config(_cfg(DEEPSEEK_API_KEY=secret, BOT_QQ=0))
    assert secret not in str(exc.value)

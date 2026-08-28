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


# ---------- P3-3：MCP 配置校验（fail-fast，不静默降级） ----------
_MCP_OK = dict(
    MCP_ENABLED=True,
    MCP_SERVER_URL="https://mcp.example.com/mcp",
    MCP_TIMEOUT=15,
    MCP_MAX_TOOL_CALLS=5,
    MCP_ALLOWED_TOOLS="web_search, fetch_url",
)


def test_mcp_enabled_without_server_url_rejected():
    with pytest.raises(ValueError, match="MCP_SERVER_URL"):
        validate_config(_cfg(MCP_ENABLED=True, MCP_SERVER_URL=""))


def test_mcp_enabled_loopback_url_rejected():
    with pytest.raises(ValueError, match="MCP_SERVER_URL 不合法"):
        validate_config(_cfg(**{**_MCP_OK, "MCP_SERVER_URL": "http://127.0.0.1:9000/mcp"}))


def test_mcp_enabled_invalid_scheme_rejected():
    with pytest.raises(ValueError, match="MCP_SERVER_URL 不合法"):
        validate_config(_cfg(**{**_MCP_OK, "MCP_SERVER_URL": "ftp://example.com/mcp"}))


def test_mcp_timeout_invalid_rejected():
    with pytest.raises(ValueError, match="MCP_TIMEOUT"):
        validate_config(_cfg(**{**_MCP_OK, "MCP_TIMEOUT": 0}))


def test_mcp_max_tool_calls_negative_rejected():
    with pytest.raises(ValueError, match="MCP_MAX_TOOL_CALLS"):
        validate_config(_cfg(**{**_MCP_OK, "MCP_MAX_TOOL_CALLS": -1}))


def test_mcp_allowed_tools_format_rejected():
    with pytest.raises(ValueError, match="MCP_ALLOWED_TOOLS"):
        validate_config(_cfg(**{**_MCP_OK, "MCP_ALLOWED_TOOLS": "web_search, bad tool!"}))


def test_mcp_valid_config_passes():
    validate_config(_cfg(**_MCP_OK))  # 不抛异常


def test_mcp_disabled_not_blocked_by_missing_url():
    """MCP_ENABLED=false 时，缺少 SERVER_URL 不应阻止 Bot 启动。"""
    validate_config(_cfg(MCP_ENABLED=False, MCP_SERVER_URL=""))  # 不抛异常

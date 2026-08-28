"""ConfigService 校验测试（P3-4）：enum / 数值范围 / 端口 / 敏感项。"""
from src.services.config_service import ConfigService


class FakeSettings:
    """轻量 Settings 替身：仅含 ConfigService.SCHEMA 涉及的字段（含默认值）。"""

    DEFAULTS = {
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
        "DEEPSEEK_API_KEY": "sk-real-key-123456",
        "DEEPSEEK_API_URL": "https://api.deepseek.com/chat/completions",
        "VISION_MODEL": "deepseek-v4-flash-vision-exp",
        "MAX_REPLY_LENGTH": 40,
        "BOT_NICKNAME": "花璃",
        "USER_COOLDOWN": 5,
        "BOT_COOLDOWN": 2,
        "MAX_CONSECUTIVE_REPLIES": 3,
        "ONLY_REPLY_WHEN_AT": False,
        "DAILY_AI_CALL_BUDGET": 1000,
        "GROUP_DAILY_AI_CALL_BUDGET": 300,
        "USER_AI_CALL_MIN_INTERVAL": 10,
        "AI_MAX_RETRIES": 3,
        "AI_CIRCUIT_BREAKER_FAILURES": 10,
        "AI_CIRCUIT_BREAKER_PAUSE_SECONDS": 60,
        "MEMORY_TTL_DAYS": 0,
        "MODEL_MEMORY_TTL_DAYS": 30,
        "STICKER_ENABLED": False,
        "STICKER_COOLDOWN": 60,
        "MCP_ENABLED": False,
        "MCP_SERVER_URL": "",
        "MCP_TIMEOUT": 15,
        "MCP_MAX_TOOL_CALLS": 5,
        "MCP_ALLOWED_TOOLS": "",
        "LOG_LEVEL": "INFO",
        "LOG_FORMAT": "text",
        "WS_PORT": 3001,
        "HTTP_API_BASE": "http://127.0.0.1:3000",
        "MEMORY_PATH": "./data/memory.db",
    }

    def __init__(self, **overrides):
        for k, v in self.DEFAULTS.items():
            setattr(self, k, v)
        for k, v in overrides.items():
            setattr(self, k, v)


class _FakeRepo:
    """内存版 SettingsRepository（仅需 ConfigService 用到的接口）。"""

    def __init__(self, store=None):
        self._store = dict(store or {})

    def get_config(self, key):
        return self._store.get(key)

    def set_config(self, key, value):
        self._store[key] = value

    def list_configs(self):
        return list(self._store.items())


def _svc(**cfg_overrides):
    return ConfigService(FakeSettings(**cfg_overrides), _FakeRepo())


# ---------- P3-4：enum 校验 ----------
def test_log_format_enum_enforced():
    svc = _svc()
    ok, _ = svc.update("LOG_FORMAT", "bogus")
    assert ok is False
    ok, _ = svc.update("LOG_FORMAT", "json")
    assert ok is True
    assert svc.config.LOG_FORMAT == "json"


def test_log_level_enum_enforced():
    svc = _svc()
    assert svc.update("LOG_LEVEL", "verbose")[0] is False
    assert svc.update("LOG_LEVEL", "DEBUG")[0] is True
    assert svc.config.LOG_LEVEL == "DEBUG"


# ---------- P3-4：数值范围校验 ----------
def test_timeout_range_enforced():
    svc = _svc()
    assert svc.update("MCP_TIMEOUT", "0")[0] is False   # < 1
    assert svc.update("MCP_TIMEOUT", "abc")[0] is False
    assert svc.update("MCP_TIMEOUT", "30")[0] is True
    assert svc.config.MCP_TIMEOUT == 30


def test_max_tool_calls_range_enforced():
    svc = _svc()
    assert svc.update("MCP_MAX_TOOL_CALLS", "-1")[0] is False
    assert svc.update("MCP_MAX_TOOL_CALLS", "999999")[0] is False  # > 1000
    assert svc.update("MCP_MAX_TOOL_CALLS", "0")[0] is True
    assert svc.config.MCP_MAX_TOOL_CALLS == 0


def test_reply_length_range_enforced():
    svc = _svc()
    assert svc.update("MAX_REPLY_LENGTH", "0")[0] is False
    assert svc.update("MAX_REPLY_LENGTH", "10000")[0] is False
    assert svc.update("MAX_REPLY_LENGTH", "80")[0] is True


def test_port_range_enforced():
    svc = _svc()
    assert svc.update("WS_PORT", "70000")[0] is False
    assert svc.update("WS_PORT", "0")[0] is False
    assert svc.update("WS_PORT", "8080")[0] is True


# ---------- P3-4：secret 校验 ----------
def test_secret_min_length():
    svc = _svc()
    assert svc.update("DEEPSEEK_API_KEY", "short")[0] is False
    ok, msg = svc.update("DEEPSEEK_API_KEY", "sk-abcdef-123456")
    assert ok is True
    assert "立即生效" in msg


def test_secret_empty_means_no_change():
    svc = _svc()
    ok, msg = svc.update("DEEPSEEK_API_KEY", "")
    assert ok is False
    assert "未输入新值" in msg

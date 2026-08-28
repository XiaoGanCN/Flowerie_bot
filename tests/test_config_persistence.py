"""Web UI 持久化配置启动加载测试（P2-2 修复）。

覆盖 repair.txt 要求：
1. .env 提供默认配置
2. persistent config 覆盖 .env
3. persistent config 不存在时回退 .env
4. persistent config + .env + code default 完整优先级
5. Bot restart 后 persistent config 仍然生效（process1 保存 → process2 启动读取）
6. 当前进程热更新继续正常
7. Secret 不被错误覆盖
8. 无效 persistent config 不导致 Bot 启动进入危险状态
9. Web UI 显示值与实际运行配置一致
"""
import os
import tempfile

from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService
from tests.test_config_service import FakeSettings


def _repo(path):
    return SettingsRepository(path)


# ---------- 1+2+3：优先级与回退 ----------
def test_persisted_overrides_env():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "settings.db")
        repo = _repo(path)
        repo.set_config("DEEPSEEK_MODEL", "db-model")
        # "env" 提供 deepseek-v3，持久化应覆盖它
        config = FakeSettings(DEEPSEEK_MODEL="env-model")
        svc = ConfigService(config, repo)
        n = svc.apply_persisted()
        assert n >= 1
        assert config.DEEPSEEK_MODEL == "db-model"


def test_no_persisted_falls_back_to_env():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(os.path.join(td, "settings.db"))
        config = FakeSettings(DEEPSEEK_MODEL="env-model")
        svc = ConfigService(config, repo)
        assert svc.apply_persisted() == 0
        assert config.DEEPSEEK_MODEL == "env-model"


def test_priority_chain_code_env_persistent():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(os.path.join(td, "settings.db"))
        repo.set_config("MAX_REPLY_LENGTH", "200")  # persistent 最高
        config = FakeSettings(MAX_REPLY_LENGTH=100)  # env 次之
        ConfigService(config, repo).apply_persisted()
        assert config.MAX_REPLY_LENGTH == 200  # persistent 胜出
        # 未被持久化的键：回退 env 值
        assert config.BOT_NICKNAME == "花璃"
        # 未被 env 覆盖的键：回退 code default
        assert config.MCP_TIMEOUT == 15


# ---------- 5：重启后持久化仍生效（process1 → process2） ----------
def test_restart_persistence_survives():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "settings.db")
        # process 1：Web UI 保存配置
        repo1 = _repo(path)
        repo1.set_config("MCP_ENABLED", "true")
        repo1.set_config("MCP_SERVER_URL", "https://mcp.example.com/mcp")
        repo1.set_config("MCP_MAX_TOOL_CALLS", "2")
        repo1.set_config("LOG_FORMAT", "json")
        repo1.close()
        # process 2：全新 Settings（.env/默认）+ 同一 settings.db
        repo2 = _repo(path)
        config2 = FakeSettings()  # 默认 MCP_ENABLED=False
        svc2 = ConfigService(config2, repo2)
        n = svc2.apply_persisted()
        assert n == 4
        assert config2.MCP_ENABLED is True
        assert config2.MCP_SERVER_URL == "https://mcp.example.com/mcp"
        assert config2.MCP_MAX_TOOL_CALLS == 2
        assert config2.LOG_FORMAT == "json"
        repo2.close()


# ---------- 6：进程内热更新仍正常 ----------
def test_hot_update_still_works():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(os.path.join(td, "settings.db"))
        config = FakeSettings()
        svc = ConfigService(config, repo)
        ok, msg = svc.update("STICKER_ENABLED", "true")
        assert ok is True and "立即生效" in msg
        assert config.STICKER_ENABLED is True  # 运行实例已更新
        assert repo.get_config("STICKER_ENABLED") == "true"  # 已持久化


# ---------- 7：Secret 不被错误覆盖 ----------
def test_secret_applied_and_masked():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(os.path.join(td, "settings.db"))
        repo.set_config("DEEPSEEK_API_KEY", "sk-abcdef-123456")
        config = FakeSettings()
        svc = ConfigService(config, repo)
        svc.apply_persisted()
        # 运行配置拿到真实密钥（用户保存的原值）
        assert config.DEEPSEEK_API_KEY == "sk-abcdef-123456"
        # 显示层仍脱敏
        listed = {c["key"]: c["current"] for c in svc.list_configs()}
        assert "sk-abcdef" not in listed["DEEPSEEK_API_KEY"]
        assert "****" in listed["DEEPSEEK_API_KEY"]


# ---------- 8：无效持久化配置跳过，不进入危险状态 ----------
def test_invalid_persisted_values_skipped():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(os.path.join(td, "settings.db"))
        repo.set_config("MCP_MAX_TOOL_CALLS", "-5")     # 非法（负值）
        repo.set_config("LOG_FORMAT", "bogus")          # 非法（enum 外）
        repo.set_config("MCP_TIMEOUT", "not-a-number")  # 非法
        repo.set_config("MCP_ENABLED", "true")          # 合法
        config = FakeSettings()
        svc = ConfigService(config, repo)
        n = svc.apply_persisted()
        assert n == 1  # 只有合法项被应用
        assert config.MCP_ENABLED is True
        assert config.MCP_MAX_TOOL_CALLS == 5    # 保持默认
        assert config.LOG_FORMAT == "text"        # 保持默认
        assert config.MCP_TIMEOUT == 15           # 保持默认


def test_unknown_persisted_key_skipped():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(os.path.join(td, "settings.db"))
        repo.set_config("NOT_IN_SCHEMA", "x")
        repo.set_config("DEEPSEEK_MODEL", "db-model")
        config = FakeSettings()
        n = ConfigService(config, repo).apply_persisted()
        assert n == 1  # 未知键被跳过，不崩溃
        assert config.DEEPSEEK_MODEL == "db-model"


# ---------- 9：Web UI 显示值与实际运行配置一致 ----------
def test_ui_shows_runtime_value_after_apply():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(os.path.join(td, "settings.db"))
        repo.set_config("MAX_REPLY_LENGTH", "120")
        config = FakeSettings()
        svc = ConfigService(config, repo)
        svc.apply_persisted()
        listed = {c["key"]: c["current"] for c in svc.list_configs()}
        assert listed["MAX_REPLY_LENGTH"] == "120"   # 显示层
        assert config.MAX_REPLY_LENGTH == 120        # 运行层（一致）
        assert svc.get_value("MAX_REPLY_LENGTH") == "120"

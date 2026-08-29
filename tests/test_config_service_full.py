"""ConfigService 全量配置与 .env 持久化测试（Web UI 配置中心核心）。

覆盖任务要求：
- 全部配置变量（60+）都在 SCHEMA / list_configs 中
- bool/int/string/secret/textarea/列表 保存
- 保存后真正写入 .env（保留原变量与注释）
- 非法值拒绝且不写 .env
- 并发保存不损坏 .env
- 保存后重载（新 EnvFileStore 读取）配置正确
"""
import json
import os
import tempfile
import threading

from src.config import Settings
from src.repositories.env_store import EnvFileStore
from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService
from tests.test_config_service import FakeSettings, _FakeRepo


def _service(tmp: str, **cfg_overrides):
    repo = SettingsRepository(os.path.join(tmp, "settings.db"))
    config = FakeSettings(**cfg_overrides)
    svc = ConfigService(config, repo, env_path=os.path.join(tmp, ".env"))
    return config, repo, svc


def _env(tmp: str) -> EnvFileStore:
    return EnvFileStore(os.path.join(tmp, ".env"))


# ---------- 全量配置 ----------
def test_all_settings_fields_covered():
    """Settings 的每个字段都出现在 ConfigService.SCHEMA（除管理账号两项）。"""
    from tests.test_router_regression import make_config
    cfg = make_config(DEEPSEEK_API_KEY="sk-x" * 20, BOT_QQ=10001, WEB_UI_PASSWORD="x")
    # 用真实 Settings 字段集做对照（pydantic 模型字段）
    real_fields = set(Settings.model_fields.keys())
    schema_keys = set(ConfigService.SCHEMA.keys())
    # 管理账号由注册页管理，不暴露为普通配置
    excluded = {"WEB_UI_USERNAME", "WEB_UI_PASSWORD"}
    missing = real_fields - schema_keys - excluded
    assert missing == set(), f"SCHEMA 缺少配置字段: {missing}"
    assert len(schema_keys) >= 60


def test_list_configs_contains_all_schema_keys():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc = _service(td)
        keys = [c["key"] for c in svc.list_configs()]
        assert set(keys) == set(ConfigService.SCHEMA.keys())
        # 表单元数据齐全
        by_key = {c["key"]: c for c in svc.list_configs()}
        assert by_key["MAX_REPLY_LENGTH"]["min"] == 1
        assert by_key["MAX_REPLY_LENGTH"]["max"] == 1000
        assert by_key["WS_PORT"]["min"] == 1
        assert "options" in by_key["LOG_LEVEL"]
        assert by_key["POKE_REPLIES"]["type"] == "textarea"


# ---------- 各类型保存 ----------
def test_bool_save_and_unchecked_is_false():
    with tempfile.TemporaryDirectory() as td:
        config, repo, svc = _service(td)
        # 显式 true
        ok, _ = svc.update_many({"ONLY_REPLY_WHEN_AT": "true"})
        assert ok is True
        assert config.ONLY_REPLY_WHEN_AT is True
        assert _env(td).read_values()["ONLY_REPLY_WHEN_AT"] == "true"
        # 显式 false（checkbox 未勾选时提交 false）
        ok, _ = svc.update_many({"ONLY_REPLY_WHEN_AT": "false"})
        assert ok is True
        assert config.ONLY_REPLY_WHEN_AT is False
        assert _env(td).read_values()["ONLY_REPLY_WHEN_AT"] == "false"


def test_int_float_string_save():
    with tempfile.TemporaryDirectory() as td:
        config, _, svc = _service(td)
        assert svc.update_many({"MAX_REPLY_LENGTH": "66"})[0] is True
        assert config.MAX_REPLY_LENGTH == 66
        assert _env(td).read_values()["MAX_REPLY_LENGTH"] == "66"
        assert svc.update_many({"BOT_NICKNAME": "小璃"})[0] is True
        assert config.BOT_NICKNAME == "小璃"
        assert _env(td).read_values()["BOT_NICKNAME"] == "小璃"


def test_secret_save_and_empty_no_change():
    with tempfile.TemporaryDirectory() as td:
        config, _, svc = _service(td, DEEPSEEK_API_KEY="sk-old-key-123456")
        ok, _ = svc.update_many({"DEEPSEEK_API_KEY": ""})
        assert ok is False  # 留空 = 不修改
        assert config.DEEPSEEK_API_KEY == "sk-old-key-123456"
        ok, _ = svc.update_many({"DEEPSEEK_API_KEY": "sk-new-key-abcdef"})
        assert ok is True
        assert config.DEEPSEEK_API_KEY == "sk-new-key-abcdef"
        assert _env(td).read_values()["DEEPSEEK_API_KEY"] == "sk-new-key-abcdef"


def test_textarea_save():
    with tempfile.TemporaryDirectory() as td:
        config, _, svc = _service(td)
        ok, _ = svc.update_many({"POKE_REPLIES": "戳一下\n再戳\n（躲开）"})
        assert ok is True
        assert config.POKE_REPLIES == ["戳一下", "再戳", "（躲开）"]
        env_val = _env(td).read_values()["POKE_REPLIES"]
        assert json.loads(env_val) == ["戳一下", "再戳", "（躲开）"]  # .env 存 JSON 数组


def test_list_int_save():
    with tempfile.TemporaryDirectory() as td:
        config, _, svc = _service(td)
        ok, _ = svc.update_many({"ALLOWED_GROUP_IDS": "10001, 10002"})
        assert ok is True
        assert config.ALLOWED_GROUP_IDS == [10001, 10002]
        assert json.loads(_env(td).read_values()["ALLOWED_GROUP_IDS"]) == [10001, 10002]


def test_json_config_save():
    with tempfile.TemporaryDirectory() as td:
        config, _, svc = _service(td)
        servers = '[{"name":"s1","url":"https://mcp.example.com/mcp","allowed_tools":"web_search","timeout":15}]'
        ok, _ = svc.update_many({"MCP_SERVERS": servers})
        assert ok is True
        assert _env(td).read_values()["MCP_SERVERS"] == servers


# ---------- .env 持久化：保留原变量与注释 ----------
def test_env_preserves_other_vars_and_comments():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, ".env"), "w", encoding="utf-8") as f:
            f.write("# AI Configuration\nDEEPSEEK_API_KEY=sk-old\nMAX_REPLY_LENGTH=40\n")
        _, _, svc = _service(td, DEEPSEEK_API_KEY="sk-old")
        ok, _ = svc.update_many({"MAX_REPLY_LENGTH": "88"})
        assert ok is True
        text = open(os.path.join(td, ".env"), encoding="utf-8").read()
        assert "# AI Configuration" in text
        assert "DEEPSEEK_API_KEY=sk-old" in text
        assert "MAX_REPLY_LENGTH=88" in text


# ---------- 非法值拒绝 ----------
def test_invalid_value_rejected_without_env_write():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, ".env"), "w", encoding="utf-8") as f:
            f.write("MAX_REPLY_LENGTH=40\n")
        _, _, svc = _service(td)
        ok, msg = svc.update_many({"MAX_REPLY_LENGTH": "abc", "BOT_NICKNAME": "新昵称"})
        assert ok is False  # 整体校验失败 → 全部不写
        assert "MAX_REPLY_LENGTH" in msg
        assert _env(td).read_values() == {"MAX_REPLY_LENGTH": "40"}  # .env 未动
        # 单键更新路径同样拒绝
        ok, _ = svc.update("MAX_REPLY_LENGTH", "abc")
        assert ok is False
        assert _env(td).read_values() == {"MAX_REPLY_LENGTH": "40"}


def test_enum_and_range_validation():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc = _service(td)
        assert svc.update_many({"LOG_LEVEL": "verbose"})[0] is False
        assert svc.update_many({"LOG_LEVEL": "INFO"})[0] is True
        assert svc.update_many({"WS_PORT": "70000"})[0] is False
        assert svc.update_many({"USER_COOLDOWN": "-1"})[0] is False
        assert svc.update_many({"ALLOWED_GROUP_IDS": "12,abc"})[0] is False


# ---------- 保存后重载 ----------
def test_env_reload_after_save():
    """保存到 .env 后，新实例（模拟重启）从 .env 读到新值。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, svc = _service(td)
        svc.update_many({"MAX_REPLY_LENGTH": "120", "BOT_NICKNAME": "重启后"})
        # 模拟重启：全新 repo + 全新 FakeSettings（env 值）
        repo2 = SettingsRepository(os.path.join(td, "settings.db"))
        config2 = FakeSettings()
        svc2 = ConfigService(config2, repo2, env_path=os.path.join(td, ".env"))
        # .env 已经是新值
        values = _env(td).read_values()
        assert values["MAX_REPLY_LENGTH"] == "120"
        assert values["BOT_NICKNAME"] == "重启后"
        # settings.db 同步（既有持久化链），apply 后运行配置一致
        assert repo2.get_config("MAX_REPLY_LENGTH") == "120"
        svc2.apply_persisted()
        assert config2.MAX_REPLY_LENGTH == 120
        repo2.close()


# ---------- 并发保存 ----------
def test_concurrent_updates_env_intact():
    with tempfile.TemporaryDirectory() as td:
        config, repo, svc = _service(td)
        errors = []

        def worker(key, value):
            try:
                for _ in range(10):
                    svc.update(key, value)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(k, v))
                   for k, v in [("MAX_REPLY_LENGTH", "50"), ("BOT_COOLDOWN", "3"),
                                ("USER_COOLDOWN", "7"), ("REPEAT_THRESHOLD", "4")]]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        values = _env(td).read_values()
        assert values["MAX_REPLY_LENGTH"] == "50"
        assert values["BOT_COOLDOWN"] == "3"
        assert values["USER_COOLDOWN"] == "7"
        assert values["REPEAT_THRESHOLD"] == "4"
        # .env 行结构完整
        text = open(os.path.join(td, ".env"), encoding="utf-8").read()
        assert text.count("\n") >= 4


# ---------- 重启提示 ----------
def test_restart_required_message():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc = _service(td)
        ok, msg = svc.update_many({"WS_PORT": "4000"})
        assert ok is True
        assert "重启" in msg
        ok, msg = svc.update_many({"MAX_REPLY_LENGTH": "60"})
        assert ok is True
        assert "立即生效" in msg


# ---------- env_path 未配置时不写 .env（兼容既有行为） ----------
def test_no_env_store_when_path_not_given():
    repo = _FakeRepo()
    svc = ConfigService(FakeSettings(), repo)
    ok, _ = svc.update("MAX_REPLY_LENGTH", "60")
    assert ok is True
    assert svc.env_store is None

"""自定义 Prompt 测试（第四轮）：优先级 / 持久化 / 长度 / 权限 / AI 注入。"""
import tempfile

import pytest

from src.repositories.settings_repository import SettingsRepository
from src.services.prompt_manager import PromptManager


@pytest.fixture()
def pm():
    tmp = tempfile.TemporaryDirectory()
    repo = SettingsRepository(f"{tmp.name}/settings.db")
    manager = PromptManager(repo, max_length=100)
    yield manager, repo, tmp
    repo.close()
    tmp.cleanup()


# ---------- 优先级 ----------
def test_default_builtin_prompt(pm):
    manager, _, _ = pm
    assert manager.get_effective_prompt(123) == ""  # 无自定义 → 内置人设


def test_global_custom_prompt(pm):
    manager, _, _ = pm
    manager.set_global_prompt("你是可爱的小助手")
    assert manager.get_effective_prompt(123) == "你是可爱的小助手"
    assert manager.get_effective_prompt(None) == "你是可爱的小助手"


def test_group_custom_prompt(pm):
    manager, _, _ = pm
    manager.set_group_prompt(100, "本群专属人格")
    assert manager.get_effective_prompt(100) == "本群专属人格"
    assert manager.get_effective_prompt(200) == ""  # 其他群不受影响


def test_group_overrides_global(pm):
    manager, _, _ = pm
    manager.set_global_prompt("全局人格")
    manager.set_group_prompt(100, "群专属人格")
    assert manager.get_effective_prompt(100) == "群专属人格"  # 群 > 全局
    assert manager.get_effective_prompt(200) == "全局人格"


# ---------- reset ----------
def test_reset_group_prompt(pm):
    manager, _, _ = pm
    manager.set_global_prompt("全局人格")
    manager.set_group_prompt(100, "群专属人格")
    assert manager.reset_group_prompt(100) is True
    assert manager.get_effective_prompt(100) == "全局人格"  # 回退到全局
    assert manager.reset_group_prompt(100) is False  # 已重置


def test_reset_global_prompt(pm):
    manager, _, _ = pm
    manager.set_global_prompt("全局人格")
    assert manager.reset_global_prompt() is True
    assert manager.get_effective_prompt(100) == ""  # 回退到内置


# ---------- 持久化 ----------
def test_prompt_persistence(pm):
    manager, repo, tmp = pm
    manager.set_global_prompt("重启后还在")
    manager.set_group_prompt(7, "群的也在")
    # 模拟重启：新建 manager 同库
    manager2 = PromptManager(SettingsRepository(f"{tmp.name}/settings.db"), max_length=100)
    assert manager2.get_effective_prompt(7) == "群的也在"
    assert manager2.get_effective_prompt(8) == "重启后还在"


# ---------- 长度限制 ----------
def test_prompt_length_limit(pm):
    manager, _, _ = pm
    with pytest.raises(ValueError):
        manager.set_global_prompt("长" * 101)  # 超 max_length=100
    with pytest.raises(ValueError):
        manager.set_group_prompt(1, "长" * 101)
    # 边界内可设置
    manager.set_global_prompt("长" * 100)
    assert len(manager.get_global_prompt()) == 100


# ---------- AI 注入 ----------
async def test_custom_prompt_injected_into_system_prompt():
    """群自定义 Prompt 进入 system prompt，且位于安全声明之前。"""
    from src.services.ai_client import AIClient
    from tests.test_ai_client import make_config as ai_config

    tmp = tempfile.TemporaryDirectory()
    try:
        repo = SettingsRepository(f"{tmp.name}/settings.db")
        manager = PromptManager(repo, max_length=500)
        manager.set_group_prompt(10, "你是一个话痨")

        class FakeHTTPClient:
            last_payload = None

            async def post(self, url, headers=None, json=None, timeout=None):
                FakeHTTPClient.last_payload = json

                class R:
                    status_code = 200

                    def json(self):
                        return {"choices": [{"message": {"content": "好"}}]}

                return R()

        ai = AIClient(ai_config(), None)
        ai.client = FakeHTTPClient()
        await ai.chat_once("在吗", "（暂无历史聊天记录）", user_id=1, group_id=10,
                           custom_prompt=manager.get_effective_prompt(10))
        system_prompt = FakeHTTPClient.last_payload["messages"][0]["content"]
        assert "你是一个话痨" in system_prompt
        # 自定义 Prompt 在安全声明之前
        assert system_prompt.index("你是一个话痨") < system_prompt.index("【输入安全声明")
    finally:
        tmp.cleanup()


# ---------- 命令权限 ----------
async def test_prompt_command_permission():
    """非管理员无法修改 Prompt；管理员可以。"""
    from src.core.command_handler import CommandHandler
    from tests.test_router_regression import FakeSender, make_config

    tmp = tempfile.TemporaryDirectory()
    try:
        repo = SettingsRepository(f"{tmp.name}/settings.db")
        manager = PromptManager(repo, max_length=500)
        config = make_config(ADMIN_QQ_IDS=[999])
        sender = FakeSender()
        handler = CommandHandler(config, sender, None, manager)

        # 普通用户修改 → 拒绝
        await handler.handle("/prompt set 我是新人格", 111, 1)
        assert manager.get_global_prompt() == ""
        assert "管理员" in sender.sent[-1][1]

        # 管理员修改 → 成功
        await handler.handle("/prompt set 我是新人格", 999, 1)
        assert manager.get_global_prompt() == "我是新人格"
        assert "已更新" in sender.sent[-1][1]

        # 管理员重置群 Prompt
        await handler.handle("/prompt group set 群人格", 999, 5)
        assert manager.get_group_prompt(5) == "群人格"
        await handler.handle("/prompt group reset", 999, 5)
        assert manager.get_group_prompt(5) == ""

        # 查看命令（所有人可用）
        await handler.handle("/prompt show", 111, 5)
        assert "当前生效" in sender.sent[-1][1] or "没有自定义" in sender.sent[-1][1]
    finally:
        tmp.cleanup()

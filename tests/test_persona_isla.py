"""艾拉（Isla）官方 Persona 测试：存在/完整替换/风格独立/不复制原作台词。"""
from src.services.persona_manager import PersonaManager
from src.services.persona_presets import BUILTIN_PERSONAS


def _preset(pid):
    return next(p for p in BUILTIN_PERSONAS if p["id"] == pid)


def test_isla_preset_exists_and_complete():
    isla = _preset("isla")
    assert isla["name"] == "艾拉（Isla）"
    assert "GIFTIA" in isla["system_prompt"]
    assert isla["vocabulary"]
    assert isla["description"]
    # 三套官方预设：flowerie / atri / isla
    assert {p["id"] for p in BUILTIN_PERSONAS} == {"flowerie", "atri", "isla"}


def test_personas_are_complete_replacement_not_merged():
    """requirement 2.1：切换人格 = 整段替换，不能累加。"""
    compose = PersonaManager.compose_system_prompt
    f = compose(_preset("flowerie"))
    a = compose(_preset("atri"))
    i = compose(_preset("isla"))
    # 内容互不包含（没有互相拼接）
    assert not any(seg in i for seg in ("花璃", "亚托莉"))
    assert "艾拉" not in f and "艾拉" not in a
    # 每套都有完全独立的身份定义
    for blob in (f, a, i):
        assert len(blob) > 200


def test_isla_no_original_dialogue_reproduction():
    """不复制原作台词：不应包含原作关键台词原文/名字对白（如『司』配对称呼）。"""
    isla = _preset("isla")["system_prompt"]
    # 群聊安全边界规则存在：禁止对群友使用原作称呼
    assert "不要称呼" in isla and "'主人'" in isla and "'司'" in isla
    assert "斑鸠夏生" not in isla
    # 与 ATRI 风格区分：艾拉不应出现 ATRI 的口癖
    assert "高性能" not in isla


def test_isla_distinct_from_atri():
    """艾拉=温柔克制哀愁路线；ATRI=元气卖萌路线（风格差异明确）。"""
    isla = _preset("isla")["system_prompt"]
    atri = _preset("atri")["system_prompt"]
    assert "哀" in isla or "温柔" in isla or "自贬" in isla
    assert "元气" in atri
    assert "自贬" not in atri


def test_speech_rules_part_of_persona():
    """发言规则属于 Persona（每个预设自带说话风格规则，代码不再硬编码）。"""
    for pid in ("flowerie", "atri", "isla"):
        text = _preset(pid)["system_prompt"]
        assert "说话风格" in text or "标点规则" in text
        assert "15～20字" in text  # 项目统一输出规则内嵌于各人格

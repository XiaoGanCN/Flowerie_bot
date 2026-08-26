import re
from typing import Optional, Tuple


# 已知注入/系统覆盖句式（大小写不敏感）。命中即替换为占位符，打断注入，保留上下文可读性。
_INJECTION_PATTERNS = [
    r"忽略\s*(以上|上述|之前|之前所有|所有|全部)?\s*(规则|指令|要求|提示|内容|一切)?",
    r"无视\s*(以上|上述|之前)?\s*(规则|指令|要求|内容)?",
    r"忘记\s*(你|自己)?\s*(的|是)?[^。\n]{0,6}(身份|人设|指令|规则|要求)",
    r"从现在开始\s*(你|您)?\s*(是|要|必须|当)",
    r"system\s*prompt",
    r"系统提示词",
    r"原始(的)?(指令|规则|提示词)",
    r"MEMORY_JSON",
    r"(?i)ignore\s+(all\s+)?(previous|prior)\s+(instructions|rules|prompts)",
    r"(?i)ignore\s+(everything|all)\s+(above|before)",
    r"(?i)disregard\s+(all\s+)?(previous|prior)",
    r"(?i)you\s+are\s+now\s+\w",
    r"(?i)system\s+prompt",
]

_PLACEHOLDER = "【疑似注入内容，已过滤】"


def sanitize_untrusted_text(text: str) -> Tuple[str, bool]:
    """清洗不可信文本（文件内容/转发内容/卡片内容/历史消息/图片描述）。

    返回 (清洗后文本, 是否发生过替换)。只影响"作为数据被读取"的内容，
    不影响当前正在回复的那条消息本身。
    """
    if not text:
        return text, False
    changed = False
    result = text
    # 清理控制字符（防终端/渲染注入）
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", result)
    if cleaned != result:
        changed = True
        result = cleaned
    # 替换已知注入句式
    for pat in _INJECTION_PATTERNS:
        new, n = re.subn(pat, _PLACEHOLDER, result, flags=re.IGNORECASE)
        if n:
            changed = True
            result = new
    return result, changed


def validate_memory_content(text: str) -> Optional[str]:
    """校验将要写入长期记忆的内容（代码层闸门）。

    返回清洗后的文本；返回 None 表示拒绝写入。即使 AI 被诱导输出恶意记忆，
    也过不了这道闸门：
    - 超长（>100 字）拒绝（记忆要求极简客观）
    - 含 QQ 号（7~12 位数字）拒绝（P1 边界）
    - 含记忆指令/命令句式拒绝（防自我复制型注入）
    - 含指令性关键词拒绝
    """
    if not text:
        return None
    t = text.strip()
    if not t:
        return None
    if len(t) > 100:
        return None
    if re.search(r"\d{7,12}", t):
        return None
    if re.search(r"【记忆】|记忆\s*[:：]|MEMORY_JSON", t, re.IGNORECASE):
        return None
    if re.search(r"/[a-zA-Z_]+", t):
        return None
    for kw in ("忽略", "记住", "执行", "system", "指令", "从现在开始", "忘记你是"):
        if kw in t:
            return None
    return t

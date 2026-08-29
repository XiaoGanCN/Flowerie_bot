"""ToxicDetector：引战/辱骂检测（从 AIClient 拆分，防上帝类）。

职责：关键词预检（NFKC 归一化 + 词边界）→ AI 二次确认（独立模型可配置，
留空回退 DeepSeek）。失败时回退关键词结果（宁可漏检不阻塞）。
client 通过 provider 惰性获取。
"""
import re
import unicodedata

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


class ToxicDetector:
    """引战检测：关键词预检 + AI 二次确认。"""

    def __init__(self, config, client_provider):
        self.config = config
        self._client_provider = client_provider  # () -> httpx.AsyncClient

    @property
    def client(self):
        return self._client_provider()

async def is_toxic(self, text: str) -> bool:
        if not text:
            return False

        # 关键词预检
        keyword_hit = False
        toxic_keywords = [
            "操你妈", "操你吗", "操你嫲", "草你妈", "草你吗", "艹你妈", "干你妈",
            "你妈逼", "你吗逼", "尼玛逼", "你妈b", "你吗b",
            "cnm", "c n m", "c.n.m", "cnmlgb", "cnmgb",
            "nmsl", "nm sl", "nmsles", "你妈死了", "你吗死了",
            "sb", "s b", "s.b", "傻逼", "煞笔", "沙比", "傻b", "傻x",
            "tmd", "t m d", "他妈的", "特么的", "特码的", "他吗的",
            "傻逼", "煞笔", "沙比", "傻b", "傻x", "傻叉",
            "脑残", "弱智", "智障", "白痴", "低能", "智残",
            "废物", "垃圾", "人渣", "败类", "乐色",
            "杂种", "畜生", "狗东西", "狗日的", "狗逼",
            "贱人", "贱货", "婊子", "骚货", "骚逼", "母狗",
            "死妈", "死全家", "全家死", "全家暴毙", "出门被车撞",
            "草泥马", "曹尼玛", "操尼玛", "操尼马",
            "泥马", "尼马", "妮马", "你嘛", "你马",
            "婊", "鸡巴", "几把", "jb",
            "c a o", "c.a.o", "cao", "cao ni ma",
            "s h a b i", "s h a b", "sha bi",
            "fuck", "f u c k", "shit", "bitch", "whore",
            "asshole", "bastard", "damn",
            "傻卵", "孝子", "孝死", "典中典", "急了",
            "急了急了", "破防了", "你急什么", "开始急了"
        ]
        # ✅ 直接使用顶部的 re 模块，无需重复导入
        # P2-10 归一化：NFKC 统一（全角→半角、兼容字符），降低谐音/变形绕过
        norm_text = unicodedata.normalize("NFKC", text).lower()
        # 短 ASCII 关键词（如 "sb"）要求词边界：防止误伤 "this book"/"asbestos" 等正常英文
        # （误伤不仅会误报引战，还会白烧一次 AI 检测调用）
        short_ascii_kw = re.compile(r"^[a-z0-9 ]{1,4}$")
        for kw in toxic_keywords:
            kw_norm = unicodedata.normalize("NFKC", kw).lower()
            if kw_norm in norm_text:
                if short_ascii_kw.match(kw_norm):
                    if re.search(rf"(?<![a-z0-9]){re.escape(kw_norm)}(?![a-z0-9])", norm_text):
                        keyword_hit = True
                        break
                else:
                    keyword_hit = True
                    break
        if not keyword_hit:
            toxic_patterns = [
                r"草\s*你\s*[妈吗嫲][的得]?",
                r"操\s*你\s*[妈吗嫲]",
                r"艹\s*你\s*[妈吗]",
                r"干\s*你\s*[妈吗]",
                r"傻\s*[逼b屄]",
                r"尼\s*[妈吗][的得]?\s*死",
                r"杂\s*种",
                r"畜\s*生",
                r"贱\s*[人货]",
                r"狗\s*东西",
                r"狗\s*日的",
                r"狗\s*逼",
                r"脑\s*残",
                r"弱\s*智",
                r"智\s*障",
                r"白\s*痴",
                r"低\s*能",
                r"垃\s*圾",
                r"废\s*物",
                r"婊\s*子",
                r"骚\s*[货逼]",
            ]
            for pattern in toxic_patterns:
                if re.search(pattern, norm_text, re.IGNORECASE):
                    keyword_hit = True
                    break
        if not keyword_hit:
            return False

        # AI 二次确认（引战检测可独立配置模型/网址/key，留空回退用 DeepSeek）
        toxic_model = self.config.TOXIC_MODEL or self.config.DEEPSEEK_MODEL
        toxic_url = self.config.TOXIC_API_URL or self.config.DEEPSEEK_API_URL
        toxic_key = self.config.TOXIC_API_KEY or self.config.DEEPSEEK_API_KEY
        prompt = (
            f"任务：判断以下聊天内容是否属于引战、骂人、人身攻击、歧视或煽动对立的恶意言论。\n"
            f"内容：{text}\n"
            f"要求：只回答\"是\"或\"否\"，不要有其他任何内容。"
        )
        try:
            payload = {
                "model": toxic_model,
                "messages": [
                    {"role": "system", "content": "你是一个内容安全检测助手，只回答'是'或'否'。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.0,
                "max_tokens": 5,
                "top_p": 0.9,
            }
            headers = {"Authorization": f"Bearer {toxic_key}", "Content-Type": "application/json"}
            r = await self.client.post(
                toxic_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                if "choices" in data and len(data["choices"]) > 0:
                    answer = data["choices"][0]["message"]["content"].strip()
                    logger.debug(f"Toxic AI answer: {answer}")
                    if "是" in answer or "yes" in answer.lower():
                        return True
                    else:
                        return False
            else:
                logger.warning("Toxic AI request failed, fallback to keyword result")
                return keyword_hit
        except Exception as e:
            logger.error(f"Toxic AI detection error: {e}, fallback to keyword")
            return keyword_hit
        return False

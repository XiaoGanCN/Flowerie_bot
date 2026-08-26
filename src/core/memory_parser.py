import re
from typing import Tuple


class MemoryParser:
    """记忆指令解析：识别 AI 回复里的「记忆: / 【记忆】QQ号:」指令，以及强制记忆触发检测。"""

    def parse_memory_update(self, memory_update: str, default_user_id: int) -> Tuple[int, str]:
        """把记忆指令解析成 (目标用户QQ, 记忆内容)；无效时返回 (default_user_id, "")。"""
        if not memory_update:
            return default_user_id, ""
        match = re.match(r'^【记忆】\s*(\d+)\s*[:：]\s*(.*)', memory_update)
        if match:
            target_uid = int(match.group(1))
            mem_text = match.group(2).strip()
            return target_uid, mem_text
        if memory_update.startswith("记忆:") or memory_update.startswith("记忆："):
            parts = re.split(r'[:：]', memory_update, maxsplit=1)
            if len(parts) == 2:
                mem_text = parts[1].strip()
                return default_user_id, mem_text
        mem_text = memory_update.replace("【记忆】", "").strip()
        if mem_text:
            return default_user_id, mem_text
        return default_user_id, ""

    def should_force_memory(self, clean_text: str, full_text: str, has_at_others: bool) -> bool:
        """判断消息是否包含明显的个人偏好/特征表达，触发静默记忆记录。"""
        if full_text.startswith("/"):
            return False
        if has_at_others:
            return False
        personal_patterns = re.compile(
            r'(我|本人)\s*(比较|更|最|超|特别|尤其|相当|非常|真的|有点|有些|不太)?\s*'
            r'(喜欢|爱|讨厌|享受|沉迷|擅长|习惯|害怕|怕|恨|厌恶|欣赏|崇拜|热爱|酷爱|钟情于|偏好|倾向于|不喜欢|不爱|反感|抗拒|抵触|恐惧|畏惧|担忧|焦虑|羡慕|嫉妒|佩服|敬佩|仰慕|痴迷|上瘾|戒不掉|suki)\s*'
            r'|(我|本人)\s*(打|玩|用|看|听|吃|喝|穿|戴|开|骑|坐|住|去|走|跑|跳|做|搞|弄|整|干)\s*.+?\s*'
            r'(很厉害|很强|厉害|牛逼|强|猛|水平高|水平可以|水平不错|还可以|挺好的|还行|不错|一般|差|菜|拉胯|不行|垃圾|弱|废|坑|菜鸡|萌新|大神|高手|大师|王者|宗师|钻石|铂金|黄金|白银|青铜)'
            r'|(我|本人)\s*(经常|偶尔|平时|一直|总是|老|天天|每周|每月|每年|每天|几乎|很少|几乎不|从不|基本上|大致|一般|通常)\s*(打|玩|用|看|听|吃|喝|穿|戴|开|骑|坐|住|去)\s*.+'
            r'|(我|本人)\s*(打|玩|用|看|听|吃|喝|穿|戴)\s*.+?\s*(比较多|很多|挺多|不多|少|频繁|稀少|大量|成堆|成片|成天|整天|整晚|整夜)'
            r'|(这|这个|这游戏|这东西|这活动|这电影|这剧|这书|这歌|这衣服|这鞋|这包|这车|这手机|这电脑|这软件|这APP|这家店|这餐厅|这地方|这城市|这天气|这季节)\s*.*?\s*'
            r'(好|不|挺|超|贼|很|还|真的|确实|特别|相当|非常|有点|有些)\s*(好玩|不好玩|好看|不好看|好吃|不好吃|好喝|不好喝|好听|不好听|好用|不好用|好穿|不好穿|好开|不好开|好骑|不好骑|好住|不好住|好走|不好走|好去|不好去|有意思|没意思|有趣|无聊|精彩|平淡|震撼|感人|催泪|搞笑|幽默|压抑|致郁|治愈|爽|不爽|坑|不坑|值|不值|划算|不划算)'
            r'|(我|本人)\s*(觉得|感觉|认为|以为|猜想|估计|猜测|琢磨|寻思|合计)\s*(这|这个|这游戏|这东西|这活动)\s*.*?\s*(不错|一般|还行|可以|挺好|很棒|超赞|绝了|神作|佳作|平庸|烂|糟|差)'
            r'|(我|本人)\s*(能|会|可以|能够)\s*(打|玩|用|看|听|吃|喝|做|搞|弄|整)\s*.+?\s*(了|过|到|得|来|去)'
            r'|我的(爱好|兴趣|特长|习惯|最爱|最恨|恐惧|担忧|理想|梦想|目标|愿望|计划|打算|安排)'
            r'|(I\s*(like|love|enjoy|prefer|hate|fear|adore|cherish|fancy|am\s+fond\s+of|dislike|loathe|detest|despise|admire|respect|appreciate|value|treasure|relish|savor|abhor|abominate|execrate))',
            re.IGNORECASE
        )
        return bool(personal_patterns.search(clean_text))

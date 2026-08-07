import time
import re
import random
from collections import deque
from typing import Dict, List, Deque, Optional, Tuple  # ✅ 新增 Tuple
from loguru import logger

from src.config import Settings
from src.models import GroupState, GlobalState
from src.services.memory_manager import MemoryManager


class PolicyEngine:
    def __init__(self, config: Settings, memory_manager: MemoryManager):
        self.config = config
        self.memory_manager = memory_manager
        self.groups: Dict[int, GroupState] = {}
        self.global_state = GlobalState()

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState()
        return self.groups[group_id]

    # ---------- 用户冷却 ----------
    def can_user_reply(self, user_id: int, group_id: int) -> bool:
        state = self.get_group_state(group_id)
        last = state.user_last_time.get(str(user_id), 0.0)
        return (time.time() - last) >= self.config.USER_COOLDOWN

    def update_user_time(self, user_id: int, group_id: int) -> None:
        state = self.get_group_state(group_id)
        state.user_last_time[str(user_id)] = time.time()

    # ---------- 机器人冷却 ----------
    def can_bot_reply(self, group_id: int) -> bool:
        now = time.time()
        if now - self.global_state.bot_last_reply_global < self.config.BOT_COOLDOWN:
            logger.debug("Global bot cooldown")
            return False
        state = self.get_group_state(group_id)
        if now < state.block_until:
            logger.debug(f"Group {group_id} blocked until {state.block_until}")
            return False
        return True

    def record_bot_reply(self, group_id: int) -> None:
        now = time.time()
        self.global_state.bot_last_reply_global = now
        state = self.get_group_state(group_id)
        state.group_last_reply_time = now
        state.consecutive_replies += 1
        logger.debug(f"Group {group_id} consecutive replies: {state.consecutive_replies}")
        if state.consecutive_replies >= self.config.MAX_CONSECUTIVE_REPLIES:
            state.block_until = now + self.config.BOT_CONSECUTIVE_REPLY_COOLDOWN
            state.consecutive_replies = 0
            logger.info(f"Group {group_id} entered cooldown for {self.config.BOT_CONSECUTIVE_REPLY_COOLDOWN}s")

    # ---------- 上下文 ----------
    def add_context(self, group_id: int, user_id: int, message: str, is_bot: bool = False) -> None:
        state = self.get_group_state(group_id)
        state.context.append({
            "user_id": user_id,
            "message": message,
            "is_bot": is_bot,
            "time": time.time()
        })

    def get_context_text(self, group_id: int, max_messages: int = 150) -> str:
        state = self.get_group_state(group_id)
        msgs = list(state.context)[-max_messages:]
        lines = []
        for idx, m in enumerate(msgs, 1):
            who = "机器人(花璃)" if m["is_bot"] else f"用户{m['user_id']}"
            lines.append(f"[{idx}] {who}: {m['message']}")
        return "\n".join(lines)

    # ---------- 复读 ----------
    def check_and_record_repeat(self, content: str, group_id: int) -> bool:
        """返回 True 表示应复读"""
        if not content or len(content.strip()) < 2 or content.startswith("/"):
            return False
        state = self.get_group_state(group_id)
        cache_key = content
        now = time.time()
        if cache_key not in state.msg_timestamps:
            state.msg_timestamps[cache_key] = deque()
        queue = state.msg_timestamps[cache_key]
        window = self.config.REPEAT_WINDOW
        while queue and now - queue[0] > window:
            queue.popleft()
        queue.append(now)
        if len(queue) >= self.config.REPEAT_THRESHOLD:
            last_repeat = state.repeat_cache.get(cache_key, 0)
            if now - last_repeat > window:
                state.repeat_cache[cache_key] = now
                return True
        return False

    # ---------- 主动聊天 ----------
    def should_active_chat(self, group_id: int) -> bool:
        now = time.time()
        if now - self.global_state.last_active_chat_time < self.config.ACTIVE_CHAT_COOLDOWN:
            return False
        if now < self.global_state.active_cooldown_until:
            return False
        if not self.can_bot_reply(group_id):
            return False
        hour = time.localtime(now).tm_hour
        if self.config.NIGHT_SILENCE_START <= hour < self.config.NIGHT_SILENCE_END:
            return False
        if random.random() < 0.10:
            return True
        return False

    def record_active_chat(self) -> None:
        now = time.time()
        prev = self.global_state.last_active_chat_time
        self.global_state.last_active_chat_time = now
        if now - prev < 600:
            self.global_state.consecutive_active_count += 1
        else:
            self.global_state.consecutive_active_count = 1
        if self.global_state.consecutive_active_count >= 2:
            self.global_state.active_cooldown_until = now + 1800
            logger.info("Entered 30min active cooldown after 2 consecutive active chats")

    # ---------- 回复概率 ----------
    def should_reply_by_context(self, group_id: int) -> bool:
        state = self.get_group_state(group_id)
        recent_msgs = list(state.context)[-5:]
        if not recent_msgs:
            return random.random() < 0.02
        user_msgs = [m for m in recent_msgs if not m["is_bot"]]
        if not user_msgs:
            return False
        prob = 0.03
        if len(user_msgs) >= 2:
            prob += 0.01
        if len(set(m["user_id"] for m in user_msgs)) == 1:
            prob = 0.02
        last_msg = recent_msgs[-1]
        if last_msg and not last_msg["is_bot"] and len(last_msg["message"]) < 2:
            prob = 0.02
        bot_count = sum(1 for m in recent_msgs[-3:] if m.get("is_bot", False))
        if bot_count >= 2:
            prob *= 0.3
        prob = max(0.01, min(0.05, prob))
        roll = random.random()
        logger.debug(f"Context reply prob for group {group_id}: {prob:.2f}, roll={roll:.2f}")
        return roll < prob

    # ---------- 重复回复检测 ----------
    def is_duplicate_reply(self, group_id: int, reply: str) -> bool:
        state = self.get_group_state(group_id)
        recent = state.recent_bot_replies
        if not recent:
            return False
        if reply in recent:
            return True
        words = set(reply)
        for old in recent:
            old_words = set(old)
            if not old_words:
                continue
            overlap = len(words & old_words) / len(old_words)
            if overlap >= 0.9:
                return True
        return False

    def add_recent_reply(self, group_id: int, reply: str) -> None:
        state = self.get_group_state(group_id)
        state.recent_bot_replies.append(reply)

    # ---------- 戳戳去重 ----------
    def get_poke_reply(self) -> str:
        available = [r for r in self.config.POKE_REPLIES if r not in self.global_state.poke_recent_replies]
        if not available:
            reply = random.choice(self.config.POKE_REPLIES)
        else:
            reply = random.choice(available)
        self.global_state.poke_recent_replies.append(reply)
        return reply

    # ---------- 记忆指令解析 ✅ 修复类型注解 ----------
    def parse_memory_update(self, memory_update: str, default_user_id: int) -> Tuple[int, str]:
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

    # ---------- 强制记忆触发检测 ----------
    def should_force_memory(self, clean_text: str, full_text: str, has_at_others: bool) -> bool:
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
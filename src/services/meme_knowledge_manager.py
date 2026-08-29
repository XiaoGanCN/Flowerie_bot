"""MemeKnowledgeManager：群聊梗/黑话知识业务层（按群完全隔离）。

职责：
- 消息缓冲（内存，有界）：记录每群近期消息供每日总结任务批量消费
- 知识检索注入：按当前消息提取可能相关的 term，只注入命中的知识
  （绝不把整个群知识库塞进 system prompt）
- 知识写入：去重（UNIQUE(group_id, normalized_term)）、长度/注入清洗闸门、
  可信度、数量上限治理、来源标注
- 知识编辑：按 id + group_id 双作用域（Web UI 用，防跨群操作）

安全边界（与任务要求对齐）：
- 知识库内容永远作为【不可信上下文知识】注入，绝不成为 system instruction
- 用户说一句不会永久写入：写入必须经过 AI 总结判断（add_knowledge 的
  source 只能是 summary/manual/web，且内容过清洗闸门）
- 每群最多 MAX_GROUP_MEMES 条；超限清理最不活跃的旧条目，不删活跃知识
"""
import re
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from src.core.sanitizer import sanitize_untrusted_text
from src.repositories.meme_knowledge_repository import MemeKnowledgeRepository
from src.utils.logging_setup import get_logger
from src.utils.metrics import registry

logger = get_logger(__name__)

_M_WRITE = registry.counter("meme_knowledge_write_total", "梗知识写入次数")
_M_REJECT = registry.counter("meme_knowledge_rejections_total", "梗知识写入拒绝次数", ["reason"])

# 置信度排序（低 < 中 < 高），合并时取高者
_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class MemeKnowledgeManager:
    """群聊梗知识：缓冲 / 检索注入 / 写入治理（存储委托 repository）。"""

    def __init__(self, repository: MemeKnowledgeRepository, max_memes_per_group: int = 500,
                 buffer_per_group: int = 1000, max_buffered_groups: int = 200,
                 term_max_length: int = 40, meaning_max_length: int = 200):
        self.repository = repository
        self.max_memes_per_group = max(10, int(max_memes_per_group or 500))
        self.buffer_per_group = max(50, int(buffer_per_group or 1000))
        self.max_buffered_groups = max(10, int(max_buffered_groups or 200))
        self.term_max_length = max(2, int(term_max_length or 40))
        self.meaning_max_length = max(10, int(meaning_max_length or 200))
        # 消息缓冲（进程内）：group_id -> deque[(ts, user_id, text)]，有界
        self._buffers: Dict[int, Deque[tuple]] = {}
        self._buffer_activity: Dict[int, float] = {}  # group -> 最近缓冲时间

    def close(self) -> None:
        self.repository.close()

    # ================= 消息缓冲（每日总结的数据源） =================
    def record_message(self, group_id: int, user_id: int, text: str,
                       ts: Optional[float] = None) -> None:
        """记录一条群消息进缓冲（有界；超群数上限淘汰最久未活跃的群）。"""
        if not text or not text.strip():
            return
        if group_id not in self._buffers:
            if len(self._buffers) >= self.max_buffered_groups:
                # 淘汰最久未活跃的群缓冲（LRU 近似）
                stale = min(self._buffer_activity, key=self._buffer_activity.get)
                self._buffers.pop(stale, None)
                self._buffer_activity.pop(stale, None)
            self._buffers[group_id] = deque(maxlen=self.buffer_per_group)
        self._buffers[group_id].append((ts if ts is not None else time.time(), int(user_id or 0), text))
        self._buffer_activity[group_id] = time.time()

    def buffered_groups(self) -> List[int]:
        return list(self._buffers.keys())

    def buffered_count(self, group_id: int) -> int:
        buf = self._buffers.get(group_id)
        return len(buf) if buf else 0

    def drain_messages(self, group_id: int, max_chars_per_msg: int = 200,
                       max_total_chars: int = 6000) -> List[str]:
        """取出某群缓冲的全部消息文本（截断后），并清空缓冲。

        返回按时间排序的消息文本列表（批量喂给一次 AI 总结请求）。
        """
        buf = self._buffers.pop(group_id, None)
        self._buffer_activity.pop(group_id, None)
        if not buf:
            return []
        lines: List[str] = []
        total = 0
        for _ts, _uid, text in buf:
            t = (text or "").strip().replace("\n", " ")[:max_chars_per_msg]
            if not t:
                continue
            if total + len(t) > max_total_chars:
                break
            lines.append(t)
            total += len(t)
        return lines

    # ================= 检索注入（只注入命中的知识） =================
    def retrieve_matches(self, group_id: int, text: str, max_items: int = 10) -> List[dict]:
        """提取当前消息中出现的本群梗（term 子串匹配，按 term 长度优先）。

        只返回命中项（不把整个知识库塞进 prompt）；命中项带 last_seen 更新。
        """
        if not text or not text.strip():
            return []
        terms = self.repository.list_all_terms(group_id)
        if not terms:
            return []
        hits: List[Tuple[str, int]] = []
        for term in terms:
            if term and len(term) >= 1 and term in text:
                hits.append((term, len(term)))
        # 长词优先（避免短词过度命中），最多 max_items
        hits.sort(key=lambda x: -x[1])
        results: List[dict] = []
        seen: set = set()
        for term, _ln in hits[: max_items * 2]:
            row = self.repository.get_by_term(group_id, term)
            if row is None or row["id"] in seen:
                continue
            seen.add(row["id"])
            self.repository.touch_last_seen(row["id"])
            results.append(row)
            if len(results) >= max_items:
                break
        return results

    def build_context_block(self, group_id: int, text: str, max_items: int = 10) -> str:
        """构建注入 system prompt 的群知识块（不可信上下文知识，仅命中项）。

        返回空字符串表示没有命中（不注入任何内容）。
        """
        rows = self.retrieve_matches(group_id, text, max_items=max_items)
        if not rows:
            return ""
        lines = []
        for r in rows:
            meaning = (r.get("meaning") or "").strip()
            if not meaning:
                continue
            lines.append(f"- {r.get('term')}：{meaning}")
        if not lines:
            return ""
        return "\n".join(lines)

    # ================= 知识写入（去重 / 清洗 / 上限） =================
    def add_knowledge(self, group_id: int, term: str, meaning: str, examples: str = "",
                      source: str = "summary", confidence: str = "medium",
                      status: str = "active") -> Tuple[bool, str]:
        """写入/更新一条知识。

        清洗闸门（防污染/防注入）：term/meaning 长度与内容校验；命中注入句式
        或疑似 QQ 号/群号的长数字串直接拒绝。同群同词（归一化）自动合并
        （更新理解、提升置信度、刷新 last_seen）。
        """
        term = (term or "").strip()
        meaning = (meaning or "").strip()
        if not term:
            _M_REJECT.inc({"reason": "empty_term"})
            return False, "词条为空"
        if len(term) > self.term_max_length:
            _M_REJECT.inc({"reason": "term_too_long"})
            return False, f"词条过长（{len(term)} 字，上限 {self.term_max_length}）"
        if not meaning:
            _M_REJECT.inc({"reason": "empty_meaning"})
            return False, "含义为空"
        if len(meaning) > self.meaning_max_length:
            meaning = meaning[: self.meaning_max_length]
        if re.search(r"\d{5,}", term):
            _M_REJECT.inc({"reason": "digits_in_term"})
            return False, "词条疑似包含 QQ 号/群号，拒绝写入"
        # 注入句式清洗（知识内容绝不能携带指令）
        term, _t1 = sanitize_untrusted_text(term)
        meaning, _t2 = sanitize_untrusted_text(meaning)
        if not term or not meaning:
            _M_REJECT.inc({"reason": "sanitized_empty"})
            return False, "词条内容不合规"
        confidence = confidence if confidence in _CONFIDENCE_ORDER else "medium"

        existing = self.repository.get_by_term(group_id, term)
        if existing is not None:
            # 已有：更新理解（含义取更长/更新的）、合并 examples、置信度取高
            old_conf = existing.get("confidence") or "low"
            new_conf = confidence if _CONFIDENCE_ORDER[confidence] > _CONFIDENCE_ORDER.get(old_conf, 0) else old_conf
            merged_examples = self._merge_examples(existing.get("examples") or "", examples)
            merged_meaning = meaning if len(meaning) >= len(existing.get("meaning") or "") else existing["meaning"]
            self.repository.update_knowledge(
                existing["id"], group_id,
                meaning=merged_meaning, examples=merged_examples,
                confidence=new_conf, source=source, status=status,
            )
            _M_WRITE.inc()
            return True, "updated"
        # 新增：数量上限治理（超限拒绝新增，保护活跃知识）
        if self.repository.count_by_group(group_id) >= self.max_memes_per_group:
            _M_REJECT.inc({"reason": "group_cap"})
            logger.warning("meme_knowledge_cap_reached group=%s term=%r", group_id, term)
            return False, f"本群知识已达上限（{self.max_memes_per_group} 条），未写入"
        self.repository.insert_knowledge(
            group_id, term, meaning, examples=examples, source=source,
            confidence=confidence, status=status,
        )
        _M_WRITE.inc()
        logger.info("meme_knowledge_write group=%s term=%r conf=%s source=%s",
                    group_id, term, confidence, source,
                    extra={"event": "meme_knowledge_write"})
        return True, "created"

    @staticmethod
    def _merge_examples(old: str, new: str) -> str:
        """合并示例（去重行，总长有界）。"""
        merged = []
        seen = set()
        for line in (old + "\n" + new).split("\n"):
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                merged.append(line)
            if sum(len(x) for x in merged) > 400:
                break
        return "\n".join(merged)

    # ================= 编辑（Web UI，双作用域） =================
    def list_for_group(self, group_id: int, search: str = "", limit: int = 200) -> List[dict]:
        return self.repository.list_by_group(group_id, search=search or None, limit=limit)

    def get_by_id(self, knowledge_id: int, group_id: int) -> Optional[dict]:
        return self.repository.get_by_id(knowledge_id, group_id)

    def update_knowledge(self, knowledge_id: int, group_id: int,
                         meaning: Optional[str] = None, examples: Optional[str] = None,
                         confidence: Optional[str] = None, source: Optional[str] = None,
                         status: Optional[str] = None) -> Tuple[bool, str]:
        """编辑知识（id + group_id 双作用域）。"""
        row = self.repository.get_by_id(knowledge_id, group_id)
        if row is None:
            return False, "知识不存在（或不属于该群）"
        fields: Dict[str, str] = {}
        if meaning is not None:
            meaning = (meaning or "").strip()
            if not meaning:
                return False, "含义不能为空"
            if len(meaning) > self.meaning_max_length:
                return False, f"含义过长（上限 {self.meaning_max_length} 字）"
            meaning, _ = sanitize_untrusted_text(meaning)
            fields["meaning"] = meaning
        if examples is not None:
            fields["examples"] = (examples or "").strip()[:400]
        if confidence is not None:
            if confidence not in _CONFIDENCE_ORDER:
                return False, "confidence 只能是 low/medium/high"
            fields["confidence"] = confidence
        if source is not None:
            fields["source"] = (source or "manual").strip()[:50]
        if status is not None:
            if status not in ("active", "inactive"):
                return False, "status 只能是 active/inactive"
            fields["status"] = status
        if not fields:
            return False, "没有可更新的字段"
        self.repository.update_knowledge(knowledge_id, group_id, **fields)
        _M_WRITE.inc()
        return True, "已更新"

    def delete_knowledge(self, knowledge_id: int, group_id: int) -> Tuple[bool, str]:
        if self.repository.delete_by_id(knowledge_id, group_id):
            return True, "已删除"
        return False, "知识不存在（或不属于该群）"

    def clear_group(self, group_id: int) -> Tuple[bool, str]:
        n = self.repository.delete_all_by_group(group_id)
        return True, f"已清空本群 {n} 条知识"

    # ================= 全局治理（上限 / 惰性清理） =================
    def enforce_caps(self) -> int:
        """全库治理：每群超限条目清理（不删活跃知识）。返回清理条数。"""
        removed = 0
        groups: set = set()
        for row in self.repository.list_all():
            groups.add(row["group_id"])
        for gid in groups:
            removed += self.repository.trim_group_to_max(gid, self.max_memes_per_group)
        return removed

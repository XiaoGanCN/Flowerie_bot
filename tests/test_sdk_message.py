"""SDK 中层/下层测试：BotMessage · Transformer（CQ 阉割 · 出站段转换）。"""

from src.sdk.message import BotMessage
from src.sdk.onebot.transformer import (
    extract_at_list,
    extract_images,
    extract_reply_id,
    extract_text,
    to_bot_event,
    to_bot_message_payload,
)


# ---------- BotMessage（中层） ----------
def test_bot_message_builder():
    m = BotMessage().add_text("你好").at(123).image("http://a/b.png").reply(99)
    assert m.text == "你好"
    assert m.at_list == ["123"]
    assert m.images == ["http://a/b.png"]
    assert m.reply_id == 99
    assert m.has("at") and m.has("image") and m.has("reply") and m.has("text")
    assert not m.has("voice")
    kinds = list(m)
    assert kinds == [("text", "你好"), ("at", "123"), ("image", "http://a/b.png")]


def test_bot_message_iter_empty():
    assert list(BotMessage()) == [] and not BotMessage()


# ---------- Transformer（下层）CQ 阉割 ----------
def test_extract_text_cq_stripped():
    raw = "Hi [CQ:at,qq=10001] 世界 [CQ:image,url=http://x/i.png]"
    # 文本提取跳过段（其余由 at/images 结构化提供）
    assert "10001" not in extract_text(raw)
    assert extract_text([{"type": "text", "data": {"text": "abc"}},
                         {"type": "image", "data": {"file": "x.png"}}]) == "abc"


def test_extract_at_images_reply():
    raw = "[CQ:at,qq=10001] 来图 [CQ:image,url=http://x/i.png] [CQ:reply,id=777]"
    assert extract_at_list(raw) == ["10001"]
    assert extract_images(raw) == ["http://x/i.png"]
    assert extract_reply_id(raw) == "777"
    # 段数组形态
    arr = [{"type": "at", "data": {"qq": "10002"}},
           {"type": "reply", "data": {"id": "888"}},
           {"type": "image", "data": {"url": "u"}}]
    assert extract_at_list(arr) == ["10002"]
    assert extract_reply_id(arr) == "888"
    assert extract_images(arr) == ["u"]


# ---------- OneBot raw → 领域 BotEvent ----------
def test_to_bot_event_group_message():
    raw = {"post_type": "message", "message_type": "group", "group_id": 1, "user_id": 2,
           "message_id": 3, "time": 4, "message": "[CQ:at,qq=2] hello"}
    ev = to_bot_event(raw)
    assert ev.kind == "message" and ev.scope == "group"
    assert ev.is_group and not ev.is_private
    assert ev.user_id == 2 and ev.group_id == 1
    assert ev.text == " hello"   # at 段被阉割
    assert ev.at_list == ["2"]
    assert ev.message.text == " hello"


def test_to_bot_event_notice_request_lifecycle():
    assert to_bot_event({"post_type": "notice", "notice_type": "group_increase"}).kind == "notice"
    assert to_bot_event({"post_type": "request", "request_type": "friend"}).kind == "request"
    assert to_bot_event({"post_type": "meta_event", "meta_event_type": "heartbeat"}).kind == "lifecycle"


# ---------- 出站：BotMessage → OneBot 段数组（下层唯一出口） ----------
def test_to_bot_message_payload_outbound():
    m = BotMessage("hi").at(5).image("f.png").reply(77)
    segs = to_bot_message_payload(m)
    assert segs == [
        {"type": "reply", "data": {"id": 77}},
        {"type": "text", "data": {"text": "hi"}},
        {"type": "at", "data": {"qq": "5"}},
        {"type": "image", "data": {"file": "f.png"}},
    ]
    # str 透传（CQ 码由平台解析）
    assert to_bot_message_payload("plain") == "plain"

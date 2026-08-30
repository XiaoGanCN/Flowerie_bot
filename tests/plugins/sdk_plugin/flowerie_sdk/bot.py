"""插件侧 FlowerieBot：SDK 模式入口。

用法（插件 plugin.py）：
    from flowerie_sdk import FlowerieBot, command
    bot = FlowerieBot()

    @command("hello")
    async def hello(event):
        await event.reply("你好")

    def on_startup(context, api=None):
        bot.attach(api)
        bot.register()          # 上报 matchers（一次性）

    def on_message(event, api=None):
        return bot.route(event)  # SDK 路由；无匹配返回 None
"""
from typing import Any, Dict, List, Optional

from flowerie_sdk.event import BotEvent
from flowerie_sdk.matcher import collect
from flowerie_sdk.message import BotMessage


class FlowerieBot:
    def __init__(self):
        self._api = None
        self._handlers: List[tuple] = []  # (matcher定义, handler)
        self._listeners: Dict[str, List[tuple]] = {}  # post_type -> [(priority, handler)]
        self._matched_name = ""
        self._last_args = ""
        self._registered = False
        self._module = None

    # ---------- 生命周期 ----------
    def attach(self, api, module=None) -> None:
        """绑定 api；默认自动识别调用方（插件入口）模块用于 matcher 收集。"""
        self._api = api
        if module is None:
            import inspect
            for f in inspect.stack()[2:]:
                g = f.frame.f_globals
                mod_name = str(g.get("__name__") or "")
                if not mod_name.startswith("flowerie_sdk"):
                    self._module = g  # 插件模块命名空间（f_globals）
                    break
        else:
            self._module = getattr(module, "__dict__", module) if not isinstance(module, dict) else module

    def register(self) -> Optional[dict]:
        """收集插件模块级 matcher handlers 并上报主进程（幂等）。"""
        if self._api is None or self._registered:
            return None
        import inspect
        if self._module is not None:
            items = (self._module.items() if isinstance(self._module, dict) else vars(self._module).items())
            for _name, val in list(items):
                if inspect.isfunction(val):
                    for m in collect(val):
                        self._handlers.append((m, val))
        if not self._handlers:
            import sys
            for _, mod in list(sys.modules.items()):
                if mod is not None and "flowerie_plugin_" in getattr(mod, "__name__", ""):
                    for _name, val in vars(mod).items():
                        if inspect.isfunction(val):
                            for m in collect(val):
                                self._handlers.append((m, val))
        if not self._handlers:
            return None
        matchers = [{"kind": m["kind"], "pattern": m["pattern"], "priority": m["priority"],
                     "block": m["block"], "name": m["name"], "rule": m.get("rule")}
                    for m, _ in self._handlers]
        result = self._api.matcher_register(matchers)
        self._registered = True
        return result

    def listen(self, post_type: str, priority: int = 0, stop: bool = False):
        """@bot.listen("notice")——本地事件监听（主进程全量投递时过滤分发）。"""
        def wrap(func):
            self._listeners.setdefault(post_type, []).append((priority, stop, func))
            return func
        return wrap

    # ---------- 路由 ----------
    async def route(self, event_dict: Dict[str, Any]):
        """事件入口（插件 on_message/on_notice 调用）：按 matched 或监听器分发。

        返回 actions 结果列表（SDK 模式下通常为空=无动作回传）。
        """
        if self._api is None:
            return None
        event = BotEvent(event_dict, self)
        matched = event_dict.get("matched") or {}
        if isinstance(matched, list):
            matched = matched[0] if matched else {}   # 主进程按 priority 降序；命中链第一优先
        self._matched_name = str(matched.get("name") or "")
        self._last_args = str(matched.get("args") or "")
        if self._matched_name:
            for m, handler in self._handlers:
                if self._matched_name == m.get("name"):
                    await self._invoke(handler, event)
                    break
        else:
            listeners = sorted(self._listeners.get(event.post_type, []),
                               key=lambda x: x[0], reverse=True)
            for _priority, stop, handler in listeners:
                if event._stopped:
                    break
                try:
                    await self._invoke(handler, event)
                except Exception:  # noqa: BLE001 - 监听器异常隔离
                    continue
                if stop or event._stopped:
                    break
        return None

    @staticmethod
    async def _invoke(handler, event) -> None:
        import asyncio
        result = handler(event)
        if asyncio.iscoroutine(result):
            await result

    # ---------- 消息 API（经协议 action，插件不碰 OneBot） ----------
    def _r(self, result):
        if not isinstance(result, dict):
            return {}
        if result.get("ok") is False or result.get("error"):
            raise BotAPIError(str(result.get("error") or result.get("reason") or "动作失败"))
        return result

    async def send(self, target, message, *, reply_id=None) -> int:
        if self._api is None:
            raise BotAPIError("bot 未 attach")
        if isinstance(target, int) or str(target).isdigit():
            target = ("group", int(target))
        payload = {"group_id": int(target[1])} if target[0] == "group" else {"user_id": int(target[1])}
        payload["message"] = to_onebot(message)
        if reply_id:
            payload["reply_id"] = int(reply_id)
        res = self._api.send_message(payload) if target[0] == "group" else \
            self._api.send_private_message(payload)
        return int(self._r(res).get("message_id") or 0)

    async def reply(self, event, message=None, **kwargs) -> int:
        if not event.message_id:
            raise BotAPIError("事件无 message_id，无法回复")
        if event.group_id:
            res = self._api.send_reply({"group_id": int(event.group_id),
                                        "message": to_onebot(message),
                                        "reply_id": int(event.message_id)})
        else:
            res = self._api.send_private_message({"user_id": int(event.user_id),
                                                 "message": to_onebot(message)})
        return int(self._r(res).get("message_id") or 0)

    async def recall(self, message_id: int) -> None:
        self._r(self._api.delete_message({"message_id": int(message_id)}))

    async def get_message(self, message_id: int):
        return self._r(self._api.get_message({"message_id": int(message_id)}))

    async def get_context(self, group_id: int, max_messages: int = 10):
        return self._r(self._api.get_context({"group_id": int(group_id),
                                              "count": int(max_messages)}))

    async def get_group_member(self, group_id: int, user_id: int):
        return self._r(self._api.get_group_member({"group_id": int(group_id),
                                                   "user_id": int(user_id)}))

    async def get_group_members(self, group_id: int):
        return self._r(self._api.get_group_members({"group_id": int(group_id)}))

    async def is_group_admin(self, group_id: int, user_id: int) -> bool:
        res = self._r(self._api.is_group_admin({"group_id": int(group_id), "user_id": int(user_id)}))
        return bool(res.get("result", res.get("ok")))

    async def is_group_owner(self, group_id: int, user_id: int) -> bool:
        res = self._r(self._api.is_group_owner({"group_id": int(group_id), "user_id": int(user_id)}))
        return bool(res.get("result", res.get("ok")))

    async def mute(self, group_id: int, user_id: int, duration_seconds: int) -> None:
        self._r(self._api.group_ban({"group_id": int(group_id), "user_id": int(user_id),
                                     "duration": int(duration_seconds)}))

    async def kick(self, group_id: int, user_id: int) -> None:
        self._r(self._api.group_kick({"group_id": int(group_id), "user_id": int(user_id)}))


class BotAPIError(Exception):
    """SDK 动作失败（协议层错误文本）。"""


def to_onebot(message) -> Any:
    """str / BotMessage → 协议可传形式（段数组；下层在协议边界做 OneBot 拼装）。

    插件只构造领域消息：text/at/image/reply 在此转成段数组交给主进程。
    """
    if isinstance(message, BotMessage):
        segments = []
        if message.reply_id is not None:
            segments.append({"type": "reply", "data": {"id": int(message.reply_id)}})
        if message.text:
            segments.append({"type": "text", "data": {"text": message.text}})
        for uid in message.at_list:
            segments.append({"type": "at", "data": {"qq": str(uid)}})
        for img in message.images:
            segments.append({"type": "image", "data": {"file": str(img)}})
        return segments
    return message

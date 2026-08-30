"""统一 Bot 异常体系：插件无需感知底层 HTTP/OneBot 实现细节。"""


class BotError(Exception):
    """Flowerie SDK 基础异常（所有 Bot 异常基类）。"""


class BotAPIError(BotError):
    """平台 API 调用失败（OneBot HTTP 错误 / retcode != 0 / 服务端不支持）。"""


class BotTimeoutError(BotError):
    """平台 API 调用超时。"""


class BotPermissionError(BotError):
    """权限检查未通过（require_permission / matcher 规则拦截）。"""


class MessageNotFoundError(BotError):
    """消息不存在（撤回/查询时 message_id 无效）。"""


class UnsupportedOperationError(BotError):
    """当前平台/配置不支持该操作（如平台无对应 OneBot 扩展）。"""

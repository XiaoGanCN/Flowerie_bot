"""统一日志基础设施（Python 标准 logging）。

- dev 格式：人类可读（彩色时间戳 + trace_id 前缀）
- prod 格式：JSON lines（LOG_FORMAT=json），每条记录含 ts/level/logger/trace_id/event/msg 及 extra 字段
- 敏感信息脱敏：API Key / Bearer token / GitHub token / 常见 access_token 参数自动打码
- trace_id：由 TraceIdFilter 从 contextvars 注入每条记录
- 事件埋点：调用方在 extra 里带 event=<事件名>，便于 grep / 采集

用法：:

    logger.info("message_received group=%s user=%s", g, u, extra={"event": "message_received"})
"""
import json
import logging
import logging.handlers
import re
import sys
import time
from typing import Optional

from src.utils.trace import get_trace_id

# ---------- 敏感信息脱敏 ----------
_SENSITIVE_PATTERNS: list = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),                 # OpenAI 风格 API Key
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_***"),                 # GitHub token
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer ***"),     # Authorization 头
    (re.compile(r"(access_token|api_key|apikey|token)=([^&\s\"']{6,})", re.IGNORECASE), r"\1=***"),
]


def redact(text: str) -> str:
    """把文本中的敏感信息替换为占位符。"""
    if not text:
        return text
    for pattern, repl in _SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---------- Formatter ----------
# LogRecord 标准实例字段（extra 提取时排除，避免污染 JSON 输出）
_STD_RECORD_FIELDS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
})


class _BaseFormatter(logging.Formatter):
    """统一处理：脱敏 + trace_id + 结构化 extra。"""

    def _fields(self, record: logging.LogRecord) -> dict:
        fields = {}
        for key, value in record.__dict__.items():
            if key in _STD_RECORD_FIELDS:
                continue
            fields[key] = value
        return fields

    def _safe_message(self, record: logging.LogRecord) -> str:
        try:
            return redact(record.getMessage())
        except Exception:
            return redact(str(record.msg))


class TextFormatter(_BaseFormatter):
    """开发环境人类可读格式。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        trace = getattr(record, "trace_id", "") or get_trace_id() or "-"
        event = getattr(record, "event", None)
        event_part = f" [{event}]" if event else ""
        extra = self._fields(record)
        extra_part = f" {extra}" if extra else ""
        base = f"{ts} | {record.levelname:<8} | {record.name}{event_part} | trace={trace} | {self._safe_message(record)}{extra_part}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


class JsonFormatter(_BaseFormatter):
    """生产环境 JSON lines 格式。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "") or get_trace_id() or "-",
            "msg": self._safe_message(record),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        extra = self._fields(record)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TraceIdFilter(logging.Filter):
    """把当前 contextvars 中的 trace_id 注入每条 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()  # type: ignore[attr-defined]
        return True


# ---------- 初始化 ----------
def init_logging(level: str = "INFO", fmt: str = "text", log_file: Optional[str] = "logs/bot.log") -> None:
    """初始化全局日志。

    :param level:  日志级别（DEBUG/INFO/WARNING/ERROR）
    :param fmt:    "text"（人类可读）或 "json"（JSON lines）
    :param log_file: 文件日志路径；None 则只输出到 stdout
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    # 避免重复初始化
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()

    formatter: logging.Formatter
    if fmt == "json":
        formatter = JsonFormatter()
    else:
        formatter = TextFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=500 * 1024 * 1024, backupCount=3, encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            pass  # 日志目录不可写时不影响主流程

    for h in root.handlers:
        h.addFilter(TraceIdFilter())

    # 抑制第三方库的过吵日志
    for noisy in ("httpx", "httpcore", "websockets", "aiohttp", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取统一 logger（模块级使用：get_logger(__name__)）。"""
    return logging.getLogger(name)

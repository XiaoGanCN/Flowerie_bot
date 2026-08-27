"""兼容入口：旧 setup_logger 接口 -> 标准 logging 基础设施。"""
from typing import Optional

from src.utils.logging_setup import get_logger as _get_logger
from src.utils.logging_setup import init_logging


def setup_logger(level: str = "INFO", fmt: str = "text"):
    """兼容旧调用（main.py 曾调用 setup_logger(config.LOG_LEVEL)）。"""
    init_logging(level=level, fmt=fmt)
    return _get_logger()


def get_logger(name: Optional[str] = None):
    return _get_logger(name)

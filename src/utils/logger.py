import sys
from loguru import logger
from typing import Optional

def setup_logger(level: str = "INFO"):
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=level,
        colorize=True,
    )
    # 可选：写入文件
    logger.add(
        "logs/bot.log",
        rotation="500 MB",
        retention="10 days",
        level=level,
        format="{time} | {level} | {name}:{function}:{line} - {message}",
    )
    return logger

# 全局便捷函数
def get_logger() -> logger:
    return logger
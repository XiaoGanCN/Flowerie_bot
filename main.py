#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.utils.logger import setup_logger
from src.services.ai_client import AIClient
from src.services.memory_manager import MemoryManager
from src.services.file_parser import FileParser
from src.services.sender import Sender
from src.core.policy_engine import PolicyEngine
from src.core.message_router import MessageRouter
from src.core.websocket_server import WebSocketServer
from loguru import logger


async def main():
    config = load_config()
    setup_logger(config.LOG_LEVEL)

    memory_manager = MemoryManager(config.MEMORY_PATH, config.MEMORY_TTL_DAYS, config.AUDIT_LOG_PATH)

    # 优雅管理异步资源
    async with AIClient(config, memory_manager) as ai_client, Sender(config) as sender:
        file_parser = FileParser(config)
        policy_engine = PolicyEngine(config, memory_manager)
        message_router = MessageRouter(
            config=config,
            ai_client=ai_client,
            memory_manager=memory_manager,
            file_parser=file_parser,
            sender=sender,
            policy_engine=policy_engine,
        )
        ws_server = WebSocketServer(config, message_router)

        # 启动主动聊天循环（若配置允许）
        await message_router.start()

        # 启动 WebSocket 服务（会自动阻塞直到中断）
        try:
            await ws_server.run()
        finally:
            # 优雅退出前：停掉后台循环、保存最近上下文、关闭 WS 服务
            await message_router.stop()
            await ws_server.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到退出信号 (Ctrl+C)，正在关闭...")
    except Exception as e:
        logger.exception(f"运行异常: {e}")
        sys.exit(1)
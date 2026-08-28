#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config, validate_config
from src.core.message_router import MessageRouter
from src.core.policy_engine import PolicyEngine
from src.core.websocket_server import WebSocketServer
from src.repositories.settings_repository import SettingsRepository
from src.repositories.sticker_repository import StickerRepository
from src.services.ai_client import AIClient
from src.services.file_parser import FileParser
from src.services.mcp_tool_manager import McpToolManager
from src.services.memory_manager import MemoryManager
from src.services.prompt_manager import PromptManager
from src.services.sender import Sender
from src.services.sticker_manager import StickerManager
from src.utils.logging_setup import get_logger, init_logging
from src.utils.metrics import registry

logger = get_logger(__name__)



async def main():
    config = load_config()
    # 启动阶段即校验配置：类型错误/必填缺失直接报错退出
    validate_config(config)
    init_logging(level=config.LOG_LEVEL, fmt=config.LOG_FORMAT)

    logger.info("花璃启动中...", extra={"event": "startup"})

    memory_manager = MemoryManager(config.MEMORY_PATH, config.MEMORY_TTL_DAYS, config.AUDIT_LOG_PATH, config.MODEL_MEMORY_TTL_DAYS)
    settings_repo = SettingsRepository(config.SETTINGS_DB_PATH)
    prompt_manager = PromptManager(settings_repo, max_length=config.MAX_CUSTOM_PROMPT_LENGTH)

    # 优雅管理异步资源（HTTP session / AI 客户端）
    async with AIClient(config, memory_manager) as ai_client, Sender(config) as sender:
        sticker_repo = StickerRepository(config.STICKER_DB_PATH)
        sticker_manager = StickerManager(config, sticker_repo, ai_client)
        tool_manager = McpToolManager(config)
        file_parser = FileParser(config)
        policy_engine = PolicyEngine(config, memory_manager)
        message_router = MessageRouter(
            config=config,
            ai_client=ai_client,
            memory_manager=memory_manager,
            file_parser=file_parser,
            sender=sender,
            policy_engine=policy_engine,
            prompt_manager=prompt_manager,
            sticker_manager=sticker_manager,
            tool_manager=tool_manager,
        )
        ws_server = WebSocketServer(config, message_router)

        # 启动后台任务（主动聊天 / 上下文备份，经 TaskManager 统一管理）
        await message_router.start()

        # 启动 WebSocket 服务（会自动阻塞直到中断）
        try:
            await ws_server.run()
        finally:
            # ===== 优雅关闭顺序 =====
            # 1) 停止接收新任务、取消后台任务并等待
            logger.info("shutdown_started: 停止后台任务", extra={"event": "shutdown_started"})
            await message_router.stop()
            # 2) 关闭 WebSocket 服务
            await ws_server.shutdown()
            # 3) 关闭 HTTP 客户端 / 数据库连接
            await file_parser.close()
            memory_manager.close()
            settings_repo.close()
            sticker_manager.close()
            await tool_manager.close()
            # 4) 输出进程内 metrics 摘要
            logger.info(
                "shutdown metrics=%s", registry.export_text().replace("\n", " | ")[:800],
                extra={"event": "shutdown_metrics"},
            )
            logger.info("shutdown_finished", extra={"event": "shutdown_finished"})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到退出信号 (Ctrl+C)，正在关闭...")
    except Exception as e:
        logger.exception("运行异常: %s", e)
        sys.exit(1)

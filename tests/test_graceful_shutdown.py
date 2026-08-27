"""优雅关闭流程测试：Router 后台任务注册→停止→资源释放。"""
from tests.test_router_regression import build_router


async def test_router_start_stop_graceful():
    """start() 注册后台任务，stop() 全部取消并保存上下文，无泄漏。"""
    router, config, ai, sender, mm = build_router()
    await router.start()
    assert router.task_manager.running_count() >= 1
    await router.stop()
    assert router.task_manager.running_count() == 0
    # 再次 stop 幂等
    await router.stop()


async def test_router_start_stop_only_reply_when_at():
    """ONLY_REPLY_WHEN_AT=true 时不注册主动聊天任务，只保留上下文备份。"""
    from src.utils.task_manager import BackgroundTaskManager

    router, config, ai, sender, mm = build_router()
    config.ONLY_REPLY_WHEN_AT = True
    # 重建 router（ONLY_REPLY_WHEN_AT 在 start 时读取）
    tm = BackgroundTaskManager()
    router.task_manager = tm
    await router.start()
    names = tm.task_names()
    assert "active_chat" not in names
    assert "context_backup" in names
    await router.stop()


async def test_stop_persists_context_backup():
    """stop() 时执行最后一次上下文备份（崩溃恢复数据不丢）。"""
    router, config, ai, sender, mm = build_router()
    saved = {"n": 0}

    async def fake_save():
        saved["n"] += 1

    router.policy_engine.save_context_backup = fake_save
    await router.start()
    await router.stop()
    assert saved["n"] >= 1

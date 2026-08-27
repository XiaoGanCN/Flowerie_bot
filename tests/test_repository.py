"""MemoryRepository 抽象层测试：CRUD / 检索 / 清理 / 并发访问。"""
import asyncio
import os
import tempfile

import pytest

from src.repositories.base import MemoryNote
from src.repositories.sqlite_repository import SQLiteMemoryRepository


@pytest.fixture()
def repo():
    tmp = tempfile.TemporaryDirectory()
    r = SQLiteMemoryRepository(os.path.join(tmp.name, "memory.db"))
    yield r
    r.close()
    tmp.cleanup()


def _note(text, uid=1, gid=10, **kw):
    return MemoryNote(user_id=uid, group_id=gid, text=text, created_at=1000.0, **kw)


# ---------- CRUD ----------
def test_insert_and_list(repo):
    repo.insert_note(_note("喜欢喝奶茶"))
    repo.insert_note(_note("怕黑"))
    repo.commit()
    notes = repo.list_notes(1, 10)
    assert [n.text for n in notes] == ["喜欢喝奶茶", "怕黑"]
    assert notes[0].note_id is not None
    assert notes[0].confidence == "model"


def test_list_limit_recent(repo):
    for i in range(10):
        repo.insert_note(_note(f"note{i}"))
    repo.commit()
    recent = repo.list_notes(1, 10, limit=3)
    assert [n.text for n in recent] == ["note7", "note8", "note9"]


def test_search_like_escape(repo):
    repo.insert_note(_note("喜欢100%纯度的奶茶"))
    repo.insert_note(_note("喜欢_下划线"))
    repo.insert_note(_note("喜欢普通"))
    repo.commit()
    assert len(repo.search_notes(1, 10, "100%")) == 1  # % 不是通配符
    assert len(repo.search_notes(1, 10, "_下划线")) == 1  # _ 不是通配符
    assert len(repo.search_notes(1, 10, "喜欢")) == 3


def test_delete_and_count(repo):
    repo.insert_note(_note("a"))
    nid = repo.insert_note(_note("b"))
    repo.insert_note(_note("c"))
    repo.commit()
    assert repo.count_notes(1, 10) == 3
    repo.delete_note(nid)
    repo.commit()
    assert repo.count_notes(1, 10) == 2
    assert repo.delete_user_notes(1, 10) == 2
    repo.commit()
    assert repo.count_notes(1, 10) == 0


def test_trim_notes(repo):
    for i in range(10):
        repo.insert_note(_note(f"note{i}"))
    repo.commit()
    removed = repo.trim_notes(1, 10, keep=3)
    assert removed == 7
    repo.commit()
    assert [n.text for n in repo.list_notes(1, 10)] == ["note7", "note8", "note9"]


def test_iter_user_groups(repo):
    repo.insert_note(_note("a", uid=1, gid=10))
    repo.insert_note(_note("b", uid=2, gid=10))
    repo.insert_note(_note("c", uid=1, gid=20))
    repo.commit()
    assert set(repo.iter_user_groups()) == {(1, 10), (2, 10), (1, 20)}


def test_kv(repo):
    repo.kv_set(1, 10, "nickname", "小可爱")
    repo.kv_set(1, 10, "nickname", "小可爱二号")
    repo.commit()
    assert dict(repo.kv_list(1, 10)) == {"nickname": "小可爱二号"}
    assert repo.kv_list(2, 10) == []


def test_user_group_isolation(repo):
    repo.insert_note(_note("属于用户1"))
    repo.insert_note(_note("属于用户2", uid=2))
    repo.commit()
    assert [n.text for n in repo.list_notes(1, 10)] == ["属于用户1"]
    assert [n.text for n in repo.list_notes(2, 10)] == ["属于用户2"]


# ---------- 并发访问 ----------
async def test_concurrent_writes_same_loop(repo):
    """同一事件循环内并发写入（模拟多消息并发），不丢数据不抛错。"""
    async def writer(i):
        repo.insert_note(_note(f"并发{i}", uid=1, gid=10))
        repo.commit()

    await asyncio.gather(*(writer(i) for i in range(50)))
    assert repo.count_notes(1, 10) == 50


async def test_concurrent_writes_threads(repo):
    """to_thread 并发写（模拟 save() 路径），锁保证不损坏。"""
    import threading

    errors = []

    def writer(i):
        try:
            repo.insert_note(_note(f"线程{i}", uid=1, gid=10))
            repo.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert repo.count_notes(1, 10) == 20


async def test_memory_manager_with_injected_repository():
    """MemoryManager 可通过注入 repository 工作（业务层不感知 SQL）。"""
    from src.services.memory_manager import MemoryManager

    tmp = tempfile.TemporaryDirectory()
    try:
        r = SQLiteMemoryRepository(os.path.join(tmp.name, "m.db"))
        mm = MemoryManager(os.path.join(tmp.name, "unused.json"), repository=r)
        await mm.append_memory_text(1, 10, "喜欢喝奶茶")
        await mm.append_memory_text(1, 10, "喜欢喝奶茶")  # 去重
        await mm.append_memory_text(1, 10, "怕黑")
        assert mm.get_user_notes(1, 10) == ["喜欢喝奶茶", "怕黑"]
        mm.close()
    finally:
        tmp.cleanup()

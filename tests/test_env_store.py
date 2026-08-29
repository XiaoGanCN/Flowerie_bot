"""EnvFileStore 测试：.env 增量更新（保留变量/注释/格式、特殊字符、原子写、并发）。"""
import os
import tempfile
import threading

from src.repositories.env_store import EnvFileStore


def _store(tmp: str, content: str = "") -> EnvFileStore:
    path = os.path.join(tmp, ".env")
    if content:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return EnvFileStore(path)


def _read(tmp: str) -> str:
    with open(os.path.join(tmp, ".env"), "r", encoding="utf-8", newline="") as f:
        return f.read()


# ---------- 基本替换 ----------
def test_update_replaces_value_keeps_others():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "FOO=123\nBAR=hello\nBAZ=true\n")
        store.update({"FOO": "456"})
        text = _read(td)
        assert "FOO=456" in text
        assert "BAR=hello" in text
        assert "BAZ=true" in text
        assert store.read_values() == {"FOO": "456", "BAR": "hello", "BAZ": "true"}


# ---------- 注释保留 ----------
def test_update_keeps_full_line_comments():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "# AI Configuration\nDEEPSEEK_API_KEY=xxx\n\n# Bot\nBOT_QQ=1\n")
        store.update({"DEEPSEEK_API_KEY": "yyy"})
        text = _read(td)
        assert "# AI Configuration" in text
        assert "# Bot" in text
        assert "\n\n" in text  # 空行保留
        assert "DEEPSEEK_API_KEY=yyy" in text


def test_update_keeps_inline_comment():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "FOO=123  # 必填\nBAR=hello\n")
        store.update({"FOO": "456"})
        text = _read(td)
        assert "FOO=456  # 必填" in text
        assert "BAR=hello" in text


# ---------- 格式保留 ----------
def test_update_keeps_indent_and_export():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "  export FOO=1\nBAR=2\n")
        store.update({"FOO": "9"})
        text = _read(td)
        assert "  export FOO=9" in text
        assert "BAR=2" in text


def test_update_no_trailing_newline_preserved_on_pure_replace():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "FOO=1\nBAR=2")
        store.update({"FOO": "3"})
        assert _read(td) == "FOO=3\nBAR=2"  # 无末尾换行，纯替换时保留


def test_update_crlf_preserved():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "FOO=1\r\nBAR=2\r\n")
        store.update({"FOO": "3"})
        text = _read(td)
        assert text == "FOO=3\r\nBAR=2\r\n"
        assert "\r\r" not in text  # 不能出现双重回车


# ---------- 特殊字符 ----------
def test_special_chars_roundtrip():
    values = {
        "A_SPACE": "hello world",
        "B_HASH": "a#b",
        "C_EQ": "a=b",
        "D_QUOTE": 'say "hi"',
        "E_CN": "花璃爱吃🍰",
        "F_EMPTY": "",
        "G_BACKSLASH": r"path\with\slashes",
    }
    with tempfile.TemporaryDirectory() as td:
        store = _store(td)
        store.update(values)
        assert store.read_values() == values


def test_multiline_value_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td)
        store.update({"POKE": "line1\nline2\nline3"})
        assert store.read_values() == {"POKE": "line1\nline2\nline3"}


def test_multiline_existing_value_replaced_cleanly():
    """多行引号值被整体替换为单行，不残留续行。"""
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, 'GREETING="hello\nworld"\nFOO=1\n')
        store.update({"GREETING": "hi"})
        text = _read(td)
        assert 'GREETING=hi' in text
        assert "world" not in text  # 续行被移除
        assert "FOO=1" in text


def test_append_new_key_at_end():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "FOO=1\n")
        store.update({"BAR": "2", "BAZ": "3"})
        text = _read(td)
        assert text == "FOO=1\nBAR=2\nBAZ=3\n"
        assert store.read_values() == {"FOO": "1", "BAR": "2", "BAZ": "3"}


def test_create_missing_file():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td)  # 文件不存在
        store.update({"NEW_KEY": "value with spaces"})
        assert store.read_values() == {"NEW_KEY": "value with spaces"}


# ---------- 编码函数 ----------
def test_encode_value():
    assert EnvFileStore._encode_value("plain") == "plain"
    assert EnvFileStore._encode_value("") == ""
    assert EnvFileStore._encode_value("a b") == '"a b"'
    assert EnvFileStore._encode_value("a#b") == '"a#b"'
    assert EnvFileStore._encode_value("a=b") == '"a=b"'
    assert EnvFileStore._encode_value('a"b') == '"a\\"b"'
    assert EnvFileStore._encode_value("l1\nl2") == '"l1\\nl2"'
    assert EnvFileStore._encode_value("中文") == '"中文"'


# ---------- 原子写入 ----------
def test_atomic_write_no_leftover_temp():
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "FOO=1\n")
        store.update({"FOO": "2"})
        leftovers = [f for f in os.listdir(td) if f.endswith(".tmp") or f.startswith(".env.")]
        assert leftovers == []
        assert os.path.exists(os.path.join(td, ".env"))


def test_concurrent_updates_no_loss():
    """多个线程同时更新不同键：.env 不损坏，全部键都在。"""
    with tempfile.TemporaryDirectory() as td:
        store = _store(td, "BASE=0\n")
        errors = []

        def worker(i):
            try:
                for _ in range(20):
                    store.update({f"KEY{i}": str(i)})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        values = store.read_values()
        assert values["BASE"] == "0"
        for i in range(8):
            assert values[f"KEY{i}"] == str(i)
        # 文件内容仍是合法行结构
        text = _read(td)
        for line in text.splitlines():
            assert "=" in line or line.strip() == "" or line.strip().startswith("#")

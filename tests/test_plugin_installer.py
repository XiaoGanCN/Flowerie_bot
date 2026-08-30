"""PluginInstaller 安全测试（白盒 + 黑盒）：ZIP Slip / Bomb / Symlink / 穿越 /
manifest 注入 / URL 下载 SSRF / 重定向拒绝 / 大小限制 / 超时。"""
import io
import json
import zipfile

import pytest

from src.plugins.installer import PluginInstaller, PluginInstallError


def _manifest_bytes(**over):
    data = {
        "id": "test_plugin", "name": "Test Plugin", "version": "1.0.0",
        "author": "t", "description": "d", "runtime": "python", "entry": "plugin.py",
        "api_version": "1", "permissions": [], "config": {},
    }
    data.update(over)
    return json.dumps(data, ensure_ascii=False).encode()


def _make_zip(entries, manifest_bytes=None):
    """entries: list[(name, bytes, is_symlink)]"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if manifest_bytes is not None:
            zf.writestr("manifest.json", manifest_bytes)
        for name, data, is_symlink in entries:
            info = zipfile.ZipInfo(name)
            if is_symlink:
                info.external_attr = (0o120000 << 16) | 0o644
            else:
                info.external_attr = (0o100644 << 16)
            zf.writestr(info, data)
    buf.seek(0)
    return buf.getvalue()


def _installer(tmp_path, **over):
    kw = dict(plugins_dir=str(tmp_path), max_zip_bytes=1024 * 1024,
              max_unzipped_bytes=1024 * 1024, max_files=50, max_depth=8,
              max_entry_bytes=512 * 1024)
    kw.update(over)
    return PluginInstaller(**kw)


# ---------- 正常安装 ----------
def test_install_valid_zip(tmp_path):
    inst = _installer(tmp_path)
    z = _make_zip([("plugin.py", b"def on_message(): pass\n", False)], manifest_bytes=_manifest_bytes())
    m = inst.install_from_bytes(z)
    assert m.id == "test_plugin"
    assert (tmp_path / "test_plugin" / "plugin.py").is_file()
    assert (tmp_path / "test_plugin" / "manifest.json").is_file()


def test_install_single_prefix_dir(tmp_path):
    inst = _installer(tmp_path)
    z = _make_zip([
        ("pkg/plugin.py", b"def on_message(): pass\n", False),
        ("pkg/manifest.json", _manifest_bytes(), False),
        ("pkg/README.md", b"hi", False),
    ])
    m = inst.install_from_bytes(z)
    assert m.id == "test_plugin"
    assert (tmp_path / "test_plugin" / "plugin.py").is_file()
    assert (tmp_path / "test_plugin" / "README.md").is_file()
    assert not (tmp_path / "test_plugin" / "pkg").exists()


def test_install_single_json_declarative(tmp_path):
    inst = _installer(tmp_path)
    data = _manifest_bytes(runtime="json", entry="", declarations=[])
    m = inst.install_from_bytes(data)
    assert m.runtime == "json"
    assert (tmp_path / "test_plugin" / "manifest.json").is_file()


# ---------- ZIP Slip / Path Traversal ----------
@pytest.mark.parametrize("bad_name", [
    "../evil.txt",                  # 真穿越
    "a/../../evil.txt",
    "/abs/evil.txt",                # 绝对路径
    "dir\\evil.txt",                # 反斜杠
    "C:/windows/evil.txt",
])
def test_zip_slip_rejected(tmp_path, bad_name):
    inst = _installer(tmp_path)
    z = _make_zip([(bad_name, b"evil", False)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="穿越|绝对路径|反斜杠|非法"):
        inst.install_from_bytes(z)
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "test_plugin").exists()


# ---------- Symlink Escape ----------
def test_symlink_rejected(tmp_path):
    inst = _installer(tmp_path)
    z = _make_zip([("plug", b"/storage/emulated/0", True),
                   ("plugin.py", b"x", False)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="符号链接"):
        inst.install_from_bytes(z)


def test_symlink_entry_rejected_after_extract(tmp_path):
    """入口文件为符号链接 → 拒绝（即便成员名合法）。"""
    inst = _installer(tmp_path)
    z = _make_zip([("plugin.py", b"x", True)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="符号链接|入口"):
        inst.install_from_bytes(z)


# ---------- Zip Bomb ----------
def test_zip_bomb_unzipped_size_limit(tmp_path):
    inst = _installer(tmp_path, max_unzipped_bytes=10 * 1024)
    big = b"\x00" * (512 * 1024)  # 高度可压缩 → 小 zip，大解压体积
    z = _make_zip([("plugin.py", big, False)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="Zip Bomb|解压后总大小"):
        inst.install_from_bytes(z)


def test_zip_bomb_file_count_limit(tmp_path):
    inst = _installer(tmp_path, max_files=3)
    z = _make_zip([(f"f{i}.txt", b"x", False) for i in range(6)],
                  manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="文件数"):
        inst.install_from_bytes(z)


def test_zip_bomb_max_zip_bytes(tmp_path):
    inst = _installer(tmp_path, max_zip_bytes=1000)
    z = _make_zip([("plugin.py", b"y" * 5000, False)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="大小上限"):
        inst.install_from_bytes(z)


def test_directory_depth_limit(tmp_path):
    inst = _installer(tmp_path, max_depth=2)
    z = _make_zip([("a/b/c/d/plugin.py", b"x", False)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="深度"):
        inst.install_from_bytes(z)


# ---------- Manifest 注入与入口检查 ----------
def test_manifest_injection_rejected(tmp_path):
    inst = _installer(tmp_path)
    z = _make_zip([("plugin.py", b"x", False)],
                  manifest_bytes=_manifest_bytes(evil_field="drop tables"))
    with pytest.raises(PluginInstallError, match="未知字段|field"):
        inst.install_from_bytes(z)


def test_entry_missing_rejected(tmp_path):
    inst = _installer(tmp_path)
    z = _make_zip([("other.py", b"x", False)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="入口文件不存在"):
        inst.install_from_bytes(z)


def test_entry_size_limit(tmp_path):
    inst = _installer(tmp_path, max_entry_bytes=100)
    z = _make_zip([("plugin.py", b"x" * 5000, False)], manifest_bytes=_manifest_bytes())
    with pytest.raises(PluginInstallError, match="入口文件"):
        inst.install_from_bytes(z)


def test_duplicate_install_rejected(tmp_path):
    inst = _installer(tmp_path)
    z = _make_zip([("plugin.py", b"x", False)], manifest_bytes=_manifest_bytes())
    inst.install_from_bytes(z)
    with pytest.raises(PluginInstallError, match="已存在"):
        inst.install_from_bytes(z)


# ---------- URL 下载：SSRF / 重定向 / 大小 / 类型 / 超时 ----------
class _FakeResp:
    def __init__(self, status, headers, chunks, eof=False):
        self.status_code = status
        self.headers = headers
        self._chunks = list(chunks)
        self._eof = eof

    async def aiter_bytes(self, _size):
        for c in self._chunks:
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _fake_client_monkey(monkeypatch, resp, downloader):
    """monkeypatch 掉 DNS/URL 校验与 httpx.AsyncClient：安装器全程走假 client（黑盒可控）。"""
    import httpx

    import src.plugins.installer as inst_mod

    async def _ok_dns(parts):
        return True, ""

    class _FakeClient:
        """httpx.AsyncClient 替身：stream() 返回预设响应（可配置状态/头/字节流）。"""

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def stream(self, *a, **k):
            return resp

    monkeypatch.setattr(inst_mod, "validate_mcp_server_url", lambda u, allowed=None: (True, ""))
    monkeypatch.setattr(downloader, "_check_dns", _ok_dns)
    # installer 在函数内 import httpx（同一模块对象）：替换其 AsyncClient 即全链路生效
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_url_install_rejects_loopback(tmp_path):
    inst = _installer(tmp_path)
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("http://127.0.0.1/plugin.zip")
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("http://localhost/plugin.zip")
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("http://192.168.1.10/plugin.zip")
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("http://[::1]/plugin.zip")
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("http://10.0.0.1/plugin.zip")
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("http://myhost.local/plugin.zip")


@pytest.mark.asyncio
async def test_url_install_extension_and_scheme(tmp_path):
    inst = _installer(tmp_path)
    with pytest.raises(PluginInstallError, match=".zip 或 .json"):
        await inst.install_from_url("https://example.com/plugin.exe")
    with pytest.raises(PluginInstallError, match=".zip 或 .json"):
        await inst.install_from_url("https://example.com/plugin")
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("ftp://example.com/plugin.zip")
    with pytest.raises(PluginInstallError, match="SSRF"):
        await inst.install_from_url("https://user:pass@example.com/plugin.zip")


@pytest.mark.asyncio
async def test_url_install_redirect_rejected(monkeypatch, tmp_path):
    inst = _installer(tmp_path)
    resp = _FakeResp(302, {"content-type": "text/html"}, [b"<html>moved</html>"])
    await _fake_client_monkey(monkeypatch, resp, inst)
    with pytest.raises(PluginInstallError, match="重定向"):
        await inst.install_from_url("http://127.0.0.1/plugin.zip")


@pytest.mark.asyncio
async def test_url_install_content_length_rejected(monkeypatch, tmp_path):
    inst = _installer(tmp_path, download_max_bytes=1000)
    resp = _FakeResp(200, {"content-length": "1099511627776", "content-type": "application/zip"}, [])
    await _fake_client_monkey(monkeypatch, resp, inst)
    with pytest.raises(PluginInstallError, match="Content-Length"):
        await inst.install_from_url("http://127.0.0.1/plugin.zip")


@pytest.mark.asyncio
async def test_url_install_stream_abort_over_limit(monkeypatch, tmp_path):
    inst = _installer(tmp_path, download_max_bytes=1000)
    chunks = [b"x" * 500] * 10  # 无 Content-Length，流式累计超限中止
    resp = _FakeResp(200, {"content-type": "application/zip"}, chunks)
    await _fake_client_monkey(monkeypatch, resp, inst)
    with pytest.raises(PluginInstallError, match="超出上限"):
        await inst.install_from_url("http://127.0.0.1/plugin.zip")


@pytest.mark.asyncio
async def test_url_install_content_type_rejected(monkeypatch, tmp_path):
    inst = _installer(tmp_path, download_max_bytes=100 * 1024)
    resp = _FakeResp(200, {"content-type": "text/html"}, [b"<html>evil</html>"])
    await _fake_client_monkey(monkeypatch, resp, inst)
    with pytest.raises(PluginInstallError, match="内容类型"):
        await inst.install_from_url("http://127.0.0.1/plugin.zip")


@pytest.mark.asyncio
async def test_url_install_success(monkeypatch, tmp_path):
    inst = _installer(tmp_path, download_max_bytes=100 * 1024)
    z = _make_zip([("plugin.py", b"def on_message(): pass\n", False)], manifest_bytes=_manifest_bytes())
    resp = _FakeResp(200, {"content-type": "application/zip"}, [z])
    await _fake_client_monkey(monkeypatch, resp, inst)
    m = await inst.install_from_url("http://127.0.0.1/plugin.zip")
    assert m.id == "test_plugin"
    assert (tmp_path / "test_plugin" / "plugin.py").is_file()

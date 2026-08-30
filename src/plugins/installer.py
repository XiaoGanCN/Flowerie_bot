"""PluginInstaller：插件安全安装（本地文件 / URL 下载 / ZIP 解包）。

安全防线（全部强制执行，任何保护级别不豁免）：
1. ZIP Slip / Path Traversal：成员名禁止绝对路径与 .. 段；解包时再次做
   realpath 前缀校验（纵深防御）
2. Zip Bomb：压缩文件大小 / 解压后总大小 / 文件数 / 目录深度 四重上限
3. Symlink Escape：拒绝任何符号链接成员（不创建、不跟随）
4. Manifest Injection：manifest 全字段 schema 校验（manifest.PluginManifest）
5. 入口文件检查：entry 必须存在、是安全相对路径、普通文件（非链接）、大小受限
6. URL 下载（继承 MCP 同款 SSRF 思想）：
   - scheme 仅 http/https；禁止 userinfo；回环/私网/链路本地/组播/保留/0.0.0.0
     与 .local/.localhost 主机后缀一律拒绝（validate_mcp_server_url 复用）
   - **DNS 解析结果再校验**（validate_mcp_resolved_ips 复用，防 DNS rebinding）
   - redirect 一律拒绝（follow_redirects=False，3xx 判失败）
   - Content-Length 预检 + 流式累计大小双保险（超限即中止）
   - 大小 / 超时 / 扩展名 / Content-Type 检查
"""
import asyncio
import io
import json
import os
import shutil
import tempfile
import zipfile
from typing import Optional, Tuple
from urllib.parse import urlsplit

from src.core.sanitizer import validate_mcp_resolved_ips, validate_mcp_server_url
from src.plugins.manifest import PluginManifest, PluginManifestError
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# 默认资源上限（均可由构造参数覆盖）
DEFAULT_MAX_ZIP_BYTES = 5 * 1024 * 1024          # 压缩包大小上限 5MB
DEFAULT_MAX_UNZIPPED_BYTES = 50 * 1024 * 1024    # 解压后总大小上限 50MB
DEFAULT_MAX_FILES = 200                          # 文件数上限
DEFAULT_MAX_DEPTH = 16                           # 目录深度上限
DEFAULT_MAX_ENTRY_BYTES = 1024 * 1024            # 单个条目（入口文件）上限 1MB
DEFAULT_DOWNLOAD_MAX_BYTES = 5 * 1024 * 1024     # URL 下载最大字节数
DEFAULT_DOWNLOAD_TIMEOUT = 15.0                  # URL 下载超时（秒）
DEFAULT_DOWNLOAD_MAX_REDIRECTS = 0               # 重定向：0 = 一律拒绝（防 redirect SSRF）
_ALLOWED_DOWNLOAD_EXT = (".zip", ".json")
_ALLOWED_CONTENT_TYPES = (
    "application/zip", "application/x-zip-compressed", "application/octet-stream",
    "application/json", "text/json", "application/x-json", "text/plain",
)
_MAX_CONTENT_TYPE_LEN = 64

# 允许的 manifest 字段名（manifest 校验同款；此表用于提示）
_MANIFEST_STALE_ERROR = "manifest 字段非法"


def _norm_name(filename: str) -> str:
    """ZIP 成员名规范化：去 ./ 前缀与尾部 '/'，统一用正斜杠。"""
    name = str(filename).replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/")


def _looks_like_json(data: bytes) -> bool:
    """判断上传字节是否像 JSON（首非空白字符为 { 或 [）。"""
    text = data[:512].lstrip()
    return text.startswith((b"{", b"["))


def _quick_manifest(data: bytes) -> Optional[PluginManifest]:
    """尝试把字节直接解析为 manifest（失败返回 None，走 ZIP 路径）。"""
    try:
        return PluginManifest.from_dict(json.loads(data.decode("utf-8")))
    except (PluginManifestError, UnicodeDecodeError, ValueError):
        return None


class PluginInstallError(Exception):
    """插件安装失败（携带可展示的拒绝原因）。"""


class PluginInstaller:
    """安全安装器：输入（字节 / 文件 / URL）→ 校验 → 解包到插件目录 → 返回 manifest。"""

    def __init__(self, plugins_dir: str,
                 max_zip_bytes: int = DEFAULT_MAX_ZIP_BYTES,
                 max_unzipped_bytes: int = DEFAULT_MAX_UNZIPPED_BYTES,
                 max_files: int = DEFAULT_MAX_FILES,
                 max_depth: int = DEFAULT_MAX_DEPTH,
                 max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
                 download_max_bytes: int = DEFAULT_DOWNLOAD_MAX_BYTES,
                 download_timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
                 download_max_redirects: int = DEFAULT_DOWNLOAD_MAX_REDIRECTS,
                 max_plugins: int = 100):
        self.plugins_dir = os.path.abspath(plugins_dir)
        self.max_zip_bytes = int(max_zip_bytes)
        self.max_unzipped_bytes = int(max_unzipped_bytes)
        self.max_files = int(max_files)
        self.max_depth = int(max_depth)
        self.max_entry_bytes = int(max_entry_bytes)
        self.download_max_bytes = int(download_max_bytes)
        self.download_timeout = float(download_timeout)
        self.download_max_redirects = int(download_max_redirects)
        self.max_plugins = int(max_plugins)

    # ---------- 入口 ----------
    def install_from_bytes(self, data: bytes, source: str = "upload",
                           filename: str = "") -> PluginManifest:
        """从字节流安装（Web UI 上传 / 文件路径读取的公共路径）。"""
        if not data:
            raise PluginInstallError("上传内容为空")
        # 兼容裸 manifest.json（JSON 文件上传）
        if _looks_like_json(data):
            manifest = _quick_manifest(data)
            if manifest is not None:
                return self._install_single_json(manifest, source)
        return self._install_zip(io.BytesIO(data), source)

    def install_from_file(self, path: str, source: str = "file") -> PluginManifest:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise PluginInstallError(f"读取插件文件失败: {e}") from e
        return self.install_from_bytes(data, source=source, filename=os.path.basename(path))

    async def install_from_url(self, url: str, source: str = "url") -> PluginManifest:
        """URL 下载安装（SSRF / 大小 / 重定向 / 类型全部受控；绝不 requests.get 后直接执行）。"""
        import httpx

        url = (url or "").strip()
        if not url:
            raise PluginInstallError("URL 为空")
        parts = urlsplit(url)
        ext = os.path.splitext(parts.path)[1].lower()
        if ext not in _ALLOWED_DOWNLOAD_EXT:
            raise PluginInstallError(
                f"URL 必须指向 .zip 或 .json 插件包（当前扩展名: {ext or '无'}）")
        # SSRF 防线①：字面量校验（scheme/userinfo/回环/私网/主机后缀）
        ok, reason = validate_mcp_server_url(url)
        if not ok:
            raise PluginInstallError(f"插件 URL 不合法（SSRF 防护）: {reason}")
        # SSRF 防线②：DNS 解析结果校验（防 DNS rebinding）
        ok, reason = await self._check_dns(parts)
        if not ok:
            raise PluginInstallError(f"插件 URL DNS 解析不合法（SSRF 防护）: {reason}")
        try:
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.download_timeout),
                    follow_redirects=False,  # 3xx 一律拒绝：不做二次跳转
                    max_redirects=self.download_max_redirects) as client:
                with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as buf:
                    total = 0
                    content_type = ""
                    async with client.stream("GET", url, headers={
                            "User-Agent": "flowerie-plugin-installer/1.2.0"}) as resp:
                        if resp.status_code >= 300:
                            raise PluginInstallError(
                                f"下载失败（HTTP {resp.status_code}，重定向一律拒绝）")
                        if resp.status_code != 200:
                            raise PluginInstallError(f"下载失败（HTTP {resp.status_code}）")
                        content_type = (resp.headers.get("content-type", "") or "").split(";")[0].strip()
                        length_header = resp.headers.get("content-length")
                        if length_header is not None:
                            try:
                                declared = int(length_header)
                            except ValueError:
                                declared = None
                            if declared is not None and declared > self.download_max_bytes:
                                raise PluginInstallError(
                                    f"Content-Length 超出下载上限（{declared} > {self.download_max_bytes}）")
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            total += len(chunk)
                            if total > self.download_max_bytes:
                                raise PluginInstallError(
                                    f"下载大小超出上限（>{self.download_max_bytes} 字节，已中止）")
                            buf.write(chunk)
                    buf.seek(0)
                    data = buf.read()
        except httpx.HTTPError as e:
            raise PluginInstallError(f"下载失败: {type(e).__name__}") from e
        # Content-Type 检查（缺省/未知类型按 octet-stream 处理，html 明确拒绝）
        if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
            raise PluginInstallError(f"下载内容类型不被允许: {content_type[:_MAX_CONTENT_TYPE_LEN]}")
        if not data:
            raise PluginInstallError("下载内容为空")
        return self.install_from_bytes(data, source=source, filename=parts.path.split("/")[-1])

    # ---------- 内部：ZIP 安全解包 ----------
    def _install_zip(self, buf: io.BytesIO, source: str) -> PluginManifest:
        try:
            size = len(buf.getvalue())
        except (AttributeError, TypeError):
            size = self.max_zip_bytes + 1
        if size > self.max_zip_bytes:
            raise PluginInstallError(
                f"插件包超过大小上限（{size} > {self.max_zip_bytes} 字节）")
        if not zipfile.is_zipfile(buf):
            raise PluginInstallError("上传文件不是合法的 ZIP 插件包")
        try:
            zf = zipfile.ZipFile(buf)
        except zipfile.BadZipFile as e:
            raise PluginInstallError(f"ZIP 损坏: {e}") from e
        try:
            with zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                if not infos:
                    raise PluginInstallError("ZIP 为空")
                if len(infos) > self.max_files:
                    raise PluginInstallError(f"ZIP 文件数超过上限（{len(infos)} > {self.max_files}）")
                total_unzipped = 0
                for info in infos:
                    total_unzipped += info.file_size
                    if total_unzipped > self.max_unzipped_bytes:
                        raise PluginInstallError(
                            f"解压后总大小超过上限（>{self.max_unzipped_bytes} 字节，拒绝 Zip Bomb）")
                    self._check_member(info)
                # 定位 manifest.json（允许整体包一个顶层目录：pkg/manifest.json）
                manifest_infos = [i for i in infos
                                  if _norm_name(i.filename) == "manifest.json"
                                  or _norm_name(i.filename).endswith("/manifest.json")]
                manifest_info = None
                prefix = ""
                candidates = sorted(manifest_infos, key=lambda i: i.filename.count("/"))
                if candidates:
                    manifest_info = candidates[0]
                    if manifest_info.filename.count("/") == 0:
                        prefix = ""
                    else:
                        prefix = _norm_name(manifest_info.filename)[: -len("manifest.json") - 1]
                        # 仅允许"统一单层前缀"
                        all_under_prefix = all(
                            _norm_name(i.filename).startswith(prefix + "/") or
                            _norm_name(i.filename) == "manifest.json" for i in infos)
                        if not all_under_prefix:
                            raise PluginInstallError("ZIP 内 manifest.json 不在根目录，且混合了多个顶层目录")
                else:
                    raise PluginInstallError("ZIP 缺少 manifest.json（插件包必须以 manifest.json 描述元数据）")
                manifest_bytes = zf.read(manifest_info)
                if len(manifest_bytes) > 64 * 1024:
                    raise PluginInstallError("manifest.json 超过大小上限（64KB）")
                try:
                    manifest = PluginManifest.from_dict(json.loads(manifest_bytes.decode("utf-8")))
                except (PluginManifestError, UnicodeDecodeError, ValueError) as e:
                    raise PluginInstallError(f"manifest 校验失败: {e}") from e
                target_dir = os.path.join(self.plugins_dir, manifest.id)
                if os.path.exists(target_dir):
                    raise PluginInstallError(f"插件 {manifest.id} 已存在（请先卸载再安装）")
                os.makedirs(target_dir, exist_ok=True)
                try:
                    for info in infos:
                        name = _norm_name(info.filename)
                        if prefix and name.startswith(prefix + "/"):
                            name = name[len(prefix) + 1:]
                        member_path = os.path.join(target_dir, name)
                        # 纵深防御：realpath 必须仍在 target_dir 下（路径穿越不可能到达这里，
                        # 但对已存在的成员名做最终校验）
                        if os.path.commonpath([os.path.realpath(target_dir), os.path.realpath(member_path)]) != os.path.realpath(target_dir):
                            raise PluginInstallError(f"非法解包目标（ZIP Slip）: {info.filename}")
                        os.makedirs(os.path.dirname(member_path), exist_ok=True)
                        with open(member_path, "wb") as out:
                            shutil.copyfileobj(zf.open(info), out)
                    # 统一的 manifest.json 落地（发现/扫描/更新都以磁盘 manifest 为准）
                    manifest_path = os.path.join(target_dir, "manifest.json")
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        f.write(manifest.to_json())
                    entry_path = os.path.join(target_dir, manifest.entry) if manifest.entry else ""
                    if manifest.runtime in ("python", "node"):
                        if not entry_path or not os.path.isfile(entry_path):
                            raise PluginInstallError(f"入口文件不存在: {manifest.entry}")
                        if os.path.islink(entry_path):
                            raise PluginInstallError("入口文件不能是符号链接")
                        if os.path.getsize(entry_path) > self.max_entry_bytes:
                            raise PluginInstallError(f"入口文件超过大小上限（{self.max_entry_bytes} 字节）")
                    # 拒绝解包目录下任何符号链接（防通过链接读取插件目录外文件）
                    for root, _dirs, files in os.walk(target_dir):
                        for fname in files:
                            fp = os.path.join(root, fname)
                            if os.path.islink(fp):
                                raise PluginInstallError(f"插件包不允许包含符号链接: {os.path.relpath(fp, target_dir)}")
                except Exception:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    raise
            logger.info("plugin_installed id=%s version=%s source=%s", manifest.id, manifest.version, source)
            return manifest
        except PluginInstallError:
            raise
        except Exception as e:  # noqa: BLE001 - 统一转为安装错误
            raise PluginInstallError(f"插件安装失败: {type(e).__name__}: {e}") from e

    def _check_member(self, info: zipfile.ZipInfo) -> None:
        """成员名/链接/深度检查（提取前）。"""
        raw = str(info.filename)
        name = _norm_name(raw)
        # 反斜杠必须在归一化前检查（_norm_name 会把 \\ 转成 /）
        if "\\" in raw:
            raise PluginInstallError(f"非法成员名（反斜杠）: {info.filename}")
        if not name or name.startswith("/") or ":" in name:
            raise PluginInstallError(f"非法成员名（绝对路径）: {info.filename}")
        if ".." in name.split("/"):
            raise PluginInstallError(f"路径穿越被拒绝（ZIP Slip）: {info.filename}")
        if name.startswith("./"):
            raise PluginInstallError(f"非法成员名: {info.filename}")
        if info.filename.count("/") > self.max_depth:
            raise PluginInstallError(f"目录深度超过上限: {info.filename}")
        # Symlink / 特殊文件（外部属性含类型位）：unix 模式下 mode & 0o170000
        mode = (info.external_attr >> 16) & 0xFFFF
        ftype = mode & 0o170000
        if ftype == 0o120000:
            raise PluginInstallError(f"符号链接被拒绝: {info.filename}")
        if ftype not in (0, 0o100000):
            raise PluginInstallError(f"特殊文件类型被拒绝: {info.filename}")

    # ---------- 内部：单文件 JSON（纯 manifest 声明式插件） ----------
    def _install_single_json(self, manifest: PluginManifest, source: str) -> PluginManifest:
        if manifest.runtime != "json":
            raise PluginInstallError(
                "单个 manifest.json 仅支持 runtime=json（Python/Node 插件必须是 ZIP 包）")
        target_dir = os.path.join(self.plugins_dir, manifest.id)
        if os.path.exists(target_dir):
            raise PluginInstallError(f"插件 {manifest.id} 已存在（请先卸载再安装）")
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write(manifest.to_json())
        logger.info("plugin_installed id=%s version=%s source=%s", manifest.id, manifest.version, source)
        return manifest

    async def _check_dns(self, parts) -> Tuple[bool, str]:
        """连接前解析主机名并校验全部解析结果（防 DNS rebinding / IPv4-mapped 绕过）。"""
        host = (parts.hostname or "").lower()
        if not host:
            return False, "host_missing"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port)
        except Exception as e:  # noqa: BLE001 - 解析失败按拒绝处理
            return False, f"dns_failed:{type(e).__name__}"
        ips = [info[4][0] for info in infos]
        return validate_mcp_resolved_ips(host, ips, [])

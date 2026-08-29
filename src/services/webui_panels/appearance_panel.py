"""Web UI 外观域处理器（主题/背景颜色/背景图片/透明度）。

从 WebUIServer 拆分（防上帝类）：外观偏好持久化于 settings.db webui_prefs。
"""
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote

from aiohttp import web

from src.services.web_ui_assets import THEMES, theme_default_alpha
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# 背景图片上传限制（服务端强制）
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB
_ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
_EXT_MAP = {"png": "png", "jpeg": "jpg", "webp": "webp", "gif": "gif"}
# 固定文件名扩展名 → Content-Type（不依赖系统 mimetypes 猜测）
_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def detect_image_type(data: bytes) -> Optional[str]:
    """按魔数检测真实图片格式（不信任扩展名/MIME）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_hex_color(value: str) -> bool:
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""))


_HEX6_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.I)
_RGBA_RE = re.compile(r"^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*[\d.]+\s*\)$", re.I)
_RGB3_RE = re.compile(r"^\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*$")


def normalize_color(value: str) -> Optional[str]:
    """把用户输入的颜色解析成标准 `#RRGGBB`。支持多种写法：
    - `#FDEEF3` / `FDEEF3`
    - `253,238,243` / `253, 238, 243`
    - `rgb(253,238,243)` / `rgba(253,238,243,0.5)`
    非法返回 None。
    """
    v = (value or "").strip()
    if not v:
        return None
    m = _HEX6_RE.match(v)
    if m:
        return "#" + m.group(1).upper()
    m = _RGB_RE.match(v) or _RGBA_RE.match(v) or _RGB3_RE.match(v)
    if m:
        nums = [int(x) for x in m.groups() if x is not None]
        if len(nums) == 3 and all(0 <= x <= 255 for x in nums):
            return "#%02X%02X%02X" % (nums[0], nums[1], nums[2])
    return None


class AppearancePanelMixin:

    async def _handle_panel_appearance_save(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        # 上传大小预检（防超大 multipart 打爆内存）
        clen = request.headers.get("Content-Length", "")
        if clen.isdigit() and int(clen) > MAX_UPLOAD_BYTES + 1_048_576:
            return web.HTTPFound(f"/panel?tab=appearance&msg={quote('文件过大（最大 5MB）')}&err=1")
        try:
            form = await request.post()
        except Exception:  # noqa: BLE001
            return web.HTTPFound(f"/panel?tab=appearance&msg={quote('表单解析失败')}&err=1")

        errors: List[str] = []
        theme = str(form.get("theme", "") or "")
        if theme and theme not in THEMES:
            errors.append("主题无效")
        # 背景颜色：严格跟主题走。**只有用户在文本输入框明确填写**才算自定义，
        # 且保存为该主题的自定义背景；文本输入框留空 = 清除该主题自定义、用主题默认色。
        # 不读取色器值（无 JS 下切主题时取色器会残留旧主题色，不能作为保存信号），
        # 这样"选黑色主题背景就该是黑"、且能清掉以前残留的浅色。
        color_text = str(form.get("bg_color_input", "") or "").strip()
        if color_text:
            bg_color = normalize_color(color_text)
            if bg_color is None:
                errors.append("背景颜色格式无效（支持 #RRGGBB 或 R,G,B 或 rgb(r,g,b)）")
                bg_color = ""
        else:
            bg_color = ""  # 用该主题默认背景
        opacity_raw = str(form.get("bg_image_opacity", "") or "100")
        try:
            opacity = int(opacity_raw)
            if not (0 <= opacity <= 100):
                raise ValueError
        except ValueError:
            errors.append("图片透明度必须是 0~100 的整数")
            opacity = 100
        panel_opacity_raw = str(form.get("panel_opacity", "") or "").strip()
        try:
            panel_opacity = int(panel_opacity_raw) if panel_opacity_raw else int(round(theme_default_alpha(theme or "default") * 100))
            if not (0 <= panel_opacity <= 100):
                raise ValueError
        except ValueError:
            errors.append("主题面板透明度必须是 0~100 的整数")
            panel_opacity = 90
        size = str(form.get("bg_size", "") or "cover")
        if size not in ("cover", "contain"):
            errors.append("图片显示方式无效")
            size = "cover"
        position = str(form.get("bg_position", "") or "center")
        if position not in ("center", "top", "bottom", "left", "right"):
            errors.append("图片位置无效")
            position = "center"

        file_field = form.get("bg_image")
        upload_data: Optional[bytes] = None
        upload_hint = ""
        if file_field is not None and getattr(file_field, "filename", ""):
            upload_hint = file_field.filename
            try:
                upload_data = file_field.file.read()
            except Exception:  # noqa: BLE001
                errors.append("读取上传文件失败")

        if errors:
            return web.HTTPFound(f"/panel?tab=appearance&msg={quote('未保存：' + '；'.join(errors))}&err=1")

        # 全部通过 → 持久化（主题/颜色/透明度/显示方式；背景色按主题隔离）
        if theme:
            self._set_pref("theme", theme)
        # bg_color 为空=清除该主题自定义色（用主题默认背景）；有值=保存为该主题背景
        self._set_pref(self._bg_color_pref_key(theme or "default"), bg_color)
        self._set_pref("bg_image_opacity", str(opacity))
        self._set_pref("bg_size", size)
        self._set_pref("bg_position", position)
        self._set_pref("panel_opacity", str(panel_opacity))
        panel_style = str(form.get("panel_style", "") or "clear").strip()
        if panel_style not in ("glass", "clear"):
            panel_style = "clear"
        self._set_pref("panel_style", panel_style)
        message = "外观设置已保存"
        if upload_data is not None:
            ok, file_msg = self._save_background_image(upload_data, upload_hint)
            if not ok:
                return web.HTTPFound(f"/panel?tab=appearance&msg={quote(file_msg)}&err=1")
            message = file_msg
        logger.info("web_ui appearance saved theme=%s", theme, extra={"event": "config_reload"})
        return web.HTTPFound(f"/panel?tab=appearance&msg={quote(message)}")

    async def _handle_panel_appearance_restore(self, request: web.Request) -> web.Response:
        """恢复默认主题/背景样式（不删除背景图片，由 delete-image 单独管理）。"""
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        self._set_pref("theme", "default")
        for k, _ in self.config_service.repository.list_prefs():
            if k.startswith("bg_color__"):
                self.config_service.repository.delete_pref(k)
        self._set_pref("panel_opacity", "")
        self._set_pref("panel_style", "clear")
        self._set_pref("bg_image_opacity", "100")
        self._set_pref("bg_size", "cover")
        self._set_pref("bg_position", "center")
        logger.info("web_ui appearance restored", extra={"event": "config_reload"})
        return web.HTTPFound(f"/panel?tab=appearance&msg={quote('已恢复默认主题与背景样式')}")

    async def _handle_panel_appearance_delete_image(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        self._delete_bg_image()
        logger.info("web_ui background image deleted", extra={"event": "config_reload"})
        return web.HTTPFound(f"/panel?tab=appearance&msg={quote('背景图片已删除')}")

    async def _handle_panel_background(self, request: web.Request) -> web.Response:
        """提供已上传的背景图片（仅管理员 token，显式 Content-Type，nosniff）。"""
        if not self._check_token(request):
            return web.Response(status=403, text="Forbidden")
        path = self._background_path()
        if path is None:
            return web.Response(status=404, text="Not Found")
        try:
            data = path.read_bytes()
        except OSError:  # noqa: BLE001
            return web.Response(status=404, text="Not Found")
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        resp = web.Response(body=data, content_type=content_type)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    def _background_dir(self) -> Path:
        return Path(self._data_dir) / "background"

    def _background_path(self) -> Optional[Path]:
        fname = self._pref("bg_image", "")
        if not fname or os.path.basename(fname) != fname or not re.fullmatch(r"background\.(png|jpg|webp|gif)", fname):
            return None
        path = self._background_dir() / fname
        return path if path.is_file() else None

    def _delete_bg_image(self) -> None:
        bg_dir = self._background_dir()
        try:
            for old in bg_dir.glob("background.*"):
                old.unlink()
        except OSError:  # noqa: BLE001
            pass
        self._set_pref("bg_image", "")

    def _save_background_image(self, data: bytes, hint_filename: str) -> Tuple[bool, str]:
        """服务端图片校验与持久化（大小/扩展名/魔数，固定安全文件名）。"""
        if len(data) > MAX_UPLOAD_BYTES:
            return False, "文件过大（最大 5MB）"
        hint_ext = os.path.splitext(hint_filename or "")[1].lstrip(".").lower()
        if hint_ext not in _ALLOWED_IMAGE_EXTS:
            return False, "文件扩展名不合法（仅允许 png/jpg/jpeg/webp/gif）"
        img_type = detect_image_type(data)
        if img_type is None:
            return False, "文件内容不是合法图片（PNG/JPEG/WEBP/GIF）"
        bg_dir = self._background_dir()
        bg_dir.mkdir(parents=True, exist_ok=True)
        # 覆盖旧图（固定文件名，绝不用用户提供的文件名 → 无路径穿越）
        for old in bg_dir.glob("background.*"):
            try:
                old.unlink()
            except OSError:  # noqa: BLE001
                pass
        target = bg_dir / f"background.{_EXT_MAP[img_type]}"
        tmp = bg_dir / ".background.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, target)
        self._set_pref("bg_image", target.name)
        return True, "背景图片已更新"

    def _bg_color_pref_key(theme: str) -> str:
        return f"bg_color__{theme}"

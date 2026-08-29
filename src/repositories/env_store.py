"""EnvFileStore：`.env` 文件的可靠增量更新（Web UI 配置持久化核心）。

设计目标（对应任务要求）：
1. 保留原有变量 —— 只替换 KEY 所在行，其余行原样保留
2. 保留注释 —— 行内注释（`KEY=value  # 说明`）替换后重新接回新值后面
3. 尽量保持原有格式 —— 未触及的行逐字节保留（含空行/缩进/注释/行尾格式）
4. 正确处理特殊字符 —— dotenv 兼容编码：空格 / `#` / `=` / 引号 / 中文 /
   换行（`\n` 转义）/ 空字符串，都按 python-dotenv 规则编解码
5. 原子写入 —— 同目录临时文件 → flush + fsync → os.replace，绝不留半个 .env
6. 并发提交 —— 线程锁串行化，多个请求同时保存不会互相覆盖

编码约定（与 pydantic-settings / python-dotenv 一致）：
- 无特殊字符的值直接写 `KEY=value`
- 含特殊字符（空格/#/=/引号/中文/换行等）用双引号包裹并转义
- 多行值写为 `KEY="line1\nline2"`（dotenv 解析器会还原为真实换行）
- 列表/复杂类型（如 POKE_REPLIES）由调用方序列化为 JSON 数组字符串
"""
import os
import re
import tempfile
import threading
from typing import Dict, List, Tuple

from dotenv import dotenv_values

# 行格式：可选缩进 + 可选 export + KEY = value
_LINE_RE = re.compile(r"^([ \t]*)(export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*)$")
# 无特殊字符、可安全不加引号直接写的值
_SAFE_UNQUOTED_RE = re.compile(r"^[A-Za-z0-9_./:@%+,\-]*$")


class EnvFileStore:
    def __init__(self, path: str = ".env"):
        self._path = path
        self._lock = threading.RLock()

    # ---------- 读取 ----------
    def read_values(self) -> Dict[str, str]:
        """解析当前 .env 的全部键值（dotenv 语义，不做 ${VAR} 插值）。文件不存在返回空。"""
        if not os.path.exists(self._path):
            return {}
        return dict(dotenv_values(self._path, interpolate=False))

    # ---------- 更新 ----------
    def update(self, updates: Dict[str, str]) -> None:
        """增量更新：只改 updates 中的键，其余行（注释/空行/其他变量）逐字节保留。

        更新采用原子写入（临时文件 + fsync + os.replace），配合线程锁保证并发安全。
        """
        if not updates:
            return
        with self._lock:
            content = self._read_text()
            newline = self._detect_newline(content)
            if content == "":
                lines: List[str] = []
                had_trailing = False
            else:
                lines = content.split("\n")
                # split 末位 "" 是末尾换行的产物，去掉它并记录原文件是否以换行结尾
                had_trailing = content.endswith("\n")
                if had_trailing:
                    lines = lines[:-1]
            pending = dict(updates)
            out: List[str] = []
            i, n = 0, len(lines)
            while i < n:
                raw = lines[i]
                body = raw[:-1] if raw.endswith("\r") else raw
                m = _LINE_RE.match(body)
                if not m:
                    out.append(body if newline == "\r\n" else raw)
                    i += 1
                    continue
                indent, export_prefix, key, value_part = m.group(1), m.group(2) or "", m.group(3), m.group(4)
                if key not in pending:
                    out.append(body if newline == "\r\n" else raw)
                    i += 1
                    continue
                end_i, comment = self._entry_span(value_part, lines, i)
                out.append(f"{indent}{export_prefix}{key}={self._encode_value(pending.pop(key))}{comment}")
                i = end_i + 1
            appended = False
            if pending:
                for k, v in pending.items():
                    out.append(f"{k}={self._encode_value(v)}")
                appended = True
            if not out:
                return
            text = newline.join(out)
            # 原文件以换行结尾、或追加了新键 → 保证末尾换行；纯替换且原文件无末尾换行则原样保留
            if had_trailing or appended:
                text += newline
            self._atomic_write(text)

    # ---------- 内部实现 ----------
    def _read_text(self) -> str:
        if not os.path.exists(self._path):
            return ""
        # newline=""：禁用通用换行转换，保留原始 \r\n（否则 CRLF 文件会被转成 \n）
        with open(self._path, "r", encoding="utf-8", newline="") as f:
            return f.read()

    @staticmethod
    def _detect_newline(content: str) -> str:
        return "\r\n" if "\r\n" in content else "\n"

    def _entry_span(self, value_part: str, lines: List[str], start_i: int) -> Tuple[int, str]:
        """确定一个条目的结束行与要保留的行内注释。

        处理多行引号值（`KEY="line1` 换行 `line2"`）：找到闭合引号所在行为止，
        整段在替换时被单行新值取代（多行值无需解码，因为我们会整体覆盖）。
        返回 (end_line_index, 注释串)。
        """
        q = value_part[:1]
        if q in ('"', "'"):
            end_i, comment = self._find_quote_end(q, value_part, lines, start_i)
            return end_i, comment
        # 单行条目：剥离行内注释（引号外的 ` #` 起点）
        return start_i, self._split_inline_comment(value_part)[1]

    def _find_quote_end(self, quote: str, first_val: str, lines: List[str], start_i: int) -> Tuple[int, str]:
        """从首行开始找闭合引号；返回 (结束行索引, 该行注释)。

        注意跳过首字符（起始引号本身），否则会把起始引号误判为闭合引号。
        """
        i = start_i
        text = first_val[1:] if first_val.startswith(quote) else first_val
        while True:
            closed_at, closed = self._scan_quote(quote, text)
            if closed:
                # 注释只可能出现在闭合引号之后的同一行
                comment = self._split_inline_comment(text[closed_at + 1:])[1]
                return i, comment
            i += 1
            if i >= len(lines):
                # 未闭合：保守处理，当作单行条目（不会崩溃，仅注释可能丢失）
                return start_i, self._split_inline_comment(first_val)[1]
            body = lines[i][:-1] if lines[i].endswith("\r") else lines[i]
            text = body

    @staticmethod
    def _scan_quote(quote: str, text: str) -> Tuple[int, bool]:
        """扫描 text，返回 (闭合引号位置, 是否闭合)。双引号内支持 \\ 转义。"""
        esc = False
        for idx, ch in enumerate(text):
            if esc:
                esc = False
                continue
            if quote == '"' and ch == "\\":
                esc = True
                continue
            if ch == quote:
                return idx, True
        return -1, False

    @staticmethod
    def _split_inline_comment(value: str) -> Tuple[str, str]:
        """在引号外找 ` #`（或 `\t#`）起点，返回 (无注释部分, 含前导空白的注释串)。

        注释串包含 # 前的空白（如 `  # 说明`），保证替换后注释格式不变。
        """
        in_s = in_d = False
        esc = False
        for i, ch in enumerate(value):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_d:
                esc = True
                continue
            if ch == "'" and not in_d:
                in_s = not in_s
                continue
            if ch == '"' and not in_s:
                in_d = not in_d
                continue
            if not in_s and not in_d and ch == "#" and (i == 0 or value[i - 1] in " \t"):
                # 回退到 # 前的第一个空白，把空白一并算进注释
                j = i
                while j > 0 and value[j - 1] in " \t":
                    j -= 1
                return value[:j].rstrip(), value[j:]
        return value, ""

    @staticmethod
    def _encode_value(value: str) -> str:
        """dotenv 兼容编码：安全字符直接写；否则双引号包裹并转义。"""
        if value == "":
            return ""
        if _SAFE_UNQUOTED_RE.fullmatch(value):
            return value
        escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("\n", "\\n").replace("\r", "\\r"))
        return f'"{escaped}"'

    def _atomic_write(self, content: str) -> None:
        dirname = os.path.dirname(os.path.abspath(self._path)) or "."
        os.makedirs(dirname, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=dirname)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

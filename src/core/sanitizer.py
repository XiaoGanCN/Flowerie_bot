import ipaddress
import json
import re
from typing import Any, Optional, Tuple


def check_image_url(url: str, allowed_hosts: Optional[list] = None) -> Tuple[bool, str]:
    """校验图片下载 URL（SSRF 防线第一道闸，纯函数便于测试）。

    规则：
    - 空 URL 拒绝
    - 仅允许 http/https 与 data: URI（file:// ftp:// javascript: 等一律拒绝）
    - 设置 IMAGE_ALLOWED_HOSTS 时：只放行白名单主机 + loopback
      （NapCat 本地图片就是 127.0.0.1，loopback 必须放行——这是已知的信任边界）
    返回 (是否允许, 拒绝原因)。原因 '' 表示允许。
    """
    if not url:
        return False, "empty"
    if url.startswith("data:"):
        return True, ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False, f"scheme_rejected:{url[:24]}"
    if allowed_hosts:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        # loopback 放行（NapCat 本地图片就是 127.0.0.1——已知信任边界）：
        # 覆盖整个 127.0.0.0/8 与 localhost / ::1
        loopback = host in ("localhost", "::1") or (host or "").startswith("127.")
        if not loopback and host not in allowed_hosts:
            return False, f"host_rejected:{host}"
    return True, ""


def validate_mcp_server_url(url: str, allowed_hosts: Optional[list] = None) -> Tuple[bool, str]:
    """校验 MCP server URL（MCP 路径的 SSRF 防线，纯函数便于测试）。

    默认行为：MCP server 是管理员配置的**外部**服务，内网/回环/链路本地目标
    一律拒绝。若管理员通过 MCP_ALLOWED_HOSTS **显式**放行了某主机（如自建的
    内网/本机 MCP server），该主机的回环/私网限制可绕过——这是用户明确配置的
    信任边界（与 IMAGE_ALLOWED_HOSTS 放行 NapCat loopback 同模式）。

    规则：
    - 空 URL 拒绝
    - 仅允许 http / https scheme
    - 禁止 URL userinfo（user:pass@）
    - 禁止回环（localhost、127.0.0.0/8、::1）、0.0.0.0、私网 IPv4/IPv6、
      链路本地、组播、保留地址等字面量 IP 目标（除非主机在 allowed_hosts 中）
    - 主机名形态：拒绝 .local / .localhost 后缀（除非显式放行）；
      其余主机名依赖 httpx 默认不跟随重定向（mcp_client 不设置 follow_redirects）
      作为重定向边界——3xx 响应按错误处理，不会二次跳转到内网。
    返回 (是否允许, 拒绝原因)。原因 '' 表示允许。
    """
    if not url or not url.strip():
        return False, "empty"
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False, "invalid_url"
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"scheme_rejected:{scheme or 'none'}"
    if parts.username is not None or parts.password is not None:
        return False, "userinfo_rejected"
    host = (parts.hostname or "").lower()
    if not host:
        return False, "host_missing"
    allowed = set((h or "").strip().lower() for h in (allowed_hosts or []))
    # 显式白名单：用户配置放行的主机/地址直接通过（信任边界由管理员明确建立）
    if host in allowed:
        return True, ""
    if host in ("localhost", "localhost.localdomain"):
        return False, "loopback_rejected"
    if host == "0.0.0.0":
        return False, "wildcard_rejected"
    # 字面量 IP：ipaddress 统一覆盖 IPv4/IPv6 的回环/私网/链路本地/组播/保留等
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved \
                or ip.is_multicast or ip.is_unspecified:
            return False, f"ip_rejected:{host}"
    else:
        # 主机名形态：拒绝常见内网命名域后缀（DNS rebinding 目标域）
        if host.endswith(".local") or host.endswith(".localhost"):
            return False, f"host_rejected:{host}"
    return True, ""


# 已知注入/系统覆盖句式（大小写不敏感）。命中即替换为占位符，打断注入，保留上下文可读性。
_INJECTION_PATTERNS = [
    r"忽略\s*(以上|上述|之前|之前所有|所有|全部)?\s*(规则|指令|要求|提示|内容|一切)?",
    r"无视\s*(以上|上述|之前)?\s*(规则|指令|要求|内容)?",
    r"忘记\s*(你|自己)?\s*(的|是)?[^。\n]{0,6}(身份|人设|指令|规则|要求)",
    r"从现在开始\s*(你|您)?\s*(是|要|必须|当)",
    r"system\s*prompt",
    r"系统提示词",
    r"原始(的)?(指令|规则|提示词)",
    r"MEMORY_JSON",
    r"(?i)ignore\s+(all\s+)?(previous|prior)\s+(instructions|rules|prompts)",
    r"(?i)ignore\s+(everything|all)\s+(above|before)",
    r"(?i)disregard\s+(all\s+)?(previous|prior)",
    r"(?i)you\s+are\s+now\s+\w",
    r"(?i)system\s+prompt",
]

_PLACEHOLDER = "【疑似注入内容，已过滤】"


def sanitize_untrusted_text(text: str) -> Tuple[str, bool]:
    """清洗不可信文本（文件内容/转发内容/卡片内容/历史消息/图片描述）。

    返回 (清洗后文本, 是否发生过替换)。只影响"作为数据被读取"的内容，
    不影响当前正在回复的那条消息本身。
    """
    if not text:
        return text, False
    changed = False
    result = text
    # 清理控制字符与零宽/格式字符（防终端/渲染注入、防零宽字符绕过关键词过滤）
    cleaned = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b-\u200f\u2028-\u202f\ufeff\u00ad]",
        "",
        result,
    )
    if cleaned != result:
        changed = True
        result = cleaned
    # 替换已知注入句式
    for pat in _INJECTION_PATTERNS:
        new, n = re.subn(pat, _PLACEHOLDER, result, flags=re.IGNORECASE)
        if n:
            changed = True
            result = new
    return result, changed


def validate_memory_content(text: str) -> Optional[str]:
    """校验将要写入长期记忆的内容（代码层闸门）。

    返回清洗后的文本；返回 None 表示拒绝写入。即使 AI 被诱导输出恶意记忆，
    也过不了这道闸门：
    - 超长（>100 字）拒绝（记忆要求极简客观）
    - 含 QQ 号（7~12 位数字）拒绝（P1 边界）
    - 含记忆指令/命令句式拒绝（防自我复制型注入）
    - 含指令性关键词拒绝
    """
    if not text:
        return None
    t = text.strip()
    if not t:
        return None
    if len(t) > 100:
        return None
    # QQ 号（5~12 位数字，覆盖老号段）一律拒绝，防止记忆里残留他人/自己的 QQ
    if re.search(r"\d{5,12}", t):
        return None
    if re.search(r"【记忆】|记忆\s*[:：]|MEMORY_JSON", t, re.IGNORECASE):
        return None
    if re.search(r"/[a-zA-Z_]+", t):
        return None
    for kw in ("忽略", "记住", "执行", "system", "指令", "从现在开始", "忘记你是"):
        if kw in t:
            return None
    return t


def validate_mcp_resolved_ips(host: str, ips: list, allowed_hosts: Optional[list] = None) -> Tuple[bool, str]:
    """MCP DNS 解析结果策略校验（SSRF 第二道闸，纯函数便于测试）。

    在 URL 字面量校验之后，连接前解析主机名得到的**每一个** IP 都必须通过：
    - 主机在 MCP_ALLOWED_HOSTS 显式白名单内 → 放行（管理员明确建立的信任边界）
    - 否则任何解析结果落在 回环/私网/链路本地/组播/保留/未指定 一律拒绝
      （防 DNS rebinding：公网域名解析出 127.0.0.1 或 192.168.x.x 时拦截）
    - IPv4/IPv6 统一处理；IPv4-mapped IPv6（::ffff:127.0.0.1）按其映射的 IPv4 判断
    返回 (是否允许, 拒绝原因)。原因 '' 表示允许。
    """
    if not ips:
        return False, "dns_empty"
    allowed = set((h or "").strip().lower() for h in (allowed_hosts or []))
    if host in allowed:
        return True, ""
    for raw in ips:
        try:
            ip = ipaddress.ip_address(str(raw).split("%")[0])
        except ValueError:
            continue  # 解析不出有效 IP 的条目跳过（getaddrinfo 已保证是 IP）
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 → 127.0.0.1，按 IPv4 判断
        if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved \
                or ip.is_multicast or ip.is_unspecified:
            return False, f"dns_ip_rejected:{ip}"
    return True, ""


def sanitize_tool_metadata(name: str, description: str, input_schema: Any,
                           max_name: int = 64, max_desc: int = 500,
                           max_schema_chars: int = 4000) -> Tuple[str, str, Any]:
    """MCP tool 元数据按**不可信外部输入**处理（防 tool description 注入）。

    - name：仅保留安全字符，超长截断（allowlist 在调用层仍由 Python 强制）
    - description：清洗控制字符/注入句式（sanitize_untrusted_text）+ 截断
    - inputSchema：JSON 序列化大小上限，超限降级为空 object schema；
      结构异常（非 dict）也降级为空 object schema
    返回 (name, description, input_schema)。
    """
    import re as _re
    safe_name = _re.sub(r"[^A-Za-z0-9_.\-]", "_", str(name or ""))[:max_name]
    desc = str(description or "")[:max_desc * 2]
    desc, _changed = sanitize_untrusted_text(desc)
    desc = desc[:max_desc]
    schema = input_schema if isinstance(input_schema, dict) else {"type": "object", "properties": {}}
    try:
        schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        schema = {"type": "object", "properties": {}}
        schema_text = "{}"
    if len(schema_text) > max_schema_chars:
        schema = {"type": "object", "properties": {}}
    return safe_name, desc, schema

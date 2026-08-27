"""SSRF 边界测试（场景 14）：私有地址 / loopback 变体 / 白名单。"""
from src.core.sanitizer import check_image_url


def test_loopback_variants_allowed_with_whitelist():
    allowed = ["cdn.example.com"]
    # 127.0.0.0/8 全部放行
    for host in ("127.0.0.1", "127.0.0.2", "127.8.8.8", "127.255.255.255"):
        ok, reason = check_image_url(f"http://{host}:3000/img.jpg", allowed)
        assert ok, f"{host} should be allowed: {reason}"
    assert check_image_url("http://localhost:3000/img.jpg", allowed)[0]
    assert check_image_url("http://[::1]:3000/img.jpg", allowed)[0]


def test_private_ipv4_rejected_with_whitelist():
    allowed = ["cdn.example.com"]
    for host in ("10.0.0.1", "172.16.0.1", "172.32.0.1", "192.168.1.1", "0.0.0.0"):
        ok, reason = check_image_url(f"http://{host}/img.jpg", allowed)
        assert not ok, f"{host} should be rejected with whitelist: {reason}"
        assert reason.startswith("host_rejected")


def test_private_ipv6_rejected_with_whitelist():
    allowed = ["cdn.example.com"]
    for host in ("[fc00::1]", "[fe80::1]", "[::]", "[fd00::abcd]"):
        ok, reason = check_image_url(f"http://{host}/img.jpg", allowed)
        assert not ok, f"{host} should be rejected with whitelist: {reason}"


def test_no_whitelist_allows_private_but_blocks_bad_schemes():
    # 无白名单：http/https 放行（含内网——本地部署信任边界，文档已说明）
    assert check_image_url("http://192.168.1.1/x.jpg", None)[0]
    # 但 scheme 白名单仍然生效
    for bad in ("file:///etc/passwd", "ftp://192.168.1.1/x", "gopher://x", "javascript:alert(1)"):
        ok, reason = check_image_url(bad, None)
        assert not ok, f"{bad} should be rejected: {reason}"
        assert reason.startswith("scheme_rejected")


def test_whitelist_host_case_insensitive_and_port():
    allowed = ["cdn.example.com"]
    assert check_image_url("http://CDN.EXAMPLE.COM:8080/x.jpg", allowed)[0]
    assert check_image_url("https://cdn.example.com/x.jpg", allowed)[0]
    assert not check_image_url("http://evil.example.com/x.jpg", allowed)[0]


def test_data_uri_scheme_allowed():
    assert check_image_url("data:image/jpeg;base64,AAAA", None)[0]
    assert check_image_url("data:text/html;base64,AAAA", None)[0]  # scheme 层放行，大小/类型在下载层校验


def test_url_with_userinfo():
    allowed = ["cdn.example.com"]
    # 带 userinfo 的 URL：host 解析仍是白名单主机
    assert check_image_url("http://user:pass@cdn.example.com/x.jpg", allowed)[0]
    # 内网地址伪装在 userinfo 里无效（host 是白名单主机）
    assert not check_image_url("http://user@192.168.1.1@evil.com/x.jpg", allowed)[0]

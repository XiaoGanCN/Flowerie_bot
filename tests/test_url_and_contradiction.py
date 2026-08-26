import asyncio
import os
import tempfile
import unittest

from src.core.sanitizer import check_image_url
from src.services.memory_manager import MemoryManager


def run(coro):
    return asyncio.run(coro)


class TestImageUrlGuard(unittest.TestCase):
    """SSRF 攻击面验证：scheme 白名单 / 主机白名单 / loopback 信任边界逐项测试。"""

    def test_empty_rejected(self):
        ok, reason = check_image_url("")
        self.assertFalse(ok)
        self.assertEqual(reason, "empty")

    def test_scheme_allowlist(self):
        # 只允许 http/https/data:，其余协议全部拒绝
        for bad in ["file:///etc/passwd", "ftp://evil.com/a.jpg", "javascript:alert(1)", "//evil.com/a.jpg", "gopher://x"]:
            ok, reason = check_image_url(bad)
            self.assertFalse(ok, f"should reject {bad}")
            self.assertIn("scheme_rejected", reason)

    def test_http_https_allowed(self):
        self.assertTrue(check_image_url("http://example.com/a.jpg")[0])
        self.assertTrue(check_image_url("https://example.com/a.jpg")[0])

    def test_data_uri_allowed(self):
        self.assertTrue(check_image_url("data:image/jpeg;base64,AAAA")[0])

    def test_host_allowlist(self):
        allowed = ["cdn.example.com"]
        # 白名单命中
        self.assertTrue(check_image_url("http://cdn.example.com/a.jpg", allowed)[0])
        # 白名单未命中拒绝
        ok, reason = check_image_url("http://evil.com/a.jpg", allowed)
        self.assertFalse(ok)
        self.assertIn("host_rejected", reason)
        # loopback 永远放行（NapCat 本地图依赖此行为——已知信任边界）
        self.assertTrue(check_image_url("http://127.0.0.1:3000/img/1.jpg", allowed)[0])
        self.assertTrue(check_image_url("http://localhost:3000/img/1.jpg", allowed)[0])
        # 未设置白名单（None / 空列表）→ 放行所有 http/https
        self.assertTrue(check_image_url("http://evil.com/a.jpg", None)[0])
        self.assertTrue(check_image_url("http://evil.com/a.jpg", [])[0])

    def test_host_case_insensitive(self):
        self.assertTrue(check_image_url("http://CDN.EXAMPLE.COM/a.jpg", ["cdn.example.com"])[0])


class TestMemoryContradiction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "memory.json")
        self.mm = MemoryManager(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def notes(self, uid=1, gid=10):
        return [n.get("text") if isinstance(n, dict) else n for n in self.mm.get_user_memory(uid, gid).get("notes", [])]

    def test_quit_replaces_like(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(1, 10, "退游了 不打三角洲了"))
        self.assertEqual(self.notes(), ["退游了 不打三角洲了"])

    def test_unlike_replaces_like(self):
        run(self.mm.append_memory_text(1, 10, "喜欢红色"))
        run(self.mm.append_memory_text(1, 10, "现在不喜欢红色了"))
        self.assertEqual(self.notes(), ["现在不喜欢红色了"])

    def test_no_false_contradiction(self):
        # 不同主题不误伤
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(1, 10, "讨厌蚯蚓"))
        self.assertEqual(set(self.notes()), {"喜欢打三角洲", "讨厌蚯蚓"})


if __name__ == "__main__":
    unittest.main()

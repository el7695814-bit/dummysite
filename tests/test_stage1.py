import http.client
import tempfile
import unittest
from pathlib import Path

from core import banner
from core.hosts import BEGIN, END, HostsManager
from core.logs import RequestLog
from core.registry import Registry, RegistryError, normalize_domain
from core.server import DummyServer


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reg = Registry(self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalize_accepts_valid_and_rejects_invalid(self):
        self.assertEqual(normalize_domain(" Example.COM. "), "example.com")
        for bad in ["", "nodot", "bad domain.com", "a..b.com", "-x.com", "x.com/path"]:
            with self.assertRaises(RegistryError, msg=bad):
                normalize_domain(bad)

    def test_add_persists_and_reloads(self):
        self.reg.add("123.com")
        again = Registry(self.root / "data")
        self.assertIsNotNone(again.get("123.com"))
        self.assertTrue(again.get("123.com").enabled)

    def test_duplicate_rejected(self):
        self.reg.add("a.com")
        with self.assertRaises(RegistryError):
            self.reg.add("a.com")

    def test_custom_content_copied_and_removed(self):
        src = self.root / "page.html"
        src.write_text("<html><body>hi</body></html>", encoding="utf-8")
        self.reg.add("a.com", content_source=src)
        copied = self.reg.content_dir / "a.com.html"
        self.assertTrue(copied.is_file())
        self.reg.remove("a.com")
        self.assertFalse(copied.exists())

    def test_disable_and_enabled_list(self):
        self.reg.add("a.com")
        self.reg.add("b.com")
        self.reg.set_enabled("b.com", False)
        self.assertEqual(self.reg.enabled_domains(), ["a.com"])

    def test_default_page_set_and_clear(self):
        self.assertIsNone(self.reg.default_path())
        src = self.root / "default.html"
        src.write_text("<html></html>", encoding="utf-8")
        self.reg.set_default(src)
        self.assertIsNotNone(self.reg.default_path())
        self.reg.set_default(None)
        self.assertIsNone(self.reg.default_path())


class HostsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hosts = self.root / "hosts"
        self.original = "# system comment\n127.0.0.1 localhost\n::1 localhost\n"
        self.hosts.write_text(self.original, encoding="utf-8")
        self.mgr = HostsManager(self.hosts, self.root / "backup")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_adds_block_and_keeps_other_lines(self):
        self.mgr.apply(["123.com", "a.com"])
        text = self.hosts.read_text(encoding="utf-8")
        self.assertIn("127.0.0.1 localhost", text)
        self.assertIn("127.0.0.1 123.com", text)
        self.assertIn(BEGIN, text)
        self.assertIn(END, text)
        self.assertEqual(sorted(self.mgr.managed_domains()), ["123.com", "a.com"])

    def test_apply_replaces_block_without_duplicating(self):
        self.mgr.apply(["a.com"])
        self.mgr.apply(["b.com"])
        text = self.hosts.read_text(encoding="utf-8")
        self.assertEqual(text.count(BEGIN), 1)
        self.assertNotIn("a.com", text)
        self.assertIn("b.com", text)

    def test_remove_block_restores_original_content(self):
        self.mgr.apply(["a.com"])
        self.mgr.remove_block()
        self.assertEqual(self.hosts.read_text(encoding="utf-8").strip(), self.original.strip())
        self.assertFalse(self.mgr.has_block())

    def test_backup_created_once_and_restorable(self):
        self.mgr.apply(["a.com"])
        self.mgr.apply(["b.com"])
        self.assertTrue(self.mgr.backup_path.exists())
        self.assertEqual(self.mgr.backup_path.read_text(encoding="utf-8"), self.original)
        self.hosts.write_text("broken", encoding="utf-8")
        self.assertTrue(self.mgr.restore_backup())
        self.assertEqual(self.hosts.read_text(encoding="utf-8"), self.original)


class BannerTests(unittest.TestCase):
    def test_injected_after_body_open_tag(self):
        out = banner.inject(b"<html><body class='x'><p>hi</p></body></html>", "text/html; charset=utf-8")
        text = out.decode("utf-8")
        self.assertIn("dummysite-banner", text)
        self.assertLess(text.index("<body"), text.index("dummysite-banner"))
        self.assertIn("<p>hi</p>", text)

    def test_prepended_when_no_body(self):
        out = banner.inject(b"<p>hi</p>", "text/html")
        self.assertTrue(out.startswith(b"<div"))

    def test_non_html_unchanged(self):
        data = b'{"a": 1}'
        self.assertEqual(banner.inject(data, "application/json"), data)
        self.assertEqual(banner.inject(b"plain", "text/plain; charset=utf-8"), b"plain")

    def test_banner_text_is_hebrew(self):
        self.assertIn("תוכן דמה", banner.BANNER_TEXT)


class LogTests(unittest.TestCase):
    def test_record_recent_and_clear(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = RequestLog(Path(tmp.name) / "log.jsonl")
        log.record("a.com", "/", "served_default")
        log.record("b.com", "/x", "not_found")
        self.assertEqual(log.recent()[0]["domain"], "b.com")
        self.assertTrue((Path(tmp.name) / "log.jsonl").exists())
        log.clear()
        self.assertEqual(log.recent(), [])
        self.assertFalse((Path(tmp.name) / "log.jsonl").exists())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.reg = Registry(root / "data")
        cls.log = RequestLog(root / "data" / "log.jsonl")

        custom = root / "custom.html"
        custom.write_text("<html><body><h1>CUSTOM</h1></body></html>", encoding="utf-8")
        cls.reg.add("custom.com", content_source=custom)
        cls.reg.add("default.com")

        cls.reg.add("off.com")
        cls.reg.set_enabled("off.com", False)

        txt = root / "note.txt"
        txt.write_text("plain text content", encoding="utf-8")
        cls.reg.add("text.com", content_source=txt)

        cls.server = DummyServer(cls.reg, cls.log, host="127.0.0.1", port=0)
        cls.port = cls.server.address[1]
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.tmp.cleanup()

    def get(self, host, path="/"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers={"Host": host})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Type"), body

    def test_custom_html_served_with_banner(self):
        status, ctype, body = self.get("custom.com")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"CUSTOM", body)
        self.assertIn("תוכן דמה".encode("utf-8"), body)

    def test_host_with_port_is_matched(self):
        status, _, body = self.get("custom.com:8080")
        self.assertEqual(status, 200)
        self.assertIn(b"CUSTOM", body)

    def test_domain_without_custom_gets_empty_page_with_banner(self):
        status, _, body = self.get("default.com")
        self.assertEqual(status, 200)
        self.assertIn(b"<body>", body)
        self.assertIn(b"</body>", body)
        self.assertIn("תוכן דמה".encode("utf-8"), body)

    def test_plain_text_not_bannered(self):
        status, ctype, body = self.get("text.com")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", ctype)
        self.assertEqual(body, b"plain text content")

    def test_unknown_domain_is_404(self):
        status, _, _ = self.get("unknown.com")
        self.assertEqual(status, 404)

    def test_disabled_domain_is_404(self):
        status, _, _ = self.get("off.com")
        self.assertEqual(status, 404)

    def test_requests_are_logged(self):
        self.get("unknown.com", "/abc")
        last = self.log.recent(1)[0]
        self.assertEqual(last["domain"], "unknown.com")
        self.assertEqual(last["path"], "/abc")
        self.assertEqual(last["result"], "not_found")


if __name__ == "__main__":
    unittest.main()


class HostsLineEndingTests(unittest.TestCase):
    def test_crlf_file_keeps_crlf_after_apply_and_remove(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        hosts = root / "hosts"
        hosts.write_bytes(b"127.0.0.1 localhost\r\n::1 localhost\r\n")
        mgr = HostsManager(hosts, root / "backup")
        mgr.apply(["a.com"])
        self.assertNotIn(b"\r\r\n", hosts.read_bytes())
        self.assertEqual(hosts.read_bytes().count(b"\r\n"), hosts.read_bytes().count(b"\n"))
        mgr.remove_block()
        self.assertEqual(hosts.read_bytes(), b"127.0.0.1 localhost\r\n::1 localhost\r\n")

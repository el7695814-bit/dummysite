import socket
import ssl
import tempfile
import unittest
from pathlib import Path

from core.banner import BANNER_TEXT
from core.hosts import BEGIN
from core.service import Service, ServiceError
from tests.test_stage2_certs import FakeRunner


def https_get(port: int, ca_file: str, host: str, path: str = "/") -> tuple[int, bytes]:
    """Real TLS request to localhost, verifying the certificate against our CA."""
    ctx = ssl.create_default_context(cafile=ca_file)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while True:
                chunk = tls.recv(65536)
                if not chunk:
                    break
                data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    return status, body


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.hosts_file = root / "hosts"
        self.original_hosts = "127.0.0.1 localhost\n"
        self.hosts_file.write_text(self.original_hosts, encoding="utf-8")

        self.svc = Service(
            data_dir=root / "data",
            hosts_path=self.hosts_file,
            http_port=0,
            https_port=0,
            runner=FakeRunner(),
        )
        html = root / "page.html"
        html.write_text("<html><body><h1>SHOP</h1></body></html>", encoding="utf-8")
        self.svc.registry.add("shop.test", content_source=html)
        self.svc.registry.add("off.test")
        self.svc.registry.set_enabled("off.test", False)

    def tearDown(self):
        if self.svc.running:
            self.svc.stop()

    def test_start_writes_only_enabled_domains_to_hosts(self):
        self.svc.start()
        text = self.hosts_file.read_text(encoding="utf-8")
        self.assertIn(BEGIN, text)
        self.assertIn("127.0.0.1 shop.test", text)
        self.assertNotIn("off.test", text)
        self.assertIn("127.0.0.1 localhost", text)

    def test_stop_restores_hosts_exactly(self):
        self.svc.start()
        self.svc.stop()
        self.assertEqual(self.hosts_file.read_text(encoding="utf-8"), self.original_hosts)
        self.assertFalse(self.svc.running)

    def test_start_without_enabled_domains_refused(self):
        self.svc.registry.set_enabled("shop.test", False)
        with self.assertRaises(ServiceError):
            self.svc.start()
        self.assertEqual(self.hosts_file.read_text(encoding="utf-8"), self.original_hosts)

    def test_double_start_refused(self):
        self.svc.start()
        with self.assertRaises(ServiceError):
            self.svc.start()

    def test_https_serves_custom_content_with_banner_and_verified_cert(self):
        self.svc.start()
        _, https_port = self.svc.ports()
        status, body = https_get(https_port, str(self.svc.certs.ca_cert_path), "shop.test")
        self.assertEqual(status, 200)
        self.assertIn(b"SHOP", body)
        self.assertIn(BANNER_TEXT.encode("utf-8"), body)

    def test_disabled_domain_is_not_trusted_over_https(self):
        self.svc.start()
        _, https_port = self.svc.ports()
        with self.assertRaises((ssl.SSLError, OSError)):
            https_get(https_port, str(self.svc.certs.ca_cert_path), "off.test")

    def test_http_and_https_both_answer(self):
        self.svc.start()
        http_port, _ = self.svc.ports()
        conn = socket.create_connection(("127.0.0.1", http_port), timeout=5)
        conn.sendall(b"GET / HTTP/1.1\r\nHost: shop.test\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        conn.close()
        self.assertIn(b"200 OK", data)
        self.assertIn(b"SHOP", data)

    def test_dns_cache_flushed_on_start_and_stop(self):
        self.svc.start()
        self.svc.stop()
        flushes = [c for c in self.svc._runner.calls if c[:2] == ["ipconfig", "/flushdns"]]
        self.assertEqual(len(flushes), 2)

    def test_purge_removes_everything_it_created(self):
        self.svc.start()
        self.svc.stop()
        self.svc.purge()
        self.assertEqual(self.hosts_file.read_text(encoding="utf-8"), self.original_hosts)
        self.assertFalse(self.svc.certs.is_installed())
        self.assertEqual(list(self.svc.certs.certs_dir.glob("*")), [])
        self.assertFalse(self.svc.log.log_path.exists())


if __name__ == "__main__":
    unittest.main()

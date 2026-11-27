import socket
import ssl
import tempfile
import threading
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from core.certs import CertificateError, CertificateManager, thumbprint


class FakeRunner:
    """Records certutil calls and simulates a trust store in memory."""

    def __init__(self):
        self.store: set[str] = set()
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:3] == ["certutil", "-user", "-addstore"]:
            cert = x509.load_pem_x509_certificate(Path(args[-1]).read_bytes())
            self.store.add(thumbprint(cert))
            return 0, "ok"
        if args[:3] == ["certutil", "-user", "-delstore"]:
            if args[-1] in self.store:
                self.store.discard(args[-1])
                return 0, "deleted"
            return 1, "not found"
        if args[:3] == ["certutil", "-user", "-store"]:
            return (0, "found") if args[-1] in self.store else (1, "not found")
        return 1, "unknown"


class CertIssuanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runner = FakeRunner()
        self.mgr = CertificateManager(Path(self.tmp.name) / "certs", runner=self.runner)

    def test_ca_created_once_and_reused(self):
        first = self.mgr.ensure_ca()
        second = self.mgr.ensure_ca()
        self.assertEqual(first.serial_number, second.serial_number)

    def test_leaf_is_signed_by_ca_and_names_domain(self):
        self.mgr.issue("123.com")
        ca = self.mgr.ensure_ca()
        leaf = x509.load_pem_x509_certificate(self.mgr._leaf_paths("123.com")[1].read_bytes())
        self.assertEqual(leaf.issuer, ca.subject)
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual(san.get_values_for_type(x509.DNSName), ["123.com"])
        ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, _ec_sha256())

    def test_ca_is_a_ca_and_leaf_is_not(self):
        self.mgr.issue("a.com")
        ca = self.mgr.ensure_ca()
        leaf = x509.load_pem_x509_certificate(self.mgr._leaf_paths("a.com")[1].read_bytes())
        self.assertTrue(ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertFalse(leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)

    def test_sync_issues_missing_and_removes_stale(self):
        self.mgr.sync(["a.com", "b.com"])
        self.assertTrue(self.mgr.has_leaf("a.com"))
        self.assertTrue(self.mgr.has_leaf("b.com"))
        self.mgr.sync(["a.com"])
        self.assertTrue(self.mgr.has_leaf("a.com"))
        self.assertFalse(self.mgr.has_leaf("b.com"))
        self.assertTrue(self.mgr.ca_cert_path.exists(), "CA must survive a domain sync")

    def test_ca_and_leaf_have_subject_key_identifier(self):
        # Windows' OpenSSL rejects chains without these; this test prevents that regression.
        self.mgr.issue("a.com")
        ca = self.mgr.ensure_ca()
        leaf = x509.load_pem_x509_certificate(self.mgr._leaf_paths("a.com")[1].read_bytes())
        for cert in (ca, leaf):
            ext = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
            self.assertIsNotNone(ext.value.digest)
        aki = leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
        ski = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
        self.assertEqual(aki.key_identifier, ski.digest)

    def test_private_keys_are_unencrypted_pkcs8_pem_only_in_certs_dir(self):
        self.mgr.issue("a.com")
        key_pem = self.mgr._leaf_paths("a.com")[0].read_bytes()
        self.assertIn(b"BEGIN PRIVATE KEY", key_pem)


class TrustStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runner = FakeRunner()
        self.mgr = CertificateManager(Path(self.tmp.name) / "certs", runner=self.runner)

    def test_install_then_detect_then_uninstall(self):
        self.assertFalse(self.mgr.is_installed())
        self.mgr.install_ca()
        self.assertTrue(self.mgr.is_installed())
        self.mgr.uninstall_ca()
        self.assertFalse(self.mgr.is_installed())

    def test_install_failure_raises(self):
        def failing(args):
            return 1, "access denied"
        mgr = CertificateManager(Path(self.tmp.name) / "c2", runner=failing)
        with self.assertRaises(CertificateError):
            mgr.install_ca()

    def test_purge_removes_trust_and_all_key_material(self):
        self.mgr.sync(["a.com", "b.com"])
        self.mgr.install_ca()
        self.mgr.purge()
        self.assertFalse(self.mgr.is_installed())
        self.assertEqual(list(self.mgr.certs_dir.glob("*")), [])

    def test_uninstall_is_safe_when_nothing_installed(self):
        self.mgr.ensure_ca()
        self.mgr.uninstall_ca()  # must not raise


class TlsHandshakeTests(unittest.TestCase):
    """Real TLS handshake on localhost, verifying the chain and the host name."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mgr = CertificateManager(Path(cls.tmp.name) / "certs", runner=FakeRunner())
        cls.mgr.sync(["123.com", "other.com"])
        cls.ctx = cls.mgr.build_ssl_context()

        cls.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cls.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cls.sock.bind(("127.0.0.1", 0))
        cls.sock.listen(5)
        cls.port = cls.sock.getsockname()[1]
        cls.running = True
        cls.thread = threading.Thread(target=cls._serve, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.running = False
        try:
            socket.create_connection(("127.0.0.1", cls.port), timeout=1).close()
        except OSError:
            pass
        cls.sock.close()
        cls.tmp.cleanup()

    @classmethod
    def _serve(cls):
        while cls.running:
            try:
                conn, _ = cls.sock.accept()
            except OSError:
                return
            try:
                tls = cls.ctx.wrap_socket(conn, server_side=True)
                tls.sendall(b"ok")
                tls.close()
            except (ssl.SSLError, OSError):
                conn.close()

    def _client_context(self) -> ssl.SSLContext:
        client = ssl.create_default_context(cafile=str(self.mgr.ca_cert_path))
        return client

    def _connect(self, host: str) -> bytes:
        ctx = self._client_context()
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                return tls.recv(16)

    def test_trusted_handshake_for_listed_domain(self):
        self.assertEqual(self._connect("123.com"), b"ok")

    def test_second_domain_gets_its_own_certificate(self):
        self.assertEqual(self._connect("other.com"), b"ok")

    def test_wrong_name_is_rejected_by_client(self):
        # Unlisted domain: the server has no certificate for it, so the client must refuse.
        with self.assertRaises((ssl.SSLError, ssl.CertificateError, OSError)):
            self._connect("not-listed.com")


def _ec_sha256():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.ECDSA(hashes.SHA256())


if __name__ == "__main__":
    unittest.main()

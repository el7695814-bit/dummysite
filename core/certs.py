"""Local certificate authority and per-domain server certificates.

Design choices:
- The CA lives only on this computer, in the data directory.
- The CA is installed in the CURRENT USER trust store, not the machine store.
  That keeps administrator rights out of the certificate step and limits the
  trust to this Windows user account.
- A server certificate is issued only for domains that are listed and enabled.
- Every removal is complete: the CA is taken out of the trust store and the
  private keys are deleted.
"""

from __future__ import annotations

import datetime as dt
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_COMMON_NAME = "DummySite Local CA"
CA_VALIDITY_DAYS = 3650
LEAF_VALIDITY_DAYS = 365

# Runner signature: takes a list of arguments, returns (returncode, stdout).
# Injected so tests can run without touching the real Windows trust store.
Runner = Callable[[Sequence[str]], tuple[int, str]]


def _default_runner(args: Sequence[str]) -> tuple[int, str]:
    # CREATE_NO_WINDOW stops Windows from opening a console window for certutil
    # when the program runs without one (pythonw).
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=flags,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def thumbprint(cert: x509.Certificate) -> str:
    """SHA-1 thumbprint as used by Windows certificate stores, uppercase hex."""
    return cert.fingerprint(hashes.SHA1()).hex().upper()


@dataclass
class CertificateManager:
    certs_dir: Path
    runner: Runner = _default_runner

    def __post_init__(self) -> None:
        self.certs_dir = Path(self.certs_dir)
        self.certs_dir.mkdir(parents=True, exist_ok=True)

    # ----- paths -------------------------------------------------------
    @property
    def ca_key_path(self) -> Path:
        return self.certs_dir / "ca.key"

    @property
    def ca_cert_path(self) -> Path:
        return self.certs_dir / "ca.crt"

    def _leaf_paths(self, domain: str) -> tuple[Path, Path]:
        return self.certs_dir / f"{domain}.key", self.certs_dir / f"{domain}.crt"

    # ----- CA ----------------------------------------------------------
    def ensure_ca(self) -> x509.Certificate:
        """Create the CA if it does not exist yet, and return its certificate."""
        if self.ca_key_path.exists() and self.ca_cert_path.exists():
            return self._load_cert(self.ca_cert_path)

        key = _ec_key()
        name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DummySite (local test use)"),
            ]
        )
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=CA_VALIDITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        self._write_key(self.ca_key_path, key)
        self.ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return cert

    def issue(self, domain: str) -> None:
        """Issue (or re-issue) a server certificate for one domain, signed by the CA."""
        ca_cert = self.ensure_ca()
        ca_key = self._load_key(self.ca_key_path)

        key = _ec_key()
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=LEAF_VALIDITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        key_path, cert_path = self._leaf_paths(domain)
        self._write_key(key_path, key)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    def has_leaf(self, domain: str) -> bool:
        key_path, cert_path = self._leaf_paths(domain)
        return key_path.exists() and cert_path.exists()

    def remove_leaf(self, domain: str) -> None:
        for p in self._leaf_paths(domain):
            p.unlink(missing_ok=True)

    def sync(self, enabled_domains: Sequence[str]) -> None:
        """Make certificates match the enabled list: issue missing, remove stale."""
        wanted = set(enabled_domains)
        for domain in wanted:
            if not self.has_leaf(domain):
                self.issue(domain)
        for path in self.certs_dir.glob("*.crt"):
            name = path.stem
            if name != "ca" and name not in wanted:
                self.remove_leaf(name)

    # ----- trust store (Windows, current user) -------------------------
    def is_installed(self) -> bool:
        if not self.ca_cert_path.exists():
            return False
        ca = self._load_cert(self.ca_cert_path)
        code, _ = self.runner(
            ["certutil", "-user", "-store", "Root", thumbprint(ca)]
        )
        return code == 0

    def install_ca(self) -> None:
        self.ensure_ca()
        code, out = self.runner(
            ["certutil", "-user", "-addstore", "-f", "Root", str(self.ca_cert_path)]
        )
        if code != 0:
            raise CertificateError(f"התקנת התעודה נכשלה: {out.strip()}")

    def uninstall_ca(self) -> None:
        if not self.ca_cert_path.exists():
            return
        ca = self._load_cert(self.ca_cert_path)
        code, out = self.runner(
            ["certutil", "-user", "-delstore", "Root", thumbprint(ca)]
        )
        # 'not found' is acceptable: the goal is that the CA is absent afterwards.
        if code != 0 and self.is_installed():
            raise CertificateError(f"הסרת התעודה נכשלה: {out.strip()}")

    def purge(self) -> None:
        """Full certificate cleanup: uninstall the CA and delete all key material."""
        self.uninstall_ca()
        for path in self.certs_dir.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)

    # ----- TLS -------------------------------------------------------
    def build_ssl_context(self, default_domain: Optional[str] = None) -> ssl.SSLContext:
        """Server TLS context that picks the right certificate by SNI name."""
        base = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        base.minimum_version = ssl.TLSVersion.TLSv1_2
        fallback_domain = default_domain

        def pick(sock, server_name, _ctx):
            name = (server_name or "").lower()
            if not self.has_leaf(name):
                if fallback_domain and self.has_leaf(fallback_domain):
                    name = fallback_domain
                else:
                    return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            key_path, cert_path = self._leaf_paths(name)
            ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
            sock.context = ctx
            return None

        base.sni_callback = pick
        return base

    # ----- helpers ---------------------------------------------------
    @staticmethod
    def _write_key(path: Path, key) -> None:
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        path.write_bytes(pem)

    @staticmethod
    def _load_key(path: Path):
        return serialization.load_pem_private_key(path.read_bytes(), password=None)

    @staticmethod
    def _load_cert(path: Path) -> x509.Certificate:
        return x509.load_pem_x509_certificate(path.read_bytes())


class CertificateError(RuntimeError):
    pass

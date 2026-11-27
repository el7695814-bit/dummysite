"""Service controller: ties registry, certificates, hosts file, and servers together.

start(): syncs certificates for valid enabled domains, writes the hosts block,
         and starts the HTTP and HTTPS servers. Invalid names are skipped and listed.
stop():  stops both servers and removes the hosts block, then flushes DNS.
         Certificates stay so the next start is quick; purge() removes them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .certs import CertificateManager, Runner, _default_runner
from .hosts import HostsManager, default_hosts_path
from .logs import RequestLog
from .registry import Registry
from .server import DummyServer


class ServiceError(RuntimeError):
    pass


class Service:
    def __init__(
        self,
        data_dir: Path,
        hosts_path: Optional[Path] = None,
        http_port: int = 80,
        https_port: int = 443,
        bind_host: str = "127.0.0.1",
        runner: Runner = _default_runner,
    ):
        self.data_dir = Path(data_dir)
        self.registry = Registry(self.data_dir)
        self.certs = CertificateManager(self.data_dir / "certs", runner=runner)
        self.hosts = HostsManager(hosts_path or default_hosts_path(), self.data_dir / "backup")
        self.log = RequestLog(self.data_dir / "requests.jsonl")
        self.http_port = http_port
        self.https_port = https_port
        self.bind_host = bind_host
        self.skipped_domains: list[str] = []
        self._runner = runner
        self._http: Optional[DummyServer] = None
        self._https: Optional[DummyServer] = None

    @property
    def running(self) -> bool:
        return self._http is not None

    def start(self) -> None:
        if self.running:
            raise ServiceError("השירות כבר פועל")
        domains = self.registry.enabled_domains(valid_only=True)
        self.skipped_domains = [e.domain for e in self.registry.all() if e.enabled and not e.valid]
        if not domains:
            raise ServiceError("אין דומיינים תקינים ופעילים להפעלה")

        self.certs.sync(domains)
        ssl_ctx = self.certs.build_ssl_context()

        # Start servers first. If a port is busy, nothing is written to the hosts file yet.
        http = DummyServer(self.registry, self.log, self.bind_host, self.http_port)
        try:
            https = DummyServer(
                self.registry, self.log, self.bind_host, self.https_port, ssl_context=ssl_ctx
            )
        except OSError:
            http._httpd.server_close()
            raise
        http.start()
        https.start()
        self._http, self._https = http, https

        try:
            self.hosts.apply(domains)
        except OSError:
            self.stop()
            raise
        self.flush_dns()

    def stop(self) -> None:
        for server in (self._http, self._https):
            if server is not None:
                server.stop()
        self._http = self._https = None
        self.hosts.remove_block()
        self.flush_dns()

    def flush_dns(self) -> None:
        """Browsers cache name lookups; flushing makes hosts changes take effect at once."""
        try:
            self._runner(["ipconfig", "/flushdns"])
        except OSError:
            pass  # not fatal: the change still applies once the cache expires

    def ports(self) -> tuple[int, int]:
        """Actual bound ports, useful when ports 0 were requested in tests."""
        if not self.running:
            raise ServiceError("השירות אינו פועל")
        return self._http.address[1], self._https.address[1]

    def purge(self) -> None:
        """Complete removal of everything this program changed, except the user's data files."""
        if self.running:
            self.stop()
        self.hosts.remove_block()
        self.certs.purge()
        self.log.clear()

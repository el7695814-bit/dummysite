"""Detect a real internet connection.

The program refuses to start, and stops automatically, when a real connection
is detected. The probe is a plain TCP connection to a public address, with a
short timeout, so it works without any DNS or HTTP dependency.
"""

from __future__ import annotations

import socket
from typing import Callable, Sequence

PROBE_TARGETS: Sequence[tuple[str, int]] = (("1.1.1.1", 443), ("8.8.8.8", 443))


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def internet_reachable(
    probe: Callable[[str, int], bool] = tcp_probe,
    targets: Sequence[tuple[str, int]] = PROBE_TARGETS,
) -> bool:
    """True if any target answers. One success is enough to count as online."""
    return any(probe(host, port) for host, port in targets)

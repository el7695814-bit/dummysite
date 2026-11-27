"""Manage only our own block inside the hosts file.

Every line we add lives between BEGIN and END markers. Removing the block
restores the file to its previous state. Other lines are never touched.
A backup of the original file is kept in the data directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

BEGIN = "# >>> DummySite BEGIN (do not edit inside this block)"
END = "# <<< DummySite END"
LOCAL_IP = "127.0.0.1"


def default_hosts_path() -> Path:
    import os

    root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root) / "System32" / "drivers" / "etc" / "hosts"


class HostsManager:
    def __init__(self, hosts_path: Path, backup_dir: Path):
        self.hosts_path = Path(hosts_path)
        self.backup_path = Path(backup_dir) / "hosts.backup"
        self.backup_dir = Path(backup_dir)

    # ----- reading -----------------------------------------------------
    def _read_lines(self) -> list[str]:
        text = self.hosts_path.read_text(encoding="utf-8", errors="surrogateescape")
        return text.splitlines()

    def _eol(self) -> str:
        """Use the file's own line ending, so Windows' CRLF is not silently changed."""
        if self.hosts_path.exists() and b"\r\n" in self.hosts_path.read_bytes():
            return "\r\n"
        return "\n"

    def _write_lines(self, lines: list[str]) -> None:
        eol = self._eol()
        text = eol.join(lines) + eol
        self.hosts_path.write_bytes(text.encode("utf-8", errors="surrogateescape"))

    def _strip_block(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        inside = False
        for line in lines:
            if line.strip() == BEGIN:
                inside = True
                continue
            if line.strip() == END:
                inside = False
                continue
            if not inside:
                out.append(line)
        return out

    # ----- public API --------------------------------------------------
    def managed_domains(self) -> list[str]:
        """Return the domains currently listed in our block."""
        result: list[str] = []
        inside = False
        for line in self._read_lines():
            if line.strip() == BEGIN:
                inside = True
                continue
            if line.strip() == END:
                break
            if inside and line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    result.append(parts[1])
        return result

    def apply(self, domains: Iterable[str]) -> None:
        """Replace our block with exactly the given domains."""
        self._ensure_backup()
        lines = self._strip_block(self._read_lines())
        block = [BEGIN]
        block += [f"{LOCAL_IP} {d}" for d in sorted(set(domains))]
        block.append(END)
        self._write_lines(lines + block)

    def remove_block(self) -> None:
        """Remove our block. Other lines stay as they were."""
        if not self.hosts_path.exists():
            return
        lines = self._strip_block(self._read_lines())
        self._write_lines(lines)

    def restore_backup(self) -> bool:
        """Restore the original hosts file from the backup, if one exists."""
        if self.backup_path.exists():
            shutil.copyfile(self.backup_path, self.hosts_path)
            return True
        return False

    def has_block(self) -> bool:
        return any(line.strip() == BEGIN for line in self._read_lines())

    # ----- internals ---------------------------------------------------
    def _ensure_backup(self) -> None:
        if not self.backup_path.exists():
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.hosts_path, self.backup_path)

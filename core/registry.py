"""Domain registry: the list of dummy domains, their content, and import/export.

Storage layout (under the data directory):
    config.json                 -> {"default_source": ..., "domains": [...]}
    content/<domain>.<ext>      -> copies of content files, one per domain
    content/default.html        -> default page (optional; empty page if missing)

Each entry remembers where its HTML came from (source_path), so the program can
open that folder, and so the list can be exported and imported.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class RegistryError(ValueError):
    """Raised for invalid input (bad domain name, missing file, and so on)."""


def clean_domain(name: str) -> str:
    return (name or "").strip().lower().rstrip(".")


def is_valid_domain(name: str) -> bool:
    return bool(_DOMAIN_RE.match(name or ""))


def normalize_domain(name: str) -> str:
    """Strict form (used by the command line): raises for an invalid name."""
    value = clean_domain(name)
    if not is_valid_domain(value):
        raise RegistryError(f"שם דומיין לא תקין: {name!r}")
    return value


def _file_stem(domain: str) -> str:
    return re.sub(r"[^a-z0-9.\-]", "_", domain) or "site"


@dataclass
class DomainEntry:
    domain: str
    enabled: bool = True
    content_file: Optional[str] = None
    content_type: str = "text/html; charset=utf-8"
    source_path: Optional[str] = None
    valid: bool = True


class Registry:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.content_dir = self.data_dir / "content"
        self.config_path = self.data_dir / "config.json"
        self.content_dir.mkdir(parents=True, exist_ok=True)
        self._domains: dict[str, DomainEntry] = {}
        self._default_source: Optional[str] = None
        self.load()

    # ----- persistence -------------------------------------------------
    def load(self) -> None:
        self._domains = {}
        self._default_source = None
        if not self.config_path.exists():
            return
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(DomainEntry)}
        for d in raw.get("domains", []):
            entry = DomainEntry(**{k: v for k, v in d.items() if k in known})
            self._domains[entry.domain] = entry
        self._default_source = raw.get("default_source")

    def save(self) -> None:
        payload = {
            "default_source": self._default_source,
            "domains": [asdict(e) for e in self.all()],
        }
        tmp = self.config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.config_path)

    # ----- queries -----------------------------------------------------
    def all(self) -> list[DomainEntry]:
        return sorted(self._domains.values(), key=lambda e: e.domain)

    def get(self, domain: str) -> Optional[DomainEntry]:
        return self._domains.get(clean_domain(domain))

    def enabled_domains(self, valid_only: bool = False) -> list[str]:
        return [e.domain for e in self.all() if e.enabled and (e.valid or not valid_only)]

    # ----- mutations ---------------------------------------------------
    def add(self, domain: str, content_source: Optional[Path] = None, strict: bool = True) -> DomainEntry:
        """strict=False keeps an invalid name as a marked entry instead of refusing it."""
        if strict:
            name, valid = normalize_domain(domain), True
        else:
            name = clean_domain(domain)
            if not name:
                raise RegistryError("שם הדומיין ריק")
            valid = is_valid_domain(name)
        if name in self._domains:
            raise RegistryError(f"הדומיין {name} כבר קיים ברשימה")
        entry = DomainEntry(domain=name, valid=valid)
        self._domains[name] = entry
        if content_source is not None:
            self._set_content(entry, Path(content_source))
        self.save()
        return entry

    def remove(self, domain: str) -> None:
        name = clean_domain(domain)
        entry = self._domains.pop(name, None)
        if entry is None:
            raise RegistryError(f"הדומיין {name} לא נמצא")
        if entry.content_file:
            (self.content_dir / entry.content_file).unlink(missing_ok=True)
        self.save()

    def set_enabled(self, domain: str, enabled: bool) -> None:
        self._require(domain).enabled = bool(enabled)
        self.save()

    def set_content(self, domain: str, content_source: Optional[Path]) -> None:
        entry = self._require(domain)
        if content_source is None:
            if entry.content_file:
                (self.content_dir / entry.content_file).unlink(missing_ok=True)
            entry.content_file = None
            entry.content_type = "text/html; charset=utf-8"
            entry.source_path = None
        else:
            self._set_content(entry, Path(content_source))
        self.save()

    def set_default(self, content_source: Optional[Path]) -> None:
        target = self.content_dir / "default.html"
        if content_source is None:
            target.unlink(missing_ok=True)
            self._default_source = None
        else:
            src = Path(content_source)
            if not src.is_file():
                raise RegistryError("קובץ ברירת המחדל לא נמצא")
            shutil.copyfile(src, target)
            self._default_source = str(src.resolve())
        self.save()

    def default_path(self) -> Optional[Path]:
        target = self.content_dir / "default.html"
        return target if target.is_file() else None

    def default_source(self) -> Optional[str]:
        return self._default_source

    # ----- import / export ---------------------------------------------
    def export_data(self) -> dict:
        return {
            "version": 1,
            "default_source": self._default_source,
            "domains": [
                {"domain": e.domain, "enabled": e.enabled, "source_path": e.source_path}
                for e in self.all()
            ],
        }

    def import_data(self, data: dict) -> tuple[int, int, list[str]]:
        """Returns (added, updated, problems). Existing domains are updated, not duplicated."""
        added = updated = 0
        problems: list[str] = []
        for item in data.get("domains", []):
            raw = str(item.get("domain", ""))
            try:
                existing = self.get(raw)
                if existing is None:
                    entry = self.add(raw, strict=False)
                    added += 1
                else:
                    entry = existing
                    updated += 1
                entry.enabled = bool(item.get("enabled", True))
                src = item.get("source_path")
                if src:
                    if Path(src).is_file():
                        self._set_content(entry, Path(src))
                    else:
                        problems.append(f"קובץ לא נמצא עבור {entry.domain}: {src}")
            except RegistryError as exc:
                problems.append(str(exc))
        default = data.get("default_source")
        if default:
            if Path(default).is_file():
                self.set_default(Path(default))
            else:
                problems.append(f"דף ברירת המחדל לא נמצא: {default}")
        self.save()
        return added, updated, problems

    # ----- helpers -----------------------------------------------------
    def _require(self, domain: str) -> DomainEntry:
        entry = self.get(domain)
        if entry is None:
            raise RegistryError(f"הדומיין {clean_domain(domain)} לא נמצא")
        return entry

    def _set_content(self, entry: DomainEntry, src: Path) -> None:
        if not src.is_file():
            raise RegistryError("קובץ התוכן לא נמצא")
        ext = src.suffix.lower() or ".bin"
        dest_name = f"{_file_stem(entry.domain)}{ext}"
        if entry.content_file and entry.content_file != dest_name:
            (self.content_dir / entry.content_file).unlink(missing_ok=True)
        shutil.copyfile(src, self.content_dir / dest_name)
        entry.content_file = dest_name
        entry.source_path = str(src.resolve())
        guessed, _ = mimetypes.guess_type(dest_name)
        guessed = guessed or "application/octet-stream"
        if guessed.startswith("text/") or guessed in ("application/json", "application/javascript"):
            entry.content_type = f"{guessed}; charset=utf-8"
        else:
            entry.content_type = guessed

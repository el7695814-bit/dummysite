"""Request history: what was requested, when, and what was returned."""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Optional


class RequestLog:
    def __init__(self, log_path: Path, max_memory: int = 500):
        self.log_path = Path(log_path)
        self._lock = threading.Lock()
        self._recent: Deque[dict] = deque(maxlen=max_memory)

    def record(self, domain: str, path: str, result: str, note: Optional[str] = None) -> None:
        """result: 'served_custom', 'served_default', 'served_empty', 'not_found'."""
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "domain": domain,
            "path": path,
            "result": result,
            "note": note or "",
        }
        with self._lock:
            self._recent.append(entry)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._recent)[-limit:][::-1]

    def clear(self) -> None:
        with self._lock:
            self._recent.clear()
            if self.log_path.exists():
                self.log_path.unlink()

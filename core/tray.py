"""System tray icon. Optional: needs the packages pystray and pillow.

Install once with:  python -m pip install pystray pillow
If they are missing, the interface explains how to install them.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class TrayIcon:
    def __init__(self, on_show: Callable[[], None], on_quit: Callable[[], None]):
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon = None

    @staticmethod
    def available() -> bool:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except ImportError:
            return False

    def start(self) -> None:
        if self._icon is not None:
            return
        import pystray
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((4, 4, 60, 60), fill=(79, 70, 229, 255))
        menu = pystray.Menu(
            pystray.MenuItem("הצג", lambda _icon, _item: self._on_show(), default=True),
            pystray.MenuItem("יציאה", lambda _icon, _item: self._on_quit()),
        )
        self._icon = pystray.Icon("DummySite", image, "תוכן דמה", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self) -> None:
        icon: Optional[object] = self._icon
        self._icon = None
        if icon is not None:
            icon.stop()

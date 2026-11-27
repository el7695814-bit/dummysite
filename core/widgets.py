"""Rounded widgets drawn on a canvas: buttons, status pills, and a scrollbar.

ttk cannot round corners, so these are drawn directly. Each one mimics the
small part of the standard widget API that the interface uses.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from .theme import FONT, FONT_BOLD, PALETTE as P


def rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """Corner points for a smooth rounded rectangle (use with smooth=True)."""
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class RoundButton(tk.Canvas):
    VARIANTS = {
        "default": dict(fill=P["card"], text=P["text"], hover=P["primary_soft"], line=P["border"]),
        "primary": dict(fill=P["primary"], text="#ffffff", hover=P["primary_hover"], line=P["primary"]),
        "danger": dict(fill=P["danger"], text="#ffffff", hover=P["danger_hover"], line=P["danger"]),
    }

    def __init__(self, master, text: str, command, variant: str = "default",
                 bg: str = P["side"], height: int = 40, radius: int = 14, font=FONT):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=bg)
        self._text = text
        self._command = command
        self._colors = self.VARIANTS[variant]
        self._radius = radius
        self._font = font
        self._enabled = True
        self._hover = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)

    def state(self, flags=None):
        if flags is not None:
            self._enabled = "disabled" not in flags
            self.configure(cursor="hand2" if self._enabled else "arrow")
            self._draw()
        return [] if self._enabled else ["disabled"]

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._draw()

    def _on_click(self, event):
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if self._enabled and inside and self._command:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        if not self._enabled:
            fill, fg, line = P["disabled_bg"], P["disabled_fg"], P["border"]
        elif self._hover:
            fill, fg, line = self._colors["hover"], self._colors["text"], self._colors["line"]
        else:
            fill, fg, line = self._colors["fill"], self._colors["text"], self._colors["line"]
        self.create_polygon(rounded_points(1, 1, w - 1, h - 1, self._radius),
                            smooth=True, fill=fill, outline=line)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class Pill(tk.Canvas):
    """Rounded status badge. With a command, a click runs it."""

    def __init__(self, master, bg: str = P["bg"], height: int = 30, command=None):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=bg)
        self._text = ""
        self._color = P["muted"]
        self._command = command
        self._font = tkfont.Font(self, font=FONT_BOLD)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonRelease-1>", self._on_click)

    def set(self, text: str, color: str) -> None:
        self._text, self._color = text, color
        self.configure(width=self._font.measure(text) + 28,
                       cursor="hand2" if self._command else "arrow")
        self._draw()

    def _on_click(self, event) -> None:
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if self._command and inside:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        self.create_polygon(rounded_points(0, 0, w, h, h / 2), smooth=True,
                            fill=self._color, outline=self._color)
        self.create_text(w / 2, h / 2, text=self._text, fill="#ffffff", font=FONT_BOLD)


class RoundScrollbar(tk.Canvas):
    """Vertical scrollbar with a rounded thumb. Works with Treeview and Canvas."""

    def __init__(self, master, command, width: int = 12, bg: str = P["bg"]):
        super().__init__(master, width=width, highlightthickness=0, bd=0, bg=bg)
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._dragging = False
        self._grab = 0.0
        self._hover = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_dragging", False))
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    def set(self, first, last) -> None:
        self._first, self._last = float(first), float(last)
        self._draw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._draw()

    def _press(self, event) -> None:
        h = self.winfo_height()
        top, bottom = self._thumb_span(h)
        self._dragging = True
        self._grab = event.y - top
        if not (top <= event.y <= bottom):
            self._move_to((event.y - (bottom - top) / 2) / h)

    def _drag(self, event) -> None:
        if self._dragging:
            self._move_to((event.y - self._grab) / self.winfo_height())

    def _move_to(self, fraction: float) -> None:
        self._command("moveto", max(0.0, min(1.0, fraction)))

    def _thumb_span(self, h: int) -> tuple[float, float]:
        top = self._first * h
        bottom = max(self._last * h, top + 28)
        return top, min(bottom, h)

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        self.create_polygon(rounded_points(2, 0, w - 2, h, (w - 4) / 2), smooth=True,
                            fill=P["track"], outline=P["track"])
        top, bottom = self._thumb_span(h)
        color = P["thumb_hover"] if self._hover or self._dragging else P["thumb"]
        self.create_polygon(rounded_points(2, top + 2, w - 2, bottom - 2, (w - 4) / 2),
                            smooth=True, fill=color, outline=color)

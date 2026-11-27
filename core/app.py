"""Graphical interface (tkinter). Run with: python -m core.app  (or double-click DummySite.pyw).

Rules enforced here (see core/policy.py):
- The service starts only with at least one valid enabled domain, admin rights,
  and no real internet connection, unless the bypass option is ticked.
- While running outside bypass mode, a detected real connection stops the service.
- The bypass option is off on every launch.
- The domain list cannot change while the service runs.
"""

from __future__ import annotations

import ctypes
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .banner import inject
from .cli import default_data_dir
from .connectivity import internet_reachable
from .policy import can_start, must_auto_stop
from .registry import RegistryError
from .server import EMPTY_PAGE
from .service import Service, ServiceError
from .theme import FONT, FONT_SMALL, FONT_TITLE, PALETTE as P
from .tray import TrayIcon
from .widgets import Pill, RoundButton, RoundScrollbar

CHECK_INTERVAL_MS = 5000
TRAY_POLL_MS = 200
ROW_H = 34
DOMAIN_COL = "#0"
CONTENT_COL = "#2"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _windowless_python() -> str:
    """pythonw.exe runs without a console window; fall back to python.exe if it is missing."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe)


def relaunch_as_admin() -> bool:
    """Start a new elevated, windowless copy of the program. True if Windows accepted it."""
    project_root = Path(__file__).resolve().parent.parent
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, ""
    else:
        exe, params = _windowless_python(), f'"{project_root / "DummySite.pyw"}"'
    # ShellExecute returns a value above 32 on success.
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(project_root), 1)
    return result > 32


def _inside(widget, container) -> bool:
    w, c = str(widget), str(container)
    return w == c or w.startswith(c + ".")


class App(tk.Tk):
    def __init__(self, service: Service):
        super().__init__()
        self.svc = service
        self.test_mode = False  # the bypass option is always off at launch
        self.real_internet = False
        self._probe_queue: queue.Queue = queue.Queue()
        self._tray_queue: queue.Queue = queue.Queue()
        self._tray = TrayIcon(on_show=lambda: self._tray_queue.put("show"),
                              on_quit=lambda: self._tray_queue.put("quit"))
        self._rules: list[tuple[ttk.Widget, str]] = []
        self._side_scrollbar_shown = True
        self._tree_scroll_shown = False
        self._ca_cache: bool | None = None
        self._hover_row: str | None = None
        self._wheel_acc = 0.0
        self._sort_col, self._sort_rev = "domain", False
        self._heading_labels = {"domain": "דומיין", "state": "מצב", "content": "תוכן"}

        self.title("DummySite - תוכן דמה")
        self.geometry("1040x680")
        self.minsize(680, 480)
        self.configure(bg=P["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_styles()
        self._build_layout()
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.refresh()
        self._schedule_probe()
        self.after(TRAY_POLL_MS, self._poll_tray)

    # ----- styling -------------------------------------------------------
    def _build_styles(self) -> None:
        self._link_font = tkfont.Font(self, family="Segoe UI", size=10, underline=True)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=P["bg"], foreground=P["text"], font=FONT)
        style.configure("TFrame", background=P["bg"])
        style.configure("Side.TFrame", background=P["side"])
        style.configure("TLabel", background=P["bg"], foreground=P["text"], font=FONT)
        style.configure("Title.TLabel", font=FONT_TITLE, background=P["bg"], foreground=P["primary"])
        style.configure("Section.TLabel", background=P["side"], foreground=P["muted"], font=FONT_SMALL)
        style.configure("Muted.TLabel", background=P["bg"], foreground=P["muted"], font=FONT_SMALL)
        style.configure("TCheckbutton", background=P["side"], foreground=P["text"], font=FONT)
        style.map("TCheckbutton", background=[("active", P["side"])])
        style.configure("TEntry", fieldbackground=P["card"], foreground=P["text"], bordercolor=P["border"],
                        lightcolor=P["border"], darkcolor=P["border"], padding=8, font=FONT)
        style.map("TEntry", bordercolor=[("focus", P["primary"])], lightcolor=[("focus", P["primary"])])
        style.configure("Treeview", font=FONT, rowheight=ROW_H, background=P["card"],
                        fieldbackground=P["card"], foreground=P["text"], bordercolor=P["border"])
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background=P["bg"],
                        foreground=P["muted"], relief="flat")
        style.map("Treeview", background=[("selected", P["primary_soft"])],
                  foreground=[("selected", P["text"])])

    # ----- layout --------------------------------------------------------
    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(24, 18, 24, 8))
        header.pack(fill="x")
        ttk.Label(header, text="תוכן דמה", style="Title.TLabel").pack(side="right")
        chips = ttk.Frame(header)
        chips.pack(side="left")
        self.chip_service = Pill(chips, command=self.toggle_service)
        self.chip_service.pack(side="left", padx=(0, 8))
        self.chip_ca = Pill(chips, command=self.toggle_ca)
        self.chip_ca.pack(side="left", padx=(0, 8))
        self.chip_net = Pill(chips)
        self.chip_net.pack(side="left")

        body = ttk.Frame(self, padding=(24, 4, 24, 12))
        body.pack(fill="both", expand=True)

        # Right side: scrollable button panel.
        side_wrap = ttk.Frame(body)
        side_wrap.pack(side="right", fill="y", padx=(16, 0))
        self.side_canvas = tk.Canvas(side_wrap, width=260, highlightthickness=0, bg=P["side"], bd=0)
        self.side_scroll = RoundScrollbar(side_wrap, command=self.side_canvas.yview, bg=P["side"])
        self.side_canvas.configure(yscrollcommand=self.side_scroll.set)
        self.side_canvas.pack(side="left", fill="y")
        self.side_scroll.pack(side="right", fill="y")

        self.side = ttk.Frame(self.side_canvas, style="Side.TFrame", padding=16)
        self._side_window = self.side_canvas.create_window((0, 0), window=self.side, anchor="nw")
        self.side.bind("<Configure>", self._on_side_configure)
        self.side_canvas.bind("<Configure>", self._on_canvas_configure)

        self._section("דומיינים")
        self._btn("הוסף דומיין", self.add_domain, "idle")
        self._btn("הפעל / כבה", self.toggle_domain, "sel")
        self._btn("הסר דומיין", self.remove_domain, "sel")

        self._section("תוכן")
        self._btn("בחר תוכן לדומיין", self.set_domain_content, "sel")
        self._btn("החזר לתוכן ברירת מחדל", self.reset_domain_content, "sel")
        self._btn("בחר דף ברירת מחדל", self.set_default, "idle")
        self._btn("הצג דף ברירת מחדל", self.preview_default, "always")

        self._section("רשימה")
        self._btn("ייצוא רשימה", self.export_list, "always")
        self._btn("ייבוא רשימה", self.import_list, "idle")

        self._section("תעודות והיסטוריה")
        self._btn("היסטוריית בקשות", self.show_history, "always")

        self._section("אחר")
        self._btn("מזעור למגש המערכת", self.minimize_to_tray, "always")
        self.test_var = tk.BooleanVar(value=False)
        check = ttk.Checkbutton(self.side, text="עקיפת בדיקת אינטרנט", variable=self.test_var,
                                command=self._toggle_test_mode)
        check.pack(anchor="e", pady=(4, 10), fill="x")
        self._rules.append((check, "always"))
        self._btn("ניקוי מלא", self.purge, "idle", variant="danger", pady_top=12)

        # Main area: search, then the table.
        main = ttk.Frame(body)
        main.pack(side="left", fill="both", expand=True)

        search_row = ttk.Frame(main)
        search_row.pack(fill="x", pady=(0, 12))
        self.search_var = tk.StringVar()
        clear = RoundButton(search_row, "✕", self._clear_search, bg=P["bg"], height=40, radius=12)
        clear.configure(width=44)
        clear.pack(side="left", padx=(8, 0))
        entry = ttk.Entry(search_row, textvariable=self.search_var, justify="right", font=FONT)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.search_entry = entry

        self.tree = ttk.Treeview(main, columns=("state", "content"), show="tree headings",
                                 selectmode="browse")
        for col, label in self._heading_labels.items():
            tree_col = DOMAIN_COL if col == "domain" else col
            self.tree.heading(tree_col, text=label, anchor="e", command=lambda c=col: self._sort_by(c))
        self.tree.column("#0", width=260, anchor="e", stretch=True)
        self.tree.column("state", width=100, anchor="center", stretch=False)
        self.tree.column("content", width=320, anchor="e", stretch=True)
        self.tree.tag_configure("invalid", foreground=P["danger"])
        self.tree.tag_configure("link", font=self._link_font)

        self.tree_scroll = RoundScrollbar(main, command=self.tree.yview, bg=P["bg"])
        self.tree.configure(yscrollcommand=self.tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Configure>", lambda _e: self._update_tree_scrollbar())
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._on_tree_leave)
        self.tree.bind("<Return>", lambda _e: self._open_selected_site())

        self.search_var.trace_add("write", lambda *_: self.refresh())

        self.footer = ttk.Label(self, text="", style="Muted.TLabel")
        self.footer.pack(fill="x", padx=24, pady=(0, 10))

    def _section(self, title: str) -> None:
        ttk.Label(self.side, text=title, style="Section.TLabel").pack(anchor="e", pady=(14, 4))

    def _btn(self, text, command, kind, variant="default", pady_top=0) -> RoundButton:
        btn = RoundButton(self.side, text, command, variant=variant, bg=P["side"])
        btn.pack(fill="x", pady=(pady_top, 6))
        self._rules.append((btn, kind))
        return btn

    # ----- scrolling -----------------------------------------------------
    def _on_side_configure(self, _event) -> None:
        self.side_canvas.configure(scrollregion=self.side_canvas.bbox("all"))
        self._update_side_scrollbar()

    def _on_canvas_configure(self, event) -> None:
        self.side_canvas.itemconfigure(self._side_window, width=event.width)
        self._update_side_scrollbar()

    def _update_side_scrollbar(self) -> None:
        needs = self.side.winfo_reqheight() > self.side_canvas.winfo_height()
        if needs and not self._side_scrollbar_shown:
            self.side_scroll.pack(side="right", fill="y")
            self._side_scrollbar_shown = True
        elif not needs and self._side_scrollbar_shown:
            self.side_scroll.pack_forget()
            self._side_scrollbar_shown = False

    def _update_tree_scrollbar(self) -> None:
        rows = len(self.tree.get_children())
        visible = self.tree.winfo_height() - ROW_H  # the header takes one row's height
        needs = rows > 0 and rows * ROW_H > visible
        if needs and not self._tree_scroll_shown:
            self.tree_scroll.pack(side="right", fill="y", before=self.tree)
            self._tree_scroll_shown = True
        elif not needs and self._tree_scroll_shown:
            self.tree_scroll.pack_forget()
            self._tree_scroll_shown = False

    def _on_wheel(self, event) -> None:
        """One handler for both panels: two-finger touchpad scrolling and the mouse wheel."""
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        if _inside(widget, self.side_canvas):
            target = self.side_canvas
        elif _inside(widget, self.tree):
            target = self.tree
        else:
            return
        # Touchpads send small deltas; accumulate them so slow gestures still scroll.
        self._wheel_acc += -event.delta / 120
        steps = int(self._wheel_acc)
        if steps:
            self._wheel_acc -= steps
            target.yview_scroll(steps, "units")

    # ----- state ---------------------------------------------------------
    def _update_buttons(self) -> None:
        running = self.svc.running
        has_sel = self._selected() is not None
        for widget, kind in self._rules:
            if kind == "sel":
                enabled = has_sel and not running
            elif kind == "idle":
                enabled = not running
            else:  # "always"
                enabled = True
            widget.state(["!disabled"] if enabled else ["disabled"])

    def _ca_installed(self) -> bool:
        if self._ca_cache is None:
            self._ca_cache = self.svc.certs.is_installed()
        return self._ca_cache

    @staticmethod
    def _chip(pill: Pill, text: str, color: str) -> None:
        pill.set(text, color)

    def _visible_entries(self):
        query = self.search_var.get().strip().lower()
        keys = {
            "domain": lambda e: e.domain,
            "state": lambda e: (0 if (e.valid and e.enabled) else 1 if e.valid else 2, e.domain),
            "content": lambda e: (e.source_path or e.content_file or "", e.domain),
        }
        entries = []
        for e in self.svc.registry.all():
            name = Path(e.source_path).name if e.source_path else (e.content_file or "ברירת מחדל")
            if query and query not in e.domain and query not in name.lower():
                continue
            entries.append(e)
        return sorted(entries, key=keys[self._sort_col], reverse=self._sort_rev)

    def _row_tags(self, domain: str, link: bool) -> tuple[str, ...]:
        entry = self.svc.registry.get(domain)
        tags = []
        if entry is not None and not entry.valid:
            tags.append("invalid")
        if link:
            tags.append("link")
        return tuple(tags)

    def refresh(self) -> None:
        running = self.svc.running
        self._chip(self.chip_service,
                   "השירות: פועל" if running else "השירות: כבוי",
                   P["success"] if running else P["danger"])
        installed = self._ca_installed()
        self._chip(self.chip_ca,
                   "CA: מותקן" if installed else "CA: לא מותקן",
                   P["success"] if installed else P["danger"])
        self._chip(self.chip_net,
                   "אינטרנט אמיתי: זוהה" if self.real_internet else "אינטרנט אמיתי: לא זוהה",
                   P["danger"] if self.real_internet else P["muted"])

        selected = self._selected()
        self._hover_row = None
        self.tree.delete(*self.tree.get_children())
        for entry in self._visible_entries():
            if not entry.valid:
                state = "לא תקין"
            else:
                state = "פעיל" if entry.enabled else "כבוי"
            if entry.source_path:
                content = Path(entry.source_path).name
            else:
                content = entry.content_file or "ברירת מחדל"
            self.tree.insert("", "end", iid=entry.domain, text=entry.domain,
                             values=(state, content), tags=self._row_tags(entry.domain, False))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        self._update_headings()
        self._update_tree_scrollbar()
        self._update_buttons()
        self._update_footer()

    def _update_headings(self) -> None:
        for col, label in self._heading_labels.items():
            arrow = ""
            if col == self._sort_col:
                arrow = "  ▼" if self._sort_rev else "  ▲"
            self.tree.heading(DOMAIN_COL if col == "domain" else col, text=label + arrow)

    def _update_footer(self) -> None:
        if self.test_mode:
            text = ""
        elif self.real_internet and not self.svc.running:
            text = 'מחובר לאינטרנט אמיתי, לכן ההפעלה חסומה. לעקיפה סמן "עקיפת בדיקת אינטרנט".'
        else:
            text = ""
        self.footer.configure(text=text)

    def _selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _guard_editable(self) -> bool:
        if self.svc.running:
            messagebox.showinfo("השירות פועל", "יש לעצור את השירות לפני שינוי הרשימה.")
            return False
        return True

    def _show_error(self, exc: Exception) -> None:
        messagebox.showerror("שגיאה", str(exc))

    def _clear_search(self) -> None:
        self.search_var.set("")
        self.search_entry.focus_set()

    # ----- sorting -------------------------------------------------------
    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col, self._sort_rev = col, False
        self.refresh()

    # ----- table interaction --------------------------------------------
    def _on_tree_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        if col == DOMAIN_COL:
            self._open_site(row)
        elif col == CONTENT_COL:
            self._open_folder(row)

    def _on_tree_motion(self, event) -> None:
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        linkable = bool(row) and col in (DOMAIN_COL, CONTENT_COL)
        if linkable:
            self.tree.configure(cursor="hand2")
            if row != self._hover_row:
                self._clear_hover()
                self._hover_row = row
                self.tree.item(row, tags=self._row_tags(row, link=True))
        else:
            self.tree.configure(cursor="")
            self._clear_hover()

    def _on_tree_leave(self, _event) -> None:
        self.tree.configure(cursor="")
        self._clear_hover()

    def _clear_hover(self) -> None:
        row = self._hover_row
        self._hover_row = None
        if row and self.tree.exists(row):
            self.tree.item(row, tags=self._row_tags(row, link=False))

    def _open_selected_site(self) -> None:
        domain = self._selected()
        if domain:
            self._open_site(domain)

    def _open_site(self, domain: str) -> None:
        entry = self.svc.registry.get(domain)
        if entry is None:
            return
        if not entry.valid:
            messagebox.showinfo("דומיין לא תקין", f"{domain} אינו שם דומיין תקין, ולכן לא ניתן לפתוח אותו.")
            return
        if not self.svc.running:
            messagebox.showinfo("השירות כבוי", "הפעל את השירות כדי שהאתר ייפתח עם תוכן הדמה.")
            return
        webbrowser.open(f"https://{domain}")

    def _open_folder(self, domain: str) -> None:
        """Open Explorer with the HTML file selected: the original file if it still exists."""
        entry = self.svc.registry.get(domain)
        if entry is None:
            return
        target = None
        if entry.source_path and Path(entry.source_path).is_file():
            target = Path(entry.source_path)
        elif entry.content_file and (self.svc.registry.content_dir / entry.content_file).is_file():
            target = self.svc.registry.content_dir / entry.content_file
        if target is None:
            messagebox.showinfo("אין קובץ", f"לדומיין {domain} אין קובץ HTML מוגדר.")
            return
        try:
            subprocess.Popen(f'explorer /select,"{target}"')
        except OSError as exc:
            self._show_error(exc)

    # ----- domain actions ------------------------------------------------
    def add_domain(self) -> None:
        if not self._guard_editable():
            return
        name = simpledialog.askstring("הוספת דומיין", "שם הדומיין (לדוגמה: shop.test):", parent=self)
        if not name:
            return
        try:
            entry = self.svc.registry.add(name, strict=False)
        except RegistryError as exc:
            self._show_error(exc)
            return
        if not entry.valid:
            messagebox.showinfo(
                "דומיין לא תקין",
                f"{entry.domain} נשמר, אבל אינו שם דומיין תקין. הוא מסומן באדום ולא יופעל.",
            )
        self.refresh()

    def toggle_domain(self) -> None:
        if not self._guard_editable():
            return
        domain = self._selected()
        if not domain:
            return
        entry = self.svc.registry.get(domain)
        self.svc.registry.set_enabled(domain, not entry.enabled)
        self.refresh()

    def set_domain_content(self) -> None:
        if not self._guard_editable():
            return
        domain = self._selected()
        if not domain:
            return
        path = filedialog.askopenfilename(
            title="בחר קובץ תוכן",
            filetypes=[("HTML / טקסט", "*.html *.htm *.txt *.json"), ("כל הקבצים", "*.*")],
        )
        if not path:
            return
        try:
            self.svc.registry.set_content(domain, Path(path))
        except RegistryError as exc:
            self._show_error(exc)
        self.refresh()

    def reset_domain_content(self) -> None:
        if not self._guard_editable():
            return
        domain = self._selected()
        if domain:
            self.svc.registry.set_content(domain, None)
            self.refresh()

    def remove_domain(self) -> None:
        if not self._guard_editable():
            return
        domain = self._selected()
        if not domain:
            return
        if not messagebox.askyesno("הסרת דומיין", f"להסיר את {domain} מהרשימה?"):
            return
        self.svc.registry.remove(domain)
        self.refresh()

    def set_default(self) -> None:
        if not self._guard_editable():
            return
        path = filedialog.askopenfilename(
            title="בחר דף ברירת מחדל",
            filetypes=[("HTML / טקסט", "*.html *.htm *.txt"), ("כל הקבצים", "*.*")],
        )
        if path:
            try:
                self.svc.registry.set_default(Path(path))
            except RegistryError as exc:
                self._show_error(exc)
            self.refresh()

    def preview_default(self) -> None:
        """Show the default page the way a dummy domain would show it, banner included."""
        source = self.svc.registry.default_path()
        body = source.read_bytes() if source else EMPTY_PAGE
        html = inject(body, "text/html; charset=utf-8")
        target = self.svc.data_dir / "preview.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(html)
        webbrowser.open(target.as_uri())

    # ----- export / import -----------------------------------------------
    def export_list(self) -> None:
        path = filedialog.asksaveasfilename(
            title="ייצוא רשימת אתרים", defaultextension=".json",
            initialfile="dummysite-list.json", filetypes=[("רשימת אתרים", "*.json")],
        )
        if not path:
            return
        try:
            data = self.svc.registry.export_data()
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            messagebox.showinfo("ייצוא", f"נשמרו {len(data['domains'])} אתרים.")
        except OSError as exc:
            self._show_error(exc)

    def import_list(self) -> None:
        if not self._guard_editable():
            return
        path = filedialog.askopenfilename(
            title="ייבוא רשימת אתרים", filetypes=[("רשימת אתרים", "*.json")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            added, updated, problems = self.svc.registry.import_data(data)
        except (OSError, ValueError, AttributeError) as exc:
            self._show_error(exc)
            return
        summary = f"נוספו {added} אתרים, עודכנו {updated} אתרים."
        if problems:
            messagebox.showwarning("ייבוא", summary + "\n\nהערות:\n" + "\n".join(problems[:8]))
        else:
            messagebox.showinfo("ייבוא", summary)
        self.refresh()

    # ----- service and CA ------------------------------------------------
    def toggle_service(self) -> None:
        if self.svc.running:
            self.stop_service()
        else:
            self.start_service()

    def start_service(self) -> None:
        if not is_admin():
            self._ask_admin("השירות צריך הרשאות מנהל כדי לשנות את קובץ ה-hosts ולהאזין לפורטים 80 ו-443.")
            return
        decision = can_start(
            is_admin=True,
            real_internet=self.real_internet,
            test_mode=self.test_mode,
            has_enabled_domains=bool(self.svc.registry.enabled_domains(valid_only=True)),
        )
        if not decision.allowed:
            messagebox.showwarning("לא ניתן להפעיל", decision.reason)
            return
        try:
            self.svc.start()
        except (ServiceError, OSError) as exc:
            self._show_error(exc)
        else:
            if self.svc.skipped_domains:
                messagebox.showwarning(
                    "דומיינים לא תקינים",
                    "השירות הופעל, אך הדומיינים הבאים לא תקינים ולכן לא הופעלו:\n"
                    + "\n".join(self.svc.skipped_domains),
                )
        self.refresh()

    def _ask_admin(self, reason: str) -> None:
        win = tk.Toplevel(self)
        win.title("נדרשות הרשאות מנהל")
        win.configure(bg=P["card"])
        win.transient(self)
        win.resizable(False, False)
        ttk.Label(win, text=reason, wraplength=420, justify="right",
                  background=P["card"]).pack(padx=24, pady=(22, 14))
        row = ttk.Frame(win, style="Side.TFrame")
        row.pack(pady=(0, 20))
        RoundButton(row, "הפעל כמנהל", lambda: self._relaunch(win), variant="primary",
                    bg=P["card"], height=40).pack(side="right", padx=6, ipadx=14)
        RoundButton(row, "ביטול", win.destroy, bg=P["card"], height=40).pack(side="right", padx=6, ipadx=14)
        win.grab_set()

    def _relaunch(self, dialog: tk.Toplevel) -> None:
        if relaunch_as_admin():
            dialog.destroy()
            self._on_close()  # closes this window; the elevated copy opens
        else:
            messagebox.showerror(
                "לא ניתן להפעיל כמנהל",
                "אישור ההרשאות בוטל או נכשל. החלון הנוכחי נשאר פתוח.",
                parent=dialog,
            )

    def stop_service(self) -> None:
        self.svc.stop()
        self.refresh()

    def toggle_ca(self) -> None:
        if self._ca_installed():
            if messagebox.askyesno("הסרת CA", "להסיר את התעודה המקומית מחנות התעודות?"):
                self.uninstall_ca()
        else:
            self.install_ca()

    def install_ca(self) -> None:
        try:
            self._ca_cache = None
            self.svc.certs.ensure_ca()
            self.svc.certs.install_ca()
            messagebox.showinfo("CA", "ה-CA הותקן. אם הופיע חלון אזהרה של Windows, אשר אותו.")
        except Exception as exc:  # noqa: BLE001 - show any failure to the user
            self._show_error(exc)
        self.refresh()

    def uninstall_ca(self) -> None:
        try:
            self._ca_cache = None
            self.svc.certs.uninstall_ca()
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)
        self.refresh()

    def purge(self) -> None:
        if not messagebox.askyesno(
            "ניקוי מלא",
            "הפעולה תעצור את השירות, תחזיר את קובץ ה-hosts, תסיר את ה-CA, ותמחק את התעודות וההיסטוריה.\n"
            "הרשימה והתוכן של הדומיינים יישמרו. להמשיך?",
        ):
            return
        try:
            self._ca_cache = None
            self.svc.purge()
            messagebox.showinfo("ניקוי מלא", "כל השינויים הוסרו.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)
        self.refresh()

    # ----- history -------------------------------------------------------
    def show_history(self) -> None:
        win = tk.Toplevel(self)
        win.title("היסטוריית בקשות")
        win.geometry("780x460")
        win.configure(bg=P["bg"])
        table = ttk.Treeview(win, columns=("time", "domain", "path", "result"), show="headings")
        for col, text, width in (
            ("time", "זמן", 160), ("domain", "דומיין", 220), ("path", "נתיב", 180), ("result", "תוצאה", 160)
        ):
            table.heading(col, text=text, anchor="e")
            table.column(col, width=width, anchor="e")
        table.pack(fill="both", expand=True, padx=14, pady=14)
        for entry in self.svc.log.recent(200):
            table.insert("", "end", values=(entry["time"], entry["domain"], entry["path"], entry["result"]))

        def clear():
            self.svc.log.clear()
            table.delete(*table.get_children())

        RoundButton(win, "מחק היסטוריה", clear, variant="danger", bg=P["bg"], height=40).pack(
            pady=(0, 14), ipadx=20)

    # ----- bypass option and auto-stop -----------------------------------
    def _toggle_test_mode(self) -> None:
        self.test_mode = bool(self.test_var.get())
        self.refresh()

    def _schedule_probe(self) -> None:
        threading.Thread(target=self._probe_worker, daemon=True).start()
        self._drain_probe()
        self.after(CHECK_INTERVAL_MS, self._schedule_probe)

    def _probe_worker(self) -> None:
        self._probe_queue.put(internet_reachable())

    def _drain_probe(self) -> None:
        try:
            while True:
                self.real_internet = self._probe_queue.get_nowait()
        except queue.Empty:
            pass
        if must_auto_stop(running=self.svc.running, real_internet=self.real_internet,
                          test_mode=self.test_mode):
            self.svc.stop()
            messagebox.showwarning(
                "השירות נעצר",
                "זוהה חיבור אינטרנט אמיתי, והשירות נעצר אוטומטית כדי לא לשבש את הגלישה.",
            )
        self.refresh()

    # ----- system tray ---------------------------------------------------
    def minimize_to_tray(self) -> None:
        if not TrayIcon.available():
            messagebox.showinfo(
                "מגש המערכת",
                "כדי למזער למגש צריך להתקין פעם אחת:\n\npython -m pip install pystray pillow",
            )
            return
        self._tray.start()
        self.withdraw()

    def _poll_tray(self) -> None:
        try:
            while True:
                message = self._tray_queue.get_nowait()
                if message == "show":
                    self._tray.stop()
                    self.deiconify()
                    self.lift()
                elif message == "quit":
                    self._on_close()
                    return
        except queue.Empty:
            pass
        self.after(TRAY_POLL_MS, self._poll_tray)

    def _on_close(self) -> None:
        self._tray.stop()
        if self.svc.running:
            self.svc.stop()
        self.destroy()


def main() -> None:
    App(Service(data_dir=default_data_dir())).mainloop()


if __name__ == "__main__":
    main()

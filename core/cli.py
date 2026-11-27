"""Command-line interface. Usage: python -m core.cli <command> [options]

Data is kept in %APPDATA%\\DummySite on Windows, or in --data-dir if given.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .registry import RegistryError
from .service import Service, ServiceError


def default_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "DummySite"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dummysite", description="Offline dummy-site tool")
    p.add_argument("--data-dir", type=Path, default=None)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show domains and their state")

    add = sub.add_parser("add", help="Add a domain, optionally with its own content file")
    add.add_argument("domain")
    add.add_argument("--file", type=Path, default=None)

    rm = sub.add_parser("remove", help="Remove a domain")
    rm.add_argument("domain")

    en = sub.add_parser("enable", help="Enable a domain")
    en.add_argument("domain")

    dis = sub.add_parser("disable", help="Disable a domain")
    dis.add_argument("domain")

    sd = sub.add_parser("set-default", help="Set the default page, or clear it with no file")
    sd.add_argument("--file", type=Path, default=None)

    sub.add_parser("install-ca", help="Install the local CA in the current user's trust store")
    sub.add_parser("uninstall-ca", help="Remove the local CA from the trust store")

    run = sub.add_parser("run", help="Run the service until Enter is pressed")
    run.add_argument("--http-port", type=int, default=80)
    run.add_argument("--https-port", type=int, default=443)

    sub.add_parser("log", help="Show the last requests")
    sub.add_parser("clear-log", help="Delete request history")
    sub.add_parser("purge", help="Remove all changes: hosts block, CA, certificates, history")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir or default_data_dir()
    svc = Service(data_dir=data_dir)
    try:
        return _dispatch(args, svc)
    except (RegistryError, ServiceError) as exc:
        print(f"שגיאה: {exc}")
        return 1


def _dispatch(args, svc: Service) -> int:
    cmd = args.command
    if cmd == "list":
        entries = svc.registry.all()
        if not entries:
            print("אין דומיינים ברשימה")
        for e in entries:
            state = "פעיל" if e.enabled else "כבוי"
            content = e.content_file or "ברירת מחדל"
            print(f"{e.domain:30} {state:6} תוכן: {content}")
        print(f"CA מותקן: {'כן' if svc.certs.is_installed() else 'לא'}")
        return 0
    if cmd == "add":
        svc.registry.add(args.domain, content_source=args.file)
        print(f"נוסף: {args.domain}")
        return 0
    if cmd == "remove":
        svc.registry.remove(args.domain)
        print(f"הוסר: {args.domain}")
        return 0
    if cmd in ("enable", "disable"):
        svc.registry.set_enabled(args.domain, cmd == "enable")
        print(f"{args.domain}: {'הופעל' if cmd == 'enable' else 'כובה'}")
        return 0
    if cmd == "set-default":
        svc.registry.set_default(args.file)
        print("ברירת המחדל עודכנה" if args.file else "ברירת המחדל נוקתה")
        return 0
    if cmd == "install-ca":
        svc.certs.install_ca()
        print("ה-CA הותקן. Windows עשוי להציג חלון אזהרה, אשר אותו עם Yes.")
        return 0
    if cmd == "uninstall-ca":
        svc.certs.uninstall_ca()
        print("ה-CA הוסר מחנות התעודות.")
        return 0
    if cmd == "run":
        svc.http_port = args.http_port
        svc.https_port = args.https_port
        svc.start()
        print("השירות פועל. לחץ Enter כדי לעצור.")
        try:
            input()
        finally:
            svc.stop()
        print("השירות נעצר וקובץ ה-hosts שוחזר.")
        return 0
    if cmd == "log":
        for entry in reversed(svc.log.recent(50)):
            print(f"{entry['time']}  {entry['domain']:30} {entry['path']:20} {entry['result']}")
        return 0
    if cmd == "clear-log":
        svc.log.clear()
        print("היסטוריית הבקשות נמחקה.")
        return 0
    if cmd == "purge":
        svc.purge()
        print("כל השינויים הוסרו.")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

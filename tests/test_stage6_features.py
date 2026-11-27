import json
import tempfile
import unittest
from pathlib import Path

from core.hosts import HostsManager
from core.registry import Registry, RegistryError
from core.service import Service
from core.tray import TrayIcon
from tests.test_stage2_certs import FakeRunner


class InvalidDomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reg = Registry(Path(self.tmp.name) / "data")

    def test_invalid_name_is_kept_and_marked(self):
        entry = self.reg.add("1112מצ.4", strict=False)
        self.assertFalse(entry.valid)
        again = Registry(Path(self.tmp.name) / "data")
        self.assertFalse(again.get("1112מצ.4").valid)
        self.assertIn("1112מצ.4", [e.domain for e in again.all()])

    def test_strict_mode_still_refuses(self):
        with self.assertRaises(RegistryError):
            self.reg.add("1112מצ.4")

    def test_valid_name_is_marked_valid(self):
        self.assertTrue(self.reg.add("shop.test").valid)


class ImportExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.page = self.root / "page.html"
        self.page.write_text("<html>x</html>", encoding="utf-8")

    def test_round_trip_keeps_domains_state_and_source_locations(self):
        src = Registry(self.root / "a")
        src.add("shop.test", content_source=self.page)
        src.add("off.test")
        src.set_enabled("off.test", False)
        exported = json.loads(json.dumps(src.export_data(), ensure_ascii=False))

        dst = Registry(self.root / "b")
        added, updated, problems = dst.import_data(exported)
        self.assertEqual((added, updated, problems), (2, 0, []))
        self.assertFalse(dst.get("off.test").enabled)
        self.assertEqual(Path(dst.get("shop.test").source_path), self.page.resolve())

    def test_import_updates_existing_and_reports_missing_files(self):
        dst = Registry(self.root / "b")
        dst.add("shop.test")
        data = {"domains": [
            {"domain": "shop.test", "enabled": False, "source_path": str(self.root / "gone.html")},
        ]}
        added, updated, problems = dst.import_data(data)
        self.assertEqual((added, updated), (0, 1))
        self.assertFalse(dst.get("shop.test").enabled)
        self.assertEqual(len(problems), 1)


class ServiceSkipsInvalidTests(unittest.TestCase):
    def test_start_skips_invalid_and_hosts_only_gets_valid(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        hosts = root / "hosts"
        hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        svc = Service(data_dir=root / "data", hosts_path=hosts, http_port=0, https_port=0,
                      runner=FakeRunner())
        svc.registry.add("shop.test")
        svc.registry.add("1112מצ.4", strict=False)
        svc.start()
        try:
            text = hosts.read_text(encoding="utf-8")
            self.assertIn("127.0.0.1 shop.test", text)
            self.assertNotIn("1112", text)
            self.assertEqual(svc.skipped_domains, ["1112מצ.4"])
        finally:
            svc.stop()
        self.assertEqual(hosts.read_text(encoding="utf-8"), "127.0.0.1 localhost\n")

    def test_start_refused_when_only_invalid_domains(self):
        from core.service import ServiceError
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        svc = Service(data_dir=Path(tmp.name) / "d", hosts_path=Path(tmp.name) / "h",
                      http_port=0, https_port=0, runner=FakeRunner())
        svc.registry.add("bad name", strict=False)
        with self.assertRaises(ServiceError):
            svc.start()


class TrayTests(unittest.TestCase):
    def test_availability_check_never_crashes(self):
        self.assertIsInstance(TrayIcon.available(), bool)


if __name__ == "__main__":
    unittest.main()

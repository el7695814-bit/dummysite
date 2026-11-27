import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from core.cli import main


def run(data_dir, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["--data-dir", str(data_dir), *args])
    return code, buf.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"

    def test_add_list_enable_disable_remove(self):
        self.assertEqual(run(self.data, "add", "shop.test")[0], 0)
        code, out = run(self.data, "list")
        self.assertIn("shop.test", out)
        self.assertIn("פעיל", out)
        run(self.data, "disable", "shop.test")
        self.assertIn("כבוי", run(self.data, "list")[1])
        run(self.data, "enable", "shop.test")
        self.assertEqual(run(self.data, "remove", "shop.test")[0], 0)
        self.assertIn("אין דומיינים", run(self.data, "list")[1])

    def test_invalid_domain_reports_error_and_exit_code(self):
        code, out = run(self.data, "add", "not valid")
        self.assertEqual(code, 1)
        self.assertIn("שגיאה", out)

    def test_add_with_content_file(self):
        src = Path(self.tmp.name) / "p.html"
        src.write_text("<html></html>", encoding="utf-8")
        run(self.data, "add", "shop.test", "--file", str(src))
        self.assertIn("shop.test.html", run(self.data, "list")[1])


if __name__ == "__main__":
    unittest.main()

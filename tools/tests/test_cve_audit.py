"""CVE fetch/cache behavior, using mocked HTTPS responses only."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cve_audit", ROOT / "tools/audit-cve-records.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def run_audit(self, root, argv):
        with patch.object(audit, "ROOT", root), patch("sys.argv", ["audit"] + argv), contextlib.redirect_stdout(io.StringIO()):
            return audit.main()

    def make_reference(self, root):
        (root / "drone-wireless-vuln-reference.md").write_text("CVE-2099-12345", encoding="utf-8")

    def test_default_has_no_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_reference(root)
            with patch.object(audit, "urlopen") as fetch:
                self.assertEqual(self.run_audit(root, []), 1)
                fetch.assert_not_called()

    def test_missing_public_record_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_reference(root)
            error = HTTPError("https://example.invalid", 404, "Not found", {}, None)
            with patch.object(audit, "urlopen", side_effect=error):
                self.assertEqual(self.run_audit(root, ["--refresh"]), 0)
            with patch.object(audit, "urlopen") as fetch:
                self.assertEqual(self.run_audit(root, []), 0)
                fetch.assert_not_called()
            index = json.loads((root / "work/cve-record-index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["entries"][0]["lookup_status"], "no-public-record")
            self.assertFalse(index["entries"][0]["lab_verified"])

    def test_wrong_identifier_not_promoted_to_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_reference(root)
            data = json.dumps({"cveMetadata": {"cveId": "CVE-2099-99999", "state": "PUBLISHED"}}).encode()
            with patch.object(audit, "urlopen", return_value=io.BytesIO(data)):
                self.assertEqual(self.run_audit(root, ["--refresh"]), 1)
            self.assertFalse((root / "work/cve-records/CVE-2099-12345.json").exists())


if __name__ == "__main__":
    unittest.main()

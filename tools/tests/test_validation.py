"""Verify that corpus tampering and stale evidence are detected."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("validation", ROOT / "tools/validate-project.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class ValidationTests(unittest.TestCase):
    def test_committed_corpus_passes(self):
        self.assertEqual(validation.check_artifacts(ROOT), [])

    def test_byte_corruption_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "firmware", root / "firmware")
            path = root / "firmware/reference/elrs-4.1.0.lua"
            with path.open("ab") as stream:
                stream.write(b"corruption")
            errors = validation.check_artifacts(root)
            self.assertTrue(any("SHA-256 mismatch" in item for item in errors))
            self.assertTrue(any("Register hash/size mismatch" in item for item in errors))

    def test_missing_provenance_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "firmware", root / "firmware")
            path = root / "firmware/artifact-register.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["artifacts"].pop()
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(any("exactly once" in error for error in validation.check_artifacts(root)))

    def test_unsafe_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "firmware").mkdir()
            (root / "firmware/SHA256SUMS").write_text("0" * 64 + "  firmware/../../outside\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                validation.manifest_entries(root)

    def test_missing_local_link_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "README.md"
            path.write_text("[bad](missing.md)\n[remote](https://example.com)\n[anchor](#heading)", encoding="utf-8")
            errors = validation.local_link_errors(root, [path])
            self.assertEqual(len(errors), 1)

    def test_new_cve_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "research").mkdir()
            (root / "drone-wireless-vuln-reference.md").write_text("CVE-2099-12345", encoding="utf-8")
            (root / "research/cve-record-index.json").write_text('{"entries": []}', encoding="utf-8")
            self.assertEqual(validation.check_cve_coverage(root), ["CVE missing publication review: CVE-2099-12345"])


if __name__ == "__main__":
    unittest.main()

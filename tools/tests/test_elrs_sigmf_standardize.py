"""Tests for tools/elrs-sigmf-standardize.py (RQ1 SigMF capture standardization).

These tests run against a small synthetic sc16le fixture (no private capture,
no network, no hardware), per the project's SYNTHETIC/OFFLINE-TESTED evidence
target. They verify:

* the SigMF record has the correct version, datatype, and sample count;
* the original-file SHA-256 is recorded as provenance and matches an
  independent computation;
* the sidecar is written next to the IQ file and the IQ file is byte-for-byte
  untouched (the original file hash is preserved);
* idempotency (a second write without --force does not clobber);
* the ``--check`` invariant validator accepts a good sidecar and rejects a
  tampered one;
* a non-sc16le file size (not a multiple of 4 bytes) is rejected.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "elrs_sigmf_standardize", _TOOLS / "elrs-sigmf-standardize.py"
)
_mod = importlib.util.module_from_spec(_SPEC)
sys.modules["elrs_sigmf_standardize"] = _mod
_SPEC.loader.exec_module(_mod)


def _make_sc16le_fixture(path: Path, n_samples: int = 16) -> bytes:
    """Write a deterministic interleaved complex int16 fixture.

    Bytes: I0,Q0,I1,Q1,... each int16 little-endian. Returns the raw bytes so
    the caller can recompute the SHA-256 independently.
    """
    import struct

    raw = bytearray()
    for i in range(n_samples):
        # Deterministic pseudo-samples in int16 range.
        i_val = (i * 7) % 65536 - 32768
        q_val = (i * 13) % 65536 - 32768
        raw += struct.pack("<hh", i_val, q_val)
    path.write_bytes(bytes(raw))
    return bytes(raw)


class SigmfBuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.meta = _mod.CaptureMeta(
            filename="fixture.iq",
            creation_time_estimate=None,
            notes="synthetic fixture",
        )
        self.raw = _make_sc16le_fixture(self.tmp / "fixture.iq", n_samples=16)

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_version_and_datatype(self):
        rec = _mod.build_sigmf_meta(self.meta, self.tmp / "fixture.iq")
        self.assertEqual(rec["global"]["core:version"], "1.0.0")
        self.assertEqual(rec["global"]["core:datatype"], "sc16le")

    def test_sample_count_from_file_size(self):
        rec = _mod.build_sigmf_meta(self.meta, self.tmp / "fixture.iq")
        # 16 samples * 4 bytes = 64 bytes -> 16 samples
        self.assertEqual(rec["global"]["core:sample_size"], 16)
        self.assertEqual(rec["global"]["core:sample_rate"], 4_000_000)

    def test_provenance_sha_matches_independent_hash(self):
        rec = _mod.build_sigmf_meta(self.meta, self.tmp / "fixture.iq")
        expected = hashlib.sha256(self.raw).hexdigest()
        self.assertEqual(rec["global"]["provenance:original_sha256"], expected)

    def test_annotation_covers_full_capture(self):
        rec = _mod.build_sigmf_meta(self.meta, self.tmp / "fixture.iq")
        self.assertEqual(rec["annotations"][0]["core:length"], 16)
        self.assertEqual(rec["annotations"][0]["core:extends"], "global")

    def test_private_flagged(self):
        rec = _mod.build_sigmf_meta(self.meta, self.tmp / "fixture.iq")
        self.assertIs(rec["global"]["provenance:private"], True)


class SigmfWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.meta = _mod.CaptureMeta(filename="fixture.iq", notes="synthetic")
        self.raw = _make_sc16le_fixture(self.tmp / "fixture.iq", n_samples=32)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_sidecar_next_to_iq(self):
        out = _mod.write_sigmf_meta(self.tmp / "fixture.iq", self.meta)
        self.assertTrue(out.exists())
        self.assertEqual(out.name, "fixture.iq.sigmf-meta")
        # The IQ file is byte-for-byte untouched.
        self.assertEqual((self.tmp / "fixture.iq").read_bytes(), self.raw)

    def test_idempotent_without_force(self):
        out = _mod.write_sigmf_meta(self.tmp / "fixture.iq", self.meta)
        first = out.read_text()
        # Mutate the sidecar, then re-write without force: must NOT clobber.
        out.write_text(first + "\n# sentinel\n")
        _mod.write_sigmf_meta(self.tmp / "fixture.iq", self.meta)
        self.assertIn("# sentinel", out.read_text())

    def test_force_overwrites(self):
        out = _mod.write_sigmf_meta(self.tmp / "fixture.iq", self.meta)
        out.write_text("garbage")
        _mod.write_sigmf_meta(self.tmp / "fixture.iq", self.meta, force=True)
        json.loads(out.read_text())  # valid JSON again

    def test_non_sc16le_size_rejected(self):
        bad = self.tmp / "bad.iq"
        bad.write_bytes(b"\x00\x01\x02")  # 3 bytes, not a multiple of 4
        with self.assertRaises(ValueError):
            _mod.sample_count(bad)


class SigmfCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.meta = _mod.CaptureMeta(filename="fixture.iq", notes="synthetic")
        _make_sc16le_fixture(self.tmp / "fixture.iq", n_samples=8)

    def tearDown(self):
        self._tmp.cleanup()

    def test_check_accepts_good_sidecar(self):
        out = _mod.write_sigmf_meta(self.tmp / "fixture.iq", self.meta)
        report = _mod.validate_sigmf_meta(out)
        self.assertTrue(all(report.values()))

    def test_check_rejects_tampered_sidecar(self):
        out = _mod.write_sigmf_meta(self.tmp / "fixture.iq", self.meta)
        data = json.loads(out.read_text())
        data["global"]["core:sample_size"] = 999  # breaks annotation==size
        out.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            _mod.validate_sigmf_meta(out)


class TrackedCorpusTests(unittest.TestCase):
    """The tracked-capture table is internally consistent (no I/O on captures)."""

    def test_two_tracked_captures(self):
        self.assertEqual(len(_mod.TRACKED_CAPTURES), 2)

    def test_oct16_has_creation_time_anchor(self):
        oct16 = _mod.TRACKED_CAPTURES[
            "tinyhawk_rc_pair_20261016_retry_2441MHz_4Msps_sc16.iq"
        ]
        self.assertEqual(oct16.creation_time_estimate, "2026-10-16T17:45:37.014895Z")

    def test_oct15_has_no_anchor(self):
        oct15 = _mod.TRACKED_CAPTURES[
            "e8_tinyhawk_pair_on_2026-10-15_2441MHz_4Msps_rx2_sc16.iq"
        ]
        self.assertIsNone(oct15.creation_time_estimate)

    def test_sample_rate_and_center_consistent(self):
        for meta in _mod.TRACKED_CAPTURES.values():
            self.assertEqual(meta.sample_rate_hz, 4_000_000)
            self.assertEqual(meta.center_hz, 2_441_000_000.0)


class PipelineSigmfReaderTests(unittest.TestCase):
    """The passive pipeline's read_sigmf_timebase() reads the sidecar (RQ1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # The pipeline module loads elrs-sigmf-standardize.py the same way.
        spec = importlib.util.spec_from_file_location(
            "elrs_passive_pipeline", _TOOLS / "elrs-passive-pipeline.py"
        )
        # The pipeline imports elrs_core and optional DSP at load; load it in an
        # isolated namespace to avoid side effects, then grab the one function.
        import types

        mod = types.ModuleType("elrs_passive_pipeline_under_test")
        code = (_TOOLS / "elrs-passive-pipeline.py").read_text(encoding="utf-8")
        # Provide the constants/imports read_sigmf_timebase depends on.
        mod_globals = {
            "json": __import__("json"),
            "Path": Path,
            "SAMPLE_FORMATS": {"sc16le": (4, "<hh"), "cf32le": (8, "<ff")},
            "__name__": "elrs_passive_pipeline_under_test",
        }
        # Extract just the function source to exec it in isolation.
        import ast

        tree = ast.parse(code)
        fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "read_sigmf_timebase"
        )
        mod_globals["__file__"] = str(_TOOLS / "elrs-passive-pipeline.py")
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<fn>", "exec"), mod_globals)
        self.read_sigmf_timebase = mod_globals["read_sigmf_timebase"]

    def tearDown(self):
        self._tmp.cleanup()

    def _make_iq_with_sidecar(self, name="cap.iq", nsamples=8):
        import struct

        iq = self.tmp / name
        raw = b"".join(struct.pack("<hh", i, i) for i in range(nsamples))
        iq.write_bytes(raw)
        meta = _mod.CaptureMeta(filename=name, notes="synthetic")
        _mod.write_sigmf_meta(iq, meta)
        return iq

    def test_reads_timebase_from_sidecar(self):
        iq = self._make_iq_with_sidecar()
        tb = self.read_sigmf_timebase(iq)
        self.assertIsNotNone(tb)
        self.assertEqual(tb["sample_rate"], 4_000_000)
        self.assertEqual(tb["center_hz"], 2_441_000_000)
        self.assertEqual(tb["format"], "sc16le")
        # Provenance SHA recorded and matches the file.
        import hashlib

        self.assertEqual(
            tb["sha256"], hashlib.sha256(iq.read_bytes()).hexdigest()
        )

    def test_returns_none_without_sidecar(self):
        iq = self.tmp / "no-sidecar.iq"
        iq.write_bytes(b"\x00" * 16)
        self.assertIsNone(self.read_sigmf_timebase(iq))

    def test_returns_none_on_corrupt_sidecar(self):
        iq = self._make_iq_with_sidecar()
        sidecar = iq.with_suffix(iq.suffix + ".sigmf-meta")
        sidecar.write_text("not json {")
        self.assertIsNone(self.read_sigmf_timebase(iq))

    def test_returns_none_on_unsupported_datatype(self):
        iq = self._make_iq_with_sidecar()
        sidecar = iq.with_suffix(iq.suffix + ".sigmf-meta")
        data = _mod.build_sigmf_meta(
            _mod.CaptureMeta(filename="cap.iq"), iq
        )
        data["global"]["core:datatype"] = "ci8"  # not in SAMPLE_FORMATS
        sidecar.write_text(json.dumps(data))
        self.assertIsNone(self.read_sigmf_timebase(iq))


if __name__ == "__main__":
    unittest.main()

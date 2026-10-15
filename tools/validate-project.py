#!/usr/bin/env python3
"""Offline repository checks, Python 3.10+, no third-party dependencies.

Run from any directory: python tools/validate-project.py
Checks Python syntax without importing tools, firmware bytes/provenance coverage,
JSON/CSV consistency, CVE-index coverage, and local Markdown file links. Does not
contact devices, transmit RF, fetch websites, or execute research scripts.
"""
import ast
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def manifest_entries(root):
    entries = {}
    for line in (root / "firmware/SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (firmware/[^\\]+)", line)
        if not match:
            raise ValueError(f"Malformed manifest entry: {line!r}")
        digest, name = match.groups()
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or name in entries:
            raise ValueError(f"Unsafe or duplicate manifest path: {name}")
        entries[name] = digest
    if not entries:
        raise ValueError("Empty firmware manifest")
    return entries


def check_artifacts(root):
    errors = []
    entries = manifest_entries(root)
    register = json.loads((root / "firmware/artifact-register.json").read_text(encoding="utf-8"))
    artifacts = register["artifacts"]
    by_path = {item["path"]: item for item in artifacts}
    if len(by_path) != len(artifacts) or set(entries) != set(by_path):
        errors.append("Artifact register must cover every manifest entry exactly once")
    for name, digest in entries.items():
        path = root / name
        if not path.is_file():
            errors.append(f"Missing artifact: {name}")
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            errors.append(f"SHA-256 mismatch: {name}")
        item = by_path.get(name, {})
        if item.get("sha256") != digest or item.get("size_bytes") != len(data):
            errors.append(f"Register hash/size mismatch: {name}")
        for field in ("artifact_kind", "hardware_match", "source_project", "evidence"):
            if not item.get(field):
                errors.append(f"Missing provenance field {field}: {name}")
        for field in ("original_download_url", "upstream_commit", "declared_version", "e8_specific"):
            if field not in item:
                errors.append(f"Missing explicit provenance field {field}: {name}")
    return errors


def check_tables(root):
    errors = []
    raw = json.loads((root / "tools/elrs-rainbow-table.json").read_text(encoding="utf-8"))
    entries = raw if isinstance(raw, list) else raw["entries"]
    with (root / "tools/elrs-rainbow-table.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(entries) != len(rows):
        errors.append("Rainbow CSV/JSON entry counts differ")
    for index, (entry, row) in enumerate(zip(entries, rows)):
        for json_key, csv_key in (("phrase", "phrase"), ("uid_hex", "uid"), ("sync_bytes", "sync_bytes")):
            if entry[json_key] != row[csv_key]:
                errors.append(f"Rainbow row {index}: {json_key} differs")
        for field in ("fhss_seed", "crc_init"):
            if entry[field] != int(row[field], 0):
                errors.append(f"Rainbow row {index}: {field} differs")
        if bytes(entry["uid_bytes"]).hex() != entry["uid_hex"].replace(":", "").lower():
            errors.append(f"Rainbow row {index}: UID byte representation differs")
    return errors


def check_cve_coverage(root):
    text = (root / "drone-wireless-vuln-reference.md").read_text(encoding="utf-8")
    cited = set(re.findall(r"CVE-\d{4}-\d{4,}", text))
    index = json.loads((root / "research/cve-record-index.json").read_text(encoding="utf-8"))
    entries = index["entries"]
    indexed = {entry["id"] for entry in entries}
    errors = [f"CVE missing publication review: {name}" for name in sorted(cited - indexed)]
    if len(indexed) != len(entries):
        errors.append("Duplicate CVE index entries")
    for entry in entries:
        if entry["claim_review"] not in ("record-only", "reviewed-with-qualification"):
            errors.append(f"Unknown claim-review level: {entry['id']}")
        if entry["claim_review"] == "reviewed-with-qualification" and not entry.get("qualification"):
            errors.append(f"Missing reviewed qualification: {entry['id']}")
    return errors


def local_link_errors(root, paths):
    errors = []
    for path in paths:
        # Anchors are deliberately outside this check: verify local file targets.
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            parts = urlsplit(target.strip("<>"))
            if parts.scheme or not parts.path:
                continue
            resolved = (path.parent / unquote(parts.path)).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                errors.append(f"Missing/outside local link in {path.relative_to(root)}: {target}")
    return errors


def main():
    try:
        result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                cwd=ROOT, check=True, capture_output=True)
        paths = sorted({ROOT / name.decode("utf-8") for name in result.stdout.split(b"\0") if name})
        errors = []
        vendor_dirs = {"dji-sdk-protocol"}
        for path in paths:
            if path.suffix == ".py" and not any(v in path.parts for v in vendor_dirs):
                try:
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))
                except SyntaxError as exc:
                    errors.append(f"Python syntax: {exc}")
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
        errors.extend(check_artifacts(ROOT))
        errors.extend(check_tables(ROOT))
        errors.extend(check_cve_coverage(ROOT))
        errors.extend(local_link_errors(ROOT, [p for p in paths if p.suffix == ".md"]))
        for error in errors:
            print(error, file=sys.stderr)
        if errors:
            return 1
        print(f"PASS: syntax, JSON, corpus hashes/provenance, rainbow data, CVE coverage, local file links ({len(paths)} files)")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Check a workpaper bundle and replay its automated observations."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from controltrace import __version__
from controltrace.data import DEMO_CUTOFF, SOURCE_TABLES
from controltrace.exports import _json
from controltrace.rules import RULES, evaluate


class BundleVerificationError(ValueError):
    """The bundle cannot support the result it claims to contain."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleVerificationError(message)


def verify_bundle(path: str | Path) -> dict[str, Any]:
    """Verify file hashes and replay the current rules against bundled source JSON."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _require(len(names) == len(set(names)), "Bundle contains duplicate file names")
        _require("manifest.json" in names, "Bundle has no manifest.json")
        manifest = json.loads(archive.read("manifest.json"))
        expected_files = manifest.get("files_sha256")
        _require(isinstance(expected_files, dict), "Manifest has no complete file hash list")
        _require(set(expected_files) == set(names) - {"manifest.json"},
                 "Manifest file list does not match bundle contents")
        for name, expected_hash in expected_files.items():
            actual_hash = hashlib.sha256(archive.read(name)).hexdigest()
            _require(actual_hash == expected_hash, f"File hash mismatch: {name}")

        source_hashes = manifest.get("source_sha256")
        expected_source_names = {
            f"source_tables/{table}.{suffix}"
            for table in SOURCE_TABLES for suffix in ("csv", "json")
        }
        _require(isinstance(source_hashes, dict) and set(source_hashes) == expected_source_names,
                 "Source hash list is incomplete")
        _require(all(expected_files[name] == value for name, value in source_hashes.items()),
                 "Source hash list disagrees with complete file list")
        dataset_hash = hashlib.sha256(_json(source_hashes).encode("utf-8")).hexdigest()
        _require(manifest.get("dataset_sha256") == dataset_hash,
                 "Dataset fingerprint does not match source hashes")
        _require(manifest.get("audit_cutoff") == DEMO_CUTOFF,
                 "Bundle cutoff does not match the current control rules")
        _require(manifest.get("controltrace_version") == __version__,
                 "Bundle was created by a different ControlTrace version")
        _require(json.loads(archive.read("rules.json")) == RULES,
                 "Rule catalog differs from the installed ControlTrace version")

        data = {
            table: json.loads(archive.read(f"source_tables/{table}.json"))
            for table in SOURCE_TABLES if table != "expected_results"
        }
        replayed = evaluate(data)
        all_ids = [finding["finding_id"] for finding in replayed]
        _require(len(all_ids) == len(set(all_ids)), "Replay produced duplicate finding IDs")
        _require(manifest.get("all_finding_ids") == all_ids,
                 "Replayed findings differ from the bundle's full result list")
        selected_ids = manifest.get("finding_ids")
        _require(isinstance(selected_ids, list), "Manifest finding list is invalid")
        _require(selected_ids == [item for item in all_ids if item in set(selected_ids)],
                 "Selected findings are missing, repeated or out of order")
        scope = manifest.get("export_scope")
        _require(scope in ("all", "selected"), "Manifest export scope is invalid")
        if scope == "all":
            _require(selected_ids == all_ids, "Full export omits replayed findings")

        rows = list(csv.DictReader(io.StringIO(archive.read("findings.csv").decode("utf-8-sig"))))
        _require([row["finding_id"] for row in rows] == selected_ids,
                 "Findings CSV disagrees with manifest")
        replay_by_id = {finding["finding_id"]: finding for finding in replayed}
        for row in rows:
            original = replay_by_id[row["finding_id"]]
            for field in (
                "control_id", "issue_code", "classification", "risk", "system",
                "occurred_at", "entity_type", "entity_id",
            ):
                _require(row[field] == original[field],
                         f"Replayed field differs for {row['finding_id']}: {field}")
            _require(json.loads(row["evidence_ids"]) == original["evidence_ids"],
                     f"Replayed evidence differs for {row['finding_id']}")
            _require(f"workpapers/{row['finding_id']}.md" in expected_files,
                     f"Missing workpaper for {row['finding_id']}")

    return {
        "dataset_sha256": dataset_hash,
        "source_files_verified": len(source_hashes),
        "replayed_findings": len(all_ids),
        "exported_findings": len(selected_ids),
        "scope": scope,
    }

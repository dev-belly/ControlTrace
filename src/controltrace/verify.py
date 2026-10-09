"""Check a workpaper bundle and replay its automated observations."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any

from controltrace import __version__
from controltrace.data import DEMO_CUTOFF, SOURCE_TABLES
from controltrace.exports import _csv_bytes, _json, exception_csv, finding_workpaper
from controltrace.reviews import REVIEW_FIELDS, validate_review_decision
from controltrace.rules import RULES, evaluate


class BundleVerificationError(ValueError):
    """The bundle cannot support the result it claims to contain."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleVerificationError(message)


def _read_json(archive: zipfile.ZipFile, name: str) -> Any:
    """Read unambiguous, finite JSON before inspecting the bundle's schema."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, f"Duplicate JSON key in {name}: {key}")
            result[key] = value
        return result

    def finite_number(value: str) -> float:
        number = float(value)
        _require(math.isfinite(number), f"Non-finite JSON number in {name}")
        return number

    try:
        return json.loads(
            archive.read(name), object_pairs_hook=unique_object,
            parse_float=finite_number, parse_constant=finite_number,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BundleVerificationError(f"Invalid JSON in {name}: {error}") from error


def verify_bundle(path: str | Path) -> dict[str, Any]:
    """Verify file hashes and replay the current rules against bundled source JSON."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _require(len(names) == len(set(names)), "Bundle contains duplicate file names")
        _require("manifest.json" in names, "Bundle has no manifest.json")
        manifest = _read_json(archive, "manifest.json")
        _require(isinstance(manifest, dict), "Manifest must be a JSON object")
        _require(manifest.get("bundle_format_version") == 2,
                 "Unsupported bundle format; export again with this ControlTrace version")
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
        _require(_read_json(archive, "rules.json") == RULES,
                 "Rule catalog differs from the installed ControlTrace version")
        _require("reviews.json" in expected_files, "Bundle has no review history")

        source_rows = {}
        for table in SOURCE_TABLES:
            rows = _read_json(archive, f"source_tables/{table}.json")
            _require(isinstance(rows, list), f"Invalid source JSON: {table}")
            _require(all(isinstance(row, dict) for row in rows),
                     f"Source JSON rows must be objects: {table}")
            _require(archive.read(f"source_tables/{table}.csv") == _csv_bytes(rows),
                     f"Source CSV differs from JSON: {table}")
            source_rows[table] = rows
        data = {table: rows for table, rows in source_rows.items() if table != "expected_results"}
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
        histories = _read_json(archive, "reviews.json")
        _require(isinstance(histories, dict) and set(histories) == set(selected_ids),
                 "Review history does not match selected findings")
        selected_findings = []
        review_ids = set()
        for finding_id in selected_ids:
            history = histories[finding_id]
            _require(isinstance(history, list), f"Invalid review history for {finding_id}")
            order_keys = []
            for review in history:
                _require(isinstance(review, dict) and review.get("finding_id") == finding_id,
                         f"Review belongs to another finding: {finding_id}")
                _require(set(review) == REVIEW_FIELDS,
                         f"Invalid review fields for {finding_id}")
                review_id = review.get("review_id")
                _require(isinstance(review_id, str) and bool(review_id.strip())
                         and review_id not in review_ids,
                         f"Duplicate or invalid review ID: {finding_id}")
                review_ids.add(review_id)
                try:
                    reviewed_time = validate_review_decision(
                        review["reviewer"], review["status"], review["conclusion"],
                        review["notes"], review["reviewed_at"],
                    )
                except ValueError as error:
                    raise BundleVerificationError(
                        f"Invalid review for {finding_id}: {error}"
                    ) from error
                order_keys.append((reviewed_time, review_id))
            _require(order_keys == sorted(order_keys),
                     f"Review history is not in chronological order: {finding_id}")
            finding = {
                **replay_by_id[finding_id],
                "review_history": history,
                "latest_review": history[-1] if history else None,
            }
            selected_findings.append(finding)
            workpaper_name = f"workpapers/{finding_id}.md"
            _require(workpaper_name in expected_files, f"Missing workpaper for {finding_id}")
            _require(archive.read(workpaper_name) == finding_workpaper(finding, dataset_hash).encode("utf-8"),
                     f"Workpaper differs from replayed evidence or review: {finding_id}")
        _require(archive.read("findings.csv") == exception_csv(selected_findings),
                 "Findings CSV differs from replayed evidence or review")

    return {
        "dataset_sha256": dataset_hash,
        "source_files_verified": len(source_hashes),
        "replayed_findings": len(all_ids),
        "exported_findings": len(selected_ids),
        "scope": scope,
    }

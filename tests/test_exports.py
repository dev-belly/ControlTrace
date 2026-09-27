"""Workpaper exports must preserve the exact source snapshot and review trail."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

from controltrace.exports import exception_csv, workpaper_zip
from controltrace.rules import run_tests
from controltrace.store import get_finding, initialize_db, save_review


def test_workpaper_bundle_links_evidence_and_verifies_source_hashes(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    finding = run_tests(db_path)[0]
    save_review(
        db_path,
        finding["finding_id"],
        "Reviewer A",
        "closed",
        "confirmed_exception",
        "Confirmed against linked synthetic source rows.",
    )
    reviewed = get_finding(db_path, finding["finding_id"])
    bundle = workpaper_zip(db_path, {finding["finding_id"]})
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["finding_ids"] == [finding["finding_id"]]
        for name, expected_hash in manifest["source_sha256"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected_hash
        workpaper = archive.read(f"workpapers/{finding['finding_id']}.md").decode()
        for evidence_id in finding["evidence_ids"]:
            assert evidence_id in workpaper
        assert "Reviewer A" in workpaper
        assert "confirmed_exception" in workpaper
        assert reviewed["latest_review"]["reviewer"] == "Reviewer A"


def test_exception_csv_includes_cutoff_and_escapes_formula_notes(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    finding = run_tests(db_path)[0]
    save_review(
        db_path,
        finding["finding_id"],
        "Reviewer B",
        "in_review",
        "needs_more_evidence",
        "=HYPERLINK(\"https://example.invalid\")",
    )
    reviewed = get_finding(db_path, finding["finding_id"])
    csv_text = exception_csv([reviewed]).decode("utf-8-sig")
    assert "audit_cutoff" in csv_text
    assert "Reviewer B" in csv_text
    assert "'=HYPERLINK" in csv_text

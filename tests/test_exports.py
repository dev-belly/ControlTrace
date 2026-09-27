"""Workpaper exports must preserve the exact source snapshot and review trail."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from controltrace.exports import exception_csv, workpaper_zip
from controltrace.rules import run_tests
from controltrace.store import get_finding, get_table_rows, initialize_db, save_review
from controltrace.verify import BundleVerificationError, verify_bundle


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
    bundle_path = tmp_path / "workpapers.zip"
    bundle_path.write_bytes(bundle)
    result = verify_bundle(bundle_path)
    assert result["replayed_findings"] == 12
    assert result["exported_findings"] == 1
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["finding_ids"] == [finding["finding_id"]]
        for name, expected_hash in manifest["source_sha256"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected_hash
        assert json.loads(archive.read("source_tables/change_tickets.json")) == get_table_rows(
            db_path, "change_tickets"
        )
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


def test_workpaper_encodes_multiline_review_notes_as_data(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    finding = run_tests(db_path)[0]
    save_review(
        db_path, finding["finding_id"], "Reviewer C", "closed", "false_positive",
        "First line\n## Forged conclusion\n```",
    )
    bundle = workpaper_zip(db_path, {finding["finding_id"]})
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        workpaper = archive.read(f"workpapers/{finding['finding_id']}.md").decode()
        assert "\n## Forged conclusion\n" not in workpaper
        assert "First line\\n## Forged conclusion\\n```" in workpaper
        assert "Reviewer C" in workpaper


def test_bundle_verifier_rejects_changed_source_even_when_zip_is_readable(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    run_tests(db_path)
    original = workpaper_zip(db_path)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "source_tables/accounts.json":
                content += b" "
            target.writestr(name, content)
    with pytest.raises(BundleVerificationError, match="File hash mismatch"):
        verify_bundle(tampered)


def test_filtered_workpaper_export_rejects_unknown_finding_ids(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    run_tests(db_path)
    with pytest.raises(ValueError, match="Unknown finding IDs"):
        workpaper_zip(db_path, {"F-NO-SUCH-CASE"})

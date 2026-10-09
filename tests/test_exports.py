"""Workpaper exports must preserve the exact source snapshot and review trail."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from controltrace.cli import main
from controltrace.exports import _json, exception_csv, finding_workpaper, workpaper_zip
from controltrace.rules import run_tests
from controltrace.store import get_finding, get_table_rows, initialize_db, save_review
from controltrace.verify import BundleVerificationError, verify_bundle


@pytest.mark.parametrize("payload", ["[]", "null", "42", '"not a manifest"'])
def test_invalid_manifest_shape_returns_a_cli_error(tmp_path, capsys, payload):
    path = tmp_path / "invalid-manifest.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", payload)
    assert main(["verify", "--bundle", str(path)]) == 1
    assert "Manifest must be a JSON object" in capsys.readouterr().err


def test_verifier_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "duplicate-key.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", '{"bundle_format_version":2,"bundle_format_version":2}')
    with pytest.raises(BundleVerificationError, match="Duplicate JSON key"):
        verify_bundle(path)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_verifier_rejects_nonfinite_json_numbers(tmp_path, value):
    path = tmp_path / "nonfinite.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", '{"bundle_format_version":' + value + '}')
    with pytest.raises(BundleVerificationError, match="Non-finite JSON number"):
        verify_bundle(path)


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


def _rewrite_bundle_with_updated_hashes(original, path, member, replacement):
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files[member] = replacement(files[member])
    manifest = json.loads(files["manifest.json"])
    new_hash = hashlib.sha256(files[member]).hexdigest()
    manifest["files_sha256"][member] = new_hash
    if member in manifest["source_sha256"]:
        manifest["source_sha256"][member] = new_hash
        manifest["dataset_sha256"] = hashlib.sha256(
            _json(manifest["source_sha256"]).encode("utf-8")
        ).hexdigest()
    files["manifest.json"] = _json(manifest).encode("utf-8")
    with zipfile.ZipFile(path, "w") as target:
        for name, content in files.items():
            target.writestr(name, content)


@pytest.mark.parametrize("row", [None, [], 42, "not an account"])
def test_invalid_source_row_returns_a_cli_error_after_rehashing(tmp_path, capsys, row):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    run_tests(db_path)
    path = tmp_path / "invalid-source-row.zip"
    _rewrite_bundle_with_updated_hashes(
        workpaper_zip(db_path), path, "source_tables/accounts.json",
        lambda content: _json([row]).encode("utf-8"),
    )
    assert main(["verify", "--bundle", str(path)]) == 1
    assert "Source JSON rows must be objects: accounts" in capsys.readouterr().err


def test_verifier_rejects_changed_workpaper_with_rehashed_manifest(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    finding = run_tests(db_path)[0]
    original = workpaper_zip(db_path, {finding["finding_id"]})
    tampered = tmp_path / "workpaper-edited.zip"
    member = f"workpapers/{finding['finding_id']}.md"
    _rewrite_bundle_with_updated_hashes(
        original, tampered, member,
        lambda content: content.replace(b"## Automated observation", b"## Fabricated observation"),
    )
    with pytest.raises(BundleVerificationError, match="Workpaper differs"):
        verify_bundle(tampered)


def test_verifier_rejects_csv_or_review_mismatch_with_rehashed_manifest(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    finding = run_tests(db_path)[0]
    save_review(db_path, finding["finding_id"], "Reviewer", "closed", "false_positive", "Explained")
    original = workpaper_zip(db_path, {finding["finding_id"]})

    csv_edited = tmp_path / "csv-edited.zip"
    _rewrite_bundle_with_updated_hashes(
        original, csv_edited, "findings.csv", lambda content: content + b"\n",
    )
    with pytest.raises(BundleVerificationError, match="Findings CSV differs"):
        verify_bundle(csv_edited)

    reviews_edited = tmp_path / "reviews-edited.zip"
    _rewrite_bundle_with_updated_hashes(
        original, reviews_edited, "reviews.json",
        lambda content: content.replace(b"Explained", b"Invented"),
    )
    with pytest.raises(BundleVerificationError, match="Workpaper differs"):
        verify_bundle(reviews_edited)

    source_csv_edited = tmp_path / "source-csv-edited.zip"
    _rewrite_bundle_with_updated_hashes(
        original, source_csv_edited, "source_tables/accounts.csv",
        lambda content: content + b"\n",
    )
    with pytest.raises(BundleVerificationError, match="Source CSV differs from JSON"):
        verify_bundle(source_csv_edited)


def test_filtered_workpaper_export_rejects_unknown_finding_ids(tmp_path):
    db_path = tmp_path / "demo.duckdb"
    initialize_db(db_path)
    run_tests(db_path)
    with pytest.raises(ValueError, match="Unknown finding IDs"):
        workpaper_zip(db_path, {"F-NO-SUCH-CASE"})


@pytest.fixture
def reviewed_bundle(tmp_path):
    db_path = tmp_path / "reviewed.duckdb"
    initialize_db(db_path)
    finding_id = run_tests(db_path)[0]["finding_id"]
    save_review(
        db_path, finding_id, "Reviewer A", "in_review", "needs_more_evidence",
        "Checking source", "2026-10-09T00:00:00Z",
    )
    save_review(
        db_path, finding_id, "Reviewer B", "closed", "false_positive",
        "Confirmed allowed exception", "2026-10-09T00:00:00.500000Z",
    )
    return workpaper_zip(db_path, {finding_id}), get_finding(db_path, finding_id)


def _rewrite_consistent_review_bundle(original, path, finding, history):
    """Keep hashes and renderings consistent so semantic validation is exercised."""
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files["manifest.json"])
    finding = {**finding, "review_history": history, "latest_review": history[-1]}
    finding_id = finding["finding_id"]
    files["reviews.json"] = _json({finding_id: history}).encode("utf-8")
    files["findings.csv"] = exception_csv([finding])
    files[f"workpapers/{finding_id}.md"] = finding_workpaper(
        finding, manifest["dataset_sha256"]
    ).encode("utf-8")
    manifest["files_sha256"] = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in files.items() if name != "manifest.json"
    }
    files["manifest.json"] = _json(manifest).encode("utf-8")
    with zipfile.ZipFile(path, "w") as target:
        for name, content in files.items():
            target.writestr(name, content)


def test_export_selects_the_chronologically_latest_review(reviewed_bundle, tmp_path):
    bundle, finding = reviewed_bundle
    path = tmp_path / "valid-reviews.zip"
    path.write_bytes(bundle)
    assert verify_bundle(path)["exported_findings"] == 1
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        history = json.loads(archive.read("reviews.json"))[finding["finding_id"]]
        assert [review["reviewer"] for review in history] == ["Reviewer A", "Reviewer B"]
        assert "Reviewer B" in archive.read("findings.csv").decode("utf-8-sig")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"status": "not-a-status"}, "Status must be one of"),
        ({"status": "pending"}, "pending review cannot have a conclusion"),
        ({"status": "in_review"}, "in-progress review cannot have a final conclusion"),
        ({"conclusion": "needs_more_evidence"}, "closed review requires a final conclusion"),
        ({"notes": "   "}, "closed review requires notes"),
        ({"reviewer": "   "}, "Reviewer is required"),
        ({"reviewed_at": "not-a-time"}, "reviewed_at must be an ISO-8601 timestamp"),
        ({"reviewed_at": []}, "reviewed_at must be an ISO-8601 timestamp"),
        ({"notes": 42}, "Notes must be a string"),
    ],
)
def test_verifier_rejects_invalid_review_even_when_hashes_and_outputs_agree(
    reviewed_bundle, tmp_path, changes, message,
):
    bundle, finding = reviewed_bundle
    history = [dict(review) for review in finding["review_history"]]
    history[-1].update(changes)
    path = tmp_path / "invalid-review.zip"
    _rewrite_consistent_review_bundle(bundle, path, finding, history)
    with pytest.raises(BundleVerificationError, match=message):
        verify_bundle(path)


def test_verifier_rejects_reordered_history_even_when_outputs_agree(reviewed_bundle, tmp_path):
    bundle, finding = reviewed_bundle
    history = list(reversed(finding["review_history"]))
    path = tmp_path / "reordered-reviews.zip"
    _rewrite_consistent_review_bundle(bundle, path, finding, history)
    with pytest.raises(BundleVerificationError, match="Review history is not in chronological order"):
        verify_bundle(path)

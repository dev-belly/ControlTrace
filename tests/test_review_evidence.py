"""A stable finding ID cannot carry a verdict across different source evidence."""

from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest

from controltrace import store
from controltrace.data import PRIMARY_KEYS
from controltrace.exports import _json, exception_csv, finding_workpaper, workpaper_zip
from controltrace.reviews import REVIEW_ANNOTATIONS
from controltrace.rules import run_tests
from controltrace.store import get_finding, get_table_rows, initialize_db, save_review
from controltrace.verify import BundleVerificationError, verify_bundle


def _change_linked_source(db_path, finding):
    record = finding["related_records"][0]
    table = record["table"]
    with duckdb.connect(str(db_path)) as connection:
        connection.execute(
            f'UPDATE "{table}" SET source_system = ? WHERE "{PRIMARY_KEYS[table]}" = ?',
            ["Corrected synthetic extract", record["id"]],
        )


def _closed_finding(db_path):
    initialize_db(db_path)
    finding = run_tests(db_path)[0]
    save_review(
        db_path, finding["finding_id"], "Reviewer A", "closed", "confirmed_exception",
        "Checked the original linked source", expected_revision=finding["review_revision"],
    )
    return get_finding(db_path, finding["finding_id"])


def test_identical_rerun_keeps_review_and_revision(tmp_path):
    db_path = tmp_path / "case.duckdb"
    finding = _closed_finding(db_path)
    run_tests(db_path)
    assert get_finding(db_path, finding["finding_id"]) == finding


def test_changed_evidence_reopens_same_id_without_losing_history(tmp_path):
    db_path = tmp_path / "case.duckdb"
    original = _closed_finding(db_path)
    _change_linked_source(db_path, original)
    run_tests(db_path)
    changed = get_finding(db_path, original["finding_id"])
    assert changed["evidence_sha256"] != original["evidence_sha256"]
    assert changed["review_history"] == original["review_history"]
    assert changed["review_bindings"] == original["review_bindings"]
    assert changed["latest_review"] is None
    assert changed["review_status"] == "pending"
    assert changed["review_stale_count"] == 1
    with pytest.raises(ValueError, match="reload before saving"):
        save_review(
            db_path, original["finding_id"], "Reviewer B", "closed", "false_positive",
            "This form still displayed the original extract",
            expected_revision=original["review_revision"],
        )
    assert get_table_rows(db_path, "reviews") == original["review_history"]
    replacement = save_review(
        db_path, changed["finding_id"], "Reviewer B", "closed", "false_positive",
        "Reassessed the corrected extract", expected_revision=changed["review_revision"],
    )
    current = get_finding(db_path, changed["finding_id"])
    assert current["latest_review"] == replacement
    assert current["review_bindings"][replacement["review_id"]] == changed["evidence_sha256"]
    assert current["review_stale_count"] == 1


def test_corrected_approval_time_reopens_only_findings_with_changed_evidence(tmp_path):
    db_path = tmp_path / "case.duckdb"
    unrelated = _closed_finding(db_path)
    finding_id = "F-2C5FD7D5834D"
    finding = get_finding(db_path, finding_id)
    save_review(
        db_path, finding_id, "Reviewer A", "closed", "confirmed_exception",
        "CH002 approval is later than deployment D002",
        expected_revision=finding["review_revision"],
    )
    original = get_finding(db_path, finding_id)
    with duckdb.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE change_tickets SET approved_at = ? WHERE ticket_id = ?",
            ["2025-06-21T13:00:00Z", "CH002"],
        )
    run_tests(db_path)
    changed = get_finding(db_path, finding_id)
    assert changed["issue_code"] == original["issue_code"] == "deployment_approval_late"
    assert changed["evidence_sha256"] != original["evidence_sha256"]
    assert changed["review_status"] == "pending"
    assert changed["review_history"] == original["review_history"]
    assert get_finding(db_path, unrelated["finding_id"]) == unrelated


def test_stale_review_rejected_after_another_reviewer_saves(tmp_path):
    db_path = tmp_path / "case.duckdb"
    original = _closed_finding(db_path)
    save_review(db_path, original["finding_id"], "Reviewer B", notes="Requested new evidence")
    with pytest.raises(ValueError, match="reload before saving"):
        save_review(
            db_path, original["finding_id"], "Reviewer C", notes="A stale response",
            expected_revision=original["review_revision"],
        )
    assert len(get_table_rows(db_path, "reviews")) == 2


def test_simultaneous_reviewers_cannot_both_commit_same_revision(tmp_path, monkeypatch):
    db_path = tmp_path / "case.duckdb"
    original = _closed_finding(db_path)
    barrier = threading.Barrier(2)
    original_bindings = store._review_bindings

    def synchronized_bindings(connection):
        result = original_bindings(connection)
        if threading.current_thread() is not threading.main_thread():
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(store, "_review_bindings", synchronized_bindings)

    def submit(reviewer):
        try:
            save_review(
                db_path, original["finding_id"], reviewer, notes="Concurrent response",
                expected_revision=original["review_revision"],
            )
            return "saved"
        except ValueError as error:
            assert "reload before saving" in str(error)
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["Reviewer B", "Reviewer C"]))
    assert sorted(results) == ["conflict", "saved"]
    assert len(get_table_rows(db_path, "reviews")) == 2
    assert len(get_table_rows(db_path, "review_evidence")) == 2


def test_binding_failure_rolls_back_review_insert(tmp_path):
    db_path = tmp_path / "case.duckdb"
    original = _closed_finding(db_path)
    before = get_table_rows(db_path, "reviews")
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("DROP TABLE review_evidence")
        connection.execute("""
            CREATE TABLE review_evidence (
                review_id VARCHAR PRIMARY KEY, finding_sha256 VARCHAR CHECK (FALSE)
            )
        """)
    with pytest.raises(duckdb.ConstraintException):
        save_review(db_path, original["finding_id"], "Reviewer B", notes="Must remain atomic")
    assert get_table_rows(db_path, "reviews") == before
    assert get_table_rows(db_path, "review_evidence") == []


def test_legacy_database_retains_history_without_inventing_evidence_bindings(tmp_path):
    db_path = tmp_path / "legacy.duckdb"
    original = _closed_finding(db_path)
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("DROP TABLE review_evidence")
        connection.execute("ALTER TABLE findings DROP COLUMN review_write_revision")
    initialize_db(db_path)
    migrated = get_finding(db_path, original["finding_id"])
    assert migrated["review_history"] == original["review_history"]
    assert list(migrated["review_bindings"].values()) == [None]
    assert migrated["latest_review"] is None
    assert migrated["review_status"] == "pending"
    assert migrated["review_stale_count"] == 1


def _save_members(path, members):
    manifest = json.loads(members["manifest.json"])
    manifest["files_sha256"] = {
        name: hashlib.sha256(value).hexdigest()
        for name, value in members.items() if name != "manifest.json"
    }
    members["manifest.json"] = _json(manifest).encode("utf-8")
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)


def test_new_bundle_replays_pending_after_evidence_changes(tmp_path):
    db_path = tmp_path / "case.duckdb"
    original = _closed_finding(db_path)
    _change_linked_source(db_path, original)
    run_tests(db_path)
    bundle = workpaper_zip(db_path, {original["finding_id"]})
    path = tmp_path / "pending.zip"
    path.write_bytes(bundle)
    assert verify_bundle(path)["review_evidence_bound"] is True
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
        assert b"pending" in members["findings.csv"]
        assert b"Reviewer A" not in members["findings.csv"]
    # A fully rehashed CSV/workpaper claiming the old closed decision must
    # still disagree with independently replayed evidence applicability.
    current = get_finding(db_path, original["finding_id"])
    forged = {**current, "latest_review": original["latest_review"]}
    members["findings.csv"] = exception_csv([forged])
    manifest = json.loads(members["manifest.json"])
    members[f"workpapers/{original['finding_id']}.md"] = finding_workpaper(
        forged, manifest["dataset_sha256"],
    ).encode("utf-8")
    forged_path = tmp_path / "forged-closed.zip"
    _save_members(forged_path, members)
    with pytest.raises(BundleVerificationError, match="Workpaper differs"):
        verify_bundle(forged_path)


def test_legacy_v2_bundle_is_verified_with_explicit_unbound_status(tmp_path):
    db_path = tmp_path / "case.duckdb"
    finding = _closed_finding(db_path)
    bundle = workpaper_zip(db_path, {finding["finding_id"]})
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    legacy = {key: value for key, value in finding.items() if key not in REVIEW_ANNOTATIONS}
    legacy["review_history"] = finding["review_history"]
    legacy["latest_review"] = finding["latest_review"]
    members.pop("review_evidence.json")
    manifest = json.loads(members["manifest.json"])
    manifest["bundle_format_version"] = 2
    manifest["controltrace_version"] = "0.2.7"
    members["manifest.json"] = _json(manifest).encode("utf-8")
    members["findings.csv"] = exception_csv([legacy])
    members[f"workpapers/{finding['finding_id']}.md"] = finding_workpaper(
        legacy, manifest["dataset_sha256"], version="0.2.7",
    ).encode("utf-8")
    path = tmp_path / "legacy-v2.zip"
    _save_members(path, members)
    assert verify_bundle(path)["review_evidence_bound"] is False


@pytest.mark.parametrize("binding", ["a" * 63, "G" * 64, 42, [], {}])
def test_verifier_rejects_invalid_review_binding_after_rehash(tmp_path, binding):
    db_path = tmp_path / "case.duckdb"
    finding = _closed_finding(db_path)
    bundle = workpaper_zip(db_path, {finding["finding_id"]})
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    review_id = finding["latest_review"]["review_id"]
    members["review_evidence.json"] = _json({
        finding["finding_id"]: {review_id: binding},
    }).encode("utf-8")
    path = tmp_path / "invalid-binding.zip"
    _save_members(path, members)
    with pytest.raises(BundleVerificationError, match="Invalid review evidence fingerprint"):
        verify_bundle(path)


def test_review_of_another_finding_does_not_invalidate_form_revision(tmp_path):
    db_path = tmp_path / "case.duckdb"
    initialize_db(db_path)
    first, second = run_tests(db_path)[:2]
    save_review(db_path, second["finding_id"], "Reviewer B", notes="Reviewing another case")
    save_review(
        db_path, first["finding_id"], "Reviewer A", notes="My case is still unchanged",
        expected_revision=first["review_revision"],
    )
    assert len(get_table_rows(db_path, "reviews")) == 2

"""Database reruns must preserve immutable source rows and append-only review history."""

from __future__ import annotations

import pytest

from controltrace.rules import run_tests
from controltrace.store import get_table_rows, initialize_db, list_findings, save_review


def test_repeat_generation_and_tests_preserve_review_and_source_snapshot(tmp_path):
    db_path = tmp_path / "case.duckdb"
    initialize_db(db_path)
    before = get_table_rows(db_path, "employees")
    first = run_tests(db_path)
    finding_id = first[0]["finding_id"]
    save_review(
        db_path, finding_id, "Demo Reviewer", "in_review", "needs_more_evidence",
        "Need original approval ticket", "2026-09-27T09:00:00Z",
    )
    initialize_db(db_path)
    second = run_tests(db_path)
    assert [item["finding_id"] for item in first] == [item["finding_id"] for item in second]
    assert get_table_rows(db_path, "employees") == before
    assert get_table_rows(db_path, "reviews")[0]["finding_id"] == finding_id
    assert next(item for item in second if item["finding_id"] == finding_id)["latest_review"][
        "reviewer"
    ] == "Demo Reviewer"
    assert len(list_findings(db_path)) == len(second)
    with pytest.raises(ValueError, match="already uses seed"):
        initialize_db(db_path, seed=123)


def test_review_is_append_only_and_rejects_unknown_finding(tmp_path):
    db_path = tmp_path / "case.duckdb"
    initialize_db(db_path)
    finding_id = run_tests(db_path)[0]["finding_id"]
    save_review(
        db_path, finding_id, "Analyst A", "in_review", "needs_more_evidence",
        "Check source", "2026-09-27T09:00:00Z",
    )
    save_review(
        db_path, finding_id, "Analyst B", "closed", "confirmed_exception",
        "Confirmed source", "2026-09-27T10:00:00Z",
    )
    finding = next(item for item in list_findings(db_path) if item["finding_id"] == finding_id)
    assert len(finding["review_history"]) == 2
    assert finding["latest_review"]["reviewer"] == "Analyst B"
    with pytest.raises(ValueError, match="Unknown finding"):
        save_review(db_path, "F-NO-SUCH-CASE", "Analyst C")

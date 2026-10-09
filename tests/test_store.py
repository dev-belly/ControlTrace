"""Database reruns must preserve immutable source rows and append-only review history."""

from __future__ import annotations

import pytest

from controltrace.rules import run_tests
from controltrace.store import (
    get_finding,
    get_table_rows,
    initialize_db,
    list_findings,
    save_review,
)


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


def test_nondefault_seed_can_be_tested_and_rerun_without_replacing_sources(tmp_path):
    db_path = tmp_path / "another-seed.duckdb"
    initialize_db(db_path, seed=123)
    before = get_table_rows(db_path, "employees")
    first = run_tests(db_path)
    assert first
    initialize_db(db_path)
    assert get_table_rows(db_path, "employees") == before
    assert [finding["finding_id"] for finding in run_tests(db_path)] == [
        finding["finding_id"] for finding in first
    ]
    assert {row["key"]: row["value"] for row in get_table_rows(db_path, "meta")}["seed"] == "123"


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


def test_review_state_and_timezone_preserve_an_unambiguous_latest_decision(tmp_path):
    db_path = tmp_path / "case.duckdb"
    initialize_db(db_path)
    finding_id = run_tests(db_path)[0]["finding_id"]
    with pytest.raises(ValueError, match="pending review cannot have a conclusion"):
        save_review(db_path, finding_id, "Analyst", "pending", "confirmed_exception")
    with pytest.raises(ValueError, match="in-progress review cannot have a final conclusion"):
        save_review(db_path, finding_id, "Analyst", "in_review", "false_positive")
    with pytest.raises(ValueError, match="closed review requires a final conclusion"):
        save_review(db_path, finding_id, "Analyst", "closed", "needs_more_evidence")
    with pytest.raises(ValueError, match="closed review requires notes"):
        save_review(db_path, finding_id, "Analyst", "closed", "false_positive")
    first = save_review(
        db_path, finding_id, "Analyst A", "in_review", "needs_more_evidence",
        "Checking approval", "2026-09-27T18:00:00+08:00",
    )
    second = save_review(
        db_path, finding_id, "Analyst B", "closed", "confirmed_exception",
        "Approval is later than deployment", "2026-09-27T11:00:00Z",
    )
    assert first["reviewed_at"] == "2026-09-27T10:00:00Z"
    assert get_table_rows(db_path, "reviews") == [first, second]
    assert list_findings(db_path)[0]["latest_review"] == second


@pytest.mark.parametrize("write_order", [(0, 1, 2), (2, 0, 1)])
def test_latest_review_uses_actual_time_with_fractional_seconds_and_backfill(tmp_path, write_order):
    db_path = tmp_path / "case.duckdb"
    initialize_db(db_path)
    finding_id = run_tests(db_path)[0]["finding_id"]
    timestamps = [
        "2026-10-09T08:00:00+08:00",
        "2026-10-09T00:00:00.100000Z",
        "2026-10-09T00:00:00.500000Z",
    ]
    reviews = {}
    for position in write_order:
        reviews[position] = save_review(
            db_path, finding_id, f"Reviewer {position}",
            "closed" if position == 2 else "in_review",
            "false_positive" if position == 2 else "needs_more_evidence",
            "Documented review basis", timestamps[position],
        )
    expected_history = [reviews[position] for position in range(3)]
    assert get_table_rows(db_path, "reviews") == expected_history
    for finding in (get_finding(db_path, finding_id), list_findings(db_path)[0]):
        assert finding["review_history"] == expected_history
        assert finding["latest_review"] == reviews[2]
        assert finding["review_status"] == "closed"

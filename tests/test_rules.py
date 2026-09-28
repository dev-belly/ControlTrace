"""Rule outcomes, threshold edges and evidence gaps for the five IT controls."""

from __future__ import annotations

from copy import deepcopy

import pytest

from controltrace.data import generate_demo_data, parse_utc
from controltrace.rules import evaluate


def _record(data, table, key, value):
    return next(row for row in data[table] if row[key] == value)


def _has(findings, code, entity):
    return any(item["issue_code"] == code and item["entity_id"] == entity for item in findings)


def test_fixture_manifest_matches_actual_rule_outcomes():
    data = generate_demo_data()
    findings = evaluate(data)
    actual = {
        (item["control_id"], item["entity_id"], item["issue_code"], item["classification"])
        for item in findings
    }
    expected = {
        (
            item["control_id"], item["entity_id"], item["issue_code"],
            item["expected_classification"],
        )
        for item in data["expected_results"]
    }
    assert actual == expected
    assert len(findings) == 12
    assert {item["control_id"] for item in findings} == {
        "CT-01", "CT-02", "CT-03", "CT-04", "CT-05"
    }
    for finding in findings:
        assert finding["evidence_ids"]
        assert finding["timeline"]
        assert finding["rule"]["control_goal"]


def test_exact_deadline_is_on_time_for_each_time_based_control():
    data = deepcopy(generate_demo_data())
    _record(data, "accounts", "account_id", "A001")["disabled_at"] = "2025-06-12T09:00:00Z"
    _record(data, "entitlements", "entitlement_id", "EN003")["revoked_at"] = (
        "2025-06-06T09:00:00Z"
    )
    _record(data, "access_reviews", "review_id", "RV006")["completed_at"] = (
        "2025-06-15T23:59:00Z"
    )
    ticket = _record(data, "change_tickets", "ticket_id", "CH002")
    ticket["approved_at"] = "2025-06-20T10:00:00Z"
    ticket["tested_at"] = "2025-06-20T10:00:00Z"
    emergency = _record(data, "emergency_changes", "emergency_id", "EM005")
    emergency["retrospective_approver_id"] = "P014"
    emergency["retrospective_approved_at"] = "2025-06-21T09:00:00Z"
    findings = evaluate(data)
    assert not _has(findings, "terminated_account_open", "A001")
    assert not _has(findings, "transfer_old_role_retained", "EN003")
    assert not _has(findings, "privileged_review_overdue", "EN006")
    assert not _has(findings, "deployment_approval_late", "D002")
    assert not _has(findings, "deployment_test_late", "D002")
    assert not _has(findings, "emergency_approval_overdue", "D005")


def test_missing_evidence_requires_review_instead_of_a_false_conclusion():
    data = deepcopy(generate_demo_data())
    data["access_reviews"] = [
        item for item in data["access_reviews"] if item["entitlement_id"] != "EN005"
    ]
    data["emergency_changes"] = [
        item for item in data["emergency_changes"] if item["deployment_id"] != "D005"
    ]
    findings = evaluate(data)
    assert _has(findings, "privileged_review_evidence_missing", "EN005")
    assert _has(findings, "deployment_test_unknown", "D003")
    assert _has(findings, "emergency_record_unknown", "D005")
    assert not _has(findings, "emergency_approval_overdue", "D005")


@pytest.mark.parametrize(
    ("table", "key", "record_id", "field", "issue_code", "entity_id"),
    [
        ("hr_events", "event_id", "TERM-001", "event_at", "hr_event_time_unknown", "TERM-001"),
        ("entitlements", "entitlement_id", "EN006", "granted_at", "entitlement_grant_time_unknown", "EN006"),
        ("access_reviews", "review_id", "RV006", "due_at", "privileged_review_due_unknown", "EN006"),
        ("deployments", "deployment_id", "D001", "deployed_at", "deployment_time_unknown", "D001"),
        ("deployments", "deployment_id", "D005", "deployed_at", "emergency_deployment_time_unknown", "D005"),
    ],
)
def test_missing_key_timestamp_is_an_explicit_evidence_gap(
    table, key, record_id, field, issue_code, entity_id
):
    data = deepcopy(generate_demo_data())
    _record(data, table, key, record_id)[field] = ""
    finding = next(
        item for item in evaluate(data)
        if item["issue_code"] == issue_code and item["entity_id"] == entity_id
    )
    assert finding["classification"] == "manual_review"
    assert f"{table}:{record_id}" in finding["evidence_ids"]


def test_missing_grant_time_on_nonprivileged_role_is_outside_ct02_scope():
    data = deepcopy(generate_demo_data())
    privileged_roles = {row["role_id"] for row in data["roles"] if row["is_privileged"]}
    ordinary = next(
        row for row in data["entitlements"] if row["role_id"] not in privileged_roles
    )
    ordinary["granted_at"] = ""
    assert not _has(evaluate(data), "entitlement_grant_time_unknown", ordinary["entitlement_id"])


def test_missing_hr_account_links_are_explicit_evidence_gaps():
    data = deepcopy(generate_demo_data())
    data["accounts"] = [
        item for item in data["accounts"] if item["account_id"] not in {"A001", "A003"}
    ]
    findings = evaluate(data)
    for issue_code, event_id in [
        ("termination_account_extract_missing", "TERM-001"),
        ("transfer_account_extract_missing", "MOVE-003"),
    ]:
        finding = next(
            item for item in findings
            if item["issue_code"] == issue_code and item["entity_id"] == event_id
        )
        assert finding["classification"] == "manual_review"
        assert finding["evidence_ids"] == [f"hr_events:{event_id}"]
    assert not _has(findings, "terminated_account_open", "A001")
    assert not _has(findings, "transfer_old_role_retained", "EN003")


def test_missing_entitlement_role_or_account_is_not_silently_skipped():
    data = deepcopy(generate_demo_data())
    data["roles"] = [item for item in data["roles"] if item["role_id"] != "R_ADMIN"]
    data["accounts"] = [
        item for item in data["accounts"] if item["account_id"] != "A003"
    ]
    findings = evaluate(data)
    for entitlement_id in ("EN003", "EN005", "EN006", "EN014"):
        finding = next(
            item for item in findings
            if item["issue_code"] == "entitlement_mapping_missing"
            and item["entity_id"] == entitlement_id
        )
        assert finding["classification"] == "manual_review"
        assert f"entitlements:{entitlement_id}" in finding["evidence_ids"]
    assert not _has(findings, "privileged_approval_missing", "EN005")


def test_missing_role_owner_department_needs_transfer_review():
    data = deepcopy(generate_demo_data())
    _record(data, "roles", "role_id", "R_FIN")["owner_department"] = None
    findings = evaluate(data)
    item = next(
        finding for finding in findings
        if finding["issue_code"] == "transfer_role_department_unknown"
        and finding["entity_id"] == "EN003"
    )
    assert item["classification"] == "manual_review"
    assert "roles:R_FIN" in item["evidence_ids"]
    assert not _has(findings, "transfer_old_role_retained", "EN003")


def test_missing_transfer_grant_time_is_a_reviewable_evidence_gap():
    data = deepcopy(generate_demo_data())
    entitlement = _record(data, "entitlements", "entitlement_id", "EN003")
    entitlement["granted_at"] = ""
    findings = evaluate(data)
    item = next(
        finding for finding in findings
        if finding["issue_code"] == "transfer_grant_time_unknown"
        and finding["entity_id"] == "EN003"
    )
    assert item["classification"] == "manual_review"
    assert "hr_events:MOVE-003" in item["evidence_ids"]
    assert "entitlements:EN003" in item["evidence_ids"]
    assert not _has(findings, "transfer_old_role_retained", "EN003")

    entitlement["revoked_at"] = "2025-06-06T09:00:00Z"
    assert not _has(evaluate(data), "transfer_grant_time_unknown", "EN003")


@pytest.mark.parametrize("missing_time", ["", " "])
def test_missing_request_time_does_not_hide_approved_self_approval(missing_time):
    data = deepcopy(generate_demo_data())
    _record(data, "access_requests", "request_id", "Q007")["submitted_at"] = missing_time
    findings = evaluate(data)
    item = next(
        finding for finding in findings
        if finding["issue_code"] == "sensitive_self_approval"
        and finding["entity_id"] == "Q007"
    )
    assert item["classification"] == "exception"
    assert item["occurred_at"] == _record(
        data, "access_approvals", "approval_id", "AP007"
    )["decided_at"]
    assert "access_requests:Q007" in item["evidence_ids"]
    assert "access_approvals:AP007" in item["evidence_ids"]

    data["role_permissions"] = [
        row for row in data["role_permissions"] if row["role_id"] != "R_PAYMENT"
    ]
    mapping_gap = next(
        finding for finding in evaluate(data)
        if finding["issue_code"] == "request_sensitivity_mapping_missing"
        and finding["entity_id"] == "Q007"
    )
    assert mapping_gap["occurred_at"] == item["occurred_at"]


def test_privileged_approval_cannot_precede_request_and_blank_expiry_is_missing():
    data = deepcopy(generate_demo_data())
    _record(data, "access_approvals", "approval_id", "AP006")["decided_at"] = (
        "2025-06-01T09:00:00Z"
    )
    _record(data, "access_approvals", "approval_id", "AP014")["valid_until"] = " "
    findings = evaluate(data)
    assert _has(findings, "privileged_approval_missing", "EN006")
    assert not _has(findings, "privileged_approval_missing", "EN014")


@pytest.mark.parametrize(
    ("table", "key", "record_id", "field"),
    [
        ("access_requests", "request_id", "Q006", "submitted_at"),
        ("access_approvals", "approval_id", "AP006", "decided_at"),
    ],
)
def test_incomplete_approval_timing_needs_review_not_a_definite_exception(
    table, key, record_id, field
):
    data = deepcopy(generate_demo_data())
    _record(data, table, key, record_id)[field] = ""
    findings = evaluate(data)
    item = next(
        finding for finding in findings
        if finding["issue_code"] == "privileged_approval_sequence_unknown"
        and finding["entity_id"] == "EN006"
    )
    assert item["classification"] == "manual_review"
    assert f"{table}:{record_id}" in item["evidence_ids"]
    assert not _has(findings, "privileged_approval_missing", "EN006")


def test_approval_after_grant_remains_a_definite_exception():
    data = deepcopy(generate_demo_data())
    _record(data, "access_approvals", "approval_id", "AP006")["decided_at"] = (
        "2025-06-05T10:00:01Z"
    )
    findings = evaluate(data)
    assert _has(findings, "privileged_approval_missing", "EN006")
    assert not _has(findings, "privileged_approval_sequence_unknown", "EN006")


def test_missing_or_mismatched_commit_is_an_explicit_evidence_gap():
    data = deepcopy(generate_demo_data())
    data["code_commits"] = [
        item for item in data["code_commits"] if item["commit_id"] != "C004"
    ]
    _record(data, "code_commits", "commit_id", "C003")["ticket_id"] = "CH004"
    findings = evaluate(data)
    for issue_code, deployment_id in [
        ("deployment_commit_missing", "D004"),
        ("deployment_commit_ticket_mismatch", "D003"),
    ]:
        finding = next(
            item for item in findings
            if item["issue_code"] == issue_code and item["entity_id"] == deployment_id
        )
        assert finding["classification"] == "manual_review"
        assert f"deployments:{deployment_id}" in finding["evidence_ids"]
    assert _has(findings, "deployment_test_unknown", "D003")


def test_events_after_audit_cutoff_are_not_tested():
    data = deepcopy(generate_demo_data())
    _record(data, "deployments", "deployment_id", "D001")["deployed_at"] = (
        "2025-07-01T00:00:00Z"
    )
    _record(data, "access_approvals", "approval_id", "AP007")["decided_at"] = (
        "2025-07-01T00:00:00Z"
    )
    findings = evaluate(data)
    assert not _has(findings, "deployment_ticket_missing", "D001")
    assert not _has(findings, "sensitive_self_approval", "Q007")


def test_revoked_privilege_still_requires_original_approval():
    data = deepcopy(generate_demo_data())
    _record(data, "entitlements", "entitlement_id", "EN005")["revoked_at"] = (
        "2025-06-10T09:00:00Z"
    )
    findings = evaluate(data)
    assert _has(findings, "privileged_approval_missing", "EN005")
    assert not _has(findings, "privileged_review_overdue", "EN005")


def test_unresolved_deadline_equal_to_audit_cutoff_is_due():
    data = deepcopy(generate_demo_data())
    _record(data, "hr_events", "event_id", "TERM-001")["event_at"] = (
        "2025-06-28T23:59:59Z"
    )
    _record(data, "hr_events", "event_id", "MOVE-003")["event_at"] = (
        "2025-06-25T23:59:59Z"
    )
    _record(data, "deployments", "deployment_id", "D005")["deployed_at"] = (
        "2025-06-29T23:59:59Z"
    )
    findings = evaluate(data)
    assert _has(findings, "terminated_account_open", "A001")
    assert _has(findings, "transfer_old_role_retained", "EN003")
    assert _has(findings, "emergency_approval_overdue", "D005")
    assert not _has(findings, "emergency_window_open", "D005")


def test_repeated_source_events_have_unique_stable_finding_ids():
    data = deepcopy(generate_demo_data())
    second_transfer = deepcopy(_record(data, "hr_events", "event_id", "MOVE-003"))
    second_transfer.update(event_id="MOVE-003-B", event_at="2025-06-02T09:00:00Z")
    data["hr_events"].append(second_transfer)
    second_review = deepcopy(_record(data, "access_reviews", "review_id", "RV006"))
    second_review.update(review_id="RV006-B", due_at="2025-06-16T23:59:00Z")
    data["access_reviews"].append(second_review)
    second_approval = deepcopy(_record(data, "access_approvals", "approval_id", "AP007"))
    second_approval["approval_id"] = "AP007-B"
    data["access_approvals"].append(second_approval)

    first = evaluate(data)
    assert len({item["finding_id"] for item in first}) == len(first)
    for issue_code, entity_id in [
        ("transfer_old_role_retained", "EN003"),
        ("privileged_review_overdue", "EN006"),
        ("sensitive_self_approval", "Q007"),
    ]:
        assert sum(
            item["issue_code"] == issue_code and item["entity_id"] == entity_id
            for item in first
        ) == 2

    data["hr_events"].reverse()
    data["access_reviews"].reverse()
    data["access_approvals"].reverse()
    second = evaluate(data)
    ids_by_evidence = lambda rows: {  # noqa: E731 - concise snapshot of a stable key
        tuple(sorted(item["evidence_ids"])): item["finding_id"] for item in rows
    }
    assert ids_by_evidence(first) == ids_by_evidence(second)


def test_missing_sensitivity_mapping_and_unknown_deployment_type_need_review():
    data = deepcopy(generate_demo_data())
    data["role_permissions"] = [
        item for item in data["role_permissions"] if item["role_id"] != "R_PAYMENT"
    ]
    _record(data, "deployments", "deployment_id", "D004")["deployment_type"] = "OTHER"
    findings = evaluate(data)
    for issue_code, entity_id in [
        ("request_sensitivity_mapping_missing", "Q007"),
        ("deployment_type_unknown", "D004"),
    ]:
        item = next(
            finding for finding in findings
            if finding["issue_code"] == issue_code and finding["entity_id"] == entity_id
        )
        assert item["classification"] == "manual_review"
    assert not _has(findings, "sensitive_self_approval", "Q007")


def test_commit_recorded_after_deployment_is_flagged():
    data = deepcopy(generate_demo_data())
    _record(data, "code_commits", "commit_id", "C004")["committed_at"] = (
        "2025-06-21T11:00:00Z"
    )
    findings = evaluate(data)
    finding = next(
        item for item in findings
        if item["issue_code"] == "deployment_commit_late" and item["entity_id"] == "D004"
    )
    assert finding["classification"] == "exception"
    assert "code_commits:C004" in finding["evidence_ids"]


def test_multiple_emergency_records_use_any_complete_approval_within_deadline():
    data = deepcopy(generate_demo_data())
    late = deepcopy(_record(data, "emergency_changes", "emergency_id", "EM006"))
    late.update(
        emergency_id="EM006-B", retrospective_approved_at="2025-06-25T12:00:00Z"
    )
    data["emergency_changes"].append(late)
    on_time = deepcopy(_record(data, "emergency_changes", "emergency_id", "EM005"))
    on_time.update(
        emergency_id="EM005-B", retrospective_approver_id="P014",
        retrospective_approved_at="2025-06-21T09:00:00Z",
    )
    data["emergency_changes"].append(on_time)
    for rows in (data["emergency_changes"], list(reversed(data["emergency_changes"]))):
        data["emergency_changes"] = rows
        findings = evaluate(data)
        assert not _has(findings, "emergency_approval_overdue", "D005")
        assert not _has(findings, "emergency_approval_overdue", "D006")


def test_emergency_approval_timestamp_without_approver_needs_review():
    data = deepcopy(generate_demo_data())
    _record(data, "emergency_changes", "emergency_id", "EM005")[
        "retrospective_approved_at"
    ] = "2025-06-21T09:00:00Z"
    findings = evaluate(data)
    assert _has(findings, "emergency_approver_unknown", "D005")
    assert not _has(findings, "emergency_approval_overdue", "D005")


def test_emergency_approval_must_match_ticket_and_follow_deployment():
    data = deepcopy(generate_demo_data())
    _record(data, "emergency_changes", "emergency_id", "EM006")["ticket_id"] = "CH005"
    findings = evaluate(data)
    mismatch = next(
        item for item in findings
        if item["issue_code"] == "emergency_ticket_mismatch" and item["entity_id"] == "D006"
    )
    assert mismatch["classification"] == "manual_review"
    assert not _has(findings, "emergency_approval_overdue", "D006")

    _record(data, "emergency_changes", "emergency_id", "EM006").update(
        ticket_id="CH006", retrospective_approved_at="2025-06-24T08:00:00Z"
    )
    findings = evaluate(data)
    assert _has(findings, "emergency_approval_before_deployment", "D006")
    assert not _has(findings, "emergency_approval_overdue", "D006")


def test_blank_optional_timestamps_are_missing_evidence():
    data = deepcopy(generate_demo_data())
    _record(data, "accounts", "account_id", "A001")["disabled_at"] = " "
    _record(data, "change_tickets", "ticket_id", "CH003")["tested_at"] = ""
    _record(data, "emergency_changes", "emergency_id", "EM005")[
        "retrospective_approved_at"
    ] = "  "
    findings = evaluate(data)
    assert parse_utc("  ") is None
    assert parse_utc(" 2025-06-20T09:00:00Z ") == parse_utc("2025-06-20T09:00:00Z")
    with pytest.raises(ValueError):
        parse_utc("invalid timestamp")
    assert _has(findings, "terminated_account_open", "A001")
    assert _has(findings, "deployment_test_unknown", "D003")
    assert _has(findings, "emergency_approval_overdue", "D005")

"""Rule outcomes, threshold edges and evidence gaps for the five IT controls."""

from __future__ import annotations

from copy import deepcopy

from controltrace.data import generate_demo_data
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

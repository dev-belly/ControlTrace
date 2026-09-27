"""Deterministic, entirely fictional evidence for the ControlTrace demonstration.

All timestamps are UTC. Source names identify simulated extracts, not real systems.
The expected-results manifest is a demonstration fixture, never a rule input.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

DEFAULT_SEED = 20250927
DEMO_CUTOFF = "2025-06-30T23:59:59Z"
SYNTHETIC_NOTICE = "合成案例，非真实企业审计 / Synthetic case, not a real-company audit"

# SQL column definitions are also the data dictionary's machine-readable source.
TABLE_SCHEMAS: dict[str, dict[str, str]] = {
    "employees": {
        "employee_id": "VARCHAR PRIMARY KEY",
        "display_name": "VARCHAR NOT NULL",
        "current_department": "VARCHAR",
        "employment_status": "VARCHAR NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "hr_events": {
        "event_id": "VARCHAR PRIMARY KEY",
        "employee_id": "VARCHAR NOT NULL",
        "event_type": "VARCHAR NOT NULL",
        "event_at": "VARCHAR NOT NULL",
        "from_department": "VARCHAR",
        "to_department": "VARCHAR",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "accounts": {
        "account_id": "VARCHAR PRIMARY KEY",
        "employee_id": "VARCHAR NOT NULL",
        "system_id": "VARCHAR NOT NULL",
        "created_at": "VARCHAR NOT NULL",
        "disabled_at": "VARCHAR",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "roles": {
        "role_id": "VARCHAR PRIMARY KEY",
        "system_id": "VARCHAR NOT NULL",
        "role_name": "VARCHAR NOT NULL",
        "owner_department": "VARCHAR",
        "is_privileged": "BOOLEAN NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "role_permissions": {
        "permission_id": "VARCHAR PRIMARY KEY",
        "role_id": "VARCHAR NOT NULL",
        "permission_name": "VARCHAR NOT NULL",
        "is_sensitive": "BOOLEAN NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "entitlements": {
        "entitlement_id": "VARCHAR PRIMARY KEY",
        "account_id": "VARCHAR NOT NULL",
        "role_id": "VARCHAR NOT NULL",
        "request_id": "VARCHAR",
        "granted_at": "VARCHAR NOT NULL",
        "revoked_at": "VARCHAR",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "access_requests": {
        "request_id": "VARCHAR PRIMARY KEY",
        "account_id": "VARCHAR NOT NULL",
        "role_id": "VARCHAR NOT NULL",
        "requester_id": "VARCHAR NOT NULL",
        "submitted_at": "VARCHAR NOT NULL",
        "purpose": "VARCHAR NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "access_approvals": {
        "approval_id": "VARCHAR PRIMARY KEY",
        "request_id": "VARCHAR NOT NULL",
        "approver_id": "VARCHAR NOT NULL",
        "decision": "VARCHAR NOT NULL",
        "decided_at": "VARCHAR",
        "valid_until": "VARCHAR",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "access_reviews": {
        "review_id": "VARCHAR PRIMARY KEY",
        "entitlement_id": "VARCHAR NOT NULL",
        "reviewer_id": "VARCHAR NOT NULL",
        "period_end": "VARCHAR NOT NULL",
        "due_at": "VARCHAR NOT NULL",
        "completed_at": "VARCHAR",
        "decision": "VARCHAR",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "change_tickets": {
        "ticket_id": "VARCHAR PRIMARY KEY",
        "system_id": "VARCHAR NOT NULL",
        "change_type": "VARCHAR NOT NULL",
        "requester_id": "VARCHAR NOT NULL",
        "submitted_at": "VARCHAR NOT NULL",
        "approved_at": "VARCHAR",
        "tested_at": "VARCHAR",
        "change_summary": "VARCHAR NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "code_commits": {
        "commit_id": "VARCHAR PRIMARY KEY",
        "ticket_id": "VARCHAR",
        "author_id": "VARCHAR NOT NULL",
        "committed_at": "VARCHAR NOT NULL",
        "repository": "VARCHAR NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "deployments": {
        "deployment_id": "VARCHAR PRIMARY KEY",
        "system_id": "VARCHAR NOT NULL",
        "ticket_id": "VARCHAR",
        "commit_id": "VARCHAR NOT NULL",
        "deployer_id": "VARCHAR NOT NULL",
        "deployed_at": "VARCHAR NOT NULL",
        "deployment_type": "VARCHAR NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "emergency_changes": {
        "emergency_id": "VARCHAR PRIMARY KEY",
        "deployment_id": "VARCHAR NOT NULL",
        "ticket_id": "VARCHAR",
        "justification": "VARCHAR NOT NULL",
        "declared_at": "VARCHAR NOT NULL",
        "retrospective_approver_id": "VARCHAR",
        "retrospective_approved_at": "VARCHAR",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
    "expected_results": {
        "case_id": "VARCHAR PRIMARY KEY",
        "control_id": "VARCHAR NOT NULL",
        "entity_id": "VARCHAR NOT NULL",
        "issue_code": "VARCHAR NOT NULL",
        "expected_classification": "VARCHAR NOT NULL",
        "explanation": "VARCHAR NOT NULL",
        "source_system": "VARCHAR NOT NULL",
        "audit_cutoff": "VARCHAR NOT NULL",
    },
}

SOURCE_TABLES = tuple(TABLE_SCHEMAS)
PRIMARY_KEYS = {table: next(iter(columns)) for table, columns in TABLE_SCHEMAS.items()}


def _time(day: int, hour: int = 12, minute: int = 0) -> str:
    return f"2025-06-{day:02d}T{hour:02d}:{minute:02d}:00Z"


def generate_demo_data(seed: int = DEFAULT_SEED) -> dict[str, list[dict]]:
    """Return a seeded fixture with case and normal records linked by stable IDs."""
    rng = random.Random(seed)
    rows: dict[str, list[dict]] = {table: [] for table in SOURCE_TABLES}

    def add(table: str, source: str, **fields: object) -> None:
        rows[table].append({**fields, "source_system": source, "audit_cutoff": DEMO_CUTOFF})

    departments = {
        "P001": "FinanceOps", "P002": "FinanceOps", "P003": "ProductOps",
        "P004": "ProductOps", "P005": "ITOps", "P006": "ITOps",
        "P007": "PaymentsOps", "P008": "ITOps", "P009": "ITOps",
        "P010": "ITOps", "P011": "ITOps", "P012": "ProductOps",
        "P013": "FinanceOps", "P014": "ITOps", "P015": "SharedOps",
    }
    for employee_id, department in departments.items():
        number = employee_id[1:]
        is_leaver = employee_id in {"P001", "P002"}
        add(
            "employees", "SYNTHETIC_HRIS", employee_id=employee_id,
            display_name=f"Synthetic Person {number}", current_department=department,
            employment_status="TERMINATED" if is_leaver else "ACTIVE",
        )
        hire_department = "FinanceOps" if employee_id in {"P003", "P004", "P012"} else department
        add(
            "hr_events", "SYNTHETIC_HRIS", event_id=f"HIRE-{number}",
            employee_id=employee_id, event_type="HIRE", event_at="2024-01-15T09:00:00Z",
            from_department=None, to_department=hire_department,
        )
        if is_leaver:
            add(
                "hr_events", "SYNTHETIC_HRIS", event_id=f"TERM-{number}",
                employee_id=employee_id, event_type="TERMINATION", event_at=_time(10, 9),
                from_department=department, to_department=None,
            )
        if employee_id in {"P003", "P004", "P012"}:
            add(
                "hr_events", "SYNTHETIC_HRIS", event_id=f"MOVE-{number}",
                employee_id=employee_id, event_type="TRANSFER", event_at=_time(1, 9),
                from_department=None if employee_id == "P012" else "FinanceOps",
                to_department="ProductOps",
            )

    systems = {"P008": "CODE-HUB", "P009": "CODE-HUB", "P010": "CODE-HUB",
               "P011": "CODE-HUB"}
    for employee_id in departments:
        n = employee_id[1:]
        add(
            "accounts", "SYNTHETIC_IAM", account_id=f"A{n}", employee_id=employee_id,
            system_id=systems.get(employee_id, "FIN-ERP"),
            created_at="2024-01-16T09:00:00Z",
            disabled_at=_time(11, 8) if employee_id == "P002" else None,
        )

    roles = [
        ("R_VIEW", "FIN-ERP", "Read only", "GLOBAL", False),
        ("R_FIN", "FIN-ERP", "Finance operator", "FinanceOps", False),
        ("R_ADMIN", "FIN-ERP", "System administrator", "ITOps", True),
        ("R_PAYMENT", "FIN-ERP", "Payment release", "PaymentsOps", False),
        ("R_CODE", "CODE-HUB", "Code operator", "ITOps", False),
    ]
    for role_id, system_id, role_name, owner_department, is_privileged in roles:
        add(
            "roles", "SYNTHETIC_IAM", role_id=role_id, system_id=system_id,
            role_name=role_name, owner_department=owner_department,
            is_privileged=is_privileged,
        )
    for permission_id, role_id, permission_name, sensitive in [
        ("PERM-VIEW", "R_VIEW", "Read ledger", False),
        ("PERM-FIN", "R_FIN", "Edit ledger", False),
        ("PERM-ADMIN", "R_ADMIN", "Manage system access", True),
        ("PERM-PAY", "R_PAYMENT", "Release payment", True),
        ("PERM-CODE", "R_CODE", "Push code", False),
    ]:
        add(
            "role_permissions", "SYNTHETIC_IAM", permission_id=permission_id,
            role_id=role_id, permission_name=permission_name, is_sensitive=sensitive,
        )

    for person, role_id, request_id, revoked in [
        ("001", "R_VIEW", None, None), ("002", "R_VIEW", None, None),
        ("003", "R_FIN", None, None), ("004", "R_FIN", None, _time(2, 10)),
        ("005", "R_ADMIN", None, None), ("006", "R_ADMIN", "Q006", None),
        ("007", "R_PAYMENT", "Q007", None), ("008", "R_CODE", None, None),
        ("009", "R_CODE", None, None), ("010", "R_CODE", None, None),
        ("011", "R_CODE", None, None), ("012", "R_FIN", None, None),
        ("013", "R_VIEW", None, None), ("014", "R_ADMIN", "Q014", None),
        ("015", "R_VIEW", None, None),
    ]:
        add(
            "entitlements", "SYNTHETIC_IAM", entitlement_id=f"EN{person}",
            account_id=f"A{person}", role_id=role_id, request_id=request_id,
            granted_at="2025-05-01T10:00:00Z" if request_id is None else _time(5, 10),
            revoked_at=revoked,
        )

    for request_id, account_id, role_id, requester, approver in [
        ("Q006", "A006", "R_ADMIN", "P006", "P014"),
        ("Q007", "A007", "R_PAYMENT", "P007", "P007"),
        ("Q014", "A014", "R_ADMIN", "P014", "P006"),
    ]:
        add(
            "access_requests", "SYNTHETIC_ACCESS_WORKFLOW", request_id=request_id,
            account_id=account_id, role_id=role_id, requester_id=requester,
            submitted_at=_time(2, 9), purpose="Synthetic role assignment",
        )
        add(
            "access_approvals", "SYNTHETIC_ACCESS_WORKFLOW",
            approval_id=f"AP{request_id[1:]}", request_id=request_id,
            approver_id=approver, decision="APPROVED", decided_at=_time(3, 10),
            valid_until="2025-12-31T23:59:59Z",
        )
    for review_id, ent_id, completed in [
        ("RV005", "EN005", _time(14, 12)),
        ("RV006", "EN006", _time(20, 12)),
        ("RV014", "EN014", _time(15, 12)),
    ]:
        add(
            "access_reviews", "SYNTHETIC_ACCESS_REVIEW", review_id=review_id,
            entitlement_id=ent_id, reviewer_id="P013", period_end=_time(1, 0),
            due_at=_time(15, 23, 59), completed_at=completed,
            decision="RETAIN",
        )

    tickets = [
        ("CH002", "CODE-HUB", "STANDARD", "P009", _time(17), _time(21), _time(22),
         "Synthetic deployment with late gates"),
        ("CH003", "CODE-HUB", "STANDARD", "P010", _time(18), _time(19), None,
         "Synthetic deployment with missing test evidence"),
        ("CH004", "CODE-HUB", "STANDARD", "P011", _time(18), _time(19), _time(20),
         "Synthetic approved deployment"),
        ("CH005", "CODE-HUB", "EMERGENCY", "P008", _time(20, 8), None, None,
         "Synthetic service restoration"),
        ("CH006", "CODE-HUB", "EMERGENCY", "P009", _time(24, 8), None, None,
         "Synthetic service restoration"),
        ("CH007", "CODE-HUB", "EMERGENCY", "P010", _time(30, 18), None, None,
         "Synthetic cutoff-adjacent restoration"),
    ]
    for ticket_id, system_id, change_type, requester, submitted, approved, tested, summary in tickets:
        add(
            "change_tickets", "SYNTHETIC_CHANGE_MGMT", ticket_id=ticket_id,
            system_id=system_id, change_type=change_type, requester_id=requester,
            submitted_at=submitted, approved_at=approved, tested_at=tested,
            change_summary=summary,
        )
    deployments = [
        ("D001", "CODE-HUB", None, "C001", "P008", _time(20, 10), "STANDARD"),
        ("D002", "CODE-HUB", "CH002", "C002", "P009", _time(20, 10), "STANDARD"),
        ("D003", "CODE-HUB", "CH003", "C003", "P010", _time(20, 10), "STANDARD"),
        ("D004", "CODE-HUB", "CH004", "C004", "P011", _time(21, 10), "STANDARD"),
        ("D005", "CODE-HUB", "CH005", "C005", "P008", _time(20, 9), "EMERGENCY"),
        ("D006", "CODE-HUB", "CH006", "C006", "P009", _time(24, 9), "EMERGENCY"),
        ("D007", "CODE-HUB", "CH007", "C007", "P010", _time(30, 19), "EMERGENCY"),
    ]
    for deployment_id, system_id, ticket_id, commit_id, deployer, deployed, kind in deployments:
        add(
            "code_commits", "SYNTHETIC_GIT", commit_id=commit_id,
            ticket_id=ticket_id, author_id=deployer,
            committed_at=(datetime.fromisoformat(deployed.replace("Z", "+00:00"))
                          - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            repository="synthetic/code-hub",
        )
        add(
            "deployments", "SYNTHETIC_CICD", deployment_id=deployment_id,
            system_id=system_id, ticket_id=ticket_id, commit_id=commit_id,
            deployer_id=deployer, deployed_at=deployed, deployment_type=kind,
        )
    for emergency_id, deployment_id, ticket_id, declared, approver, approved in [
        ("EM005", "D005", "CH005", _time(20, 8), None, None),
        ("EM006", "D006", "CH006", _time(24, 8), "P014", _time(25, 8)),
        ("EM007", "D007", "CH007", _time(30, 18), None, None),
    ]:
        add(
            "emergency_changes", "SYNTHETIC_CHANGE_MGMT", emergency_id=emergency_id,
            deployment_id=deployment_id, ticket_id=ticket_id,
            justification="Synthetic urgent restoration", declared_at=declared,
            retrospective_approver_id=approver, retrospective_approved_at=approved,
        )

    # Seeded normal background makes the fixture less like a list of only failures.
    for i in range(1, 7):
        n = f"X{i:03d}"
        employee_id, account_id = f"P{n}", f"A{n}"
        department = rng.choice(["FinanceOps", "ProductOps", "SharedOps"])
        hire_day = rng.randint(1, 20)
        add(
            "employees", "SYNTHETIC_HRIS", employee_id=employee_id,
            display_name=f"Synthetic Person {n}", current_department=department,
            employment_status="ACTIVE",
        )
        add(
            "hr_events", "SYNTHETIC_HRIS", event_id=f"HIRE-{n}",
            employee_id=employee_id, event_type="HIRE",
            event_at=f"2024-01-{hire_day:02d}T09:00:00Z",
            from_department=None, to_department=department,
        )
        add(
            "accounts", "SYNTHETIC_IAM", account_id=account_id,
            employee_id=employee_id, system_id="FIN-ERP",
            created_at=f"2024-01-{hire_day:02d}T10:00:00Z", disabled_at=None,
        )
        add(
            "entitlements", "SYNTHETIC_IAM", entitlement_id=f"EN{n}",
            account_id=account_id, role_id="R_VIEW", request_id=None,
            granted_at="2025-05-01T10:00:00Z", revoked_at=None,
        )

    expected = [
        ("CASE-01", "CT-01", "A001", "terminated_account_open", "exception",
         "Leaver account remains enabled beyond 48 hours."),
        ("CASE-02", "CT-01", "EN003", "transfer_old_role_retained", "exception",
         "Old-department role remains after the five-day transfer window."),
        ("CASE-03", "CT-01", "EN012", "transfer_department_unknown", "manual_review",
         "The former department was not present in the HR transfer extract."),
        ("CASE-04", "CT-02", "EN005", "privileged_approval_missing", "exception",
         "Privileged entitlement has no linked approval request."),
        ("CASE-05", "CT-02", "EN006", "privileged_review_overdue", "exception",
         "Privilege review was completed after its due timestamp."),
        ("CASE-06", "CT-03", "Q007", "sensitive_self_approval", "exception",
         "Requester approved their own sensitive permission request."),
        ("CASE-07", "CT-04", "D001", "deployment_ticket_missing", "exception",
         "Production deployment has no linked change ticket."),
        ("CASE-08", "CT-04", "D002", "deployment_approval_late", "exception",
         "Change approval occurred after production deployment."),
        ("CASE-09", "CT-04", "D002", "deployment_test_late", "exception",
         "Recorded test occurred after production deployment."),
        ("CASE-10", "CT-04", "D003", "deployment_test_unknown", "manual_review",
         "The ticket has no test timestamp; evidence extraction needs review."),
        ("CASE-11", "CT-05", "D005", "emergency_approval_overdue", "exception",
         "No retrospective approval within 24 hours of the deployment."),
        ("CASE-12", "CT-05", "D007", "emergency_window_open", "manual_review",
         "The 24-hour retrospective window is open at audit cutoff."),
    ]
    for case_id, control_id, entity_id, issue_code, classification, explanation in expected:
        add(
            "expected_results", "SYNTHETIC_TEST_MANIFEST", case_id=case_id,
            control_id=control_id, entity_id=entity_id, issue_code=issue_code,
            expected_classification=classification, explanation=explanation,
        )

    return rows


def parse_utc(value: str | None) -> datetime | None:
    """Parse a fixture timestamp as an aware UTC datetime."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

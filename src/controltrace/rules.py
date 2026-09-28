"""Auditable control tests over synthetic source extracts.

The rules produce *candidate findings*. A human reviewer makes audit conclusions.
The expected-results manifest is intentionally not read by this module.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import timedelta
from typing import Any

from controltrace.data import DEMO_CUTOFF, PRIMARY_KEYS, parse_utc

RULES: dict[str, dict[str, Any]] = {
    "CT-01": {
        "name": "账号生命周期与转岗权限",
        "control_goal": "离职账号及时停用，转岗人员及时移除原部门专属权限。",
        "inputs": [
            "hr_events.employee_id/event_type/event_at/from_department/to_department",
            "accounts.employee_id/created_at/disabled_at",
            "entitlements.account_id/role_id/granted_at/revoked_at",
            "roles.owner_department",
        ],
        "logic": "离职后48小时内停用账号；转岗后5×24小时内撤销原部门专属角色。所有时间均为UTC，等于期限视为按时。",
        "exceptions": "共享角色（owner_department=GLOBAL）不视为原部门权限；来源缺少人事事件时间、原部门、角色归属、授权时间或事件关联不到账号时列为待人工判断。期限恰在审计截止日届满时纳入测试。",
        "limitations": "账号停用时间及角色归属来自模拟抽取；关联不到账号不等于员工确实没有账号，也无法判断企业批准的延长期或系统外补偿控制。",
        "threshold": "offboarding=48 hours; transfer=5 days",
    },
    "CT-02": {
        "name": "高权限审批与周期复核",
        "control_goal": "高权限在授予前获有效批准，并在规定期限内完成权限复核。",
        "inputs": [
            "roles.is_privileged", "entitlements.request_id/granted_at/revoked_at",
            "access_requests.account_id/role_id/submitted_at",
            "access_approvals.decision/decided_at/valid_until",
            "access_reviews.period_end/due_at/completed_at",
        ],
        "logic": "批准必须关联同一账号和角色、决定为APPROVED，且申请时间不晚于批准时间、批准时间不晚于授予时间、有效期不早于授予。复核完成时间不得晚于due_at。",
        "exceptions": "审批检查截止日前全部高权限授予；复核只检查到期时仍有效的授权。授予时间或复核期限缺失时列为人工核实；未到复核到期日不判逾期；有效授权无复核记录列为人工核实。职责冲突由CT-03单独检测。",
        "limitations": "本测试依赖账号、角色、审批和复核导出完整性；授权关联不到账号或角色是证据缺口，最终定性仍需核对原系统。",
        "threshold": "approval <= grant; review completion <= due_at",
    },
    "CT-03": {
        "name": "敏感权限职责分离",
        "control_goal": "敏感权限申请和批准不得由同一人完成。",
        "inputs": [
            "access_requests.requester_id/role_id", "access_approvals.approver_id/decision",
            "role_permissions.is_sensitive",
        ],
        "logic": "已批准的敏感角色申请中，requester_id等于approver_id即命中。",
        "exceptions": "只检查角色含敏感权限且存在截止日前APPROVED决定的申请；申请时间缺失不妨碍核对申请人和批准人ID，审批时序由CT-02判断。敏感性映射缺失时列为待人工判断。其他职责冲突组合暂未覆盖。",
        "limitations": "不同人员ID可能对应同一自然人，或同一共享账号可能代表多人；本演示无法识别这类身份映射。",
        "threshold": "requester_id != approver_id",
    },
    "CT-04": {
        "name": "生产变更事前控制",
        "control_goal": "标准生产部署有同系统工单，且审批与测试在部署前完成。",
        "inputs": [
            "deployments.deployment_id/system_id/ticket_id/commit_id/deployed_at/deployment_type",
            "change_tickets.system_id/approved_at/tested_at",
            "code_commits.ticket_id/committed_at",
        ],
        "logic": "标准部署须关联同系统工单，approved_at、tested_at和committed_at不晚于deployed_at；提交记录缺失、提交关联工单不一致或缺失时间列为待人工判断。",
        "exceptions": "标记为EMERGENCY的部署适用CT-05补批控制；部署时间缺失或部署类型未知时列为待人工判断。",
        "limitations": "时间戳证明记录顺序；提交记录缺失或关联工单不一致仅说明证据链需核实，不证明测试质量、审批独立性、代码内容或部署范围。",
        "threshold": "ticket exists; approval/test/commit <= deployment",
    },
    "CT-05": {
        "name": "应急变更补充审批",
        "control_goal": "应急生产部署在24小时内取得补充审批。",
        "inputs": [
            "deployments.deployment_type/deployed_at/ticket_id",
            "emergency_changes.deployment_id/ticket_id",
            "emergency_changes.retrospective_approved_at/retrospective_approver_id",
        ],
        "logic": "同部署、同工单的补批时间应位于部署后24小时内；超过期限仍无完整补批或补批过晚则命中。截止日时限尚未届满列为待人工判断。",
        "exceptions": "恰在24小时期限完成补批视为按时；期限恰在截止日届满而无补批视为逾期。部署时间缺失、工单关联不一致或补批时间早于部署需人工核实。",
        "limitations": "只测试时间和记录存在性；紧急程度、补批权限与变更合理性需人工阅读原始材料。",
        "threshold": "deployment <= retrospective approval <= deployment + 24 hours",
    },
}

_TIMELINE_FIELDS = {
    "hr_events": [("event_at", "HR事件", "event")],
    "accounts": [("created_at", "账号创建", "event"), ("disabled_at", "账号停用", "event")],
    "entitlements": [("granted_at", "权限授予", "event"), ("revoked_at", "权限撤销", "event")],
    "access_requests": [("submitted_at", "权限申请", "event")],
    "access_approvals": [("decided_at", "权限审批", "event")],
    "access_reviews": [
        ("due_at", "复核期限", "deadline"), ("completed_at", "复核完成", "event")
    ],
    "change_tickets": [
        ("submitted_at", "工单提交", "event"), ("approved_at", "变更审批", "event"),
        ("tested_at", "变更测试", "event"),
    ],
    "code_commits": [("committed_at", "代码提交", "event")],
    "deployments": [("deployed_at", "生产部署", "event")],
    "emergency_changes": [
        ("declared_at", "应急声明", "event"),
        ("retrospective_approved_at", "应急补批", "event"),
    ],
}


def _index(rows: list[dict], field: str) -> dict[str, dict]:
    return {row[field]: row for row in rows}


def _finding(
    control_id: str,
    issue_code: str,
    title: str,
    classification: str,
    risk: str,
    system: str,
    occurred_at: str,
    entity_type: str,
    entity_id: str,
    summary: str,
    rationale: str,
    evidence: list[tuple[str, dict | None]],
) -> dict[str, Any]:
    unique: dict[tuple[str, str], tuple[str, dict]] = {}
    for table, record in evidence:
        if record is not None:
            record_id = str(record[PRIMARY_KEYS[table]])
            unique[(table, record_id)] = (table, record)
    related = [
        {"table": table, "id": str(record[PRIMARY_KEYS[table]]), "record": record.copy()}
        for table, record in unique.values()
    ]
    timeline = []
    for item in related:
        record = item["record"]
        for field, label, kind in _TIMELINE_FIELDS.get(item["table"], []):
            if record.get(field):
                timeline.append({
                    "at": record[field], "event": label, "kind": kind,
                    "field": field, "evidence_id": item["id"],
                    "source_system": record["source_system"],
                })
    timeline.sort(key=lambda item: (item["at"], item["evidence_id"], item["field"]))
    digest = hashlib.sha256(f"{control_id}|{issue_code}|{entity_id}".encode()).hexdigest()[:12]
    return {
        "finding_id": f"F-{digest.upper()}",
        "control_id": control_id,
        "rule_id": control_id,
        "issue_code": issue_code,
        "title": title,
        "classification": classification,
        "risk": risk,
        "severity": risk,
        "system": system,
        "system_id": system,
        "occurred_at": occurred_at,
        "period": occurred_at[:7],
        "entity_type": entity_type,
        "entity_id": entity_id,
        "summary": summary,
        "rationale": rationale,
        "evidence_ids": [f"{item['table']}:{item['id']}" for item in related],
        "timeline": timeline,
        "related_records": related,
        "rule": {"control_id": control_id, **RULES[control_id]},
        "audit_cutoff": DEMO_CUTOFF,
    }


def evaluate(data: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Evaluate five controls. No database writes; suitable for boundary testing."""
    cutoff = parse_utc(DEMO_CUTOFF)
    assert cutoff is not None
    findings: list[dict[str, Any]] = []
    accounts = data["accounts"]
    roles = _index(data["roles"], "role_id")
    accounts_by_id = _index(accounts, "account_id")
    requests = _index(data["access_requests"], "request_id")
    tickets = _index(data["change_tickets"], "ticket_id")
    commits = _index(data["code_commits"], "commit_id")
    permissions_by_role: dict[str, list[dict]] = {}
    for permission in data["role_permissions"]:
        permissions_by_role.setdefault(permission["role_id"], []).append(permission)
    approvals_by_request: dict[str, list[dict]] = {}
    for approval in data["access_approvals"]:
        approvals_by_request.setdefault(approval["request_id"], []).append(approval)
    reviews_by_entitlement: dict[str, list[dict]] = {}
    for review in data["access_reviews"]:
        reviews_by_entitlement.setdefault(review["entitlement_id"], []).append(review)
    emergency_by_deployment: dict[str, list[dict]] = {}
    for emergency in data["emergency_changes"]:
        emergency_by_deployment.setdefault(emergency["deployment_id"], []).append(emergency)
    for emergencies in emergency_by_deployment.values():
        emergencies.sort(key=lambda row: row["emergency_id"])

    # CT-01: HR events drive the window. A late disable or revoke still counts.
    for event in data["hr_events"]:
        if event["event_type"] not in {"TERMINATION", "TRANSFER"}:
            continue
        event_at = parse_utc(event["event_at"])
        if event_at is None:
            findings.append(_finding(
                "CT-01", "hr_event_time_unknown", "人事事件生效时间缺失",
                "manual_review", "medium", "UNKNOWN", "UNKNOWN", "hr_event", event["event_id"],
                "人事事件缺少生效时间，无法计算账号或权限回收期限。",
                f"人事事件 {event['event_id']} 类型为 {event['event_type']}；"
                "event_at 为空，需核对 HR 原始记录。",
                [("hr_events", event)],
            ))
            continue
        if event["event_type"] == "TERMINATION":
            due = event_at + timedelta(hours=48)
            if cutoff < due:
                continue
            linked_accounts = [
                account for account in accounts if account["employee_id"] == event["employee_id"]
            ]
            if not linked_accounts:
                findings.append(_finding(
                    "CT-01", "termination_account_extract_missing", "离职事件未关联到账号",
                    "manual_review", "medium", "UNKNOWN", event["event_at"],
                    "hr_event", event["event_id"],
                    "账号抽取中未找到该离职员工的账号，需核实账号总体完整性。",
                    f"离职事件 {event['event_id']} 对应员工 {event['employee_id']}；"
                    "账号抽取中没有该员工记录，无法判断其是否曾有账号或是否按时停用。",
                    [("hr_events", event)],
                ))
            for account in linked_accounts:
                disabled = parse_utc(account["disabled_at"])
                if disabled is None or disabled > due:
                    findings.append(_finding(
                        "CT-01", "terminated_account_open", "离职账号未按时停用",
                        "exception", "high", account["system_id"], event["event_at"],
                        "account", account["account_id"],
                        "离职后48小时内未见账号停用。",
                        f"HR离职事件 {event['event_id']} 时间为 {event['event_at']}；"
                        f"停用期限为 {due.isoformat().replace('+00:00', 'Z')}；"
                        f"账号 {account['account_id']} 停用时间为 {account['disabled_at'] or '空'}。",
                        [("hr_events", event), ("accounts", account)],
                    ))
        elif event["event_type"] == "TRANSFER":
            due = event_at + timedelta(days=5)
            if cutoff < due:
                continue
            linked_accounts = [
                account for account in accounts if account["employee_id"] == event["employee_id"]
            ]
            if not linked_accounts:
                findings.append(_finding(
                    "CT-01", "transfer_account_extract_missing", "转岗事件未关联到账号",
                    "manual_review", "medium", "UNKNOWN", event["event_at"],
                    "hr_event", event["event_id"],
                    "账号抽取中未找到该转岗员工的账号，需核实旧权限总体完整性。",
                    f"转岗事件 {event['event_id']} 对应员工 {event['employee_id']}；"
                    "账号抽取中没有该员工记录，无法判断其是否有需撤销的旧权限。",
                    [("hr_events", event)],
                ))
            for entitlement in data["entitlements"]:
                account = accounts_by_id.get(entitlement["account_id"])
                role = roles.get(entitlement["role_id"])
                if not account or not role or account["employee_id"] != event["employee_id"]:
                    continue
                if role["owner_department"] is not None and role["owner_department"] in {
                    "GLOBAL", event["to_department"]
                }:
                    continue
                granted = parse_utc(entitlement["granted_at"])
                revoked = parse_utc(entitlement["revoked_at"])
                if revoked is not None and revoked <= due:
                    continue
                if granted is None:
                    if (role["owner_department"] == event["from_department"]
                            or role["owner_department"] is None
                            or event["from_department"] is None):
                        findings.append(_finding(
                            "CT-01", "transfer_grant_time_unknown", "转岗权限授予时间缺失",
                            "manual_review", "medium", account["system_id"], event["event_at"],
                            "entitlement", entitlement["entitlement_id"],
                            "授权缺少授予时间，无法确认是否属于转岗前已有旧权限。",
                            f"转岗事件 {event['event_id']} 对应授权 "
                            f"{entitlement['entitlement_id']} 的 granted_at 为空；"
                            f"截至回收期限 {due.isoformat().replace('+00:00', 'Z')} "
                            "未见按时撤销，需核对 IAM 原始记录。",
                            [("hr_events", event), ("accounts", account),
                             ("entitlements", entitlement), ("roles", role)],
                        ))
                    continue
                if granted > event_at:
                    continue
                if role["owner_department"] is None:
                    findings.append(_finding(
                        "CT-01", "transfer_role_department_unknown", "转岗角色归属缺失",
                        "manual_review", "medium", account["system_id"], event["event_at"],
                        "entitlement", entitlement["entitlement_id"],
                        "角色归属部门缺失，无法确认是否属于转岗前岗位权限。",
                        f"角色 {role['role_id']} 的 owner_department 为空；转岗事件 "
                        f"{event['event_id']} 对应授权 {entitlement['entitlement_id']} "
                        "在回收期限后仍有效，需核对角色目录。",
                        [("hr_events", event), ("accounts", account),
                         ("entitlements", entitlement), ("roles", role)],
                    ))
                elif event["from_department"] is None:
                    findings.append(_finding(
                        "CT-01", "transfer_department_unknown", "转岗前部门缺失，旧权限需核实",
                        "manual_review", "medium", account["system_id"], event["event_at"],
                        "entitlement", entitlement["entitlement_id"],
                        "HR转岗记录缺少原部门，无法确认角色是否应撤销。",
                        f"转岗事件 {event['event_id']} 原部门为空；角色 {role['role_id']} "
                        f"归属 {role['owner_department']}，权限 {entitlement['entitlement_id']} "
                        f"在期限 {due.isoformat().replace('+00:00', 'Z')} 后仍有效。",
                        [("hr_events", event), ("accounts", account),
                         ("entitlements", entitlement), ("roles", role)],
                    ))
                elif role["owner_department"] == event["from_department"]:
                    findings.append(_finding(
                        "CT-01", "transfer_old_role_retained", "转岗旧权限未按时撤销",
                        "exception", "high", account["system_id"], event["event_at"],
                        "entitlement", entitlement["entitlement_id"],
                        "原部门专属角色在转岗后5天仍有效。",
                        f"转岗事件 {event['event_id']} 从 {event['from_department']} "
                        f"转入 {event['to_department']}，期限为 "
                        f"{due.isoformat().replace('+00:00', 'Z')}；权限 "
                        f"{entitlement['entitlement_id']} 撤销时间为 "
                        f"{entitlement['revoked_at'] or '空'}。",
                        [("hr_events", event), ("accounts", account),
                         ("entitlements", entitlement), ("roles", role)],
                    ))

    # CT-02: test historical grant approvals and reviews due while access was active.
    for entitlement in data["entitlements"]:
        role = roles.get(entitlement["role_id"])
        granted = parse_utc(entitlement["granted_at"])
        if granted is None:
            if role is not None and not role["is_privileged"]:
                continue
            account = accounts_by_id.get(entitlement["account_id"])
            findings.append(_finding(
                "CT-02", "entitlement_grant_time_unknown", "授权生效时间缺失",
                "manual_review", "medium", account["system_id"] if account else "UNKNOWN",
                "UNKNOWN", "entitlement", entitlement["entitlement_id"],
                "授权缺少授予时间，无法判断审批是否事前完成或复核是否到期。",
                f"授权 {entitlement['entitlement_id']} 的 granted_at 为空，"
                "需核对 IAM 原始记录。",
                [("entitlements", entitlement), ("accounts", account), ("roles", role)],
            ))
            continue
        if granted > cutoff:
            continue
        account = accounts_by_id.get(entitlement["account_id"])
        if role is None or account is None:
            missing = "、".join(
                label for record, label in ((account, "账号"), (role, "角色"))
                if record is None
            )
            findings.append(_finding(
                "CT-02", "entitlement_mapping_missing", "授权关联记录缺失",
                "manual_review", "medium", account["system_id"] if account else "UNKNOWN",
                entitlement["granted_at"], "entitlement", entitlement["entitlement_id"],
                f"授权关联不到{missing}，无法完成高权限审批与复核测试。",
                f"授权 {entitlement['entitlement_id']} 指向账号 "
                f"{entitlement['account_id']} 和角色 {entitlement['role_id']}；"
                f"抽取中缺少{missing}，需先核实来源总体和关联键。",
                [("entitlements", entitlement), ("accounts", account), ("roles", role)],
            ))
            continue
        if not role["is_privileged"]:
            continue
        revoked = parse_utc(entitlement["revoked_at"])
        request = requests.get(entitlement["request_id"] or "")
        approvals = approvals_by_request.get(entitlement["request_id"] or "", [])
        submitted = parse_utc(request["submitted_at"]) if request else None
        matching_request = (
            request is not None and request["account_id"] == entitlement["account_id"]
            and request["role_id"] == entitlement["role_id"]
            and submitted is not None and submitted <= granted
        )
        valid = False
        if matching_request and submitted is not None:
            for approval in approvals:
                decided = parse_utc(approval["decided_at"])
                expires = parse_utc(approval["valid_until"])
                if (
                    approval["decision"] == "APPROVED"
                    and decided is not None and submitted <= decided <= granted
                    and (expires is None or expires >= granted)
                ):
                    valid = True
                    break
        if not valid:
            findings.append(_finding(
                "CT-02", "privileged_approval_missing", "高权限缺少有效事前审批",
                "exception", "high", account["system_id"], entitlement["granted_at"],
                "entitlement", entitlement["entitlement_id"],
                "高权限授予前未找到同账号同角色的有效批准。",
                f"权限 {entitlement['entitlement_id']} 于 {entitlement['granted_at']} 授予；"
                f"关联申请 {entitlement['request_id'] or '空'}；符合口径的事前审批不存在。",
                [("accounts", account), ("roles", role), ("entitlements", entitlement),
                 ("access_requests", request)]
                + [("access_approvals", approval) for approval in approvals],
            ))
        dated_reviews = [
            review for review in reviews_by_entitlement.get(entitlement["entitlement_id"], [])
            if parse_utc(review["due_at"]) is not None
            and parse_utc(review["due_at"]) <= cutoff
            and (revoked is None or revoked > parse_utc(review["due_at"]))
            and granted <= parse_utc(review["due_at"])
        ]
        for review in reviews_by_entitlement.get(entitlement["entitlement_id"], []):
            if parse_utc(review["due_at"]) is None:
                findings.append(_finding(
                    "CT-02", "privileged_review_due_unknown", "高权限复核期限缺失",
                    "manual_review", "medium", account["system_id"],
                    review["period_end"], "entitlement", entitlement["entitlement_id"],
                    "复核记录缺少到期时间，无法判断该周期是否逾期。",
                    f"复核 {review['review_id']} 的 due_at 为空；"
                    "需核对复核计划和源记录。",
                    [("accounts", account), ("roles", role),
                     ("entitlements", entitlement), ("access_reviews", review)],
                ))
        if not reviews_by_entitlement.get(entitlement["entitlement_id"]) and (
            revoked is None or revoked > cutoff
        ):
            findings.append(_finding(
                "CT-02", "privileged_review_evidence_missing", "高权限周期复核记录缺失",
                "manual_review", "medium", account["system_id"],
                entitlement["granted_at"], "entitlement", entitlement["entitlement_id"],
                "未抽取到该高权限的周期复核记录，需核对复核范围和期限。",
                f"权限 {entitlement['entitlement_id']} 在审计截止日仍有效，"
                "但权限复核抽取中没有关联记录；不能仅凭空值判断是否逾期。",
                [("accounts", account), ("roles", role), ("entitlements", entitlement)],
            ))
        for review in dated_reviews:
            due = parse_utc(review["due_at"])
            completed = parse_utc(review["completed_at"])
            assert due is not None
            if completed is None or completed > due:
                findings.append(_finding(
                    "CT-02", "privileged_review_overdue", "高权限复核逾期",
                    "exception", "medium", account["system_id"], review["due_at"],
                    "entitlement", entitlement["entitlement_id"],
                    "高权限复核未在记录的截止时间内完成。",
                    f"复核 {review['review_id']} 期限为 {review['due_at']}；"
                    f"完成时间为 {review['completed_at'] or '空'}。",
                    [("accounts", account), ("roles", role),
                     ("entitlements", entitlement), ("access_reviews", review)],
                ))

    # CT-03: a clear same-identity conflict on approved sensitive requests.
    for request in data["access_requests"]:
        submitted = parse_utc(request["submitted_at"])
        if submitted is not None and submitted > cutoff:
            continue
        role_permissions = permissions_by_role.get(request["role_id"], [])
        sensitive_permissions = [row for row in role_permissions if row["is_sensitive"]]
        relevant_approvals = [
            approval for approval in approvals_by_request.get(request["request_id"], [])
            if approval["decision"] == "APPROVED"
            and parse_utc(approval["decided_at"]) is not None
            and parse_utc(approval["decided_at"]) <= cutoff
        ]
        if not role_permissions and relevant_approvals:
            account = accounts_by_id.get(request["account_id"])
            findings.append(_finding(
                "CT-03", "request_sensitivity_mapping_missing", "申请角色敏感性映射缺失",
                "manual_review", "medium", account["system_id"] if account else "UNKNOWN",
                (request["submitted_at"] if submitted is not None else min(
                    approval["decided_at"] for approval in relevant_approvals
                )), "access_request", request["request_id"],
                "已批准的角色申请缺少权限映射，无法判断是否需要职责分离测试。",
                f"申请 {request['request_id']} 的角色 {request['role_id']} "
                "未在角色权限抽取中出现，需核对角色权限总体。",
                [("access_requests", request), ("roles", roles.get(request["role_id"])),
                 ("accounts", account)]
                + [("access_approvals", approval) for approval in relevant_approvals],
            ))
        if not sensitive_permissions:
            continue
        account = accounts_by_id.get(request["account_id"])
        for approval in relevant_approvals:
            if request["requester_id"] != approval["approver_id"]:
                continue
            findings.append(_finding(
                "CT-03", "sensitive_self_approval", "敏感权限由申请人自行批准",
                "exception", "high", account["system_id"] if account else "UNKNOWN",
                approval["decided_at"] or request["submitted_at"],
                "access_request", request["request_id"],
                "同一员工ID出现在敏感权限申请人与批准人字段。",
                f"申请 {request['request_id']} 的申请人和审批人均为 "
                f"{request['requester_id']}；审批记录 {approval['approval_id']} "
                "状态为APPROVED。",
                [("access_requests", request), ("access_approvals", approval),
                 ("accounts", account)]
                + [("role_permissions", permission) for permission in sensitive_permissions],
            ))

    # CT-04: standard changes only; emergency deployments use CT-05.
    for deployment in data["deployments"]:
        if deployment["deployment_type"] == "EMERGENCY":
            continue
        deployed = parse_utc(deployment["deployed_at"])
        if deployed is None:
            findings.append(_finding(
                "CT-04", "deployment_time_unknown", "生产部署时间缺失",
                "manual_review", "medium", deployment["system_id"], "UNKNOWN",
                "deployment", deployment["deployment_id"],
                "部署缺少时间，无法核对工单审批、测试和提交先后。",
                f"部署 {deployment['deployment_id']} 的 deployed_at 为空；"
                "需核对发布流水线原始记录。",
                [("deployments", deployment)],
            ))
            continue
        if deployed > cutoff:
            continue
        if deployment["deployment_type"] != "STANDARD":
            findings.append(_finding(
                "CT-04", "deployment_type_unknown", "生产部署类型无法识别",
                "manual_review", "medium", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "部署类型无法归入标准或应急控制测试，需核对来源分类。",
                f"部署 {deployment['deployment_id']} 类型为 "
                f"{deployment['deployment_type']!r}，无法选用相应测试口径。",
                [("deployments", deployment)],
            ))
            continue
        ticket = tickets.get(deployment["ticket_id"] or "")
        commit = commits.get(deployment["commit_id"])
        base_evidence = [("deployments", deployment), ("code_commits", commit)]
        if ticket is None:
            findings.append(_finding(
                "CT-04", "deployment_ticket_missing", "生产部署缺少关联工单",
                "exception", "high", deployment["system_id"], deployment["deployed_at"],
                "deployment", deployment["deployment_id"],
                "标准生产部署未找到关联变更工单。",
                f"部署 {deployment['deployment_id']} 的工单ID为 "
                f"{deployment['ticket_id'] or '空'}，在工单抽取中无匹配记录。",
                base_evidence,
            ))
            continue
        evidence = base_evidence + [("change_tickets", ticket)]
        if ticket["system_id"] != deployment["system_id"]:
            findings.append(_finding(
                "CT-04", "deployment_ticket_system_mismatch", "工单与部署系统不一致",
                "exception", "high", deployment["system_id"], deployment["deployed_at"],
                "deployment", deployment["deployment_id"],
                "关联工单不属于部署的系统。",
                f"部署系统为 {deployment['system_id']}，工单系统为 {ticket['system_id']}。",
                evidence,
            ))
            continue
        if commit is None:
            findings.append(_finding(
                "CT-04", "deployment_commit_missing", "部署关联的代码提交缺失",
                "manual_review", "medium", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "部署所指向的代码提交未出现在抽取中，需核对部署证据链。",
                f"部署 {deployment['deployment_id']} 指向提交 "
                f"{deployment['commit_id'] or '空'}，但提交抽取中没有对应记录。",
                evidence,
            ))
        elif commit["ticket_id"] != deployment["ticket_id"]:
            findings.append(_finding(
                "CT-04", "deployment_commit_ticket_mismatch", "提交与部署工单关联不一致",
                "manual_review", "medium", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "代码提交和部署指向不同工单，需核对变更证据链。",
                f"部署 {deployment['deployment_id']} 的工单为 "
                f"{deployment['ticket_id']}；提交 {commit['commit_id']} 的工单为 "
                f"{commit['ticket_id'] or '空'}。",
                evidence,
            ))
        if commit is not None:
            committed = parse_utc(commit["committed_at"])
            if committed is None:
                findings.append(_finding(
                    "CT-04", "deployment_commit_time_unknown", "代码提交时间缺失",
                    "manual_review", "medium", deployment["system_id"],
                    deployment["deployed_at"], "deployment", deployment["deployment_id"],
                    "关联提交缺少时间，无法确认其是否早于部署。",
                    f"提交 {commit['commit_id']} 的 committed_at 为空。", evidence,
                ))
            elif committed > deployed:
                findings.append(_finding(
                    "CT-04", "deployment_commit_late", "代码提交晚于生产部署",
                    "exception", "high", deployment["system_id"],
                    deployment["deployed_at"], "deployment", deployment["deployment_id"],
                    "部署引用的代码提交时间晚于生产部署。",
                    f"提交 {commit['commit_id']} 时间为 {commit['committed_at']}；"
                    f"部署 {deployment['deployment_id']} 时间为 {deployment['deployed_at']}。",
                    evidence,
                ))
        for field, unknown_issue, late_issue, label in [
            ("approved_at", "deployment_approval_unknown", "deployment_approval_late", "审批"),
            ("tested_at", "deployment_test_unknown", "deployment_test_late", "测试"),
        ]:
            recorded = parse_utc(ticket[field])
            if recorded is None:
                findings.append(_finding(
                    "CT-04", unknown_issue, f"变更{label}证据缺失",
                    "manual_review", "medium", deployment["system_id"],
                    deployment["deployed_at"], "deployment", deployment["deployment_id"],
                    f"工单未提供{label}时间，需核对源系统。",
                    f"工单 {ticket['ticket_id']} 的 {field} 为空；部署发生在 "
                    f"{deployment['deployed_at']}。空值本身尚不能证明{label}未发生。",
                    evidence,
                ))
            elif recorded > deployed:
                findings.append(_finding(
                    "CT-04", late_issue, f"变更{label}晚于生产部署",
                    "exception", "high", deployment["system_id"],
                    deployment["deployed_at"], "deployment", deployment["deployment_id"],
                    f"工单记录的{label}时间晚于部署时间。",
                    f"工单 {ticket['ticket_id']} 的 {field} 为 {ticket[field]}；"
                    f"部署 {deployment['deployment_id']} 时间为 {deployment['deployed_at']}。",
                    evidence,
                ))

    # CT-05: a deadline is measured from the deployment, not declaration time.
    for deployment in data["deployments"]:
        if deployment["deployment_type"] != "EMERGENCY":
            continue
        deployed = parse_utc(deployment["deployed_at"])
        if deployed is None:
            findings.append(_finding(
                "CT-05", "emergency_deployment_time_unknown", "应急部署时间缺失",
                "manual_review", "medium", deployment["system_id"], "UNKNOWN",
                "deployment", deployment["deployment_id"],
                "应急部署缺少时间，无法计算补充审批的24小时期限。",
                f"部署 {deployment['deployment_id']} 的 deployed_at 为空；"
                "需核对发布流水线原始记录。",
                [("deployments", deployment)] + [
                    ("emergency_changes", row)
                    for row in emergency_by_deployment.get(deployment["deployment_id"], [])
                ],
            ))
            continue
        if deployed > cutoff:
            continue
        due = deployed + timedelta(hours=24)
        emergencies = emergency_by_deployment.get(deployment["deployment_id"], [])
        ticket = tickets.get(deployment["ticket_id"] or "")
        commit = commits.get(deployment["commit_id"])
        evidence = [("deployments", deployment), ("change_tickets", ticket),
                    ("code_commits", commit)] + [
                        ("emergency_changes", row) for row in emergencies
                    ]
        matching_emergencies = [
            row for row in emergencies if row["ticket_id"] == deployment["ticket_id"]
        ]
        mismatched_emergencies = [
            row for row in emergencies if row["ticket_id"] != deployment["ticket_id"]
        ]
        if mismatched_emergencies:
            findings.append(_finding(
                "CT-05", "emergency_ticket_mismatch", "应急记录与部署工单不一致",
                "manual_review", "medium", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "应急补批记录的工单与部署工单不一致，需核对关联键。",
                f"部署 {deployment['deployment_id']} 工单为 "
                f"{deployment['ticket_id'] or '空'}；存在其他工单的应急记录，"
                "其补批不能直接证明该部署按时获批。",
                evidence,
            ))
        completed_approvals = [
            (approved, row) for row in matching_emergencies
            if (approved := parse_utc(row["retrospective_approved_at"])) is not None
            and approved <= cutoff and row["retrospective_approver_id"]
        ]
        incomplete_on_time = [
            row for row in matching_emergencies
            if (approved := parse_utc(row["retrospective_approved_at"])) is not None
            and deployed <= approved <= min(due, cutoff)
            and not row["retrospective_approver_id"]
        ]
        before_deployment = [
            row for approved, row in completed_approvals if approved < deployed
        ]
        on_time = any(deployed <= approved <= due for approved, _ in completed_approvals)
        if not emergencies:
            findings.append(_finding(
                "CT-05", "emergency_record_unknown", "应急变更记录缺失",
                "manual_review", "medium", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "部署标记为应急，但未抽取到应急变更记录。",
                f"部署 {deployment['deployment_id']} 标记EMERGENCY；补批期限 "
                f"{due.isoformat().replace('+00:00', 'Z')}，需核对抽取范围。",
                evidence,
            ))
        elif not matching_emergencies:
            continue
        elif on_time:
            continue
        elif incomplete_on_time:
            findings.append(_finding(
                "CT-05", "emergency_approver_unknown", "应急补批人缺失",
                "manual_review", "medium", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "补批时间存在，但补批人字段缺失。",
                f"应急记录 {incomplete_on_time[0]['emergency_id']} 无补批人ID，"
                "需核对审批原件。",
                evidence,
            ))
        elif before_deployment:
            findings.append(_finding(
                "CT-05", "emergency_approval_before_deployment", "应急补批早于部署",
                "manual_review", "medium", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "补批记录早于生产部署，需核对时间及审批性质。",
                f"应急记录 {before_deployment[0]['emergency_id']} 的补批时间 "
                f"{before_deployment[0]['retrospective_approved_at']} 早于部署 "
                f"{deployment['deployed_at']}。",
                evidence,
            ))
        elif cutoff < due:
            findings.append(_finding(
                "CT-05", "emergency_window_open", "应急补批窗口尚未届满",
                "manual_review", "low", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "截止日时24小时补批窗口仍开放。",
                f"部署 {deployment['deployment_id']} 时间为 {deployment['deployed_at']}；"
                f"期限 {due.isoformat().replace('+00:00', 'Z')}；"
                f"审计截止为 {DEMO_CUTOFF}。后续须跟踪。",
                evidence,
            ))
        else:
            late = min(completed_approvals, default=None, key=lambda pair: pair[0])
            detail = (
                f"最早有效补批时间 {late[0].isoformat().replace('+00:00', 'Z')} "
                f"晚于期限 {due.isoformat().replace('+00:00', 'Z')}。"
                if late else f"截至 {DEMO_CUTOFF} 未见有补批人和补批时间的完整记录。"
            )
            findings.append(_finding(
                "CT-05", "emergency_approval_overdue", "应急变更补批逾期",
                "exception", "high", deployment["system_id"],
                deployment["deployed_at"], "deployment", deployment["deployment_id"],
                "24小时补批期限已届满，未见期限内的完整补批记录。",
                f"部署 {deployment['deployment_id']} 时间为 {deployment['deployed_at']}；"
                f"{detail}",
                evidence,
            ))

    # A person can transfer twice, an entitlement can have two overdue review cycles,
    # and a request can have two self-approvals. Distinguish these source events without
    # changing IDs for the ordinary one-finding-per-entity case (preserving reviews).
    collisions = Counter(item["finding_id"] for item in findings)
    for finding in findings:
        if collisions[finding["finding_id"]] > 1:
            signature = "|".join(sorted(finding["evidence_ids"]))
            digest = hashlib.sha256(
                f"{finding['finding_id']}|{signature}".encode()
            ).hexdigest()[:12]
            finding["finding_id"] = f"F-{digest.upper()}"
    if len({item["finding_id"] for item in findings}) != len(findings):
        raise ValueError("Duplicate findings have indistinguishable source evidence")
    findings.sort(key=lambda item: (item["control_id"], item["occurred_at"],
                                    item["entity_id"], item["issue_code"]))
    return findings


def run_tests(path: str) -> list[dict[str, Any]]:
    """Run tests against a DuckDB file and persist candidates, preserving reviews."""
    from controltrace.store import run_tests as persist_run_tests

    return persist_run_tests(path)

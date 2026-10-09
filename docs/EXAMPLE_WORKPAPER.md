# Workpaper: F-2C5FD7D5834D

**Synthetic case study. Not a real company audit or an audit opinion.**

- Audit cutoff: `2025-06-30T23:59:59Z`
- Dataset SHA-256: `8781ddd17419edf9c90b31c9e25abc33ffeb2e27dc7fc182eca717a2d966de4c`
- ControlTrace version: `0.2.6`
- Control: `CT-04` — 变更审批晚于生产部署
- Classification: `exception`; risk: `high`
- System: `CODE-HUB`; subject: `deployment:D002`
- Event time: `2025-06-20T10:00:00Z`
- Source evidence IDs: `deployments:D002`, `code_commits:C002`, `change_tickets:CH002`

## Control and test basis

- **Objective:** 标准生产部署有同系统工单，且审批与测试在部署前完成。
- **Inputs:** deployments.deployment_id/system_id/ticket_id/commit_id/deployed_at/deployment_type; change_tickets.system_id/approved_at/tested_at; code_commits.ticket_id/committed_at
- **Logic:** 标准部署须关联同系统工单，approved_at、tested_at和committed_at不晚于deployed_at；提交记录缺失、提交关联工单不一致或缺失时间列为待人工判断。
- **Exceptions:** 标记为EMERGENCY的部署适用CT-05补批控制；部署时间缺失或部署类型未知时列为待人工判断。
- **Limitations:** 时间戳证明记录顺序；提交记录缺失或关联工单不一致仅说明证据链需核实，不证明测试质量、审批独立性、代码内容或部署范围。

## Automated observation

工单记录的审批时间晚于部署时间。

工单 CH002 的 approved_at 为 2025-06-21T12:00:00Z；部署 D002 时间为 2025-06-20T10:00:00Z。

## Event timeline

- `{"at": "2025-06-17T12:00:00Z", "event": "工单提交", "evidence_id": "CH002", "field": "submitted_at", "kind": "event", "source_system": "SYNTHETIC_CHANGE_MGMT"}`
- `{"at": "2025-06-20T08:00:00Z", "event": "代码提交", "evidence_id": "C002", "field": "committed_at", "kind": "event", "source_system": "SYNTHETIC_GIT"}`
- `{"at": "2025-06-20T10:00:00Z", "event": "生产部署", "evidence_id": "D002", "field": "deployed_at", "kind": "event", "source_system": "SYNTHETIC_CICD"}`
- `{"at": "2025-06-21T12:00:00Z", "event": "变更审批", "evidence_id": "CH002", "field": "approved_at", "kind": "event", "source_system": "SYNTHETIC_CHANGE_MGMT"}`
- `{"at": "2025-06-22T12:00:00Z", "event": "变更测试", "evidence_id": "CH002", "field": "tested_at", "kind": "event", "source_system": "SYNTHETIC_CHANGE_MGMT"}`

## Linked source records

    {"id": "D002", "record": {"audit_cutoff": "2025-06-30T23:59:59Z", "commit_id": "C002", "deployed_at": "2025-06-20T10:00:00Z", "deployer_id": "P009", "deployment_id": "D002", "deployment_type": "STANDARD", "source_system": "SYNTHETIC_CICD", "system_id": "CODE-HUB", "ticket_id": "CH002"}, "table": "deployments"}

    {"id": "C002", "record": {"audit_cutoff": "2025-06-30T23:59:59Z", "author_id": "P009", "commit_id": "C002", "committed_at": "2025-06-20T08:00:00Z", "repository": "synthetic/code-hub", "source_system": "SYNTHETIC_GIT", "ticket_id": "CH002"}, "table": "code_commits"}

    {"id": "CH002", "record": {"approved_at": "2025-06-21T12:00:00Z", "audit_cutoff": "2025-06-30T23:59:59Z", "change_summary": "Synthetic deployment with late gates", "change_type": "STANDARD", "requester_id": "P009", "source_system": "SYNTHETIC_CHANGE_MGMT", "submitted_at": "2025-06-17T12:00:00Z", "system_id": "CODE-HUB", "tested_at": "2025-06-22T12:00:00Z", "ticket_id": "CH002"}, "table": "change_tickets"}

## Human review

    {"conclusion": "confirmed_exception", "notes": "Synthetic ticket CH002 records approval at 2025-06-21 12:00 UTC, after deployment D002 at 2025-06-20 10:00 UTC. Confirmed timing exception for this demo; test quality and real authorization remain outside scope.", "reviewed_at": "2026-09-27T12:14:55.173393Z", "reviewer": "Demo Reviewer", "status": "closed"}

### Review history

    {"conclusion": "confirmed_exception", "finding_id": "F-2C5FD7D5834D", "notes": "Synthetic ticket CH002 records approval at 2025-06-21 12:00 UTC, after deployment D002 at 2025-06-20 10:00 UTC. Confirmed timing exception for this demo; test quality and real authorization remain outside scope.", "review_id": "REV-BC6014B8D232", "reviewed_at": "2026-09-27T12:14:55.173393Z", "reviewer": "Demo Reviewer", "status": "closed"}

## Reproduction

From the project root, run `uv run controltrace verify --bundle workpapers.zip` to check file hashes and replay the current rules against `source_tables/*.json`. The verifier compares the CSV copies, `reviews.json`, and this workpaper with the replayed result. Match the finding ID and source IDs above to the JSON rows. To regenerate the synthetic dataset separately, run `uv run controltrace generate` and then `uv run controltrace test`. CSV files are protected against spreadsheet formulas; the generation seed and cutoff are in `manifest.json`.

An automated observation is a review candidate. The reviewer is responsible for assessing evidence completeness and documenting the final conclusion.

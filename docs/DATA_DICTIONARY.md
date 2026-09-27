# 数据字典与证据血缘

> 合成案例，非真实企业审计。以下表均由固定种子 `20250927` 生成，不含真实员工、账号、密码、企业系统或代码。`src/controltrace/data.py` 中的 `TABLE_SCHEMAS` 是字段定义的机器可读来源。

## 时间和来源

所有业务时间均为 UTC ISO 8601 字符串，以 `Z` 结尾。审计截止时点是 `2025-06-30T23:59:59Z`。每个合成源表均有 `source_system`（模拟抽取来源）和 `audit_cutoff`（本次测试截止时点）字段；这些来源名是**虚构系统标签**，不代表已从任何企业系统取数。

主键是各表首列的稳定字符串 ID。下面的关联键是**逻辑关联**，用于从异常回溯到源记录；演示数据库不应被理解为源系统完整性和外键约束已通过审计验证。可空字段表示合成抽取中可能没有对应证据，规则需要将这种情况与已证实的时间违例分开处理。

## 人员、账号和权限

| 表 | 主键及字段 | 关联与用途 |
| --- | --- | --- |
| `employees` | `employee_id` 员工 ID；`display_name` 虚构姓名；`current_department` 当前部门；`employment_status` 在职/离职状态 | `employee_id` 连接 HR 事件与账号。姓名仅供界面阅读，职责分离按 ID 判断。 |
| `hr_events` | `event_id` 事件 ID；`employee_id`；`event_type` 入职、转岗或离职；`event_at` 生效时间；`from_department`、`to_department` 事件前后部门 | CT-01 以 `event_at` 计算停用或权限回收期限，以 `from_department` 对比旧角色归属。 |
| `accounts` | `account_id` 账号 ID；`employee_id`；`system_id` 系统；`created_at` 创建时间；`disabled_at` 停用时间（可空） | 一名员工可对应系统账号；本演示以账号 ID 回溯人员和授权。`disabled_at` 缺失表示抽取中没有停用时间；到期离职/转岗事件关联不到账号时列为待核实的资料缺口。 |
| `roles` | `role_id` 角色 ID；`system_id`；`role_name`；`owner_department` 角色归属部门；`is_privileged` 是否高权限 | `role_id` 连接授权和角色权限。`owner_department` 是转岗旧权限判断的演示映射；缺失时列为资料缺口。 |
| `role_permissions` | `permission_id` 权限 ID；`role_id`；`permission_name`；`is_sensitive` 是否敏感 | CT-03 基于敏感权限标志检查申请与批准职责分离；已批准申请缺少角色权限映射时列为资料缺口。高权限标志定义在 `roles`。 |
| `entitlements` | `entitlement_id` 授权 ID；`account_id`；`role_id`；`request_id` 申请 ID（可空）；`granted_at` 授予时间；`revoked_at` 撤销时间（可空） | CT-01 和 CT-02 的授权总体；以 `request_id` 回溯申请及审批。关联不到账号或角色时无法完成高权限测试，列为资料缺口。 |
| `access_requests` | `request_id` 申请 ID；`account_id`；`role_id`；`requester_id` 申请人员工 ID；`submitted_at` 申请时间；`purpose` 申请目的 | CT-02 的事前审批链起点；CT-03 将 `requester_id` 与批准人 ID 比较。 |
| `access_approvals` | `approval_id` 审批 ID；`request_id`；`approver_id` 批准人员工 ID；`decision` 批准状态；`decided_at` 审批时间（可空）；`valid_until` 有效期末（可空） | CT-02 检查批准有效性与时序；CT-03 检查申请人、批准人是否同人。 |
| `access_reviews` | `review_id` 复核 ID；`entitlement_id`；`reviewer_id`；`period_end` 所覆盖期间末；`due_at` 到期时点；`completed_at` 完成时间（可空）；`decision` 复核决定（可空） | CT-02 比较 `completed_at` 和 `due_at`；有已到期记录但未完成时按演示口径命中，完全没有复核记录时列为资料缺口。 |

## 生产变更与应急流程

| 表 | 主键及字段 | 关联与用途 |
| --- | --- | --- |
| `change_tickets` | `ticket_id` 工单 ID；`system_id`；`change_type` 常规/应急；`requester_id`；`submitted_at` 提交时间；`approved_at` 批准时间（可空）；`tested_at` 测试完成时间（可空）；`change_summary` 摘要 | CT-04 将部署与同系统工单比对，并检查批准、测试是否早于部署。 |
| `code_commits` | `commit_id` 提交 ID；`ticket_id`（可空）；`author_id`；`committed_at` 提交时间；`repository` 虚构仓库名 | 经 `commit_id` 连接部署，形成工单 → 代码提交 → 部署的证据路径。标准部署的提交记录缺失或其工单与部署不一致时列为资料缺口；提交晚于部署时列为时序异常。提交存在不等于代码已审查。 |
| `deployments` | `deployment_id` 部署 ID；`system_id`；`ticket_id`（可空）；`commit_id`；`deployer_id`；`deployed_at` 生产部署时间；`deployment_type` 常规/应急 | CT-04 的生产部署总体；应急部署交由 CT-05 检查事后补批。 |
| `emergency_changes` | `emergency_id` 应急记录 ID；`deployment_id`；`ticket_id`（可空）；`justification` 原因说明；`declared_at` 声明时间；`retrospective_approver_id` 补批人（可空）；`retrospective_approved_at` 补批时间（可空） | CT-05 以关联部署的 `deployed_at` 为 24 小时补批时限起点，同时核对部署与工单 ID；批准时间和批准人均须存在。原因文字存在不证明其合理。 |

## 测试答案与操作记录

| 表 | 字段 | 用途及边界 |
| --- | --- | --- |
| `expected_results` | `case_id`、`control_id`、`entity_id`、`issue_code`、`expected_classification`、`explanation` | 12 条预埋场景的答案清单：9 条明确异常，3 条需要人工判断。只用于测试和解释合成场景；**控制规则不能读取这张表来产生发现**。 |
| `findings` | 由规则生成的稳定发现 ID、控制/原因、风险、系统、证据 ID、时间线、关联记录和规则元数据 | 自动测试的结果层，供界面筛选、复核及导出使用。字段以 `src/controltrace/store.py` 建表定义和 `src/controltrace/rules.py` 返回结构为准。 |
| `reviews` | 发现 ID、复核人、复核时间、处理状态、结论与备注 | 人工判断的追加记录；应与自动测试原始命中区分。字段以 `src/controltrace/store.py` 为准。 |

## 预埋结果清单

以下是 `expected_results` 表中的 12 个演示场景。`exception` 表示按本案例规则有明确时序或职责冲突；`manual_review` 表示关键资料不完整或尚未到期，需人工核实。它们是合成测试预期，不能当作真实环境识别率。

| 案例 | 控制 | 对象 ID | 原因代码 | 预期分类 |
| --- | --- | --- | --- | --- |
| CASE-01 | CT-01 | `A001` | `terminated_account_open` | exception |
| CASE-02 | CT-01 | `EN003` | `transfer_old_role_retained` | exception |
| CASE-03 | CT-01 | `EN012` | `transfer_department_unknown` | manual_review |
| CASE-04 | CT-02 | `EN005` | `privileged_approval_missing` | exception |
| CASE-05 | CT-02 | `EN006` | `privileged_review_overdue` | exception |
| CASE-06 | CT-03 | `Q007` | `sensitive_self_approval` | exception |
| CASE-07 | CT-04 | `D001` | `deployment_ticket_missing` | exception |
| CASE-08 | CT-04 | `D002` | `deployment_approval_late` | exception |
| CASE-09 | CT-04 | `D002` | `deployment_test_late` | exception |
| CASE-10 | CT-04 | `D003` | `deployment_test_unknown` | manual_review |
| CASE-11 | CT-05 | `D005` | `emergency_approval_overdue` | exception |
| CASE-12 | CT-05 | `D007` | `emergency_window_open` | manual_review |

## 主要关联路径

```text
employees.employee_id ── hr_events.employee_id
                      └── accounts.employee_id ── entitlements.account_id
                                                  ├── roles.role_id ── role_permissions.role_id
                                                  └── access_requests.request_id
                                                       ├── access_approvals.request_id
                                                       └── requester_id / approver_id → employees.employee_id

entitlements.entitlement_id ── access_reviews.entitlement_id

change_tickets.ticket_id ── code_commits.ticket_id
                         └── deployments.ticket_id ── emergency_changes.deployment_id
code_commits.commit_id ──────── deployments.commit_id
```

导出工作底稿 ZIP 时，`source_tables/` 同时保存这些合成源表的原值 JSON 和防表格公式注入的 CSV；`manifest.json` 保存全部包内文件的 SHA-256、总数据指纹、版本与审计截止时点。`uv run controltrace verify --bundle exports/workpapers.zip` 可核对哈希、以 JSON 原值重放规则，并将发现 ID、分类和证据 ID 对应到源记录。包内哈希不是数字签名，不能证明来源真实性。

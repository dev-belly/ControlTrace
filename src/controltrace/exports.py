"""Portable exception lists and reproducible audit workpapers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from controltrace import __version__
from controltrace.data import SOURCE_TABLES
from controltrace.rules import RULES
from controltrace.store import DEFAULT_SEED, DEMO_CUTOFF, get_table_rows, list_findings


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _csv_bytes(rows: list[dict[str, Any]], fields: list[str] | None = None) -> bytes:
    fields = fields or sorted({key for row in rows for key in row})
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: _csv_cell(value)
                for key, value in row.items()
            }
        )
    return buffer.getvalue().encode("utf-8-sig")


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        value = _json(value)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def exception_csv(findings: list[dict[str, Any]]) -> bytes:
    """A flat list, including decisions, for review outside the app."""
    fields = [
        "finding_id", "control_id", "issue_code", "classification", "risk", "system",
        "occurred_at", "audit_cutoff", "entity_type", "entity_id", "title", "summary", "rationale",
        "evidence_ids", "reviewer", "reviewed_at", "status", "conclusion", "notes",
    ]
    rows = []
    for finding in findings:
        review = finding.get("latest_review") or {}
        rows.append(
            {
                **finding,
                "audit_cutoff": DEMO_CUTOFF,
                "reviewer": review.get("reviewer", ""),
                "reviewed_at": review.get("reviewed_at", ""),
                "status": review.get("status", "pending"),
                "conclusion": review.get("conclusion", ""),
                "notes": review.get("notes", ""),
            }
        )
    return _csv_bytes(rows, fields)


def finding_workpaper(finding: dict[str, Any], dataset_sha256: str) -> str:
    """A self-contained trail from control objective to source evidence and human decision."""
    rule = finding.get("rule") or RULES[finding["control_id"]]
    review = finding.get("latest_review") or {}
    lines = [
        f"# Workpaper: {finding['finding_id']}",
        "",
        "**Synthetic case study. Not a real company audit or an audit opinion.**",
        "",
        f"- Audit cutoff: `{DEMO_CUTOFF}`",
        f"- Dataset SHA-256: `{dataset_sha256}`",
        f"- ControlTrace version: `{__version__}`",
        f"- Control: `{finding['control_id']}` — {finding['title']}",
        f"- Classification: `{finding['classification']}`; risk: `{finding['risk']}`",
        f"- System: `{finding['system']}`; subject: `{finding['entity_type']}:{finding['entity_id']}`",
        f"- Event time: `{finding['occurred_at']}`",
        f"- Source evidence IDs: {', '.join(f'`{item}`' for item in finding['evidence_ids'])}",
        "",
        "## Control and test basis",
        "",
    ]
    for label, key in (
        ("Objective", "control_goal"),
        ("Inputs", "inputs"),
        ("Logic", "logic"),
        ("Exceptions", "exceptions"),
        ("Limitations", "limitations"),
    ):
        value = rule.get(key, "See control catalog.")
        if isinstance(value, list):
            value = "; ".join(str(item) for item in value)
        lines.append(f"- **{label}:** {value}")
    lines += ["", "## Automated observation", "", finding["summary"], "", finding["rationale"]]
    lines += ["", "## Event timeline", ""]
    for event in finding.get("timeline", []):
        lines.append(f"- `{_json(event)}`")
    lines += ["", "## Linked source records", ""]
    for record in finding.get("related_records", []):
        lines += ["```json", _json(record), "```", ""]
    lines += [
        "## Human review",
        "",
        f"- Reviewer: {review.get('reviewer') or 'Not reviewed'}",
        f"- Reviewed at: {review.get('reviewed_at') or 'Not reviewed'}",
        f"- Status: {review.get('status') or 'pending'}",
        f"- Conclusion: {review.get('conclusion') or 'pending'}",
        f"- Notes: {review.get('notes') or 'None'}",
        "",
        "### Review history",
        "",
    ]
    for item in finding.get("review_history", []):
        lines.append(f"- `{_json(item)}`")
    lines += [
        "",
        "## Reproduction",
        "",
        "From the project root: `uv run controltrace generate`, then "
        "`uv run controltrace test`. Match the finding ID and source IDs above "
        "against `source_tables/` in this bundle. The exported data is the exact "
        "synthetic input snapshot; the generation seed and cutoff are in `manifest.json`.",
        "",
        "An automated observation is a review candidate. The reviewer is responsible for "
        "assessing evidence completeness and documenting the final conclusion.",
        "",
    ]
    return "\n".join(lines)


def workpaper_zip(db_path: str | Path, finding_ids: set[str] | None = None) -> bytes:
    """Export source data, rule basis, result list and individual workpapers."""
    findings = list_findings(db_path)
    if finding_ids is not None:
        findings = [finding for finding in findings if finding["finding_id"] in finding_ids]
    source_files: dict[str, bytes] = {}
    source_hashes: dict[str, str] = {}
    for table in SOURCE_TABLES:
        rows = get_table_rows(db_path, table)
        content = _csv_bytes(rows)
        name = f"source_tables/{table}.csv"
        source_files[name] = content
        source_hashes[name] = hashlib.sha256(content).hexdigest()
    fingerprint = hashlib.sha256(_json(source_hashes).encode("utf-8")).hexdigest()
    meta = {row["key"]: row["value"] for row in get_table_rows(db_path, "meta")}
    manifest = {
        "notice": "Synthetic case study; no real enterprise audit data",
        "audit_cutoff": DEMO_CUTOFF,
        "generator_seed": int(meta.get("seed", DEFAULT_SEED)),
        "controltrace_version": __version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_hashes,
        "dataset_sha256": fingerprint,
        "finding_ids": [finding["finding_id"] for finding in findings],
    }
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in source_files.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", _json(manifest))
        archive.writestr("findings.csv", exception_csv(findings))
        archive.writestr("rules.json", _json(RULES))
        archive.writestr(
            "README.txt",
            "Synthetic ControlTrace workpapers. Check manifest.json hashes before using "
            "source_tables/*.csv. Review decisions are separate from the automated test. "
            "Run `uv run controltrace generate` and `uv run controltrace test` in the project "
            "to reproduce automated observations with the fixed seed and audit cutoff.\n",
        )
        for finding in findings:
            archive.writestr(
                f"workpapers/{finding['finding_id']}.md",
                finding_workpaper(finding, fingerprint),
            )
    return bundle.getvalue()

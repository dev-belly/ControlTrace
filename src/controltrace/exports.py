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
        lines += ["    " + _json(record), ""]
    lines += [
        "## Human review",
        "",
        "    " + _json({
            "reviewer": review.get("reviewer"),
            "reviewed_at": review.get("reviewed_at"),
            "status": review.get("status", "pending"),
            "conclusion": review.get("conclusion"),
            "notes": review.get("notes"),
        }),
        "",
        "### Review history",
        "",
    ]
    for item in finding.get("review_history", []):
        lines.append("    " + _json(item))
    lines += [
        "",
        "## Reproduction",
        "",
        "From the project root, run `uv run controltrace verify --bundle workpapers.zip` "
        "to check file hashes and replay the current rules against `source_tables/*.json`. "
        "Match the finding ID and source IDs above to the JSON rows. To regenerate the "
        "synthetic dataset separately, run `uv run controltrace generate` and then "
        "`uv run controltrace test`. CSV files are protected against spreadsheet formulas; "
        "the generation seed and cutoff are in `manifest.json`.",
        "",
        "An automated observation is a review candidate. The reviewer is responsible for "
        "assessing evidence completeness and documenting the final conclusion.",
        "",
    ]
    return "\n".join(lines)


def workpaper_zip(db_path: str | Path, finding_ids: set[str] | None = None) -> bytes:
    """Export source data, rule basis, result list and individual workpapers."""
    all_findings = list_findings(db_path)
    findings = all_findings
    if finding_ids is not None:
        unknown_ids = finding_ids - {finding["finding_id"] for finding in all_findings}
        if unknown_ids:
            raise ValueError(f"Unknown finding IDs: {', '.join(sorted(unknown_ids))}")
        findings = [finding for finding in all_findings if finding["finding_id"] in finding_ids]
    source_files: dict[str, bytes] = {}
    source_hashes: dict[str, str] = {}
    for table in SOURCE_TABLES:
        rows = get_table_rows(db_path, table)
        for suffix, content in (
            ("csv", _csv_bytes(rows)),
            ("json", _json(rows).encode("utf-8")),
        ):
            name = f"source_tables/{table}.{suffix}"
            source_files[name] = content
            source_hashes[name] = hashlib.sha256(content).hexdigest()
    fingerprint = hashlib.sha256(_json(source_hashes).encode("utf-8")).hexdigest()
    meta = {row["key"]: row["value"] for row in get_table_rows(db_path, "meta")}
    members = {
        **source_files,
        "findings.csv": exception_csv(findings),
        "rules.json": _json(RULES).encode("utf-8"),
        "README.txt": (
            "Synthetic ControlTrace workpapers. Check manifest.json hashes before using "
            "source_tables/*.json for exact source values. CSV copies protect against "
            "spreadsheet formulas. Review decisions are separate from the automated test. "
            "Run `uv run controltrace verify --bundle workpapers.zip` to replay the rules "
            "against this source snapshot.\n"
        ).encode("utf-8"),
    }
    members.update({
        f"workpapers/{finding['finding_id']}.md": finding_workpaper(finding, fingerprint).encode(
            "utf-8"
        )
        for finding in findings
    })
    manifest = {
        "notice": "Synthetic case study; no real enterprise audit data",
        "audit_cutoff": DEMO_CUTOFF,
        "generator_seed": int(meta.get("seed", DEFAULT_SEED)),
        "controltrace_version": __version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_hashes,
        "dataset_sha256": fingerprint,
        "files_sha256": {
            name: hashlib.sha256(content).hexdigest() for name, content in members.items()
        },
        "export_scope": "all" if finding_ids is None else "selected",
        "all_finding_ids": [finding["finding_id"] for finding in all_findings],
        "finding_ids": [finding["finding_id"] for finding in findings],
    }
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", _json(manifest))
    return bundle.getvalue()

"""DuckDB persistence for immutable synthetic extracts, findings and review history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb

from controltrace.data import (
    DEFAULT_SEED,
    DEMO_CUTOFF,
    PRIMARY_KEYS,
    SOURCE_TABLES,
    SYNTHETIC_NOTICE,
    TABLE_SCHEMAS,
    generate_demo_data,
    parse_utc,
)

_OTHER_TABLES = ("meta", "findings", "reviews")
ALL_TABLES = SOURCE_TABLES + _OTHER_TABLES
REVIEW_STATUSES = ("pending", "in_review", "closed")
REVIEW_CONCLUSIONS = ("confirmed_exception", "false_positive", "needs_more_evidence")


def _connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    text_path = str(path)
    if text_path != ":memory:":
        Path(text_path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(text_path)


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def initialize_db(path: str | Path, seed: int = DEFAULT_SEED) -> None:
    """Initialize once. Rerunning with the same seed never resets review records.

    A different seed requires a new database path, avoiding silent replacement of
    existing analyst work.
    """
    con = _connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)")
        prior = con.execute("SELECT value FROM meta WHERE key = 'seed'").fetchone()
        if prior:
            if prior[0] != str(seed):
                raise ValueError(
                    f"Database already uses seed {prior[0]}; choose a new database path for seed {seed}"
                )
            return
        fixture = generate_demo_data(seed)
        con.execute("BEGIN TRANSACTION")
        try:
            for table, fields in TABLE_SCHEMAS.items():
                declarations = ", ".join(f'"{name}" {type_}' for name, type_ in fields.items())
                con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({declarations})')
                columns = list(fields)
                insert_columns = ", ".join(f'"{name}"' for name in columns)
                markers = ", ".join("?" for _ in columns)
                values = [[row.get(column) for column in columns] for row in fixture[table]]
                if values:
                    con.executemany(
                        f'INSERT INTO "{table}" ({insert_columns}) VALUES ({markers})', values
                    )
            con.execute("""
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id VARCHAR PRIMARY KEY,
                    control_id VARCHAR NOT NULL,
                    issue_code VARCHAR NOT NULL,
                    title VARCHAR NOT NULL,
                    classification VARCHAR NOT NULL,
                    risk VARCHAR NOT NULL,
                    system VARCHAR NOT NULL,
                    occurred_at VARCHAR NOT NULL,
                    period VARCHAR NOT NULL,
                    entity_type VARCHAR NOT NULL,
                    entity_id VARCHAR NOT NULL,
                    payload_json VARCHAR NOT NULL,
                    refreshed_at VARCHAR NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id VARCHAR PRIMARY KEY,
                    finding_id VARCHAR NOT NULL,
                    reviewer VARCHAR NOT NULL,
                    reviewed_at VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    conclusion VARCHAR,
                    notes VARCHAR NOT NULL
                )
            """)
            for key, value in [
                ("seed", str(seed)), ("audit_cutoff", DEMO_CUTOFF),
                ("notice", SYNTHETIC_NOTICE), ("dataset_version", "1"),
            ]:
                con.execute("INSERT INTO meta VALUES (?, ?)", [key, value])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def get_table_rows(path: str | Path, table: str) -> list[dict]:
    """Read a whitelisted table as JSON-serializable dictionaries."""
    if table not in ALL_TABLES:
        raise ValueError(f"Unknown table: {table}")
    order_key = PRIMARY_KEYS.get(table, {"meta": "key", "findings": "finding_id"}.get(table))
    order_by = (
        f' ORDER BY "{order_key}"' if order_key else ' ORDER BY "reviewed_at", "review_id"'
    )
    con = _connect(path)
    try:
        return _rows(con.execute(f'SELECT * FROM "{table}"{order_by}'))
    finally:
        con.close()


def get_source_record(path: str | Path, table: str, record_id: str) -> dict | None:
    """Resolve a source evidence ID without accepting arbitrary SQL identifiers."""
    if table not in SOURCE_TABLES:
        raise ValueError(f"Unknown source table: {table}")
    key = PRIMARY_KEYS[table]
    con = _connect(path)
    try:
        records = _rows(con.execute(
            f'SELECT * FROM "{table}" WHERE "{key}" = ?', [record_id]
        ))
        return records[0] if records else None
    finally:
        con.close()


def run_tests(path: str | Path) -> list[dict]:
    """Recompute findings from source tables and keep all analyst reviews."""
    initialize_db(path)
    from controltrace.rules import evaluate

    data = {table: get_table_rows(path, table) for table in SOURCE_TABLES if table != "expected_results"}
    candidates = evaluate(data)
    refreshed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    con = _connect(path)
    try:
        con.execute("BEGIN TRANSACTION")
        try:
            for finding in candidates:
                con.execute("""
                    INSERT INTO findings (
                        finding_id, control_id, issue_code, title, classification, risk,
                        system, occurred_at, period, entity_type, entity_id,
                        payload_json, refreshed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (finding_id) DO UPDATE SET
                        control_id=excluded.control_id,
                        issue_code=excluded.issue_code,
                        title=excluded.title,
                        classification=excluded.classification,
                        risk=excluded.risk,
                        system=excluded.system,
                        occurred_at=excluded.occurred_at,
                        period=excluded.period,
                        entity_type=excluded.entity_type,
                        entity_id=excluded.entity_id,
                        payload_json=excluded.payload_json,
                        refreshed_at=excluded.refreshed_at
                """, [
                    finding["finding_id"], finding["control_id"], finding["issue_code"],
                    finding["title"], finding["classification"], finding["risk"],
                    finding["system"], finding["occurred_at"], finding["period"],
                    finding["entity_type"], finding["entity_id"],
                    json.dumps(finding, ensure_ascii=False, sort_keys=True), refreshed_at,
                ])
            if candidates:
                placeholders = ", ".join("?" for _ in candidates)
                con.execute(
                    f"DELETE FROM findings WHERE finding_id NOT IN ({placeholders})",
                    [finding["finding_id"] for finding in candidates],
                )
            else:
                con.execute("DELETE FROM findings")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return list_findings(path)


def _review_history(con: duckdb.DuckDBPyConnection, finding_ids: list[str]) -> dict[str, list[dict]]:
    if not finding_ids:
        return {}
    placeholders = ", ".join("?" for _ in finding_ids)
    records = _rows(con.execute(
        f"SELECT * FROM reviews WHERE finding_id IN ({placeholders}) "
        "ORDER BY reviewed_at, review_id", finding_ids
    ))
    history: dict[str, list[dict]] = {}
    for record in records:
        history.setdefault(record["finding_id"], []).append(record)
    return history


def list_findings(path: str | Path) -> list[dict]:
    """Return candidate findings plus the latest and full review histories."""
    con = _connect(path)
    try:
        raw = _rows(con.execute(
            "SELECT finding_id, payload_json FROM findings "
            "ORDER BY control_id, occurred_at, entity_id, issue_code"
        ))
        history = _review_history(con, [row["finding_id"] for row in raw])
        findings = []
        for row in raw:
            finding = json.loads(row["payload_json"])
            reviews = history.get(row["finding_id"], [])
            finding["latest_review"] = reviews[-1] if reviews else None
            finding["review_history"] = reviews
            finding["review_status"] = reviews[-1]["status"] if reviews else "pending"
            findings.append(finding)
        return findings
    finally:
        con.close()


def get_finding(path: str | Path, finding_id: str) -> dict | None:
    """Return a single finding with source snapshots and review history."""
    con = _connect(path)
    try:
        raw = con.execute(
            "SELECT payload_json FROM findings WHERE finding_id = ?", [finding_id]
        ).fetchone()
        if raw is None:
            return None
        finding = json.loads(raw[0])
        reviews = _review_history(con, [finding_id]).get(finding_id, [])
        finding["latest_review"] = reviews[-1] if reviews else None
        finding["review_history"] = reviews
        finding["review_status"] = reviews[-1]["status"] if reviews else "pending"
        return finding
    finally:
        con.close()


def save_review(
    path: str | Path,
    finding_id: str,
    reviewer: str,
    status: str = "in_review",
    conclusion: str | None = None,
    notes: str = "",
    reviewed_at: str | None = None,
) -> dict:
    """Append an analyst decision; never rewrite earlier reviews or source evidence."""
    if not reviewer.strip():
        raise ValueError("Reviewer is required")
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Status must be one of {REVIEW_STATUSES}")
    if conclusion is not None and conclusion not in REVIEW_CONCLUSIONS:
        raise ValueError(f"Conclusion must be one of {REVIEW_CONCLUSIONS}")
    if status == "pending" and conclusion is not None:
        raise ValueError("A pending review cannot have a conclusion")
    if status == "in_review" and conclusion not in (None, "needs_more_evidence"):
        raise ValueError("An in-progress review cannot have a final conclusion")
    if status == "closed" and conclusion not in ("confirmed_exception", "false_positive"):
        raise ValueError("A closed review requires a final conclusion")
    if status == "closed" and not notes.strip():
        raise ValueError("A closed review requires notes explaining the conclusion")
    if reviewed_at is None:
        reviewed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        reviewed_time = parse_utc(reviewed_at)
    except (TypeError, ValueError) as error:
        raise ValueError("reviewed_at must be an ISO-8601 timestamp") from error
    if reviewed_time is None:
        raise ValueError("reviewed_at must be an ISO-8601 timestamp")
    reviewed_at = reviewed_time.isoformat().replace("+00:00", "Z")
    con = _connect(path)
    try:
        if con.execute(
            "SELECT 1 FROM findings WHERE finding_id = ?", [finding_id]
        ).fetchone() is None:
            raise ValueError(f"Unknown finding: {finding_id}")
        review = {
            "review_id": f"REV-{uuid4().hex[:12].upper()}",
            "finding_id": finding_id,
            "reviewer": reviewer.strip(),
            "reviewed_at": reviewed_at,
            "status": status,
            "conclusion": conclusion,
            "notes": notes.strip(),
        }
        con.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)", list(review.values())
        )
        return review
    finally:
        con.close()

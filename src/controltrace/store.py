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
)
from controltrace.reviews import REVIEW_CONCLUSIONS as REVIEW_CONCLUSIONS
from controltrace.reviews import REVIEW_STATUSES as REVIEW_STATUSES
from controltrace.reviews import bind_review_history, finding_fingerprint, validate_review_decision

_OTHER_TABLES = ("meta", "findings", "reviews", "review_evidence")
ALL_TABLES = SOURCE_TABLES + _OTHER_TABLES
_REVIEW_ORDER_BY = "CAST(reviewed_at AS TIMESTAMPTZ), review_id"


def _connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    text_path = str(path)
    if text_path != ":memory:":
        Path(text_path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(text_path)


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def _ensure_review_evidence(con: duckdb.DuckDBPyConnection) -> None:
    # Do not backfill old decisions from today's finding: their original
    # observation may have changed before this migration.
    con.execute("""
        CREATE TABLE IF NOT EXISTS review_evidence (
            review_id VARCHAR PRIMARY KEY,
            finding_sha256 VARCHAR NOT NULL
        )
    """)
    if con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'findings'"
    ).fetchone():
        con.execute(
            "ALTER TABLE findings ADD COLUMN IF NOT EXISTS review_write_revision BIGINT DEFAULT 0"
        )


def _review_bindings(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(con.execute("SELECT review_id, finding_sha256 FROM review_evidence").fetchall())


def initialize_db(path: str | Path, seed: int | None = None) -> None:
    """Initialize once. Rerunning with the same seed never resets review records.

    A new database uses DEFAULT_SEED unless a seed is supplied. Existing
    databases retain their seed when one is omitted; an explicit different
    seed requires a new path, avoiding silent replacement of analyst work.
    """
    con = _connect(path)
    try:
        _ensure_review_evidence(con)
        con.execute("CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)")
        prior = con.execute("SELECT value FROM meta WHERE key = 'seed'").fetchone()
        if prior:
            if seed is not None and prior[0] != str(seed):
                raise ValueError(
                    f"Database already uses seed {prior[0]}; choose a new database path for seed {seed}"
                )
            return
        seed = DEFAULT_SEED if seed is None else seed
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
                    refreshed_at VARCHAR NOT NULL,
                    review_write_revision BIGINT DEFAULT 0
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
    order_key = PRIMARY_KEYS.get(table, {
        "meta": "key", "findings": "finding_id", "review_evidence": "review_id",
    }.get(table))
    order_by = (
        f' ORDER BY "{order_key}"' if order_key else f" ORDER BY {_REVIEW_ORDER_BY}"
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
        f"ORDER BY {_REVIEW_ORDER_BY}", finding_ids
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
        bindings = _review_bindings(con)
        findings = []
        for row in raw:
            finding = json.loads(row["payload_json"])
            reviews = history.get(row["finding_id"], [])
            findings.append(bind_review_history(finding, reviews, bindings))
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
        return bind_review_history(finding, reviews, _review_bindings(con))
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
    *,
    expected_revision: str | None = None,
) -> dict:
    """Append a decision bound to current evidence, optionally rejecting a stale form.

    Callers editing a previously displayed finding should pass its
    ``review_revision``. Validation and both inserts share one transaction.
    """
    if reviewed_at is None:
        reviewed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    reviewed_time = validate_review_decision(reviewer, status, conclusion, notes, reviewed_at)
    reviewed_at = reviewed_time.isoformat().replace("+00:00", "Z")
    con = _connect(path)
    try:
        con.execute("BEGIN TRANSACTION")
        try:
            raw = con.execute(
                "SELECT payload_json FROM findings WHERE finding_id = ?", [finding_id]
            ).fetchone()
            if raw is None:
                raise ValueError(f"Unknown finding: {finding_id}")
            finding = json.loads(raw[0])
            history = _review_history(con, [finding_id]).get(finding_id, [])
            current = bind_review_history(finding, history, _review_bindings(con))
            if expected_revision is not None and expected_revision != current["review_revision"]:
                raise ValueError("Finding evidence or review changed; reload before saving")
            # A real row update is essential: a no-op can be optimized away,
            # allowing two snapshot readers to both pass the revision check.
            con.execute(
                "UPDATE findings SET review_write_revision = review_write_revision + 1 "
                "WHERE finding_id = ?", [finding_id]
            )
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
            con.execute(
                "INSERT INTO review_evidence VALUES (?, ?)",
                [review["review_id"], finding_fingerprint(finding)],
            )
            con.execute("COMMIT")
            return review
        except duckdb.TransactionException as error:
            con.execute("ROLLBACK")
            raise ValueError("Finding evidence or review changed; reload before saving") from error
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()

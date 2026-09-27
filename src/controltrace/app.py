"""Streamlit evidence and review workbench."""

from __future__ import annotations

import os
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from controltrace import __version__
from controltrace.exports import exception_csv, workpaper_zip
from controltrace.rules import RULES, run_tests
from controltrace.store import (
    DEMO_CUTOFF,
    get_table_rows,
    initialize_db,
    list_findings,
    save_review,
)

DB_PATH = Path(os.environ.get("CONTROLTRACE_DB", "data/controltrace.duckdb"))

st.set_page_config(page_title="ControlTrace | IT control evidence", page_icon="◈", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.8rem; max-width: 1500px;}
      h1, h2, h3 {letter-spacing: -.025em;}
      h1 {font-weight: 720;}
      [data-testid="stMetric"] {background: #f6f8fb; border: 1px solid #e4e9f1;
        border-radius: 12px; padding: 1rem 1.1rem;}
      .case-banner {background:#edf4f9; color:#274b63; border:1px solid #cce0ec;
        border-radius:9px; padding:.65rem .9rem; font-size:.87rem;}
      .eyebrow {font-size:.75rem; color:#5f7690; letter-spacing:.11em; font-weight:700;}
      .detail-subtitle {color:#617286; font-size:.92rem; margin-top:-.5rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _load_findings() -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        initialize_db(DB_PATH)
    findings = list_findings(DB_PATH)
    if not findings:
        run_tests(DB_PATH)
        findings = list_findings(DB_PATH)
    return findings


findings = _load_findings()
counts = Counter(item["classification"] for item in findings)
reviewed = sum(bool(item.get("latest_review")) for item in findings)

st.markdown('<div class="eyebrow">IT GENERAL CONTROLS · EVIDENCE WORKBENCH</div>', unsafe_allow_html=True)
st.title("ControlTrace")
st.markdown(
    '<div class="detail-subtitle">Access lifecycle and production change control, '
    "from source event to documented review.</div>",
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="case-banner">SYNTHETIC CASE STUDY · Not real enterprise audit data. '
    "Automated observations are review candidates, not fraud findings or an audit opinion. "
    f"Audit cutoff: <strong>{DEMO_CUTOFF}</strong>.</div>",
    unsafe_allow_html=True,
)
st.write("")

with st.sidebar:
    st.header("Scope & filters")
    st.caption("All data are generated from a fixed seed. Review entries are stored separately.")
    systems = sorted({item["system"] for item in findings})
    periods = sorted({str(item["occurred_at"])[:7] for item in findings})
    control_ids = sorted(RULES)
    risks = sorted({item["risk"] for item in findings})
    classifications = sorted({item["classification"] for item in findings})
    selected_systems = st.multiselect("System", systems, default=systems)
    selected_periods = st.multiselect("Period (YYYY-MM)", periods, default=periods)
    selected_controls = st.multiselect("Control", control_ids, default=control_ids)
    selected_risks = st.multiselect("Risk", risks, default=risks)
    selected_classes = st.multiselect("Outcome", classifications, default=classifications)
    st.divider()
    st.caption(f"Database: `{DB_PATH}`")
    st.caption("Each workpaper includes source row IDs, rule basis and the review history.")

filtered = [
    item
    for item in findings
    if item["system"] in selected_systems
    and str(item["occurred_at"])[:7] in selected_periods
    and item["control_id"] in selected_controls
    and item["risk"] in selected_risks
    and item["classification"] in selected_classes
]

metric_cols = st.columns(4)
metric_cols[0].metric("Controls tested", len(RULES))
metric_cols[1].metric("Exceptions", counts.get("exception", 0))
metric_cols[2].metric("Manual review", counts.get("manual_review", 0))
metric_cols[3].metric("Reviewed findings", reviewed)

st.subheader("Test coverage")
primary_tables = {
    "CT-01": "hr_events",
    "CT-02": "entitlements",
    "CT-03": "access_requests",
    "CT-04": "deployments",
    "CT-05": "emergency_changes",
}
coverage_rows = []
for control_id, rule in RULES.items():
    source_table = primary_tables.get(control_id)
    source_count = len(get_table_rows(DB_PATH, source_table)) if source_table else 0
    coverage_rows.append(
        {
            "Control": control_id,
            "Objective": _display(rule.get("control_goal") or rule.get("objective")),
            "Primary source": source_table,
            "Source rows": source_count,
            "Observations": sum(item["control_id"] == control_id for item in findings),
        }
    )
st.dataframe(pd.DataFrame(coverage_rows), hide_index=True, use_container_width=True)
st.caption(
    "Source rows show available input volume, not a pass rate. See the control catalog "
    "and workpapers for rule scope and evidence gaps."
)

st.subheader(f"Observations · {len(filtered)} in view")
if not filtered:
    st.info("No observations match the selected filters.")
    st.stop()

table_rows = []
for item in filtered:
    latest = item.get("latest_review") or {}
    table_rows.append(
        {
            "Finding ID": item["finding_id"],
            "Control": item["control_id"],
            "System": item["system"],
            "Event time": item["occurred_at"],
            "Risk": item["risk"],
            "Outcome": item["classification"],
            "Observation": item["title"],
            "Review": latest.get("conclusion") or "pending",
        }
    )
selection = st.dataframe(
    pd.DataFrame(table_rows),
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    key="finding_table_"
    + sha256("|".join(item["finding_id"] for item in filtered).encode()).hexdigest()[:12],
)
selected_rows = selection.selection.rows
if selected_rows and 0 <= selected_rows[0] < len(filtered):
    st.session_state["selected_finding_id"] = filtered[selected_rows[0]]["finding_id"]
selected_id = st.session_state.get("selected_finding_id")
selected = next((item for item in filtered if item["finding_id"] == selected_id), filtered[0])
st.caption("Select a row to inspect its events, original records, rule and review decision.")

csv_col, zip_col, spacer = st.columns([1, 1.2, 4])
with csv_col:
    st.download_button(
        "Download exception list",
        exception_csv(filtered),
        file_name="controltrace-exceptions.csv",
        mime="text/csv",
        use_container_width=True,
    )
with zip_col:
    st.download_button(
        "Download workpapers + sources",
        workpaper_zip(DB_PATH, {item["finding_id"] for item in filtered}),
        file_name="controltrace-workpapers.zip",
        mime="application/zip",
        use_container_width=True,
    )

st.divider()
st.markdown(f'<div class="eyebrow">FINDING {selected["finding_id"]}</div>', unsafe_allow_html=True)
st.header(selected["title"])
st.caption(
    f"{selected['control_id']} · {selected['system']} · {selected['risk']} risk · "
    f"{selected['classification']} · source IDs: {', '.join(selected['evidence_ids'])}"
)
st.info(selected["summary"])
st.write(selected["rationale"])

left, right = st.columns([1.15, 1], gap="large")
with left:
    st.subheader("Event timeline")
    if selected.get("timeline"):
        for event in selected["timeline"]:
            with st.container(border=True):
                st.markdown(f"**{event.get('at', 'Time unknown')}** · {event.get('event', 'Event')}")
                st.caption(
                    f"{event.get('source_system', 'Source unknown')} · "
                    f"{event.get('evidence_id', 'No evidence ID')} · {event.get('field', '')}"
                )
    else:
        st.caption("No event timestamp available; inspect linked records below.")
    st.subheader("Linked source records")
    for index, record in enumerate(selected.get("related_records", []), 1):
        table = record.get("table", "source")
        record_id = record.get("id", record.get("record_id", index))
        with st.expander(f"{table} · {record_id}"):
            st.json(record)
    if not selected.get("related_records"):
        st.warning("No linked source row. This requires additional evidence before conclusion.")

with right:
    rule = selected.get("rule") or RULES[selected["control_id"]]
    st.subheader("Rule basis")
    for label, key in (
        ("Control objective", "control_goal"),
        ("Input fields", "inputs"),
        ("Decision logic", "logic"),
        ("Allowed exceptions", "exceptions"),
        ("Limits / false positives", "limitations"),
    ):
        st.markdown(f"**{label}**")
        st.write(_display(rule.get(key)))

    st.subheader("Human review")
    current = selected.get("latest_review") or {}
    st.caption(
        f"Current: {current.get('status', 'pending')} · "
        f"{current.get('conclusion') or 'no conclusion'}"
    )
    finding_id = selected["finding_id"]
    reviewer = st.text_input(
        "Reviewer name or initials",
        value=current.get("reviewer", ""),
        key=f"reviewer_{finding_id}",
    )
    status_options = ["pending", "in_review", "closed"]
    status = st.selectbox(
        "Processing status",
        status_options,
        index=status_options.index(current.get("status", "pending")),
        key=f"review_status_{finding_id}",
    )
    conclusion_options = {
        "pending": [None],
        "in_review": [None, "needs_more_evidence"],
        "closed": ["confirmed_exception", "false_positive"],
    }[status]
    current_conclusion = current.get("conclusion")
    conclusion = st.selectbox(
        "Review conclusion",
        conclusion_options,
        index=(
            conclusion_options.index(current_conclusion)
            if current_conclusion in conclusion_options
            else 0
        ),
        format_func=lambda value: value.replace("_", " ") if value else "No conclusion",
        key=f"review_conclusion_{finding_id}_{status}",
    )
    notes = st.text_area(
        "Review notes / exception basis",
        value=current.get("notes", ""),
        key=f"review_notes_{finding_id}",
    )
    submitted = st.button("Save review", type="primary", use_container_width=True)
    if submitted:
        if not reviewer.strip() or not notes.strip():
            st.error("Reviewer and notes are required to preserve a usable review trail.")
        else:
            try:
                save_review(DB_PATH, finding_id, reviewer.strip(), status, conclusion, notes.strip())
            except ValueError as error:
                st.error(str(error))
            else:
                st.rerun()
    if selected.get("review_history"):
        with st.expander(f"Decision history ({len(selected['review_history'])})"):
            for review in selected["review_history"]:
                st.json(review)

st.divider()
st.caption(
    f"ControlTrace v{__version__} · Synthetic evidence only · The rules highlight candidates for "
    "professional review; source completeness and business context can change the conclusion."
)

"""The review decision contract shared by persistence and bundle verification."""

from __future__ import annotations

from datetime import datetime

from controltrace.data import parse_utc

REVIEW_STATUSES = ("pending", "in_review", "closed")
REVIEW_CONCLUSIONS = ("confirmed_exception", "false_positive", "needs_more_evidence")
REVIEW_FIELDS = {
    "review_id", "finding_id", "reviewer", "reviewed_at", "status", "conclusion", "notes",
}


def validate_review_decision(
    reviewer: str,
    status: str,
    conclusion: str | None,
    notes: str,
    reviewed_at: str,
) -> datetime:
    """Reject incomplete or contradictory decisions and return their actual UTC time."""
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("Reviewer is required")
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Status must be one of {REVIEW_STATUSES}")
    if conclusion is not None and conclusion not in REVIEW_CONCLUSIONS:
        raise ValueError(f"Conclusion must be one of {REVIEW_CONCLUSIONS}")
    if not isinstance(notes, str):
        raise ValueError("Notes must be a string")
    if status == "pending" and conclusion is not None:
        raise ValueError("A pending review cannot have a conclusion")
    if status == "in_review" and conclusion not in (None, "needs_more_evidence"):
        raise ValueError("An in-progress review cannot have a final conclusion")
    if status == "closed" and conclusion not in ("confirmed_exception", "false_positive"):
        raise ValueError("A closed review requires a final conclusion")
    if status == "closed" and not notes.strip():
        raise ValueError("A closed review requires notes explaining the conclusion")
    if not isinstance(reviewed_at, str):
        raise ValueError("reviewed_at must be an ISO-8601 timestamp")
    try:
        reviewed_time = parse_utc(reviewed_at)
    except ValueError as error:
        raise ValueError("reviewed_at must be an ISO-8601 timestamp") from error
    if reviewed_time is None:
        raise ValueError("reviewed_at must be an ISO-8601 timestamp")
    return reviewed_time

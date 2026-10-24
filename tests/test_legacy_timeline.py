"""A timeline fix must not reinterpret historical review fingerprints."""

import json
import zipfile
from pathlib import Path

from controltrace.cli import main
from controltrace.data import SOURCE_TABLES, parse_utc
from controltrace.reviews import bind_review_history
from controltrace.rules import evaluate
from controltrace.verify import verify_bundle

FIXTURE = Path(__file__).parent / "fixtures" / "controltrace-0.2.9-v4-timeline.zip"


def test_actual_v029_archive_replays_with_an_explicit_legacy_timeline_notice(capsys):
    result = verify_bundle(FIXTURE)
    assert result["legacy_timeline_order"] is True
    assert result["review_evidence_bound"] is True
    assert result["exported_findings"] == 1
    assert main(["verify", "--bundle", str(FIXTURE)]) == 0
    assert "UTC chronology is not guaranteed" in capsys.readouterr().out


def test_corrected_timeline_does_not_inherit_an_old_order_review():
    with zipfile.ZipFile(FIXTURE) as archive:
        data = {table: json.loads(archive.read(f"source_tables/{table}.json")) for table in SOURCE_TABLES}
        manifest = json.loads(archive.read("manifest.json"))
        finding_id = manifest["finding_ids"][0]
        history = json.loads(archive.read("reviews.json"))[finding_id]
        bindings = json.loads(archive.read("review_evidence.json"))[finding_id]
    finding = next(item for item in evaluate(data) if item["finding_id"] == finding_id)
    instants = [parse_utc(item["at"]) for item in finding["timeline"]]
    assert instants == sorted(instants)
    rebound = bind_review_history(finding, history, bindings)
    assert rebound["review_status"] == "pending"
    assert rebound["latest_review"] is None
    assert rebound["review_stale_count"] == 1

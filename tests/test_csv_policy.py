"""Spreadsheet copies preserve source evidence and retain old replay policies."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from controltrace.exports import _csv_bytes, exception_csv, workpaper_zip
from controltrace.rules import run_tests
from controltrace.store import get_finding, initialize_db, save_review
from controltrace.verify import verify_bundle


def _rows(content):
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


@pytest.mark.parametrize(
    "value",
    ["=1+1", "+1+1", "-1+1", "@SUM(1)", "＝1+1", "＋1+1", "－1+1", "＠SUM(1)",
     " \t＝1+1", "\ufeff=1+1", "\x00=1+1", "\x7f=1+1", "\r=1+1", "\n=1+1"],
)
def test_untrusted_string_prefixes_are_visible_text_in_values_and_headers(value):
    assert _rows(_csv_bytes([{value: value}])) == [["text: " + value], ["text: " + value]]


def test_numeric_values_and_multiline_record_boundaries_are_preserved():
    text = 'ordinary, "quoted"\rCR\nLF\r\nCRLF'
    assert _rows(_csv_bytes([{"note": text, "amount": -12.5}], ["note", "amount"])) == [
        ["note", "amount"], [text, "-12.5"],
    ]


def test_visible_prefix_does_not_merge_distinct_strings():
    values = ["=1", "text: =1", "text: text: =1"]
    assert _rows(_csv_bytes([{"note": value} for value in values]))[1:] == [
        ["text: " + value] for value in values
    ]


def test_v4_review_export_keeps_original_json_and_replays(tmp_path):
    db = tmp_path / "case.duckdb"
    initialize_db(db)
    finding_id = run_tests(db)[0]["finding_id"]
    notes = "＝1+1\rsecond line\nthird line"
    save_review(db, finding_id, "Synthetic reviewer", "in_review", "needs_more_evidence", notes)
    finding = get_finding(db, finding_id)
    rows = list(csv.DictReader(io.StringIO(exception_csv([finding]).decode("utf-8-sig"), newline="")))
    assert rows[0]["notes"] == "text: " + notes
    bundle = workpaper_zip(db, {finding_id})
    path = tmp_path / "new.zip"
    path.write_bytes(bundle)
    assert verify_bundle(path)["review_evidence_bound"] is True
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["bundle_format_version"] == 4
        assert manifest["controltrace_version"] == "0.2.9"
        assert json.loads(archive.read("reviews.json"))[finding_id][0]["notes"] == notes


def test_actual_v028_fixture_retains_legacy_csv_and_evidence_binding():
    path = Path(__file__).parent / "fixtures" / "controltrace-0.2.8-v3.zip"
    result = verify_bundle(path)
    assert result["review_evidence_bound"] is True
    assert result["replayed_findings"] == 12
    assert result["exported_findings"] == 1
    with zipfile.ZipFile(path) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("findings.csv").decode("utf-8-sig"), newline="")))
        assert rows[0]["notes"].startswith("'=SUM(1,2)")
        assert "\r=1+1\nFullwidth: ＝1+1" in rows[0]["notes"]

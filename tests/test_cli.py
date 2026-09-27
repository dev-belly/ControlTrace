"""CLI exports must be usable from a fresh checkout."""

from __future__ import annotations

import io
import json
import zipfile

from controltrace.cli import main


def test_export_initializes_and_tests_a_fresh_database(tmp_path):
    db_path = tmp_path / "fresh.duckdb"
    out_path = tmp_path / "export"
    assert main(["export", "--db", str(db_path), "--out", str(out_path)]) == 0
    assert db_path.exists()
    assert (out_path / "exceptions.csv").exists()
    with zipfile.ZipFile(io.BytesIO((out_path / "workpapers.zip").read_bytes())) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["finding_ids"]) == 12
        assert "source_tables/accounts.json" in archive.namelist()
    assert main(["verify", "--bundle", str(out_path / "workpapers.zip")]) == 0

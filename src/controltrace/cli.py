"""Command line entry points for the reproducible demonstration."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="controltrace")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "generate", "test", "export"):
        command = subcommands.add_parser(name)
        command.add_argument("--db", type=Path, default=Path("data/controltrace.duckdb"))
        if name == "demo":
            command.add_argument("--port", type=int, default=8501)
            command.add_argument("--headless", action="store_true")
        if name == "export":
            command.add_argument("--out", type=Path, default=Path("exports"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from controltrace.rules import run_tests
    from controltrace.store import initialize_db

    db_path = args.db.resolve()
    if args.command in {"demo", "generate"}:
        initialize_db(db_path)
        print(f"Synthetic dataset ready: {db_path}")
    if args.command in {"demo", "test"}:
        findings = run_tests(db_path)
        counts = Counter(finding["classification"] for finding in findings)
        print(
            f"Audit cutoff fixed by demo data. {len(findings)} observations: "
            f"{counts.get('exception', 0)} exceptions, "
            f"{counts.get('manual_review', 0)} require manual review."
        )
    if args.command == "export":
        from controltrace.exports import exception_csv, workpaper_zip
        from controltrace.store import list_findings

        args.out.mkdir(parents=True, exist_ok=True)
        csv_path = args.out / "exceptions.csv"
        zip_path = args.out / "workpapers.zip"
        csv_path.write_bytes(exception_csv(list_findings(db_path)))
        zip_path.write_bytes(workpaper_zip(db_path))
        print(f"Exported {csv_path} and {zip_path}")
    if args.command == "demo":
        env = os.environ.copy()
        env["CONTROLTRACE_DB"] = str(db_path)
        app_path = Path(__file__).with_name("app.py")
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.address=127.0.0.1",
            f"--server.port={args.port}",
            f"--server.headless={'true' if args.headless else 'false'}",
        ]
        try:
            return subprocess.call(command, env=env)
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

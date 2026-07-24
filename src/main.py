"""CLI entrypoint.

    python -m src.main path/to/export.csv

Pipeline: ingest -> compile profile -> discover issues -> coverage -> report.
Writes review_queue.md (capped presentation) and review_queue.csv (full log),
and prints a one-line summary including coverage.
"""

from __future__ import annotations

import argparse
import os
import sys

from .ingest import load_ledger
from .issues import compute_coverage, discover_issues
from .profile import compute_profile
from .report import write_csv, write_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="Surface review-worthy accounting issues from a QuickBooks CSV export.",
    )
    parser.add_argument("csv_path", help="Path to the QuickBooks CSV export.")
    parser.add_argument(
        "-o", "--output-dir", default=".",
        help="Directory for review_queue.md / review_queue.csv (default: cwd).",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max issues shown in the presentation queue (default: 50). The "
        "full log is always written uncapped to review_queue.csv.",
    )
    args = parser.parse_args(argv)

    # Reasons/titles use em-dashes; keep them readable on a Windows console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not os.path.isfile(args.csv_path):
        print(f"error: no such file: {args.csv_path}", file=sys.stderr)
        return 2

    ledger = load_ledger(args.csv_path)
    profile = compute_profile(ledger)
    issues = discover_issues(ledger, profile)
    coverage = compute_coverage(ledger, issues)

    os.makedirs(args.output_dir, exist_ok=True)
    md_path = os.path.join(args.output_dir, "review_queue.md")
    csv_path = os.path.join(args.output_dir, "review_queue.csv")
    write_markdown(issues, coverage, md_path, limit=args.limit)
    write_csv(issues, csv_path)

    print(
        f"{len(ledger.transactions)} transactions -> {len(issues)} review "
        f"issues. Coverage {coverage.fraction:.0%} "
        f"({coverage.attached} attached, {coverage.cleared} cleared). "
        f"Wrote {md_path} and {csv_path}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

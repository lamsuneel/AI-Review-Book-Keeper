"""CLI entrypoint.

    python -m src.main path/to/export.csv [--strategy baseline|llm] [--no-text]

Pipeline: ingest -> compile profile -> discover issues -> coverage -> report.
Writes review_queue.md (capped presentation) and review_queue.csv (full log),
and prints a one-line summary including coverage. review_queue.md renders
identically regardless of which discovery strategy produced the issues.
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
    parser.add_argument(
        "--strategy", choices=["baseline", "llm"], default="baseline",
        help="Discovery strategy. 'baseline' is deterministic (no key). 'llm' "
        "uses the Anthropic API (needs ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--no-text", action="store_true",
        help="LLM only: strip raw per-transaction text (vendor/memo) — attribution ablation.",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not os.path.isfile(args.csv_path):
        print(f"error: no such file: {args.csv_path}", file=sys.stderr)
        return 2

    ledger = load_ledger(args.csv_path)
    profile = compute_profile(ledger)

    cost_note = ""
    if args.strategy == "llm":
        from .llm import LLMConfig, LLMKeyMissing, run_llm_discovery

        from datetime import datetime

        config = LLMConfig(include_text=not args.no_text)
        run_dir = os.path.join("runs", datetime.now().strftime("%Y%m%d-%H%M%S"), "llm")
        try:
            run = run_llm_discovery(ledger, profile, config=config,
                                    log=lambda m: print(m, file=sys.stderr),
                                    run_dir=run_dir)
        except LLMKeyMissing as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        issues = run.issues
        cost_note = (
            f" LLM: {run.n_calls} calls, {run.input_tokens}+{run.output_tokens} tok, "
            f"${run.cost_usd(config):.4f} (${run.cost_per_1000(config):.2f}/1k txns), "
            f"{run.dropped_for_provenance} dropped for provenance."
        )
    else:
        issues = discover_issues(ledger, profile)

    coverage = compute_coverage(ledger, issues)

    os.makedirs(args.output_dir, exist_ok=True)
    md_path = os.path.join(args.output_dir, "review_queue.md")
    csv_path = os.path.join(args.output_dir, "review_queue.csv")
    write_markdown(issues, coverage, md_path, limit=args.limit)
    write_csv(issues, csv_path)

    print(
        f"[{args.strategy}] {len(ledger.transactions)} transactions -> {len(issues)} "
        f"review issues. Coverage {coverage.fraction:.0%} "
        f"({coverage.attached} attached, {coverage.cleared} cleared). "
        f"Wrote {md_path} and {csv_path}.{cost_note}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

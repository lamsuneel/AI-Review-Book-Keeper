"""Session-2 discovery experiment: what does the LLM surface that the
deterministic baseline and the reviewer ground truth did not?

This is the primary output of the LLM experiment. It runs the baseline and the
LLM strategy on the SAME ledger and reports three sets (surprises FIRST — they
are the discovery output), plus a hypothesis-stability check (two LLM runs) and
cost per 1,000 transactions.

    python -m src.experiment --ledger LEDGER.csv --annotations ANNOTATIONS.csv \
        --dataset-kind fixture|validation [--no-text] [--model ...] [--runs 2]

Matching reuses evidence overlap (Jaccard >= threshold over transaction refs);
issue wording never participates. Surprises are NEVER auto-scored as false
positives — on fixtures their status is "unclassified until a reviewer sees
them" (DOMAIN.md surprise_class). Needs OPENAI_API_KEY; fails clearly without.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .ingest import load_ledger
from .issues import ReviewIssue, discover_issues
from .profile import compute_profile
from .score import Question, jaccard, load_annotations


def _best_overlap(refs: set[str], candidates: list[set[str]]) -> float:
    return max((jaccard(refs, c) for c in candidates), default=0.0)


def serialize_issue(issue: ReviewIssue) -> dict:
    """Full issue as JSON — reasons/evidence intact, taxonomy fields empty."""
    return {
        "issue_id": issue.issue_id,
        "title": issue.title,
        "category": issue.category,
        "reasons": issue.reasons,
        "evidence": [
            {"kind": e.kind, "summary": e.summary, "refs": e.refs, "accounts": e.accounts}
            for e in issue.evidence
        ],
        "suggested_investigation": issue.suggested_investigation,
        "presentation_order": issue.presentation_order,
        "reviewer_verdict": issue.reviewer_verdict,   # null until a reviewer fills it
        "surprise_class": issue.surprise_class,       # null until a reviewer classifies it
    }


def write_surprises(surprises: list[ReviewIssue], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for issue in surprises:
            fh.write(json.dumps(serialize_issue(issue)) + "\n")


def classify(llm: list[ReviewIssue], baseline: list[ReviewIssue],
             questions: list[Question], threshold: float):
    """Split LLM issues into agreements vs surprises, and find abstentions."""
    baseline_refs = [b.evidence_refs for b in baseline]
    concern_refs = [set(q.refs) for q in questions]

    agreements, surprises = [], []
    for iss in llm:
        r = iss.evidence_refs
        mb, mc = _best_overlap(r, baseline_refs), _best_overlap(r, concern_refs)
        if max(mb, mc) >= threshold:
            src = "ground_truth" if mc >= mb else "baseline"
            agreements.append((iss, src, max(mb, mc)))
        else:
            surprises.append(iss)

    llm_refs = [i.evidence_refs for i in llm]
    missed_baseline = [b for b in baseline
                       if _best_overlap(b.evidence_refs, llm_refs) < threshold]
    missed_gt = [q for q in questions
                 if _best_overlap(set(q.refs), llm_refs) < threshold]
    return agreements, surprises, missed_baseline, missed_gt


def stability(a: list[ReviewIssue], b: list[ReviewIssue], threshold: float):
    """Issue-set overlap between two runs on the identical ledger."""
    b_refs = [i.evidence_refs for i in b]
    matched = sum(1 for i in a if _best_overlap(i.evidence_refs, b_refs) >= threshold)
    denom = max(len(a), len(b)) or 1
    return matched, matched / denom


def _banner(dataset_kind: str) -> list[str]:
    bar = "=" * 72
    lines = [bar, f"LLM DISCOVERY EXPERIMENT — dataset kind: {dataset_kind.upper()}"]
    if dataset_kind == "fixture":
        lines += [
            "*** FIXTURE: measures the HARNESS, not the product. Surprises here are",
            "*** UNCLASSIFIED until a reviewer sees them — never auto-scored as FPs.",
        ]
    else:
        lines.append("VALIDATION: real reviewer annotations — counts for Risk 1/2.")
    lines.append(bar)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.experiment")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--dataset-kind", required=True, choices=["fixture", "validation"])
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--no-text", action="store_true",
                        help="Strip raw per-transaction text (attribution ablation).")
    parser.add_argument("--model", default=None, help="Override the model id.")
    parser.add_argument("--runs", type=int, default=2,
                        help="LLM runs for the stability check (>=2 measures overlap).")
    parser.add_argument("--out", default="surprises.jsonl")
    parser.add_argument("--run-dir", default=None,
                        help="Lab-notebook base dir (default runs/<timestamp>). Every API "
                             "call is persisted with the git commit and prompt-template hash.")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    for path in (args.ledger, args.annotations):
        if not os.path.isfile(path):
            print(f"error: no such file: {path}", file=sys.stderr)
            return 2

    from .llm import LLMConfig, LLMKeyMissing, run_llm_discovery

    ledger = load_ledger(args.ledger)
    profile = compute_profile(ledger)
    questions = load_annotations(args.annotations)
    baseline = discover_issues(ledger, profile)

    config = LLMConfig(include_text=not args.no_text)
    if args.model:
        config.model = args.model

    from datetime import datetime

    base_dir = args.run_dir or os.path.join("runs", datetime.now().strftime("%Y%m%d-%H%M%S"))
    try:
        runs = [run_llm_discovery(ledger, profile, config=config,
                                  log=lambda m: print(m, file=sys.stderr),
                                  run_dir=os.path.join(base_dir, f"run-{i + 1}"))
                for i in range(max(1, args.runs))]
    except LLMKeyMissing as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    run = runs[0]
    agreements, surprises, missed_base, missed_gt = classify(
        run.issues, baseline, questions, args.threshold)
    write_surprises(surprises, args.out)

    out = _banner(args.dataset_kind)
    out.append("")
    out.append(f"Baseline issues: {len(baseline)} · LLM issues: {len(run.issues)} · "
               f"reviewer questions: {len(questions)} · text={'on' if config.include_text else 'OFF'}")
    out.append(f"Dropped for provenance: {run.dropped_for_provenance}  "
               f"(issues the model could not ground and did not repair)")
    out.append("")

    # SURPRISES FIRST — the discovery output.
    out.append(f"SURPRISES ({len(surprises)}) — LLM issues matching neither baseline "
               f"nor ground truth. UNCLASSIFIED until a reviewer judges them:")
    for iss in surprises:
        out.append(f"  • {iss.title}  [{iss.category}]  refs={sorted(iss.evidence_refs)}")
        for r in iss.reasons[:3]:
            out.append(f"      - {r}")
    if not surprises:
        out.append("  (none)")
    out.append(f"  → written to {args.out}")
    out.append("")

    out.append(f"AGREEMENTS ({len(agreements)}) — LLM issues the baseline or ground "
               f"truth already anticipated:")
    for iss, src, j in agreements:
        out.append(f"  • {iss.title}  (matched {src}, Jaccard {j:.2f})")
    out.append("")

    out.append(f"ABSTENTIONS — surfaced by baseline/ground truth but NOT by the LLM:")
    for b in missed_base:
        out.append(f"  • [baseline] {b.title}  refs={sorted(b.evidence_refs)}")
    for q in missed_gt:
        out.append(f"  • [ground truth] {q.question}  refs={sorted(q.refs)}")
    if not (missed_base or missed_gt):
        out.append("  (none)")
    out.append("")

    # Stability.
    if len(runs) >= 2:
        matched, overlap = stability(runs[0].issues, runs[1].issues, args.threshold)
        out.append(f"STABILITY — two runs on the identical ledger: "
                   f"{matched}/{max(len(runs[0].issues), len(runs[1].issues))} issues "
                   f"overlap = {overlap:.0%}")
        if overlap < 0.5:
            out.append("  *** STABILITY < 50% — catastrophically low. STOP and flag "
                       "the founder before trusting any LLM numbers. ***")
    else:
        out.append("STABILITY — skipped (--runs 1)")
    out.append("")

    # Cost.
    out.append(f"COST — model {run.model}: {run.input_tokens} in + {run.output_tokens} out "
               f"tokens over {run.n_calls} calls = ${run.cost_usd(config):.4f} "
               f"(${run.cost_per_1000(config):.2f} per 1,000 transactions).")
    if len(runs) >= 2:
        total = sum(r.cost_usd(config) for r in runs)
        out.append(f"  (all {len(runs)} runs incl. stability: ${total:.4f})")
    out.append("")
    out.append(f"Lab notebook: {base_dir}/ (one JSON package per API call, with the "
               f"git commit and prompt-template hash).")
    out.append("")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

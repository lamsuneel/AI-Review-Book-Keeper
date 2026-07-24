"""Score our review issues against reviewer annotations by evidence overlap.

Matching (DOMAIN.md §6): an issue matches a reviewer question iff the Jaccard
overlap of their transaction-ref sets is >= a threshold (default 0.5). Issue
wording and category never participate — only evidence refs.

    python -m src.score --ledger LEDGER.csv --annotations ANNOTATIONS.csv \
        --dataset-kind fixture|validation [--threshold 0.5]

``--dataset-kind`` is REQUIRED and stamped into the report header (founder
ruling). A `fixture` report says, in bold, that it measures the harness — not
the product. Only `validation` runs (real ledger + real ReviewerAnnotations)
produce numbers that count for Risk 1/2.

The annotations CSV has one row per (question, ref):
    question_id, question, category, transaction_ref, conclusion
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field

from .ingest import load_ledger
from .issues import ReviewIssue, compute_coverage, discover_issues
from .profile import compute_profile


@dataclass
class Question:
    question_id: str
    question: str
    category: str
    refs: set[str] = field(default_factory=set)
    conclusion: str = ""


def load_annotations(path: str) -> list[Question]:
    questions: dict[str, Question] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            qid = r["question_id"]
            q = questions.get(qid)
            if q is None:
                q = Question(qid, r.get("question", ""), r.get("category", ""),
                             set(), r.get("conclusion", ""))
                questions[qid] = q
            ref = (r.get("transaction_ref") or "").strip()
            if ref:
                q.refs.add(ref)
    return list(questions.values())


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_fixture_manifest(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def check_fixtures(manifest: list[dict], issues: list["ReviewIssue"],
                   false_positives: list["ReviewIssue"]) -> list[tuple[dict, bool, str]]:
    """Verify each seeded imperfection still behaves as its manifest says.

    expected_miss: the ref must be covered by NO produced issue.
    expected_extra: the ref must sit in an issue that matched no concern.
    Returns (fixture, ok, observed) tuples so the report can flag drift by ID."""
    attached = set()
    for i in issues:
        attached |= i.evidence_refs
    fp_refs = set()
    for i in false_positives:
        fp_refs |= i.evidence_refs

    results = []
    for fx in manifest:
        ref = fx["transaction_ref"]
        kind = fx["kind"]
        if kind == "expected_miss":
            ok = ref not in attached
            observed = "still missed" if ok else "now surfaced by an issue"
        elif kind == "expected_extra":
            ok = ref in fp_refs
            observed = "still an extra" if ok else "no longer an unmatched extra"
        else:
            ok, observed = False, f"unknown kind '{kind}'"
        results.append((fx, ok, observed))
    return results


@dataclass
class ScoreResult:
    matched: list[tuple[Question, ReviewIssue, float]]
    missed: list[Question]  # reviewer questions no issue matched (false negatives)
    false_positives: list[ReviewIssue]  # issues matching no question
    threshold: float

    @property
    def recall(self) -> float:
        n = len(self.matched) + len(self.missed)
        return len(self.matched) / n if n else 0.0

    @property
    def precision(self) -> float:
        tp = len({i.issue_id for _, i, _ in self.matched})
        n = tp + len(self.false_positives)
        return tp / n if n else 0.0


def score(issues: list[ReviewIssue], questions: list[Question],
          threshold: float = 0.5) -> ScoreResult:
    """Greedy best-overlap matching: each question takes its best-overlapping
    issue above threshold; an issue that matches no question is a false
    positive."""
    matched = []
    matched_issue_ids: set[str] = set()
    missed = []
    for q in questions:
        best, best_j = None, 0.0
        for issue in issues:
            j = jaccard(q.refs, issue.evidence_refs)
            if j > best_j:
                best, best_j = issue, j
        if best is not None and best_j >= threshold:
            matched.append((q, best, best_j))
            matched_issue_ids.add(best.issue_id)
        else:
            missed.append(q)
    false_positives = [i for i in issues if i.issue_id not in matched_issue_ids]
    return ScoreResult(matched, missed, false_positives, threshold)


def format_report(result: ScoreResult, dataset_kind: str, n_issues: int,
                  fixture_checks: list[tuple[dict, bool, str]] | None = None) -> str:
    lines = []
    banner = "=" * 70
    lines.append(banner)
    lines.append(f"EVIDENCE-OVERLAP SCORE — dataset kind: {dataset_kind.upper()}")
    if dataset_kind == "fixture":
        lines.append("*** FIXTURE: this measures the HARNESS, not the product. ***")
        lines.append("*** Synthetic data + synthetic questions cannot produce a  ***")
        lines.append("*** product-quality number. Only real ledgers + reviewer   ***")
        lines.append("*** annotations count for Risk 1 (detection) / Risk 2.     ***")
    else:
        lines.append("VALIDATION: real reviewer annotations — counts for Risk 1/2.")
    lines.append(banner)
    lines.append("")
    lines.append(f"Match threshold (Jaccard): >= {result.threshold}")
    lines.append(f"Issues produced:           {n_issues}")
    lines.append(f"Reviewer questions:        {len(result.matched) + len(result.missed)}")
    lines.append("")
    lines.append(f"Matched (recall):          {result.recall:.0%}  "
                 f"({len(result.matched)}/{len(result.matched) + len(result.missed)})")
    lines.append(f"Issue precision:           {result.precision:.0%}  "
                 f"({len(result.matched)} matched, {len(result.false_positives)} extra)")
    lines.append("")

    if result.matched:
        lines.append("MATCHED (reviewer question -> our issue, overlap):")
        for q, issue, j in result.matched:
            lines.append(f"  [{q.question_id}] {q.question}")
            lines.append(f"       -> {issue.title}  (Jaccard {j:.2f})")
    if result.missed:
        lines.append("")
        lines.append("MISSED — reviewer questions with no matching issue (false negatives):")
        for q in result.missed:
            lines.append(f"  [{q.question_id}] {q.question}  refs={sorted(q.refs)}")
    if result.false_positives:
        lines.append("")
        lines.append("EXTRA — our issues matching no reviewer question (false positives):")
        for issue in result.false_positives:
            lines.append(f"  {issue.title}  refs={sorted(issue.evidence_refs)}")

    if fixture_checks:
        lines.append("")
        lines.append("FIXTURE INTEGRITY (seeded imperfections, by id):")
        for fx, ok, observed in fixture_checks:
            tag = "OK  " if ok else "DRIFT"
            lines.append(f"  [{tag}] {fx['fixture_id']} ({fx['kind']}, {fx['transaction_ref']}): "
                         f"{observed}")
            if not ok:
                lines.append(f"         ^ EXPECTED: {fx['description']} — this fixture no "
                             f"longer behaves as designed; investigate before trusting the run.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.score")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--dataset-kind", required=True, choices=["fixture", "validation"],
                        help="REQUIRED. Stamped into the report; 'fixture' measures "
                             "the harness, 'validation' measures the product.")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--fixture-manifest", default=None,
                        help="Optional fixture-manifest CSV. For --dataset-kind fixture it "
                             "defaults to fixture_manifest.csv beside the annotations.")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    for path in (args.ledger, args.annotations):
        if not os.path.isfile(path):
            print(f"error: no such file: {path}", file=sys.stderr)
            return 2

    ledger = load_ledger(args.ledger)
    profile = compute_profile(ledger)
    issues = discover_issues(ledger, profile)
    coverage = compute_coverage(ledger, issues)
    questions = load_annotations(args.annotations)

    result = score(issues, questions, args.threshold)

    # Fixture integrity: check seeded imperfections by id (fixtures only).
    fixture_checks = None
    manifest_path = args.fixture_manifest
    if args.dataset_kind == "fixture" and manifest_path is None:
        default = os.path.join(os.path.dirname(os.path.abspath(args.annotations)),
                               "fixture_manifest.csv")
        if os.path.isfile(default):
            manifest_path = default
    if manifest_path and os.path.isfile(manifest_path):
        manifest = load_fixture_manifest(manifest_path)
        fixture_checks = check_fixtures(manifest, issues, result.false_positives)

    print(format_report(result, args.dataset_kind, len(issues), fixture_checks))
    print(f"Coverage: {coverage.fraction:.0%} assessed "
          f"({coverage.attached} attached, {coverage.cleared} cleared).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

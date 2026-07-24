"""Render review issues for humans and tooling.

report.py's ONLY job is presentation — formatting, ordering, abbreviating,
capping. It never invents or reinterprets reasons/evidence; that content is
fixed by discovery (DOMAIN.md provenance invariant).

Two outputs:
  review_queue.md   the capped presentation queue (top `limit`), reviewer-facing
  review_queue.csv  the FULL issue log, always uncapped (founder ruling)
"""

from __future__ import annotations

import csv

from .issues import Coverage, ReviewIssue, money


def _evidence_amount(issue: ReviewIssue) -> float:
    return float(issue.rank_components.get("evidence_amount", 0.0))


def render_markdown(
    issues: list[ReviewIssue], coverage: Coverage, limit: int = 50
) -> str:
    shown = issues[:limit]
    lines = [
        "# Review Queue",
        "",
        f"**{len(issues)}** review issue(s) across **{coverage.total}** "
        f"transactions. Coverage: **{coverage.fraction:.0%}** assessed "
        f"({coverage.attached} attached to an issue, {coverage.cleared} "
        f"assessed-and-cleared).",
        "",
        "Each issue is something worth your attention, explained so you can "
        "trace it back to your own ledger. You decide — the system does not.",
        "",
    ]
    if len(issues) > len(shown):
        lines.append(
            f"_Showing the top {len(shown)} of {len(issues)} by presentation "
            f"order; the full log is in review_queue.csv._"
        )
        lines.append("")

    for issue in shown:
        lines.append(f"## {issue.presentation_order}. {issue.title}")
        lines.append("")
        lines.append(
            f"- **Category:** {issue.category} · **Evidence:** "
            f"{issue.rank_components.get('evidence_count', 0)} txn, "
            f"{money(_evidence_amount(issue))}"
        )
        lines.append("- **Why it's here:**")
        for reason in issue.reasons:
            lines.append(f"    - {reason}")
        lines.append("- **Evidence (traceable to your ledger):**")
        for e in issue.evidence:
            ref_note = f" [{', '.join(e.refs)}]" if e.refs else ""
            lines.append(f"    - _{e.kind}_: {e.summary}{ref_note}")
        lines.append(f"- **Suggested investigation:** {issue.suggested_investigation}")
        verdict = issue.reviewer_verdict or "—"
        lines.append(f"- **Reviewer verdict:** {verdict}")
        lines.append("")

    if not issues:
        lines.append("_No review issues surfaced._")
        lines.append("")

    return "\n".join(lines)


def write_markdown(
    issues: list[ReviewIssue], coverage: Coverage, path: str, limit: int = 50
) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(issues, coverage, limit))


def write_csv(issues: list[ReviewIssue], path: str) -> None:
    """The full, uncapped issue log."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "presentation_order",
                "issue_id",
                "category",
                "title",
                "evidence_refs",
                "evidence_count",
                "evidence_amount",
                "reasons",
                "suggested_investigation",
                "reviewer_verdict",
                "rank_category_weight",
            ]
        )
        for issue in issues:
            writer.writerow(
                [
                    issue.presentation_order,
                    issue.issue_id,
                    issue.category,
                    issue.title,
                    "; ".join(sorted(issue.evidence_refs)),
                    issue.rank_components.get("evidence_count", 0),
                    f"{_evidence_amount(issue):.2f}",
                    " | ".join(issue.reasons),
                    issue.suggested_investigation,
                    issue.reviewer_verdict or "",
                    issue.rank_components.get("category_weight", 0),
                ]
            )

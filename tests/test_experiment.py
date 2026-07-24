"""Experiment harness — three-set classification, surprises.jsonl, stability.
All offline (operates on ReviewIssue objects; no client needed)."""

import json

from src.experiment import classify, serialize_issue, stability, write_surprises
from src.issues import Evidence, ReviewIssue
from src.score import Question


def issue(issue_id, category, refs, title="t"):
    ev = [Evidence(kind="transaction", summary="s", refs=list(refs))]
    return ReviewIssue(issue_id=issue_id, title=title, category=category,
                       reasons=["r"], evidence=ev, suggested_investigation="Verify.")


def test_classify_agreements_surprises_abstentions():
    baseline = [issue("b1", "capex_vs_opex", {"N1"})]
    questions = [Question("Q1", "why?", "account_anomaly", {"N2"})]
    llm = [
        issue("l1", "capex_vs_opex", {"N1"}),      # matches baseline -> agreement
        issue("l2", "account_anomaly", {"N2"}),    # matches ground truth -> agreement
        issue("l3", "related_party", {"N9"}),      # matches neither -> surprise
    ]
    agreements, surprises, missed_base, missed_gt = classify(llm, baseline, questions, 0.5)
    assert {i.issue_id for i, _, _ in agreements} == {"l1", "l2"}
    assert [i.issue_id for i in surprises] == ["l3"]
    assert missed_base == [] and missed_gt == []


def test_classify_reports_abstentions():
    baseline = [issue("b1", "capex_vs_opex", {"N1"})]
    questions = [Question("Q1", "why?", "account_anomaly", {"N2"})]
    llm = [issue("l3", "other", {"N9"})]  # LLM found nothing the others did
    _, surprises, missed_base, missed_gt = classify(llm, baseline, questions, 0.5)
    assert [i.issue_id for i in surprises] == ["l3"]
    assert [b.issue_id for b in missed_base] == ["b1"]
    assert [q.question_id for q in missed_gt] == ["Q1"]


def test_stability_overlap():
    run_a = [issue("a1", "capex_vs_opex", {"N1"}), issue("a2", "other", {"N2"})]
    run_b = [issue("b1", "capex_vs_opex", {"N1"})]  # only the first recurs
    matched, overlap = stability(run_a, run_b, 0.5)
    assert matched == 1
    assert overlap == 0.5  # 1 / max(2, 1)


def test_surprises_jsonl_has_empty_taxonomy(tmp_path):
    surprises = [issue("l3", "related_party", {"N9"}, title="Surprise")]
    path = tmp_path / "surprises.jsonl"
    write_surprises(surprises, str(path))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["title"] == "Surprise"
    assert rows[0]["surprise_class"] is None   # only a reviewer may fill this
    assert rows[0]["reviewer_verdict"] is None
    assert rows[0]["evidence"][0]["refs"] == ["N9"]


def test_serialize_issue_shape():
    d = serialize_issue(issue("l1", "capex_vs_opex", {"N1"}))
    assert set(d) >= {"issue_id", "title", "category", "reasons", "evidence",
                      "suggested_investigation", "reviewer_verdict", "surprise_class"}

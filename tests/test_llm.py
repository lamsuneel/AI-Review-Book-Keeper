"""LLM strategy — all offline. A FakeClient replays canned responses so no
network or API key is ever needed."""

import json
from datetime import date

import pytest

from src.ingest import Ledger, Transaction
from src.issues import discover_issues_llm
from src.llm import (
    AnthropicClient,
    LLMConfig,
    LLMKeyMissing,
    LLMResponse,
    LLMRun,
    build_batches,
    format_batch,
    run_llm_discovery,
    validate_issue,
)
from src.profile import compute_profile

_ID = 0


def tx(name, account, amount, memo="", num=None, ttype="Expense"):
    global _ID
    _ID += 1
    return Transaction(
        txn_id=_ID, date=date(2025, 1, 10), txn_type=ttype, num=num or f"N{_ID}",
        name=name, memo=memo, account=account, split="", amount=amount,
    )


class FakeClient:
    """Replays canned response texts in order; records the prompts it saw."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        text = self.responses.pop(0) if self.responses else '{"issues": []}'
        return LLMResponse(text=text, input_tokens=100, output_tokens=50)


def _issue_json(title, category, ref, account, investigation):
    return json.dumps({"issues": [{
        "title": title, "category": category,
        "reasons": [f"{title} — see {ref}."],
        "evidence": [{"kind": "transaction", "summary": f"{ref} in {account}",
                      "refs": [ref], "accounts": [account]}],
        "suggested_investigation": investigation,
    }]})


def _ledger(txns):
    return Ledger.from_transactions(txns)


def test_end_to_end_parse_validate_build_consolidate():
    amazon = tx("Amazon", "Office Supplies", 3000.0, memo="monitors", num="N1")
    rent = tx("Landlord", "Rent", 1000.0, num="N2")
    ledger = _ledger([amazon, rent])
    profile = compute_profile(ledger)

    # Two accounts -> two batches; the same valid issue comes back each time and
    # must consolidate into one (same category + dominant vendor).
    resp = _issue_json("Possible capitalization — Amazon", "capex_vs_opex", "N1",
                       "Office Supplies", "Verify whether this meets the cap threshold.")
    client = FakeClient([resp, resp])
    run = run_llm_discovery(ledger, profile, client=client, config=LLMConfig())

    assert run.n_calls == 2
    assert run.dropped_for_provenance == 0
    assert run.input_tokens == 200 and run.output_tokens == 100
    assert len(run.issues) == 1
    issue = run.issues[0]
    assert issue.category == "capex_vs_opex"
    assert issue.evidence_refs == {"N1"}
    assert issue.presentation_order == 1
    assert issue.issue_id.startswith("llm-")
    assert run.cost_usd(LLMConfig()) > 0


def test_provenance_repair_then_accept():
    amazon = tx("Amazon", "Office Supplies", 3000.0, num="N1")
    ledger = _ledger([amazon])
    profile = compute_profile(ledger)

    bad = json.dumps({"issues": [{
        "title": "Possible capitalization — Amazon", "category": "capex_vs_opex",
        "reasons": ["cites a ref that doesn't exist"],
        "evidence": [{"kind": "transaction", "summary": "x", "refs": ["BOGUS"], "accounts": []}],
        "suggested_investigation": "Verify the capitalization threshold.",
    }]})
    good = _issue_json("Possible capitalization — Amazon", "capex_vs_opex", "N1",
                       "Office Supplies", "Verify the capitalization threshold.")
    run = run_llm_discovery(ledger, profile, client=FakeClient([bad, good]), config=LLMConfig())
    assert run.n_calls == 2  # batch + one repair
    assert run.dropped_for_provenance == 0
    assert len(run.issues) == 1 and run.issues[0].evidence_refs == {"N1"}


def test_provenance_drop_after_failed_repair():
    amazon = tx("Amazon", "Office Supplies", 3000.0, num="N1")
    ledger = _ledger([amazon])
    profile = compute_profile(ledger)
    bad = json.dumps({"issues": [{
        "title": "Ungrounded", "category": "capex_vs_opex", "reasons": ["x"],
        "evidence": [{"kind": "transaction", "summary": "x", "refs": ["BOGUS"], "accounts": []}],
        "suggested_investigation": "Verify something.",
    }]})
    run = run_llm_discovery(ledger, profile, client=FakeClient([bad, bad]), config=LLMConfig())
    assert run.dropped_for_provenance == 1
    assert run.issues == []


def test_validate_issue_rules():
    refs, accts = {"N1"}, {"Office Supplies"}

    def base(**over):
        d = {"title": "T", "category": "capex_vs_opex", "reasons": ["r"],
             "evidence": [{"kind": "transaction", "summary": "s", "refs": ["N1"],
                           "accounts": ["Office Supplies"]}],
             "suggested_investigation": "Verify the threshold."}
        d.update(over)
        return d

    assert validate_issue(base(), refs, accts) is None
    assert "unknown transaction ref" in validate_issue(
        base(evidence=[{"kind": "transaction", "summary": "s", "refs": ["ZZ"], "accounts": []}]),
        refs, accts)
    assert "unknown account" in validate_issue(
        base(evidence=[{"kind": "transaction", "summary": "s", "refs": ["N1"],
                        "accounts": ["Nope"]}]), refs, accts)
    assert "category" in validate_issue(base(category="not_a_category"), refs, accts)
    # A treatment instead of a question is rejected in code (not just the prompt).
    assert "treatment" in validate_issue(
        base(suggested_investigation="Reclassify to Fixed Assets."), refs, accts)
    # Ungrounded (no ref anywhere).
    assert "ungrounded" in validate_issue(
        base(evidence=[{"kind": "account_trend", "summary": "s", "refs": [],
                        "accounts": ["Office Supplies"]}]), refs, accts)


def test_no_text_ablation_strips_vendor_and_memo():
    amazon = tx("Amazon", "Office Supplies", 3000.0, memo="dual monitors", num="N1")
    ledger = _ledger([amazon])
    profile = compute_profile(ledger)
    batch = build_batches(ledger, LLMConfig())[0]

    with_text = format_batch(batch, profile, include_text=True)
    without = format_batch(batch, profile, include_text=False)
    assert "Amazon" in with_text and "dual monitors" in with_text
    assert "Amazon" not in without and "dual monitors" not in without
    assert "Office Supplies" in without  # account (structural) is retained


def test_from_env_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMKeyMissing) as e:
        AnthropicClient.from_env(LLMConfig())
    assert "ANTHROPIC_API_KEY" in str(e.value)


def test_cost_accounting():
    run = LLMRun(issues=[], input_tokens=1_000_000, output_tokens=100_000,
                 cache_read_tokens=0, n_calls=5, dropped_for_provenance=0,
                 n_transactions=500, model="claude-sonnet-4-6", include_text=True)
    cfg = LLMConfig(model="claude-sonnet-4-6")
    # 1M in * $3 + 0.1M out * $15 = 3 + 1.5 = $4.50
    assert round(run.cost_usd(cfg), 2) == 4.50
    assert round(run.cost_per_1000(cfg), 2) == 9.00


def test_discover_issues_llm_contract_wrapper():
    amazon = tx("Amazon", "Office Supplies", 3000.0, num="N1")
    ledger = _ledger([amazon])
    profile = compute_profile(ledger)
    resp = _issue_json("Cap — Amazon", "capex_vs_opex", "N1", "Office Supplies",
                       "Verify the threshold.")
    issues = discover_issues_llm(ledger, profile, client=FakeClient([resp]))
    assert len(issues) == 1 and issues[0].category == "capex_vs_opex"

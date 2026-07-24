"""discover_issues: grouping, the miserly refinement, ranking, coverage, and
the DOMAIN invariants (evidence traceable; investigation is a question)."""

from datetime import date

from src.ingest import Ledger, Transaction
from src.issues import compute_coverage, discover_issues
from src.profile import compute_profile

_ID = 0


def tx(name="", memo="", account="Office Supplies", amount=100.0,
       ttype="Expense", d=date(2025, 1, 10), num=None):
    global _ID
    _ID += 1
    return Transaction(
        txn_id=_ID, date=d, txn_type=ttype, num=num or f"N{_ID}",
        name=name, memo=memo, account=account, split="", amount=amount,
    )


def run(txns):
    ledger = Ledger.from_transactions(txns)
    return ledger, discover_issues(ledger, compute_profile(ledger))


def test_multiple_transactions_merge_into_one_issue():
    draw = tx(name="John Bright", memo="owner draw", account="Owner's Draw",
              amount=6_000.0, ttype="Check")
    dist = tx(name="John Bright", memo="distribution to member",
              account="Distributions", amount=5_500.0, ttype="Check")
    _, issues = run([draw, dist])
    owner = [i for i in issues if i.category == "owner_personal"]
    assert len(owner) == 1
    assert owner[0].evidence_refs == {draw.ref, dist.ref}


def test_new_vendor_suppressed_when_semantic_signal_present():
    # John Bright trips owner_personal AND new_vendor; new_vendor must be dropped.
    draw = tx(name="John Bright", memo="owner draw", account="Owner's Draw",
              amount=6_000.0, ttype="Check")
    _, issues = run([draw])
    cats = {i.category for i in issues}
    assert "owner_personal" in cats
    assert "new_vendor" not in cats


def test_round_only_transaction_becomes_other_singleton():
    r = tx(name="IRS", memo="estimated tax", account="Income Tax Expense",
           amount=5_000.0, ttype="Check")
    # 5,000 is round AND >= new-vendor threshold, so new_vendor also forms.
    # Use a clearly-recurring vendor to isolate the round-only 'other' path.
    recur = [tx(name="Rent Co", memo="", account="Rent", amount=9_000.0,
                d=date(2025, m, 1), ttype="Check") for m in (1, 2, 3)]
    # 9,000 recurring -> round_number only (new_vendor guarded by recurrence).
    _, issues = run(recur)
    others = [i for i in issues if i.category == "other"]
    assert others and all("round" in " ".join(i.reasons).lower() for i in others)


def test_ranking_orders_related_party_above_other():
    loan = tx(name="Trust", memo="loan from shareholder", account="Loan",
              amount=1_500.0, ttype="Deposit")
    recur = [tx(name="Rent Co", account="Rent", amount=9_000.0,
                d=date(2025, m, 1), ttype="Check") for m in (1, 2, 3)]
    _, issues = run([loan] + recur)
    order = {i.category: i.presentation_order for i in issues}
    assert order["related_party"] < order["other"]  # lower = surfaced sooner
    # presentation_order is a dense 1..N ranking.
    assert sorted(i.presentation_order for i in issues) == list(range(1, len(issues) + 1))
    # Each issue records what drove its rank (founder ruling #2).
    for i in issues:
        assert set(i.rank_components) == {"category_weight", "evidence_amount", "evidence_count"}


def test_coverage_accounts_for_every_transaction():
    txns = [tx(name="Amazon", account="Office Supplies", amount=3_000.0)] + [
        tx(amount=50.0) for _ in range(9)
    ]
    ledger, issues = run(txns)
    cov = compute_coverage(ledger, issues)
    assert cov.total == len(txns)
    assert cov.assessed == cov.total  # baseline assesses everything
    assert cov.attached + cov.cleared == cov.total


def test_evidence_is_traceable_and_investigation_is_a_question():
    txns = [
        tx(name="Amazon", account="Office Supplies", amount=3_000.0),
        tx(name="Trust", memo="loan from shareholder", account="Loan", amount=15_000.0),
        tx(ttype="Journal Entry", memo="depreciation", account="Depreciation",
           amount=12_000.0, num="JE1"),
    ]
    ledger, issues = run(txns)
    real_refs = {t.ref for t in ledger.transactions}
    banned = ("reclassify", "reclassif", "book to", "record to", "move to")
    for issue in issues:
        assert issue.reasons, "discovery must attach reasons"
        assert issue.evidence, "discovery must attach evidence"
        # Every evidence ref traces back to a real ledger transaction.
        assert issue.evidence_refs <= real_refs
        # suggested_investigation is a verification step, never a treatment.
        low = issue.suggested_investigation.lower()
        assert any(low.startswith(v) for v in ("verify", "confirm", "review"))
        assert not any(b in low for b in banned)

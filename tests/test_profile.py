"""Each signal is a fact the profiler computes. One test per signal type."""

from datetime import date

from src.ingest import Ledger, Transaction
from src.profile import (
    SIGNAL_ACCOUNT_OUTLIER,
    SIGNAL_CAPEX,
    SIGNAL_JOURNAL_ENTRY,
    SIGNAL_NEW_VENDOR,
    SIGNAL_OWNER_PERSONAL,
    SIGNAL_RELATED_PARTY,
    SIGNAL_ROUND_NUMBER,
    compute_profile,
)

_ID = 0


def tx(name="", memo="", account="Office Supplies", amount=100.0,
       ttype="Expense", d=date(2025, 1, 10), num=None):
    global _ID
    _ID += 1
    return Transaction(
        txn_id=_ID, date=d, txn_type=ttype, num=num or f"N{_ID}",
        name=name, memo=memo, account=account, split="", amount=amount,
    )


def signals_of(txns, target):
    profile = compute_profile(Ledger.from_transactions(txns))
    return profile.signals_for(target)


def test_capex_candidate_signal():
    hit = tx(name="Amazon", account="Office Supplies", amount=3_000.0)
    assert SIGNAL_CAPEX in signals_of([hit], hit)
    # Below threshold: no signal.
    small = tx(name="Amazon", amount=100.0)
    assert SIGNAL_CAPEX not in signals_of([small], small)
    # Already an asset account: the call is made, so no capex signal.
    asset = tx(name="Dell", account="Computer Equipment", amount=5_000.0)
    assert SIGNAL_CAPEX not in signals_of([asset], asset)
    # Non-ambiguous vendor: no signal.
    plain = tx(name="Local Roaster", amount=5_000.0)
    assert SIGNAL_CAPEX not in signals_of([plain], plain)


def test_round_number_signal():
    r = tx(amount=10_000.0)
    assert SIGNAL_ROUND_NUMBER in signals_of([r], r)
    nr = tx(amount=10_500.0)
    assert SIGNAL_ROUND_NUMBER not in signals_of([nr], nr)
    small_round = tx(amount=1_000.0)  # round but below threshold
    assert SIGNAL_ROUND_NUMBER not in signals_of([small_round], small_round)


def test_owner_personal_and_word_boundary():
    owner = tx(name="John B", memo="Owner draw - January", account="Owner's Draw")
    assert SIGNAL_OWNER_PERSONAL in signals_of([owner], owner)
    # 'drawer' must not match 'draw'.
    decoy = tx(memo="new drawer for the register")
    assert SIGNAL_OWNER_PERSONAL not in signals_of([decoy], decoy)


def test_related_party_signal():
    loan = tx(name="Family Trust", memo="Loan from shareholder", account="Loan")
    sig = signals_of([loan], loan)
    assert SIGNAL_RELATED_PARTY in sig
    assert SIGNAL_OWNER_PERSONAL not in sig  # distinct buckets


def test_journal_entry_signal():
    je = tx(ttype="Journal Entry", memo="depreciation", account="Depreciation")
    assert SIGNAL_JOURNAL_ENTRY in signals_of([je], je)


def test_account_outlier_needs_history_and_distance():
    baseline = [tx(account="Supplies", amount=100.0) for _ in range(10)]
    outlier = tx(account="Supplies", amount=1_000.0)
    txns = baseline + [outlier]
    assert SIGNAL_ACCOUNT_OUTLIER in signals_of(txns, outlier)
    assert SIGNAL_ACCOUNT_OUTLIER not in signals_of(txns, baseline[0])
    # Same outlier, too little account history -> skipped (precision guard).
    thin = [tx(account="Thin", amount=100.0) for _ in range(3)] + [
        tx(account="Thin", amount=1_000.0)
    ]
    assert SIGNAL_ACCOUNT_OUTLIER not in signals_of(thin, thin[-1])


def test_new_vendor_large_signal_and_recurring_guard():
    one_off = tx(name="Precision Machine Works", amount=8_000.0)
    assert SIGNAL_NEW_VENDOR in signals_of([one_off], one_off)
    # Recurring vendor (>2 appearances): first large txn is NOT "new".
    recurring = [tx(name="Payroll Co", amount=8_000.0, d=date(2025, m, 1)) for m in (1, 2, 3)]
    assert SIGNAL_NEW_VENDOR not in signals_of(recurring, recurring[0])
    # New but small: no signal.
    small = tx(name="One Timer", amount=1_000.0)
    assert SIGNAL_NEW_VENDOR not in signals_of([small], small)

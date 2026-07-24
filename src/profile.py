"""Compile deterministic context for a ledger — a context-compiler, not a
gatekeeper (see FOUNDER.md, AI-usage rule).

This module computes facts *about* every transaction, vendor, and account and
filters nothing out. It produces a ``LedgerProfile``: per-vendor spend history,
per-account distribution and month-over-month totals, journal-entry clusters by
date, and the set of deterministic *signals* each transaction trips
(round-number, new-vendor, ambiguous-vendor, owner/related-party language,
statistical outlier, journal entry).

Signals are facts, not verdicts. Turning signals into ``ReviewIssue``s — with
plain-English reasons and typed evidence — is the discovery step's job
(``issues.py``), per the DOMAIN.md provenance invariant.

Statistics use pandas, where a per-account groupby is genuinely the clearest
tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date

import pandas as pd

from .ingest import Ledger, Transaction

_MIN_DATE = _date.min  # sort key for transactions missing a date

# Signal vocabulary. These are context flags the discovery step reads; they are
# NOT issue categories and NOT verdicts.
SIGNAL_CAPEX = "capex_candidate"
SIGNAL_OWNER_PERSONAL = "owner_personal"
SIGNAL_RELATED_PARTY = "related_party"
SIGNAL_NEW_VENDOR = "new_vendor_large"
SIGNAL_ACCOUNT_OUTLIER = "account_outlier"
SIGNAL_JOURNAL_ENTRY = "journal_entry"
SIGNAL_ROUND_NUMBER = "round_number"


@dataclass
class Config:
    """Tunable thresholds. Defaults are starting points, not truths — they only
    earn their values against a real design-partner ledger."""

    # Vendors that sell both expensable and capitalizable goods. Substring,
    # case-insensitive match against the vendor name.
    ambiguous_vendors: list[str] = field(
        default_factory=lambda: [
            "amazon", "best buy", "dell", "apple", "home depot", "costco", "staples",
        ]
    )
    capex_amount_threshold: float = 2_500.0

    # Round-number signal: an exact multiple of this, at or above the threshold.
    round_number_multiple: float = 1_000.0
    round_number_threshold: float = 5_000.0

    # Statistical-outlier signal: only for accounts with enough history, to
    # protect precision on low-volume accounts.
    outlier_std: float = 3.0
    outlier_min_history: int = 8

    # New-vendor signal. We only have in-window history, so "first transaction"
    # would also match a recurring vendor's first paycheck/rent. To protect
    # precision (Risk 2), require the vendor NOT to be an established
    # relationship — it appears at most this many times in the ledger.
    new_vendor_amount_threshold: float = 5_000.0
    new_vendor_max_occurrences: int = 2

    # Journal entry near period end (day >= this) is worth noting as a reason.
    period_end_day: int = 27


# Account-name hints that this is already a balance-sheet / fixed-asset account,
# where the CapEx-vs-OpEx call has effectively already been made.
_ASSET_ACCOUNT_HINTS = (
    "fixed asset", "furniture", "equipment", "machinery", "leasehold",
    "vehicle", "computer hardware", "accumulated depreciation", "buildings",
)

# Owner/personal language (money moving to/for an owner). Word-boundary regex so
# "draw" never matches inside "drawer".
_OWNER_PERSONAL_PATTERNS = [
    (r"\bowner'?s?\s+draw\b", "owner draw"),
    (r"\bmember'?s?\s+draw\b", "member draw"),
    (r"\bdistribution(s)?\b", "distribution"),
    (r"\bpersonal\b", "personal"),
]

# Related-party language (loans, intercompany, inter-entity balances).
_RELATED_PARTY_PATTERNS = [
    (r"\bshareholder\b", "shareholder"),
    (r"\bloan\s+to\b", "loan to"),
    (r"\bloan\s+from\b", "loan from"),
    (r"\bdue\s+to\b", "due to (related party)"),
    (r"\bdue\s+from\b", "due from (related party)"),
    (r"\bintercompany\b", "intercompany"),
    (r"\brelated\s+part(y|ies)\b", "related party"),
]

_JOURNAL_TYPES = {"journal entry", "journal", "general journal"}


@dataclass
class VendorStat:
    """A vendor's spend history within the ledger."""

    name: str  # display name (first seen)
    refs: list[str]
    count: int
    total: float  # sum of abs amounts
    mean: float
    max: float
    first_ref: str
    first_date: _date | None
    accounts: list[str]  # distinct accounts this vendor touched, in first-seen order
    monthly_totals: dict[str, float]  # "YYYY-MM" -> sum of abs amounts


@dataclass
class AccountStat:
    """An account's amount distribution and monthly totals."""

    name: str
    refs: list[str]
    count: int
    mean: float  # of abs amounts
    std: float  # population std of abs amounts
    monthly_totals: dict[str, float]  # "YYYY-MM" -> sum of abs amounts

    def zscore(self, amount: float) -> float | None:
        """How many SDs |amount| sits above this account's mean. None if the
        account is perfectly uniform (std == 0)."""
        if self.std == 0:
            return None
        return (abs(amount) - self.mean) / self.std


@dataclass
class LedgerProfile:
    """Everything deterministic we know about the ledger, computed over ALL
    transactions (nothing filtered out)."""

    vendors: dict[str, VendorStat]  # keyed by lowercased vendor name
    accounts: dict[str, AccountStat]  # keyed by account name
    signals: dict[int, set[str]]  # txn_id -> set of SIGNAL_* it trips
    je_clusters: dict[_date, list[str]]  # date -> refs of journal entries that day

    def signals_for(self, txn: Transaction) -> set[str]:
        return self.signals.get(txn.txn_id, set())


def is_ambiguous_vendor(name: str, config: Config) -> bool:
    low = name.lower()
    return any(v in low for v in config.ambiguous_vendors)


def is_asset_account(account: str) -> bool:
    low = account.lower()
    return any(hint in low for hint in _ASSET_ACCOUNT_HINTS)


def matched_owner_personal(text: str) -> str | None:
    for pattern, label in _OWNER_PERSONAL_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


def matched_related_party(text: str) -> str | None:
    for pattern, label in _RELATED_PARTY_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


def is_round_number(amount: float, config: Config) -> bool:
    amt = abs(amount)
    return amt >= config.round_number_threshold and amt % config.round_number_multiple == 0


def is_journal_entry(txn: Transaction) -> bool:
    return txn.txn_type.strip().lower() in _JOURNAL_TYPES


def near_period_end(d: _date | None, config: Config) -> bool:
    return d is not None and d.day >= config.period_end_day


def _month_key(d: _date | None) -> str:
    return d.strftime("%Y-%m") if d else "unknown"


def _build_vendor_stats(txns: list[Transaction]) -> dict[str, VendorStat]:
    ordered = sorted(txns, key=lambda t: (t.date or _MIN_DATE, t.txn_id))
    stats: dict[str, VendorStat] = {}
    for t in ordered:
        key = t.name.strip().lower()
        if not key:
            continue
        amt = abs(t.amount)
        month = _month_key(t.date)
        if key not in stats:
            stats[key] = VendorStat(
                name=t.name.strip(), refs=[t.ref], count=1, total=amt, mean=amt,
                max=amt, first_ref=t.ref, first_date=t.date,
                accounts=([t.account] if t.account else []),
                monthly_totals={month: amt},
            )
        else:
            s = stats[key]
            s.refs.append(t.ref)
            s.count += 1
            s.total += amt
            s.mean = s.total / s.count
            s.max = max(s.max, amt)
            if t.account and t.account not in s.accounts:
                s.accounts.append(t.account)
            s.monthly_totals[month] = s.monthly_totals.get(month, 0.0) + amt
    return stats


def _build_account_stats(txns: list[Transaction]) -> dict[str, AccountStat]:
    scored = [t for t in txns if t.account]
    if not scored:
        return {}
    df = pd.DataFrame(
        {
            "account": [t.account for t in scored],
            "amount": [abs(t.amount) for t in scored],
            "month": [_month_key(t.date) for t in scored],
            "ref": [t.ref for t in scored],
        }
    )
    grp = df.groupby("account")["amount"]
    agg = pd.DataFrame({"mean": grp.mean(), "std": grp.std(ddof=0), "n": grp.size()})

    stats: dict[str, AccountStat] = {}
    for account, r in agg.iterrows():
        members = df[df["account"] == account]
        monthly = members.groupby("month")["amount"].sum().to_dict()
        stats[account] = AccountStat(
            name=account,
            refs=members["ref"].tolist(),
            count=int(r["n"]),
            mean=float(r["mean"]),
            std=float(r["std"]),
            monthly_totals={k: float(v) for k, v in monthly.items()},
        )
    return stats


def compute_profile(ledger: Ledger, config: Config | None = None) -> LedgerProfile:
    """Compile deterministic context for the whole ledger. Filters nothing."""
    config = config or Config()
    txns = ledger.transactions

    vendors = _build_vendor_stats(txns)
    accounts = _build_account_stats(txns)

    signals: dict[int, set[str]] = {}
    je_clusters: dict[_date, list[str]] = {}

    for t in txns:
        s: set[str] = set()
        text = f"{t.name} {t.memo}".lower()

        if (
            abs(t.amount) >= config.capex_amount_threshold
            and is_ambiguous_vendor(t.name, config)
            and not is_asset_account(t.account)
        ):
            s.add(SIGNAL_CAPEX)

        if matched_owner_personal(text):
            s.add(SIGNAL_OWNER_PERSONAL)
        if matched_related_party(text):
            s.add(SIGNAL_RELATED_PARTY)

        if is_round_number(t.amount, config):
            s.add(SIGNAL_ROUND_NUMBER)

        if is_journal_entry(t):
            s.add(SIGNAL_JOURNAL_ENTRY)
            if t.date is not None:
                je_clusters.setdefault(t.date, []).append(t.ref)

        acct = accounts.get(t.account)
        if acct is not None and acct.count >= config.outlier_min_history:
            z = acct.zscore(t.amount)
            if z is not None and z >= config.outlier_std:
                s.add(SIGNAL_ACCOUNT_OUTLIER)

        # New-vendor: this is the vendor's first (earliest) transaction, the
        # vendor is not an established relationship, and it's large.
        vkey = t.name.strip().lower()
        v = vendors.get(vkey)
        if (
            v is not None
            and v.first_ref == t.ref
            and v.count <= config.new_vendor_max_occurrences
            and abs(t.amount) >= config.new_vendor_amount_threshold
        ):
            s.add(SIGNAL_NEW_VENDOR)

        signals[t.txn_id] = s

    return LedgerProfile(
        vendors=vendors, accounts=accounts, signals=signals, je_clusters=je_clusters
    )

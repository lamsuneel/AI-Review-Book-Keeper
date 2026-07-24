"""Review issues: the product's unit of output (DOMAIN.md §3-5).

``discover_issues(ledger, profile) -> list[ReviewIssue]`` is the product
contract. This file also holds the **deterministic baseline** implementation of
that contract. Per founder ruling, the baseline is retained permanently as the
control: every future strategy (starting with the LLM one next session) is
judged by whether it beats the baseline on evidence-overlap against reviewer
annotations. Baseline and LLM strategies must be callable side by side, so they
share this one plain function signature — kept because it IS the contract, not
for extensibility theatre.

Discovery owns meaning: it produces the reasons and typed evidence. ``report.py``
only formats them (DOMAIN.md provenance invariant).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ingest import Ledger, Transaction
from .profile import (
    SIGNAL_ACCOUNT_OUTLIER,
    SIGNAL_CAPEX,
    SIGNAL_JOURNAL_ENTRY,
    SIGNAL_NEW_VENDOR,
    SIGNAL_OWNER_PERSONAL,
    SIGNAL_RELATED_PARTY,
    SIGNAL_ROUND_NUMBER,
    Config,
    LedgerProfile,
    matched_owner_personal,
    matched_related_party,
    near_period_end,
)

# The fixed category vocabulary (DOMAIN.md §3).
CATEGORIES = (
    "capex_vs_opex",
    "owner_personal",
    "related_party",
    "new_vendor",
    "account_anomaly",
    "journal_entry_review",
    "other",
)

# Evidence kinds (DOMAIN.md §4).
EVIDENCE_KINDS = (
    "transaction",
    "vendor_history",
    "account_trend",
    "journal_pattern",
    "other",
)

# Ranking weights per category — EDITABLE and explicitly GUESSES pending
# reviewer-agreement evidence (Risk 2). Higher = surfaced sooner. These encode
# a hunch that owner/related-party and capitalization calls cost more to miss
# than a lone round-dollar entry; only reviewer verdicts can confirm that.
CATEGORY_WEIGHTS = {
    "related_party": 6,
    "owner_personal": 5,
    "capex_vs_opex": 4,
    "journal_entry_review": 3,
    "new_vendor": 3,
    "account_anomaly": 2,
    "other": 1,
}

# Which signals form their own issue, and how their transactions are grouped.
# (round-number is deliberately absent — it is a modifier, not a category.)
_ISSUE_FORMING = {
    SIGNAL_CAPEX: "capex_vs_opex",
    SIGNAL_OWNER_PERSONAL: "owner_personal",
    SIGNAL_RELATED_PARTY: "related_party",
    SIGNAL_NEW_VENDOR: "new_vendor",
    SIGNAL_ACCOUNT_OUTLIER: "account_anomaly",
    SIGNAL_JOURNAL_ENTRY: "journal_entry_review",
}


@dataclass
class Evidence:
    """Typed, heterogeneous support for an issue (DOMAIN.md §4).

    INVARIANT: recomputable from the Ledger, and cites the transaction ``refs``
    and/or ``accounts`` it derives from. ``refs`` are the focal transactions the
    evidence is about — not an entire vendor/account population — so evidence
    stays traceable and scoring stays meaningful."""

    kind: str  # one of EVIDENCE_KINDS
    summary: str  # plain-English, recomputable claim
    refs: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)


@dataclass
class ReviewIssue:
    """One review-worthy hypothesis (DOMAIN.md §3). No confidence score."""

    issue_id: str
    title: str
    category: str
    reasons: list[str]
    evidence: list[Evidence]
    suggested_investigation: str
    presentation_order: int = 0
    reviewer_verdict: str | None = None  # correct | incorrect | partial; null until annotated
    # valuable_surprise | valid_alternative_reasoning | noise; ONLY a reviewer fills this.
    surprise_class: str | None = None
    rank_components: dict = field(default_factory=dict)  # what drove presentation_order

    @property
    def evidence_refs(self) -> set[str]:
        """Transaction refs reachable from this issue's evidence — the set used
        for evidence-overlap scoring (DOMAIN.md §6)."""
        refs: set[str] = set()
        for e in self.evidence:
            refs.update(e.refs)
        return refs


@dataclass
class Coverage:
    """Did we look at everything? (DOMAIN.md cross-cutting rules.)"""

    total: int  # transactions in the ledger
    assessed: int  # transactions the strategy considered
    attached: int  # transactions that are evidence in >= 1 issue

    @property
    def cleared(self) -> int:
        return self.assessed - self.attached

    @property
    def fraction(self) -> float:
        return self.assessed / self.total if self.total else 0.0


def money(amount: float) -> str:
    return f"${abs(amount):,.2f}"


def _date_str(t: Transaction) -> str:
    return t.date.isoformat() if t.date else "(no date)"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _txn_evidence(t: Transaction) -> Evidence:
    return Evidence(
        kind="transaction",
        summary=f"{money(t.amount)} to '{t.account}' on {_date_str(t)} "
        f"({t.txn_type or 'entry'}{', ' + t.name if t.name else ''}).",
        refs=[t.ref],
        accounts=[t.account] if t.account else [],
    )


def _round_reason(t: Transaction) -> str:
    return (
        f"Exactly {money(t.amount)} — a round-dollar amount, often a manual "
        f"entry or transfer rather than invoiced activity."
    )


# --- Per-category issue builders ------------------------------------------
# Each returns (title, reasons, evidence, suggested_investigation). Focal refs
# are the transactions in `group`; derived evidence points back at them.


def _build_capex(group, profile, config):
    vendor = group[0].name or "vendor"
    focal = [t.ref for t in group]
    reasons, evidence = [], []
    for t in group:
        reasons.append(
            f"{money(t.amount)} to '{t.account}' from {vendor} on {_date_str(t)} — "
            f"a mixed-use vendor purchase above {money(config.capex_amount_threshold)}."
        )
        evidence.append(_txn_evidence(t))
        if SIGNAL_ROUND_NUMBER in profile.signals_for(t):
            reasons.append(_round_reason(t))
    v = profile.vendors.get(vendor.strip().lower())
    if v is not None:
        reasons.append(
            f"{vendor} history in the period: {v.count} purchase(s), "
            f"largest single {money(v.max)}."
        )
        evidence.append(
            Evidence("vendor_history",
                     f"{vendor}: {v.count} purchase(s) this period, max single {money(v.max)}.",
                     refs=focal))
    title = f"Possible capitalization — {vendor}"
    investigation = (
        f"Verify whether these {vendor} purchases meet the client's "
        f"capitalization threshold rather than being expensed."
    )
    return title, reasons, evidence, investigation


def _build_owner_personal(group, profile, config):
    name = group[0].name or "owner"
    reasons, evidence = [], []
    for t in group:
        label = matched_owner_personal(f"{t.name} {t.memo}".lower()) or "owner/personal"
        reasons.append(
            f"{money(t.amount)} to '{t.account}' on {_date_str(t)} — "
            f"name/memo mentions '{label}'."
        )
        evidence.append(_txn_evidence(t))
        if SIGNAL_ROUND_NUMBER in profile.signals_for(t):
            reasons.append(_round_reason(t))
    title = f"Owner / personal activity — {name}"
    investigation = (
        "Confirm these are properly recorded as owner draws/distributions "
        "and not misclassified business expenses or personal spending."
    )
    return title, reasons, evidence, investigation


def _build_related_party(group, profile, config):
    name = group[0].name or "counterparty"
    reasons, evidence = [], []
    for t in group:
        label = matched_related_party(f"{t.name} {t.memo}".lower()) or "related party"
        reasons.append(
            f"{money(t.amount)} in '{t.account}' on {_date_str(t)} — "
            f"name/memo mentions '{label}'."
        )
        evidence.append(_txn_evidence(t))
        if SIGNAL_ROUND_NUMBER in profile.signals_for(t):
            reasons.append(_round_reason(t))
    title = f"Related-party activity — {name}"
    investigation = (
        "Confirm the related-party relationship, terms, and documentation "
        "for these amounts."
    )
    return title, reasons, evidence, investigation


def _build_new_vendor(group, profile, config):
    vendor = group[0].name or "vendor"
    focal = [t.ref for t in group]
    reasons, evidence = [], []
    for t in group:
        reasons.append(
            f"First transaction from {vendor} in the period, {money(t.amount)} "
            f"to '{t.account}' on {_date_str(t)}."
        )
        evidence.append(_txn_evidence(t))
        if SIGNAL_ROUND_NUMBER in profile.signals_for(t):
            reasons.append(_round_reason(t))
    evidence.append(
        Evidence("vendor_history",
                 f"{vendor}: first and only appearance(s) this period; no prior history.",
                 refs=focal))
    title = f"New vendor, large amount — {vendor}"
    investigation = (
        f"Confirm {vendor} is a legitimate vendor and the coding "
        f"(including any capitalization) is correct."
    )
    return title, reasons, evidence, investigation


def _build_account_anomaly(group, profile, config):
    account = group[0].account or "account"
    focal = [t.ref for t in group]
    acct = profile.accounts.get(account)
    reasons, evidence = [], []
    for t in group:
        z = acct.zscore(t.amount) if acct else None
        z_txt = f"{z:.1f} SD above" if z is not None else "well above"
        avg_txt = f" (account avg {money(acct.mean)} over {acct.count} entries)" if acct else ""
        reasons.append(
            f"{money(t.amount)} to '{account}' on {_date_str(t)} is {z_txt} the "
            f"account's usual size{avg_txt}."
        )
        evidence.append(_txn_evidence(t))
    if acct is not None:
        evidence.append(
            Evidence("account_trend",
                     f"'{account}': mean {money(acct.mean)}, std {money(acct.std)} "
                     f"over {acct.count} entries.",
                     refs=focal, accounts=[account]))
    title = f"Unusual account activity — {account}"
    investigation = (
        f"Verify why '{account}' has entries well above its usual size this "
        f"period and that they are classified correctly."
    )
    return title, reasons, evidence, investigation


def _build_journal_entry(group, profile, config):
    when = group[0].date
    focal = [t.ref for t in group]
    reasons, evidence = [], []
    for t in group:
        pe = " (near period end)" if near_period_end(t.date, config) else ""
        reasons.append(
            f"Manual journal entry of {money(t.amount)} to '{t.account}' on "
            f"{_date_str(t)}{pe}."
        )
        evidence.append(_txn_evidence(t))
        if SIGNAL_ROUND_NUMBER in profile.signals_for(t):
            reasons.append(_round_reason(t))
    if len(group) > 1:
        reasons.append(f"{len(group)} journal entries posted on {_date_str(group[0])}.")
        evidence.append(
            Evidence("journal_pattern",
                     f"{len(group)} journal entries posted on {_date_str(group[0])}.",
                     refs=focal))
    day = when.isoformat() if when else "an unknown date"
    plural = "entries" if len(group) > 1 else "entry"
    title = f"Journal {plural} — {day}"
    investigation = (
        f"Confirm the rationale and supporting documentation for the manual "
        f"journal {plural} posted on {day}."
    )
    return title, reasons, evidence, investigation


def _build_other_round(t: Transaction, profile, config):
    reasons = [_round_reason(t)]
    if t.account:
        reasons.append(f"Posted to '{t.account}' on {_date_str(t)}.")
    evidence = [_txn_evidence(t)]
    who = t.name or t.account or t.ref
    title = f"Round-dollar transaction — {who}"
    investigation = (
        "Review this round-dollar transaction and confirm its supporting "
        "documentation and classification."
    )
    return title, reasons, evidence, investigation


_BUILDERS = {
    "capex_vs_opex": _build_capex,
    "owner_personal": _build_owner_personal,
    "related_party": _build_related_party,
    "new_vendor": _build_new_vendor,
    "account_anomaly": _build_account_anomaly,
    "journal_entry_review": _build_journal_entry,
}


def _group_key(category: str, t: Transaction) -> str:
    """How transactions are bucketed into a single issue."""
    if category == "account_anomaly":
        return t.account or t.ref
    if category == "journal_entry_review":
        return t.date.isoformat() if t.date else f"no-date-{t.ref}"
    # vendor-centric categories
    return t.name.strip().lower() or t.ref


def discover_issues(
    ledger: Ledger, profile: LedgerProfile, config: Config | None = None
) -> list[ReviewIssue]:
    """DETERMINISTIC BASELINE strategy (the permanent control).

    Turns the profile's signals into grouped issues. This is intentionally
    crude — its job is to be the floor the LLM strategy must clear, not to be
    good. It never filters transactions before assessment: every transaction is
    considered; those without a signal are assessed-and-cleared (see coverage).
    """
    config = config or Config()
    txns = ledger.transactions

    # Bucket transactions into (category, key) groups. A transaction can land in
    # several buckets (evidence may overlap across issues) — that's allowed.
    buckets: dict[tuple[str, str], list[Transaction]] = {}
    round_only: list[Transaction] = []
    # Transactions whose new_vendor signal was demoted (not deleted): the fact
    # survives as a reason on the owning issue. See DOMAIN.md "demotion, not
    # deletion". Maps ref -> Transaction.
    demoted_new_vendor: dict[str, Transaction] = {}

    for t in txns:
        sig = profile.signals_for(t)
        forming = [s for s in sig if s in _ISSUE_FORMING]
        # Miserly refinement (Risk 2): "new vendor" is a weak, name-based signal.
        # Once a transaction is recognized as an owner/related-party/capitalization
        # matter, a redundant "unknown new vendor" issue only adds noise (an owner
        # draw is not a new vendor). DEMOTE new_vendor there — the signal loses its
        # own issue but survives as a reason on the owning issue (provenance
        # invariant). Genuinely new unknown vendors keep their own issue.
        if SIGNAL_NEW_VENDOR in forming and any(
            s in forming for s in (SIGNAL_CAPEX, SIGNAL_OWNER_PERSONAL, SIGNAL_RELATED_PARTY)
        ):
            forming.remove(SIGNAL_NEW_VENDOR)
            demoted_new_vendor[t.ref] = t
        if forming:
            for s in forming:
                category = _ISSUE_FORMING[s]
                buckets.setdefault((category, _group_key(category, t)), []).append(t)
        elif SIGNAL_ROUND_NUMBER in sig:
            round_only.append(t)  # round-number is the only signal -> 'other'

    issues: list[ReviewIssue] = []
    used_ids: set[str] = set()

    def _mint_id(category: str, key: str) -> str:
        base = f"{category}-{_slug(key)}"
        issue_id = base
        n = 2
        while issue_id in used_ids:
            issue_id = f"{base}-{n}"
            n += 1
        used_ids.add(issue_id)
        return issue_id

    for (category, key), group in buckets.items():
        title, reasons, evidence, investigation = _BUILDERS[category](group, profile, config)
        issues.append(
            ReviewIssue(
                issue_id=_mint_id(category, key),
                title=title,
                category=category,
                reasons=reasons,
                evidence=evidence,
                suggested_investigation=investigation,
            )
        )

    for t in round_only:
        title, reasons, evidence, investigation = _build_other_round(t, profile, config)
        issues.append(
            ReviewIssue(
                issue_id=_mint_id("other", t.ref),
                title=title,
                category="other",
                reasons=reasons,
                evidence=evidence,
                suggested_investigation=investigation,
            )
        )

    # Demotion, not deletion: attach the demoted new_vendor fact as a reason on
    # the semantic issue that owns the transaction (DOMAIN.md invariant).
    _SEMANTIC = {"capex_vs_opex", "owner_personal", "related_party"}
    for issue in issues:
        if issue.category not in _SEMANTIC:
            continue
        for ref in issue.evidence_refs:
            t = demoted_new_vendor.get(ref)
            if t is not None:
                issue.reasons.append(
                    f"Also the first transaction from {t.name} in the period — a "
                    f"new vendor; noted here rather than raised as a separate issue."
                )

    _assign_presentation_order(issues, ledger)
    return issues


def discover_issues_llm(ledger: Ledger, profile: LedgerProfile, client=None,
                        config=None) -> list[ReviewIssue]:
    """LLM discovery strategy — same product contract as the baseline, so the two
    are callable side by side (the baseline is the permanent control).

    The heavy machinery (client, prompt, batching, provenance) lives in
    ``llm.py``; this stays here so both strategies share one signature in one
    place. For run metrics (tokens, cost, dropped-for-provenance) call
    ``llm.run_llm_discovery`` directly. Requires ANTHROPIC_API_KEY unless a
    ``client`` is injected (tests inject a fake)."""
    from .llm import run_llm_discovery  # lazy: breaks the issues<->llm import cycle

    return run_llm_discovery(ledger, profile, client=client, config=config).issues


def _assign_presentation_order(issues: list[ReviewIssue], ledger: Ledger) -> None:
    """Rank deterministically: category weight -> total evidence $ -> evidence
    count. Records the driving components on each issue (founder ruling #2).
    This is a display order, NOT a confidence score."""
    ref_amount = {t.ref: abs(t.amount) for t in ledger.transactions}

    for issue in issues:
        amount = sum(ref_amount.get(r, 0.0) for r in issue.evidence_refs)
        count = len(issue.evidence_refs)
        issue.rank_components = {
            "category_weight": CATEGORY_WEIGHTS.get(issue.category, 0),
            "evidence_amount": amount,
            "evidence_count": count,
        }

    issues.sort(
        key=lambda i: (
            i.rank_components["category_weight"],
            i.rank_components["evidence_amount"],
            i.rank_components["evidence_count"],
        ),
        reverse=True,
    )
    for position, issue in enumerate(issues, start=1):
        issue.presentation_order = position


def compute_coverage(ledger: Ledger, issues: list[ReviewIssue]) -> Coverage:
    """Fraction of transactions attached to an issue or assessed-and-cleared.

    In the deterministic baseline every transaction is assessed (the profile
    computes signals for all of them), so assessed == total and this reads
    ~100%. It becomes a load-bearing number once an LLM strategy assesses in
    batches and could skip some — the plumbing is real now."""
    total = len(ledger.transactions)
    attached_refs: set[str] = set()
    for issue in issues:
        attached_refs.update(issue.evidence_refs)
    real_refs = {t.ref for t in ledger.transactions}
    attached = len(attached_refs & real_refs)
    return Coverage(total=total, assessed=total, attached=attached)

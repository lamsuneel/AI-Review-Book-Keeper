"""LLM discovery machinery (Session 2 experiment).

The product-contract function ``discover_issues_llm(ledger, profile)`` lives in
``issues.py`` alongside the deterministic baseline (founder ruling). This module
holds the supporting machinery so ``issues.py`` doesn't bloat with SDK and
prompt detail: the OpenAI client wrapper, the ledger digest, account-grouped
batching, prompt construction, JSON parsing, provenance validation + a single
repair attempt, cross-batch consolidation, and cost accounting.

Deterministic context (the LedgerProfile) is shared infrastructure given to
every call. Raw per-transaction text (vendor name, memo) is the INTERVENTION —
strippable via ``include_text=False`` for a cheap attribution ablation.

No LLM decides accounting treatments: ``suggested_investigation`` is validated
in code to be interrogative, never a treatment (belt-and-suspenders with the
prompt). Everything the model asserts must be grounded in the ledger — an issue
whose evidence cites a ref/account that doesn't exist is repaired once, then
dropped and counted (``dropped_for_provenance``).

Context-budget arithmetic (why we batch even though the model has a large window):
batching is for REASONING QUALITY and PRECISION, not just to fit. One call per
account keeps the model focused on a pattern a reviewer thinks in ("why is this
account off?"), and the digest carries per-vendor/per-account context into every
call. Rough sizes: digest ~1.5-2K tokens; a detail row ~40-60 tokens; an account
of ~150 rows ~8K tokens. A batch call is digest + one account << the window, so
the ceiling is never the constraint — focus is.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field

from .ingest import Ledger, Transaction
from .issues import (
    CATEGORIES,
    EVIDENCE_KINDS,
    Evidence,
    ReviewIssue,
    _assign_presentation_order,
    _slug,
)
from .profile import LedgerProfile

# GPT-5.5 is available in the OpenAI API (verified against the model docs), so
# it is the model per the "gpt-5.5 if available, else gpt-5" rule.
DEFAULT_MODEL = "gpt-5.5"

# Per-model pricing, USD per 1M tokens (input, output). From OpenAI's model
# pricing; verify when pricing moves. gpt-5.5 cached input is $0.50 = 0.1x input,
# which is exactly what cost_usd() charges for cache reads.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.5": (5.00, 30.00),
    "gpt-5": (1.25, 10.00),  # fallback per the requirement (not used while 5.5 is available)
}

# Treatment verbs banned from suggested_investigation (the AI never decides).
_BANNED_TREATMENT = (
    "reclassif", "move to fixed", "book to", "book as", "record to", "record as",
    "post to", "journalize", "should be capitalized", "should be expensed",
    "depreciate it", "write off",
)
_INVESTIGATION_OPENERS = ("verify", "confirm", "review", "check", "investigate",
                          "determine", "assess", "ask", "clarify")


class LLMKeyMissing(RuntimeError):
    """Raised when OPENAI_API_KEY is absent. Message tells the user what to do."""


@dataclass
class LLMConfig:
    model: str = DEFAULT_MODEL
    max_tokens: int = 8_000
    effort: str | None = "medium"  # reasoning.effort: none|low|medium|high|xhigh; None = default
    thinking: bool = True  # retained for config parity; OpenAI reasoning is set via effort
    include_text: bool = True  # False = --no-text ablation (strip vendor/memo)
    max_batch_rows: int = 200

    def price(self) -> tuple[float, float]:
        return PRICES.get(self.model, PRICES["gpt-5.5"])


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    stop_reason: str | None = None


class OpenAIClient:
    """Thin wrapper over the OpenAI SDK (Responses API). Constructed only when a
    real run is requested; tests inject a fake with the same ``complete`` shape."""

    def __init__(self, sdk_client, config: LLMConfig):
        self._client = sdk_client
        self._config = config

    @classmethod
    def from_env(cls, config: LLMConfig) -> "OpenAIClient":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMKeyMissing(
                "OPENAI_API_KEY is not set. The LLM strategy needs it - export "
                "it in your shell (never commit it; .env is gitignored). The "
                "baseline strategy (--strategy baseline) needs no key."
            )
        from openai import OpenAI  # imported lazily so offline tests never need it

        return cls(OpenAI(api_key=key), config)

    def complete(self, system: str, user: str) -> LLMResponse:
        kwargs = {
            "model": self._config.model,
            "instructions": system,
            "input": user,
            "max_output_tokens": self._config.max_tokens,
        }
        if self._config.effort:
            kwargs["reasoning"] = {"effort": self._config.effort}
        resp = self._client.responses.create(**kwargs)
        u = resp.usage
        total_in = getattr(u, "input_tokens", 0) or 0
        details = getattr(u, "input_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        stop = getattr(resp, "status", None)
        incomplete = getattr(resp, "incomplete_details", None)
        if incomplete is not None and getattr(incomplete, "reason", None):
            stop = f"{stop}:{incomplete.reason}"
        return LLMResponse(
            text=getattr(resp, "output_text", "") or "",
            # Uncached input remainder, mirroring the prior client's semantics so
            # cost_usd (which charges cache reads at 0.1x) stays correct.
            input_tokens=max(0, total_in - cached),
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=cached,
            stop_reason=stop,
        )


@dataclass
class LLMRun:
    issues: list[ReviewIssue]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    n_calls: int
    dropped_for_provenance: int
    n_transactions: int
    model: str
    include_text: bool

    def cost_usd(self, config: LLMConfig) -> float:
        pin, pout = config.price()
        return (
            self.input_tokens / 1e6 * pin
            + self.output_tokens / 1e6 * pout
            + self.cache_read_tokens / 1e6 * pin * 0.1
        )

    def cost_per_1000(self, config: LLMConfig) -> float:
        if not self.n_transactions:
            return 0.0
        return self.cost_usd(config) / self.n_transactions * 1000


# --- Lab notebook ----------------------------------------------------------
# Not production logging — experimental-condition preservation. Every API call
# is persisted so "why did the model flag this?" is answerable by inspection
# weeks later. The git commit and the prompt-template hash are captured by the
# repo, not by hand.


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@dataclass
class RunNotebook:
    run_dir: str
    git_commit: str
    template_sha256: str  # hash of the system-prompt template (the two-layer template)
    _n: int = 0

    def record(self, *, kind: str, batch_label: str, refs: list[str], system: str,
               user: str, digest: str, response: LLMResponse, config: LLMConfig) -> None:
        self._n += 1
        pkg = {
            "call_index": self._n,
            "kind": kind,  # "batch" | "repair"
            "batch_id": batch_label,
            "transaction_refs": refs,
            "model": config.model,
            "params": {"effort": config.effort, "thinking": config.thinking,
                       "include_text": config.include_text, "max_tokens": config.max_tokens},
            "digest": digest,
            "system_prompt": system,
            "user_prompt": user,
            "raw_response": response.text,
            "stop_reason": response.stop_reason,
            "usage": {"input_tokens": response.input_tokens,
                      "output_tokens": response.output_tokens,
                      "cache_read_tokens": response.cache_read_tokens},
            "git_commit": self.git_commit,
            "prompt_template_sha256": self.template_sha256,
        }
        with open(os.path.join(self.run_dir, f"call-{self._n:04d}.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(pkg, fh, indent=2, ensure_ascii=False)


def open_notebook(system: str, run_dir: str) -> RunNotebook:
    os.makedirs(run_dir, exist_ok=True)
    return RunNotebook(run_dir=run_dir, git_commit=_git_commit(),
                       template_sha256=hashlib.sha256(system.encode("utf-8")).hexdigest())


# --- Digest ----------------------------------------------------------------


def build_digest(ledger: Ledger, profile: LedgerProfile) -> str:
    """A compact, whole-ledger context present in every call. Recomputable from
    the ledger; carries the patterns a per-account batch can't see alone."""
    lines = [
        "LEDGER DIGEST (deterministic context, recomputable from the ledger):",
        f"Period {ledger.period_start}..{ledger.period_end} · "
        f"{len(ledger.transactions)} transactions · {len(ledger.accounts)} accounts.",
        "",
        "Per-account (name | n | total | mean | std | monthly totals):",
    ]
    for acct in sorted(profile.accounts.values(), key=lambda a: -a.count):
        total = sum(acct.monthly_totals.values())
        months = " ".join(f"{k}={v:,.0f}" for k, v in sorted(acct.monthly_totals.items()))
        lines.append(
            f"  {acct.name} | n={acct.count} | total={total:,.0f} | "
            f"mean={acct.mean:,.0f} | std={acct.std:,.0f} | {months}"
        )
    # Significant vendors = top-15 by spend UNION every vendor touching >=2
    # accounts. Per-vendor accounts-touched + monthly totals travel in every
    # call so per-account batching can't hide a vendor split across accounts
    # (e.g. one vendor in Repairs and Fixed Assets). Confound prevention.
    top = sorted(profile.vendors.values(), key=lambda v: -v.total)[:15]
    multi = [v for v in profile.vendors.values() if len(v.accounts) >= 2]
    seen, significant = set(), []
    for v in sorted({id(x): x for x in top + multi}.values(), key=lambda v: -v.total):
        if v.name not in seen:
            seen.add(v.name)
            significant.append(v)

    lines.append("")
    lines.append("Significant vendors (name | n | total | max | first | accounts | monthly):")
    for v in significant:
        months = " ".join(f"{k}={val:,.0f}" for k, val in sorted(v.monthly_totals.items()))
        accts = "; ".join(v.accounts) or "(none)"
        lines.append(
            f"  {v.name} | n={v.count} | total={v.total:,.0f} | max={v.max:,.0f} | "
            f"first={v.first_date} | accounts=[{accts}] | {months}"
        )

    cross = [v for v in significant if len(v.accounts) >= 2]
    if cross:
        lines.append("")
        lines.append("CROSS-ACCOUNT vendors (spend split across accounts — look for "
                     "patterns per-account batching can't see alone):")
        for v in cross:
            lines.append(f"  {v.name}: {'; '.join(v.accounts)}")
    if profile.je_clusters:
        lines.append("")
        lines.append("Journal-entry clusters (date -> refs):")
        for d, refs in sorted(profile.je_clusters.items()):
            lines.append(f"  {d}: {len(refs)} JE(s) [{', '.join(refs)}]")
    sig_counts = Counter(s for sset in profile.signals.values() for s in sset)
    if sig_counts:
        lines.append("")
        lines.append("Deterministic signals: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(sig_counts.items())))
    return "\n".join(lines)


# --- Batching --------------------------------------------------------------


@dataclass
class Batch:
    account: str
    transactions: list[Transaction]
    part: int = 0  # >0 when a very large account is chunked


def build_batches(ledger: Ledger, config: LLMConfig) -> list[Batch]:
    """Group transactions by account (the unit a reviewer scans). Chunk only if
    an account is larger than max_batch_rows; the digest carries the account's
    full stats into every chunk so the pattern isn't lost."""
    by_account: dict[str, list[Transaction]] = {}
    for t in ledger.transactions:
        by_account.setdefault(t.account or "(no account)", []).append(t)

    batches: list[Batch] = []
    for account, txns in by_account.items():
        if len(txns) <= config.max_batch_rows:
            batches.append(Batch(account, txns))
        else:
            for i in range(0, len(txns), config.max_batch_rows):
                batches.append(Batch(account, txns[i:i + config.max_batch_rows],
                                     part=i // config.max_batch_rows + 1))
    return batches


def format_batch(batch: Batch, profile: LedgerProfile, include_text: bool) -> str:
    cols = "ref | date | type | amount | account | signals"
    if include_text:
        cols += " | vendor | memo"
    part = f" (part {batch.part})" if batch.part else ""
    lines = [f"ACCOUNT: {batch.account}{part} — {len(batch.transactions)} rows", cols]
    for t in batch.transactions:
        sigs = ",".join(sorted(profile.signals_for(t))) or "-"
        row = (f"{t.ref} | {t.date} | {t.txn_type} | {abs(t.amount):,.2f} | "
               f"{t.account} | {sigs}")
        if include_text:
            row += f" | {t.name} | {t.memo}"
        lines.append(row)
    return "\n".join(lines)


# --- Prompts ---------------------------------------------------------------


# Prompt template — TWO LAYERS (see FOUNDER.md "Prompt engineering"). The layers
# are kept in separate constants so a diff makes obvious which one changed:
#   _JUDGMENT_LAYER   — what the model thinks; FROZEN except on reviewer evidence.
#   _COMPLIANCE_LAYER — how the model speaks; may change on engineering evidence.

# ---- JUDGMENT LAYER — FROZEN except in response to reviewer evidence ----
_JUDGMENT_LAYER = """\
You are helping a SENIOR ACCOUNTANT decide where to spend review attention on a \
completed client ledger. You do NOT make accounting decisions — you identify \
concerns a reviewer should investigate and cite the evidence.

Each concern you surface costs the reviewer real minutes to check. A SHORT list \
of well-supported concerns beats a long list of possibilities. When unsure, do \
not raise it.

Judgment guidance:
- reasons must name numbers/accounts/dates a reviewer can trace, never scores.
- An issue may cite exactly one transaction; singletons are fine.
- Do not raise an issue for ordinary, well-explained activity.
- The reviewer decides; you never do."""

# ---- COMPLIANCE LAYER — may change on engineering evidence ----
_COMPLIANCE_LAYER = f"""\
Return ONLY JSON, no prose, in this exact shape:
{{"issues": [
  {{
    "title": "short human title, e.g. 'Possible capitalization — Home Depot'",
    "category": one of {list(CATEGORIES)},
    "reasons": ["plain-English sentence a senior accountant would respect", ...],
    "evidence": [
      {{"kind": one of {list(EVIDENCE_KINDS)},
        "summary": "recomputable claim",
        "refs": ["exact transaction refs from the batch"],
        "accounts": ["account names it derives from"]}}
    ],
    "suggested_investigation": "a QUESTION or verification step"
  }}
]}}

Output rules:
- Every evidence item must cite refs and/or accounts that ACTUALLY appear in the \
data given to you. Never invent a ref or account. At least one evidence item per \
issue must cite a transaction ref.
- suggested_investigation must be interrogative — a question or 'Verify/Confirm/\
Review ...' step. NEVER prescribe an accounting treatment (no 'reclassify', \
'capitalize', 'book to ...')."""


def build_system_prompt() -> str:
    """Assemble the two-layer template. Do not inline judgment content here —
    edit _JUDGMENT_LAYER (reviewer evidence) or _COMPLIANCE_LAYER (engineering)
    so the layer boundary stays checkable in diffs."""
    return (
        "=== JUDGMENT LAYER (what to surface — frozen except on reviewer evidence) ===\n"
        f"{_JUDGMENT_LAYER}\n\n"
        "=== COMPLIANCE LAYER (how to format — engineering evidence) ===\n"
        f"{_COMPLIANCE_LAYER}"
    )


def build_user_prompt(digest: str, batch: Batch, profile: LedgerProfile,
                      include_text: bool) -> str:
    return (
        f"{digest}\n\n"
        f"Now review this batch of transactions and return issues as specified. "
        f"Consider both this account's pattern and the ledger-wide digest above.\n\n"
        f"{format_batch(batch, profile, include_text)}"
    )


def build_repair_prompt(invalid: list[tuple[dict, str]], batch: Batch,
                        profile: LedgerProfile, include_text: bool) -> str:
    problems = "\n".join(f"- {json.dumps(iss.get('title', '?'))}: {err}"
                         for iss, err in invalid)
    return (
        "Some issues you returned failed validation. Return corrected JSON "
        '({"issues":[...]}) for ONLY these issues, fixing the stated problem. '
        "Every ref/account must appear exactly in the batch below; "
        "suggested_investigation must be a question, never a treatment.\n\n"
        f"Problems:\n{problems}\n\n"
        f"{format_batch(batch, profile, include_text)}"
    )


# --- Parsing & validation --------------------------------------------------


def parse_issues(text: str) -> list[dict]:
    """Extract the issues array from a model response. Tolerant of code fences
    and leading/trailing prose."""
    if not text:
        return []
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    data = _loads_or_extract(s)
    if isinstance(data, dict):
        issues = data.get("issues", [])
    elif isinstance(data, list):
        issues = data
    else:
        issues = []
    return [i for i in issues if isinstance(i, dict)]


def _loads_or_extract(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} or [...] block.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = s.find(open_c), s.rfind(close_c)
        if 0 <= start < end:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def validate_issue(issue: dict, valid_refs: set[str], valid_accounts: set[str]) -> str | None:
    """Return an error string if the issue is invalid (schema, provenance, or the
    interrogative-investigation rule); None if it passes."""
    if not isinstance(issue.get("title"), str) or not issue["title"].strip():
        return "missing title"
    if issue.get("category") not in CATEGORIES:
        return f"category must be one of {list(CATEGORIES)}"
    reasons = issue.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        return "reasons must be a non-empty list"
    evidence = issue.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return "evidence must be a non-empty list"

    grounded = False
    for ev in evidence:
        if not isinstance(ev, dict):
            return "each evidence item must be an object"
        if ev.get("kind") not in EVIDENCE_KINDS:
            return f"evidence.kind must be one of {list(EVIDENCE_KINDS)}"
        refs = ev.get("refs") or []
        accts = ev.get("accounts") or []
        if not isinstance(refs, list) or not isinstance(accts, list):
            return "evidence refs/accounts must be lists"
        for r in refs:
            if r not in valid_refs:
                return f"evidence cites unknown transaction ref '{r}'"
        for a in accts:
            if a not in valid_accounts:
                return f"evidence cites unknown account '{a}'"
        if refs:
            grounded = True
    if not grounded:
        return "no evidence item cites a transaction ref (ungrounded)"

    inv = issue.get("suggested_investigation")
    if not isinstance(inv, str) or not inv.strip():
        return "missing suggested_investigation"
    low = inv.lower()
    if any(b in low for b in _BANNED_TREATMENT):
        return "suggested_investigation prescribes a treatment (must be a question)"
    if "?" not in inv and not any(low.startswith(v) for v in _INVESTIGATION_OPENERS):
        return "suggested_investigation must be interrogative (a question/verification step)"
    return None


def _to_issue(issue: dict) -> ReviewIssue:
    evidence = [
        Evidence(
            kind=ev["kind"],
            summary=str(ev.get("summary", "")),
            refs=[str(r) for r in (ev.get("refs") or [])],
            accounts=[str(a) for a in (ev.get("accounts") or [])],
        )
        for ev in issue["evidence"]
    ]
    return ReviewIssue(
        issue_id="",  # assigned during consolidation
        title=issue["title"].strip(),
        category=issue["category"],
        reasons=[str(r) for r in issue["reasons"]],
        evidence=evidence,
        suggested_investigation=issue["suggested_investigation"].strip(),
    )


# --- Consolidation ---------------------------------------------------------


def consolidate(issues: list[ReviewIssue], ledger: Ledger) -> list[ReviewIssue]:
    """Merge duplicates across batches. Two issues merge when they share a
    category AND a dominant entity (same vendor across accounts, or the same
    transaction set) — e.g. an 'Amazon' concern raised in two account batches
    becomes one issue."""
    ref_to_name = {t.ref: t.name for t in ledger.transactions}

    groups: dict[tuple, list[ReviewIssue]] = {}
    for iss in issues:
        names = [ref_to_name.get(r, "") for r in iss.evidence_refs if ref_to_name.get(r)]
        dom = Counter(names).most_common(1)[0][0] if names else ""
        key = (iss.category, dom) if dom else (iss.category, frozenset(iss.evidence_refs))
        groups.setdefault(key, []).append(iss)

    merged: list[ReviewIssue] = []
    used_ids: set[str] = set()
    for key, grp in groups.items():
        base = grp[0]
        reasons: list[str] = []
        evidence: list[Evidence] = []
        seen_reason: set[str] = set()
        seen_ev: set[tuple] = set()
        for iss in grp:
            for r in iss.reasons:
                if r not in seen_reason:
                    seen_reason.add(r)
                    reasons.append(r)
            for ev in iss.evidence:
                sig = (ev.kind, ev.summary, tuple(ev.refs))
                if sig not in seen_ev:
                    seen_ev.add(sig)
                    evidence.append(ev)
        category, dom = (key if isinstance(key[1], str) else (key[0], ""))
        slug = _slug(dom) if dom else _slug(base.title)
        issue_id = f"llm-{category}-{slug}"
        n = 2
        while issue_id in used_ids:
            issue_id = f"llm-{category}-{slug}-{n}"
            n += 1
        used_ids.add(issue_id)
        merged.append(ReviewIssue(
            issue_id=issue_id,
            title=base.title,
            category=category,
            reasons=reasons,
            evidence=evidence,
            suggested_investigation=base.suggested_investigation,
        ))
    return merged


# --- Orchestration ---------------------------------------------------------


def run_llm_discovery(ledger: Ledger, profile: LedgerProfile, client=None,
                      config: LLMConfig | None = None,
                      log=None, run_dir: str | None = None) -> LLMRun:
    """Run the batched LLM discovery and return issues + run metrics.

    If ``run_dir`` is given, a lab-notebook package is written per API call."""
    config = config or LLMConfig()
    if client is None:
        client = OpenAIClient.from_env(config)
    log = log or (lambda msg: None)

    valid_refs = {t.ref for t in ledger.transactions}
    valid_accounts = set(ledger.accounts)
    digest = build_digest(ledger, profile)
    batches = build_batches(ledger, config)

    in_tok = out_tok = cache_tok = n_calls = dropped = 0
    raw_valid: list[dict] = []

    system = build_system_prompt()
    notebook = open_notebook(system, run_dir) if run_dir else None

    for batch in batches:
        label = batch.account + (f" (part {batch.part})" if batch.part else "")
        refs = [t.ref for t in batch.transactions]
        user = build_user_prompt(digest, batch, profile, config.include_text)
        resp = client.complete(system, user)
        in_tok += resp.input_tokens
        out_tok += resp.output_tokens
        cache_tok += resp.cache_read_tokens
        n_calls += 1
        if notebook:
            notebook.record(kind="batch", batch_label=label, refs=refs, system=system,
                            user=user, digest=digest, response=resp, config=config)

        parsed = parse_issues(resp.text)
        valid, invalid = [], []
        for iss in parsed:
            err = validate_issue(iss, valid_refs, valid_accounts)
            (invalid.append((iss, err)) if err else valid.append(iss))

        if invalid:
            log(f"[{batch.account}] {len(invalid)} issue(s) failed validation; repairing once.")
            repair = build_repair_prompt(invalid, batch, profile, config.include_text)
            resp2 = client.complete(system, repair)
            in_tok += resp2.input_tokens
            out_tok += resp2.output_tokens
            cache_tok += resp2.cache_read_tokens
            n_calls += 1
            if notebook:
                notebook.record(kind="repair", batch_label=label, refs=refs, system=system,
                                user=repair, digest=digest, response=resp2, config=config)

            repaired = parse_issues(resp2.text)
            fixed_titles: set[str] = set()
            for iss in repaired:
                err = validate_issue(iss, valid_refs, valid_accounts)
                if err is None:
                    valid.append(iss)
                    fixed_titles.add(iss.get("title"))
            # An originally-invalid issue is dropped unless the repair returned a
            # valid version of it (matched by title).
            for iss, err in invalid:
                if iss.get("title") not in fixed_titles:
                    dropped += 1
                    log(f"[{batch.account}] DROPPED for provenance: "
                        f"{iss.get('title', '?')} ({err})")

        raw_valid.extend(valid)

    issues = consolidate([_to_issue(i) for i in raw_valid], ledger)
    _assign_presentation_order(issues, ledger)

    return LLMRun(
        issues=issues,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_tok,
        n_calls=n_calls,
        dropped_for_provenance=dropped,
        n_transactions=len(ledger.transactions),
        model=config.model,
        include_text=config.include_text,
    )

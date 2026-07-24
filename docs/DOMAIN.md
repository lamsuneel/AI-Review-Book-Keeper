# DOMAIN.md — Domain Model (v2)

**Status:** v2 · prose-first. This document is the source of truth. Code
mirrors this model, not the other way around. When the model and the code
disagree, the code is wrong until this document is changed. Changing this
document is allowed and expected — but every change records, in the Changelog,
the one thing we learned that forced it.

The product turns a completed ledger into a short, ranked list of things a
senior accountant should look at, each explained in terms the reviewer can
trace back to their own books. It never makes accounting decisions
(see `FOUNDER.md`). These six concepts are the whole vocabulary.

---

## 1. Ledger

The normalized set of `Transaction`s for **one client, one period**, plus the
metadata needed to reason about them: the period covered and the set of
accounts present. A Ledger is the closed world for a run — every claim the
system makes must be recomputable from the Ledger it was given, and nothing
outside it.

## 2. Transaction

One normalized ledger line: date, type, number, name/vendor, memo, account,
split, amount (signed). Each transaction carries a stable **`ref`** — its
business identity used everywhere issues, evidence, and annotations point at
it. `ref` is the QuickBooks `Num` when present, otherwise a deterministic row
id (e.g. `row0142`). `ref` is stable for a given normalized export; it is not
stable across re-exports, which is why reviewers annotate a sheet we generate,
never the raw QuickBooks file (see the design-partner protocol).

## 3. ReviewIssue

One review-worthy **hypothesis** about the ledger — a pattern or a singleton
worth a reviewer's attention. Fields:

- `issue_id` — stable id for this issue within a run.
- `title` — short, human, e.g. "Possible capitalization — Home Depot".
- `category` — one of the fixed vocabulary below.
- `reasons[]` — `Reason`s (see §5), produced by discovery.
- `evidence[]` — `Evidence` items (see §4), produced by discovery.
- `suggested_investigation` — a **question or verification step**, never an
  accounting treatment. "Verify whether this meets the client's
  capitalization threshold" is the standard; "Reclassify to Fixed Assets" is
  banned. The AI never decides.
- `presentation_order` — deterministic rank for display (see §7).
- `reviewer_verdict` — `null` until a reviewer annotates it; then
  `correct | incorrect | partial`. **This is the only confidence signal we
  trust.** There are no model-self-reported confidence scores anywhere.
- `surprise_class` — `null` unless a reviewer classifies a *surprise* issue
  (one no baseline or ground-truth concern anticipated) as
  `valuable_surprise | valid_alternative_reasoning | noise`. **Only a reviewer
  may fill it.** Discovery and fixtures log surprises; they never classify
  them. Like `reviewer_verdict`, it is trust earned from a human, not asserted
  by the system.

An issue may cite **exactly one** transaction. Singleton anomalies (e.g. a
single large journal entry on the last day of the month) are first-class
issues, not leftovers.

**Category vocabulary (fixed):**
`capex_vs_opex · owner_personal · related_party · new_vendor ·
account_anomaly · journal_entry_review · other`.

Round-dollar amounts, new-vendor flags, etc. are **signals**, not categories.
A transaction whose only signal is round-number surfaces as an `other`
singleton issue.

## 4. Evidence

The support under an issue — **typed and heterogeneous**. `kind` is a minimal
enum: `transaction | vendor_history | account_trend | journal_pattern |
other`. A `transaction` evidence item points at one `ref`; the others are
*derived* (e.g. "this vendor's prior spend never exceeded $500",
"Repairs & Maintenance is 4× its trailing average").

**INVARIANT (recomputability):** every Evidence item, whatever its kind, must
be recomputable from the Ledger, and must cite the transaction `ref`s and/or
accounts it derives from. No free-floating claims. A reviewer asking "says
who?" must always be able to trace the evidence back to rows in their own
ledger.

## 5. Reason

One plain-English sentence a senior accountant would respect, attached to an
issue **by the discovery step itself**. "First transaction from this vendor,
$8,400 to Repairs & Maintenance — verify it isn't capitalizable" is the
standard; "anomaly score 0.87" is banned.

**INVARIANT (provenance):** reasons and evidence are produced **by
discovery**, never by a later stage. `report.py` may format, order, and
abbreviate them for display; it may never invent or reinterpret them.

## 6. ReviewerAnnotation

Ground truth from a design partner — the **only** input that produces
product-quality numbers (Risks 1 & 2). Structured in three layers, matching
the protocol:

- `question` — the review question they pursued, e.g. "Why is Amazon spend
  unusually high this month?"
- `transactions_examined[]` — the `ref`s they looked at for that question
  (mandatory; recorded against refs on a sheet we generate).
- `conclusion` — free text, plus optional per-transaction outcomes.

Scoring matches an issue to a question by **evidence overlap** — Jaccard of
the transaction refs reachable from the issue's evidence vs. the question's
`transactions_examined`, threshold ≥ 0.5 (tunable). Issue wording never
participates in matching.

---

## Cross-cutting rules

- **Discovery owns meaning; rendering owns form.** Content (reasons,
  evidence, category, investigation) is fixed at discovery. `report.py` only
  presents it.
- **Signals never disappear (demotion, not deletion).** A discovery strategy
  may decide a signal doesn't deserve its *own* issue — but it may never drop
  the signal silently. A demoted signal loses issue ownership, not provenance:
  it survives as a `reason` on the issue that owns the transaction. (Learned
  from the baseline's `new_vendor`-vs-`owner_draw` duplicate-issue noise: an
  owner draw is not a new vendor, so `new_vendor` loses its own issue — but the
  "first transaction from this vendor" fact still appears as a reason.)
- **Deterministic logic compiles context; it never gatekeeps.** The profile
  computes signals over *every* transaction and filters nothing out before
  assessment. (See `FOUNDER.md`, AI-usage rule.)
- **Coverage is a first-class output.** Every transaction is either attached
  to ≥1 issue or explicitly assessed-and-cleared. The run reports the
  fraction, because "did it look at everything?" is a trust question we must
  be able to answer.
- **Fixtures are not validation.** Synthetic ledgers + synthetic expected
  issues are engineering fixtures (regression/CI only) and can never yield a
  product-quality number. Only real ledgers + `ReviewerAnnotation`s count.

## Framing (internal vocabulary only — never reviewer-facing)

Each `ReviewIssue` is a **hypothesis**; `reviewer_verdict` confirms or rejects
it. Because a hypothesis is expensive for a senior reviewer to check, the
generator must be **miserly**: precision discipline (Risk 2) outranks
hypothesis fecundity. Design partners see "what deserves your attention" —
never the words "hypothesis" or "confidence".

---

## Changelog

- **v2** — Added the *demotion, not deletion* invariant (signals keep
  provenance as reasons even when they lose issue ownership) and the
  reviewer-only `surprise_class` taxonomy field. Learned: (1) the deterministic
  baseline was *deleting* a suppressed `new_vendor` signal rather than demoting
  it, which loses a fact a reviewer might want — provenance must survive
  suppression; (2) once an LLM strategy can surface issues neither the baseline
  nor ground truth anticipated ("surprises"), we need a place to record a
  reviewer's judgment of them — but only a reviewer's, never the system's.
- **v1** — Initial domain model. Establishes the issue-level contract
  (patterns + traceable evidence as the unit of output, replacing
  flagged-transaction output), the recomputability and provenance invariants,
  and the fixture-vs-validation separation. Learned: reviewers review
  patterns, and transactions are evidence for patterns, not the unit of
  review — so the whole model must center on `ReviewIssue`, not `Transaction`.

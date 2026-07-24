# FOUNDER.md

This file explains how decisions are made in this repository.
`README.md` explains the software. This file explains the judgment behind it.

If a discussion, PR, or AI-assisted session starts drifting toward enterprise
architecture or feature expansion, point back here.

---

## What we are building

An AI Review Copilot for bookkeeping firms: a semantic review assistant that
tells a senior accountant **where to spend attention** in a completed ledger.

The unit of output is the **Review Issue** — a review-worthy *pattern* with
supporting evidence ("why did Amazon jump this month?", "why is R&M 4× normal?"),
not a flagged transaction. Reviewers review patterns; transactions are the
evidence under a pattern, not the unit of review. Each issue explains itself in
terms the reviewer can trace back to their own ledger, and proposes a
verification step — never an accounting treatment. The reviewer decides. The AI
never does. (See `docs/DOMAIN.md` for the full model.)

Mental model: **GitHub Pull Requests for accounting review.**

## What we are NOT building

- An AI bookkeeper
- A close management tool
- An anomaly detection dashboard
- A replacement for professional judgment

## The only four risks

Every line of code must reduce one of these:

1. **Detection** — Can we identify judgment-heavy transactions?
2. **Precision** — Can we keep false positives low?
3. **Trust** — Can we explain our reasoning well enough to earn it?
4. **Adoption** — Will reviewers actually change behavior?

If a proposed feature reduces none of them, the answer is no.
The burden of proof is on the feature, not on the objection.

## The success metric

Not users. Not MRR. Success is a senior reviewer saying:

> "These are exactly the transactions I would have reviewed."

And ideally:

> "I stopped scanning the whole ledger."

## Decision rules

**Features.** Default answer is no. To build something, name the risk it
reduces (1–4 above). "It would be nice" and "customers will eventually need
it" are not risks.

**Architecture.** Prefer simpler, smaller, faster over more scalable.
Boring and readable beats clever. No microservices, no premature
abstractions, no enterprise patterns. Deleting code is better than adding it.

**AI usage.** LLMs reason, explain, prioritize, and summarize. They do not
make accounting decisions. Deterministic logic is a **context-compiler, not a
gatekeeper**: it computes the ledger profile the LLM judges with (per-vendor
history, per-account distributions and deltas, new-vendor flags, journal
clustering, round-number, etc.) and produces *signals*, not verdicts. It does
**not** suppress candidates before assessment. Whether any deterministic signal
graduates to a pre-filter is a later decision we must *earn* with
reviewer-agreement evidence — not assume.

**Prompt engineering.** Prompt engineering is product development and follows
the same discipline as features. The prompt has two layers. **Compliance
instructions** (output schema, ref-citation format, question-phrasing
enforcement) govern *how the model speaks* and may be changed on engineering
evidence. **Judgment content** (what counts as review-worthy, prioritization,
the miserliness framing) governs *what the model thinks* and is **frozen except
in response to reviewer evidence** — no judgment-layer change without new
reviewer-agreement data. The prompt template keeps the two layers visibly
separated (labeled sections) so this rule is mechanically checkable in a diff.

**Pull requests.** Every PR answers "what did we learn?" — not "what did
we ship?"

**Scope.** The MVP is: upload CSV → parse → analyze → review queue with
explanations → reviewer feedback → export. Nothing else.

## Explicitly out of scope (do not re-litigate)

Authentication. Organizations. Billing. Payments. Dashboards. Analytics.
Notifications. Settings. Admin. Permissions. Workflow engines.
Collaboration. Audit trails. Integrations beyond imports. Background jobs.
Production infrastructure.

Any of these may be proposed **only** with a written justification naming
which of the four risks it validates. No risk, no build.

## Working agreement with AI assistants

When asked for a feature, challenge it first: which risk does it remove?
Recommend against building anything that removes none. This is a founder
project optimizing for validated learning, not an enterprise implementation.
The objective is reaching the first design partners with the smallest
product that meaningfully changes a senior reviewer's workflow.

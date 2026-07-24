# Session 2 Findings — LLM Hypothesis-Generation Experiment

**Framing:** this session built the first semantic discovery strategy and the
harness to observe it. Success is a prompt design + output format we're
confident showing a design partner — plus honest answers to the engineering
questions below. Fixture numbers measure the harness, not the product.

**Provider:** the LLM strategy runs on the **OpenAI Responses API** with model
**`gpt-5.5`** (a reasoning model; `reasoning.effort` = `medium`). Everything else
— prompts, JSON schema, validation, repair, provenance, metrics, lab notebook —
is provider-agnostic and unchanged.

**Status of the live numbers.** The first keyed run is **done** (gpt-5.5, on the
committed fixture) and its numbers are folded into sections 3–7 below: 14 issues,
0 dropped-for-provenance, 80% stability, 1 surprise, ~\$1.06 for the 2-run
experiment. All 37 tests still pass offline (mocked client). The command that
produced it is below.

## How to produce the live numbers

```bash
export OPENAI_API_KEY=...              # never committed; .env is gitignored
uv run python data/synthetic/generate.py
# Full experiment: three sets (surprises first), stability, cost, surprises.jsonl
uv run python -m src.experiment \
    --ledger data/synthetic/ledger.csv \
    --annotations data/synthetic/ground_truth.csv \
    --dataset-kind fixture
# Attribution ablation (does reading raw text matter?):
uv run python -m src.experiment ... --no-text
# Either strategy also runs through the normal pipeline, identical output shape:
uv run python -m src.main data/synthetic/ledger.csv --strategy llm
```

---

## 1. Batching shape, and why

**One batch per account** (chunked only if an account exceeds 200 rows; the
fixture's largest is 152, so no chunking). Rationale:

- Reviewers scan **per account** ("why is Repairs & Maintenance high?"), so an
  account is a pattern the model can reason about coherently in one call.
- Row-chunking arbitrary blocks would split a pattern across calls for no
  reason; account grouping keeps each account's activity together.
- The whole-ledger **digest** (below) rides in every call, so a vendor whose
  transactions are split across accounts (e.g. Amazon in Office Supplies *and*
  Computer) is still visible ledger-wide; a **consolidation pass** then merges
  the same vendor's concern raised in two account batches into one issue.

Grouping by **vendor** was the alternative; account won because it matches the
reviewer's scan and the spec's consolidation example ("an Amazon concern seen
in two batches is one issue"). Vendor patterns are preserved via the digest +
consolidation rather than the batch axis.

## 2. Profile context each call carries (measured on the fixture)

20 accounts → **20 batch calls**. Per call:

| Component | ~tokens |
|---|---|
| System prompt (task framing, JSON schema, rules) | ~440 |
| Ledger **digest** (per-account totals/mean/std/monthly deltas, top-15 vendors, JE clusters, signal counts) | ~845 |
| Batch detail (largest account, 152 rows, with text) | ~3,900 |
| **Largest single call (sys + digest + batch)** | **~5,200** |

Ledger-wide: batch detail totals ~13,000 tokens sent once; the digest repeats
across 20 calls (~16,900 tokens). Largest call ~5.2K against gpt-5.5's large
context window — the context ceiling is never the constraint. **Batching is for
reasoning focus and precision, not to fit.** (The repeated digest is the obvious
cost lever: prompt-caching the system+digest prefix would cut most of that
16.9K; deferred this session by the "no caching layer" constraint — noted for
later.)

## 3. Output-schema compliance & dropped-for-provenance

**RESULT (first keyed run, gpt-5.5, `medium` effort):** 14 issues produced,
**0 dropped for provenance, 0 repair calls** — every issue passed schema +
business validity on the **first** try, with all cited refs/accounts real. On
this fixture the model never asserted something it couldn't ground. How it's
measured:

- The model is asked for strict JSON (`{"issues": [...]}`). We **do not** delegate
  correctness to any provider structured-output guarantee (per DOMAIN.md v3, only
  *business* validity matters and it is ours to enforce) — so parsing is tolerant
  (strips fences, extracts the outermost JSON) and
  every issue is **strictly validated in code**: schema shape, category ∈ the
  fixed vocabulary, evidence `kind` ∈ the enum, and **provenance** — every cited
  `ref`/`account` must exist in the ledger, and at least one evidence item must
  cite a transaction ref (no ungrounded issues). The
  `suggested_investigation`-is-a-question rule is enforced in code too (banned
  treatment verbs rejected), not just requested in the prompt.
- A batch with any invalid issue gets **one repair attempt** (the specific
  errors sent back). Still-invalid issues are **dropped and counted** —
  `dropped_for_provenance` is a first-class run metric, because it measures how
  often the model asserts things it can't ground. The experiment summary and the
  `--strategy llm` one-liner both print it.

First-keyed-run answer: 14 issues, **100% passed validation first try, 0
dropped**. The repair path and drop metric are exercised by tests, not by this
run — the model simply didn't need them here.

## 4. Hypothesis stability

**RESULT: 80% (12/15 issues overlap)** across two runs on the identical fixture
ledger (evidence-overlap matching, Jaccard ≥ 0.5). Above the 50% hard-stop, so
**no STOP warning** — but ~1 in 5 issues is run-dependent, a real
hypothesis-stability signal. Good enough to proceed to a calibration session;
not good enough to treat any single run's issue list as canonical. The
interpretation rule is wired in: **if overlap < 50%, the run prints a
STOP-and-flag warning** and we halt before trusting any LLM numbers.

## 5. Cost per 1,000 transactions

**RESULT:** run 1 = **$0.586** (57,831 input + 9,786 output tokens over 20 calls)
= **$1.17 per 1,000 transactions**. The full experiment (2 runs for the stability
check) = **$1.06**. Comfortably inside the estimated range and at its low end:
gpt-5.5 emitted only ~9.8K output tokens (efficient reasoning at `medium`), and
OpenAI auto-cached the repeated system+digest prefix, so effective input was
below the naive per-call sum.

Pricing: `gpt-5.5` = **$5 / $30 per 1M** input/output, cached input **$0.50 =
0.1× input** (exactly what `cost_usd` charges for cache reads), in a config
table so a model swap re-prices automatically. At $1.17/1k transactions a
typical monthly SMB ledger (~500–2,000 txns) costs a few dollars to review —
not a constraint at this stage.

## 6. The surprises

**RESULT: 1 surprise** out of 14 LLM issues, written verbatim to
`surprises.jsonl` with `reviewer_verdict` and `surprise_class` both `null`.
Printed **first** in the run summary; **never** auto-scored as a false positive —
its status is **UNCLASSIFIED until a reviewer judges it**
(`valuable_surprise | valid_alternative_reasoning | noise`).

> **Merchant fees low relative to January sales** — `account_anomaly`
> refs `T0245, T0254, T0265, T0269, T0271, T0276, T0278, T0281`
> - Merchant Account Fees total \$464 in January versus Sales of \$61,982, while
>   February fees are \$1,188 on Sales of \$44,753 and March fees are \$1,201 on
>   Sales of \$69,452.
> - The January fee-to-sales ratio is about 0.75%, compared with about 2.65% in
>   February and 1.73% in March.

This is a **cross-account** hypothesis (Merchant Fees vs Sales, month over
month) — precisely the pattern per-account batching can't see alone, made
visible by the cross-account/monthly vendor+account aggregates we added to the
digest. Whether it is signal (fees miscoded or a January gap) or noise is a
reviewer's call, not ours.

**Abstentions (LLM misses) worth noting**, from the same run:
- The LLM **did not** flag the round **\$10,000 owner capital contribution
  (T0487)** that both the baseline and a reviewer concern caught — a real miss.
- It framed the Office-Supplies anomalies as a per-vendor capitalization
  question (matching the reviewer's capex concern) rather than the baseline's
  account-level grouping — arguably a more precise framing, but it means the
  baseline's `account_anomaly — Office Supplies` issue has no LLM match.

## 7. Honest read: is this ready to show an accounting firm?

**The plumbing is trustworthy; take it to a calibration session, not a demo.**
Still strictly true that only a reviewer can judge whether the *issues* earn
respect — that's what `reviewer_verdict` / `surprise_class` are for — but the
first keyed run cleared every engineering pre-condition I set beforehand:

- **Stability 80%** — over my ~70% "safe to show" bar, well over the 50% hard
  stop. Watch-item, not a blocker: a fifth of the queue shifts run to run, so
  no single run's list is canonical.
- **0 dropped for provenance, 0 repairs** — the strongest result. The
  code-enforced business-validity gate + the prompt produced 14 fully-grounded
  issues first try; nothing hallucinated a ref or prescribed a treatment.
- **1 surprise, and it's the good kind** — a cross-account fee/sales anomaly, a
  hypothesis neither the baseline nor the reviewer questions contained. Exactly
  what a discovery layer is for. A reviewer classifying this one
  `valuable_surprise` or `valid_alternative_reasoning` would be the greenlight
  signal; classifying it `noise` would say tighten miserliness.
- **Cost ~$1/run** — a non-issue at this stage.

What still gives me pause, to raise *with* the reviewer rather than hide:
1. **The \$10,000 owner-capital miss.** A round equity contribution the
   deterministic baseline caught, the LLM dropped. The miserliness framing may
   be trimming legitimately-reviewable round-number/equity movements. This is a
   judgment-layer question — and per the freeze rule it changes **only** on
   reviewer evidence, which a calibration session is exactly how we get.
2. **80% stability** means we should show a reviewer the *union* or a
   multi-run-stable subset, not one arbitrary run, until stability improves.

The format itself renders identically to the baseline through `report.py`, so a
reviewer sees one consistent queue regardless of strategy. Net: **ready to put
in front of a design partner as calibration** (framed per
`design-partner-protocol.md`), with the miss and the stability number named out
loud rather than papered over.

---

## Run metadata (first keyed run)

- **Provider/model:** OpenAI Responses API, `gpt-5.5`, `reasoning.effort=medium`, text on.
- **Fixture:** `data/synthetic/ledger.csv` (500 txns) + `ground_truth.csv` (13 questions), `--dataset-kind fixture`.
- **Totals:** 14 LLM issues · 13 agreements · 1 surprise · 3 abstentions · 0 dropped · 80% stability · \$1.06 (2 runs).
- **Lab notebook:** `runs/20260725-020800/` — one JSON package per call with the git commit + prompt-template hash (gitignored; local only).

*Standing caveat carried into every run summary: fixture numbers measure the
harness, not the product.*

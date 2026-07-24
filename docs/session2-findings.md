# Session 2 Findings — LLM Hypothesis-Generation Experiment

**Framing:** this session built the first semantic discovery strategy and the
harness to observe it. Success is a prompt design + output format we're
confident showing a design partner — plus honest answers to the engineering
questions below. Fixture numbers measure the harness, not the product.

**Provider:** the LLM strategy runs on the **OpenAI Responses API** with model
**`gpt-5.5`** (a reasoning model; `reasoning.effort` = `medium`). Everything else
— prompts, JSON schema, validation, repair, provenance, metrics, lab notebook —
is provider-agnostic and unchanged.

**Status of the live numbers.** The build is complete and all 37 tests pass
offline (the client is mocked, one canned response replayed). The
**live-measured** answers — schema-compliance rate, dropped-for-provenance
count, stability overlap, cost, and the verbatim surprises — require one real
keyed run, which this environment can't make (no `OPENAI_API_KEY`). Each is
marked **PENDING FIRST KEYED RUN** with the exact command to produce it. The
design answers are final now.

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

**PENDING FIRST KEYED RUN.** What's built and how it's measured:

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

The keyed run fills in: issues produced, % that passed validation first try,
and the dropped count.

## 4. Hypothesis stability

**PENDING FIRST KEYED RUN.** The experiment runs the LLM **twice** on the
identical ledger and reports issue-set overlap (same evidence-overlap matching,
Jaccard ≥ 0.5). Interpretation rule is wired in: **if overlap < 50%, the run
prints a STOP-and-flag warning** — per the session brief, catastrophically low
stability halts before we trust any LLM numbers.

## 5. Cost per 1,000 transactions

**PENDING FIRST KEYED RUN** for the measured figure. Pricing used:
`gpt-5.5` = **$5 / $30 per 1M** input/output tokens, with **cached input at
$0.50 = 0.1× input** (exactly what `cost_usd` charges for cache reads), in a
config table so a model swap re-prices automatically.

Order-of-magnitude estimate from the measured token sizes (20 calls, no
repairs): input ≈ (440+845)×20 + 13,000 ≈ **38.6K tokens** → ~$0.19 at full
price (less in practice — OpenAI auto-caches the repeated system+digest prefix
at 0.1×). Output is unmeasured and **reasoning tokens count against it at $30/1M**,
so run cost is dominated by output+reasoning — plausibly **~$0.30–0.80 per
fixture run** at `medium` effort; gpt-5.5 is more token-efficient than prior
models, which pulls the other way. Treat this as a rough floor to be replaced by
the measured number.

## 6. The surprises

**PENDING FIRST KEYED RUN.** Surprises (LLM issues matching neither the baseline
nor any reviewer question) are written verbatim to **`surprises.jsonl`** with
`reviewer_verdict` and `surprise_class` both `null`, and printed **first** in
the run summary. They are **never** auto-scored as false positives — on fixtures
their status is "unclassified until a reviewer sees them." This section gets
pasted from the first keyed run's output.

## 7. Honest read: is this ready to show an accounting firm?

**The harness is ready. The prompt+format's readiness is unknown until the first
keyed run — and, strictly, until a reviewer judges the surprises.** That's not a
hedge; it's the design: on fixtures we can only prove the machinery (parse,
ground, consolidate, rank, render, measure). Whether the *issues* are ones a
senior reviewer respects is exactly what `reviewer_verdict` and `surprise_class`
exist to answer, and only a human fills those.

What I'm confident about now:
- **Format**: issue-level output with plain-English reasons, typed evidence that
  cites real refs, and an interrogative investigation renders identically to the
  baseline through the same `report.py` — a reviewer sees one consistent queue
  regardless of strategy.
- **Safety rails hold in code, not just the prompt**: no ungrounded claims (they
  drop), no accounting-treatment prescriptions (they're rejected), everything
  traces to the reviewer's own ledger.

What would **block** showing a partner, to check on the first keyed run:
1. **Stability < ~70%** — if two runs disagree materially, the queue isn't
   trustworthy yet; fix before any demo. (<50% = hard stop.)
2. **High `dropped_for_provenance`** — frequent ungrounded assertions mean the
   prompt or batch framing needs work before a reviewer sees output.
3. **Surprises that are mostly noise** — if a reviewer classifies most surprises
   as `noise`, the miserliness instruction isn't landing; tighten it.

What would **greenlight** a calibration session (not a demo): stable runs, low
drops, and a handful of surprises at least one of which a reviewer calls
`valuable_surprise` or `valid_alternative_reasoning`. Early partner sessions are
framed as calibration anyway (see `design-partner-protocol.md`), so we don't need
perfection to start — we need trustworthy plumbing and non-embarrassing output,
both of which the first keyed run will confirm or deny.

---

*Standing caveat carried into every run summary: fixture numbers measure the
harness, not the product.*

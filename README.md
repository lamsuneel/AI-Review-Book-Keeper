# AI Review Copilot

Reviews a completed QuickBooks CSV ledger, flags only the transactions that
require accounting judgment (CapEx vs OpEx, owner draws, adjusting entries,
outliers, etc.), explains each in plain English, and produces a ranked Review
Queue so a senior accountant reviews the flagged handful instead of the whole
ledger. Detection is deterministic (rules + statistics); no LLM in this phase.

## Run

```bash
uv run python -m src.main path/to/export.csv
# writes review_queue.md and review_queue.csv
```

Generate a synthetic ledger and measure detection against known ground truth:

```bash
uv run python data/synthetic/generate.py          # writes ledger.csv + ground_truth.csv
uv run python -m src.main data/synthetic/ledger.csv
uv run pytest
```

## A note on synthetic data

The synthetic ledger validates that the **code works** — parsing, detectors,
ranking, export. It does **not** validate precision. False-positive and
detection rates only count against a real anonymized QuickBooks export from a
design partner. Getting one is the founder's top non-coding priority.

## Decision philosophy

See [FOUNDER.md](FOUNDER.md) for how features and architecture decisions are
made here. Read it before proposing changes.

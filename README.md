# AI Review Copilot

Reviews a completed QuickBooks CSV ledger and produces a ranked **Review
Queue** of *issues* — review-worthy patterns (possible capitalization, owner /
related-party activity, unusual account activity, adjusting journal entries,
new large vendors, …), each explained in plain English and backed by evidence a
senior accountant can trace straight back to their own ledger. The reviewer
works the short queue instead of scanning the whole ledger. The reviewer
decides; the system never makes accounting decisions.

The unit of output is the **Review Issue** (pattern + evidence), not the flagged
transaction. See [docs/DOMAIN.md](docs/DOMAIN.md) for the domain model and
[FOUNDER.md](FOUNDER.md) for how decisions are made here.

Detection is deterministic in this phase (rules + statistics compile context;
`discover_issues` turns signals into issues). No LLM yet — that's the next
session, and it will be evaluated against this deterministic baseline as a
permanent control.

## Run

```bash
uv run python -m src.main path/to/export.csv
# writes review_queue.md (top 50 by default) and review_queue.csv (full log)
# --limit N caps the presented queue; the CSV log is always uncapped
```

## Measure the harness (fixtures)

```bash
uv run python data/synthetic/generate.py     # writes ledger.csv + ground_truth.csv
uv run python -m src.score \
    --ledger data/synthetic/ledger.csv \
    --annotations data/synthetic/ground_truth.csv \
    --dataset-kind fixture
uv run pytest
```

## Fixtures are not validation

Synthetic ledger + synthetic reviewer questions are **engineering fixtures** —
regression tests and CI only. They measure that the *harness* works; the boring
rows are authored to avoid the signals and the questions are authored to match,
so no fixture number is a product-quality number. `score.py` requires
`--dataset-kind fixture|validation` and stamps every report with which it is.

Real precision and detection (Risk 1 / Risk 2) count **only** against a real
anonymized QuickBooks export plus real reviewer annotations from a design
partner — see [docs/design-partner-protocol.md](docs/design-partner-protocol.md).
Getting that first real ledger is the founder's top non-coding priority.

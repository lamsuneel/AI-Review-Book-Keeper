# Importers / Adapters

```
Accounting Export
      │
      ▼
Importer / Adapter        ← src/importers/<format>.py   (this layer)
      │
      ▼
Canonical Transaction CSV
      │
      ▼
Existing Review Pipeline  ← ingest → profile → discover → report  (UNCHANGED)
      │
      ▼
Review Queue
```

Foreign export formats are absorbed **only** in the adapter layer. Supporting
another accounting system means **adding another adapter** — never changing the
review engine. This is the one boundary that keeps the pipeline stable as the
number of supported inputs grows.

## The canonical transaction format (the adapter's target)

Every adapter emits a CSV with exactly these columns (read by
[`ingest.py`](../src/ingest.py), which is tolerant of header casing/order):

| Column | Meaning |
|---|---|
| `Date` | transaction date |
| `Transaction Type` | e.g. Check, Bill, Journal Entry (blank if the source lacks it) |
| `Num` | document/transaction number — becomes the stable `ref` |
| `Name` | vendor/payee (blank if the source lacks it) |
| `Memo/Description` | free-text memo |
| `Account` | account name/code |
| `Split` | counter-account (blank if absent) |
| `Amount` | **single signed** number (debits +, credits −; detection uses magnitude) |

## Adapter rules (so canonical/review logic never leaks in)

1. **Standalone.** Stdlib only. An adapter must **not** import from the pipeline
   (`ingest`/`profile`/`issues`/`report`). The canonical column list is
   duplicated in the adapter as a plain constant so the dependency is
   one-directional (adapter → canonical CSV, never adapter → pipeline).
2. **Reshaping only.** No detection, ranking, or judgment. An adapter maps
   columns and nothing else. If you're tempted to "flag" or "score" in an
   adapter, that logic belongs in the pipeline, not here.
3. **One client per Ledger.** If a source file contains multiple entities, the
   adapter splits them into one canonical CSV each (a Ledger is one
   client/period — see [DOMAIN.md](DOMAIN.md) §1).
4. **Lossless where possible.** Fields with no canonical home (e.g. a secondary
   reference) are folded into `Memo/Description` rather than dropped.

## Adapter interface

Each adapter module exposes:

- `matches(columns: list[str]) -> bool` — does this header look like our format?
- `to_canonical(source_path: str, output_dir: str) -> list[str]` — write canonical
  CSV(s), return their paths.
- a `__main__` CLI: `python -m src.importers.<format> <source.csv> <output_dir>`.

## Supported formats (registry)

### Generic GL export v1 — [`src/importers/generic_gl_v1.py`](../src/importers/generic_gl_v1.py)

- **Software:** not identifiable / vendor-neutral. Numbered GL accounts (e.g.
  `1000 Cash`, `6000 Payroll`), generic transaction descriptions, **no vendor
  and no transaction-type fields**. May contain multiple firms in one file.
- **Source columns:** `Firm, Transaction_ID, Date, Account, Debit, Credit,
  Description, Reference`
- **Shape:** one row per double-entry posting line (a single debit *or* credit).
- **Mapping highlights:** `Amount = Debit − Credit`; `Description (+Ref)` →
  `Memo/Description`; `Transaction_ID` → `Num`; split into one CSV per `Firm`;
  `Name`/`Transaction Type`/`Split` blank (absent in source).
- **Known limitation (a data-shape fact, not a bug):** because this format has
  no vendor, no memo content, and no transaction type, the deterministic
  detectors have little to read — a real review needs an export that retains
  those fields (e.g. a QuickBooks "Transaction Detail by Account" report).

  Run:
  ```bash
  uv run python -m src.importers.generic_gl_v1 <source.csv> data/real/canonical
  uv run python -m src.main data/real/canonical/<firm>.csv -o data/real/queues/<firm>
  ```

## Adding a new format

1. Create `src/importers/<format>.py` following the interface above.
2. Add a `tests/test_importers.py` case that maps a tiny sample and asserts the
   canonical output parses via `ingest.load_ledger` (the test may import the
   pipeline; the adapter may not).
3. Register it in this doc.
4. Do **not** touch the review pipeline.

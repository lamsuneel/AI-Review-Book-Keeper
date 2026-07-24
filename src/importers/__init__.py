"""Adapter layer — the only place that knows about foreign export formats.

    Accounting Export → Importer/Adapter → Canonical Transaction CSV →
        Existing Review Pipeline (unchanged) → Review Queue

Each adapter is a standalone module that reshapes ONE source export format into
the canonical transaction CSV the pipeline ingests. Adapters:

  - contain NO review/detection logic, and
  - do NOT import from the pipeline (ingest/profile/issues/report). The canonical
    column contract is the only shared knowledge, and it is duplicated here as a
    plain constant so the boundary stays one-directional.

Adding support for another accounting system means adding another adapter module
here — never changing the review engine. See docs/importers.md for the canonical
contract and the registry of supported formats.

Canonical CSV columns (what every adapter must emit; see ingest.py for the
tolerant reader):
    Date, Transaction Type, Num, Name, Memo/Description, Account, Split, Amount
`Amount` is a single signed number (debits positive, credits negative by
convention; detection uses magnitude).
"""

CANONICAL_HEADER = [
    "Date", "Transaction Type", "Num", "Name",
    "Memo/Description", "Account", "Split", "Amount",
]

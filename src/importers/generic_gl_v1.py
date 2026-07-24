"""Adapter: Generic GL export v1  ->  canonical transaction CSV.

SUPPORTED FORMAT
    Name:      Generic GL export v1
    Software:  Not identifiable / vendor-neutral. Numbered general-ledger
               accounts (e.g. "1000 Cash", "6000 Payroll"), generic transaction
               descriptions, no vendor-level or software-specific fields. May
               contain MULTIPLE entities in one file (a `Firm` column).
    Shape:     One row per double-entry posting LINE (a single debit OR credit).

    Source columns (exact):
        Firm, Transaction_ID, Date, Account, Debit, Credit, Description, Reference

COLUMN MAPPING (source -> canonical)
    Firm              -> (split key: one output CSV per firm; a Ledger is one
                          client/period, and Transaction_ID repeats across firms)
    Date              -> Date            (ISO YYYY-MM-DD, passed through)
    Transaction_ID    -> Num
    Account           -> Account
    Debit, Credit     -> Amount = Debit - Credit   (signed; magnitude is used)
    Description       -> Memo/Description  (+ " · Ref <Reference>" appended)
    (none)            -> Name              (blank — no vendor in source)
    (none)            -> Transaction Type  (blank — absent in source)
    (none)            -> Split             (blank — no counter-account in source)

This module is a STANDALONE adapter: stdlib only, no imports from the review
pipeline, no detection logic. It only reshapes columns.

Usage:
    uv run python -m src.importers.generic_gl_v1 <source.csv> <output_dir>
"""

from __future__ import annotations

import csv
import os
import sys

# Duplicated here (not imported from the pipeline) to keep the adapter -> canonical
# dependency one-directional. See src/importers/__init__.py.
CANONICAL_HEADER = [
    "Date", "Transaction Type", "Num", "Name",
    "Memo/Description", "Account", "Split", "Amount",
]

SOURCE_COLUMNS = ["Firm", "Transaction_ID", "Date", "Account",
                  "Debit", "Credit", "Description", "Reference"]


def matches(columns: list[str]) -> bool:
    """True if a header looks like this format — lets a future dispatcher pick
    the right adapter without guessing."""
    return columns == SOURCE_COLUMNS


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_") or "firm"


def _to_float(value: str) -> float:
    value = (value or "").strip()
    return float(value) if value else 0.0


def canonical_row(r: dict) -> dict:
    """Map one source posting line to a canonical transaction row."""
    amount = _to_float(r["Debit"]) - _to_float(r["Credit"])
    memo = r.get("Description", "")
    ref = (r.get("Reference") or "").strip()
    if ref:
        memo = f"{memo} · Ref {ref}"
    return {
        "Date": r["Date"],
        "Transaction Type": "",   # absent in source
        "Num": r["Transaction_ID"],
        "Name": "",               # no vendor/name in source
        "Memo/Description": memo,
        "Account": r["Account"],
        "Split": "",              # no counter-account in source
        "Amount": f"{amount:.2f}",
    }


def to_canonical(source_path: str, output_dir: str) -> list[str]:
    """Reshape a Generic GL export v1 file into one canonical CSV per firm.

    Returns the list of written canonical CSV paths. Reshaping only — no review
    logic, no pipeline imports."""
    with open(source_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = list(reader)

    if not matches(columns):
        print(f"WARNING: source columns differ from 'Generic GL export v1'.\n"
              f"  expected: {SOURCE_COLUMNS}\n  found:    {columns}", file=sys.stderr)

    by_firm: dict[str, list[dict]] = {}
    for r in rows:
        by_firm.setdefault(r.get("Firm", "firm"), []).append(r)

    os.makedirs(output_dir, exist_ok=True)
    written: list[str] = []
    for firm, frows in by_firm.items():
        path = os.path.join(output_dir, f"{_slug(firm)}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CANONICAL_HEADER)
            writer.writeheader()
            for r in frows:
                writer.writerow(canonical_row(r))
        written.append(path)
        print(f"{firm}: {len(frows)} rows -> {path}")
    return written


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("usage: python -m src.importers.generic_gl_v1 <source.csv> <output_dir>",
              file=sys.stderr)
        return 2
    written = to_canonical(argv[0], argv[1])
    print(f"\nDone. Wrote {len(written)} canonical CSV(s) to {argv[1]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

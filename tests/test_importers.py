"""Adapter layer: reshaping is correct, and the adapter stays decoupled from the
review pipeline (the test may import the pipeline; the adapter may not)."""

import csv
from pathlib import Path

from src.importers import generic_gl_v1 as adapter
from src.ingest import load_ledger

SRC = (
    "Firm,Transaction_ID,Date,Account,Debit,Credit,Description,Reference\n"
    "FirmX,TX1,2025-01-05,1000 Cash,100.00,0.0,Sales Revenue,REF1\n"
    "FirmX,TX2,2025-01-06,2000 Accounts Payable,0.0,250.50,Supplier Invoice,REF2\n"
    "FirmY,TX1,2025-02-01,6000 Payroll,900.00,0.0,Payroll Expense,REF3\n"
)


def _write_src(tmp_path):
    p = tmp_path / "gl.csv"
    p.write_text(SRC, encoding="utf-8")
    return str(p)


def test_matches_recognizes_the_format():
    assert adapter.matches(adapter.SOURCE_COLUMNS)
    assert not adapter.matches(["Date", "Amount"])


def test_maps_signed_amount_and_splits_by_firm(tmp_path):
    out = tmp_path / "canon"
    written = adapter.to_canonical(_write_src(tmp_path), str(out))
    assert len(written) == 2  # one canonical CSV per firm

    rows = list(csv.DictReader(open(out / "FirmX.csv", encoding="utf-8")))
    assert list(rows[0].keys()) == adapter.CANONICAL_HEADER
    assert rows[0]["Amount"] == "100.00"      # debit -> positive
    assert rows[1]["Amount"] == "-250.50"     # credit -> negative
    assert rows[0]["Num"] == "TX1"
    assert rows[0]["Memo/Description"] == "Sales Revenue · Ref REF1"
    # fields absent in source stay blank
    assert rows[0]["Name"] == "" and rows[0]["Transaction Type"] == "" and rows[0]["Split"] == ""


def test_output_parses_through_the_canonical_pipeline(tmp_path):
    out = tmp_path / "canon"
    adapter.to_canonical(_write_src(tmp_path), str(out))
    ledger = load_ledger(str(out / "FirmX.csv"))
    assert [t.amount for t in ledger.transactions] == [100.0, -250.50]
    assert [t.ref for t in ledger.transactions] == ["TX1", "TX2"]


def test_adapter_does_not_import_the_pipeline():
    """Enforce the one-directional boundary in code, not just by convention."""
    text = Path(adapter.__file__).read_text(encoding="utf-8")
    for banned in ("from .ingest", "from .profile", "from .issues", "from .report",
                   "from src.ingest", "from src.profile", "import ingest"):
        assert banned not in text, f"adapter must not import the pipeline: {banned}"

"""Parser edge cases and the Ledger/Transaction model."""

from datetime import date

from src.ingest import Ledger, Transaction, load_transactions, parse_amount, parse_date


def test_parse_amount_variants():
    assert parse_amount("1,200.00") == 1200.0
    assert parse_amount("$3,450.75") == 3450.75
    assert parse_amount("(500.00)") == -500.0  # parentheses = negative
    assert parse_amount("1,200.00-") == -1200.0  # trailing minus
    assert parse_amount("-42") == -42.0
    assert parse_amount("") is None  # blank distinct from zero
    assert parse_amount("   ") is None
    assert parse_amount("n/a") is None
    assert parse_amount("0.00") == 0.0


def test_parse_date_formats():
    assert parse_date("01/14/2025") == date(2025, 1, 14)
    assert parse_date("2025-01-14") == date(2025, 1, 14)
    assert parse_date("1-5-2025") == date(2025, 1, 5)
    assert parse_date("") is None
    assert parse_date("not a date") is None


def _write(tmp_path, text):
    p = tmp_path / "ledger.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_skips_preamble_totals_and_blanks(tmp_path):
    path = _write(
        tmp_path,
        "Acme Coffee, LLC\n"
        "Transaction Detail by Account\n"
        "January 2025\n"
        "\n"
        "Date,Transaction Type,Num,Name,Memo/Description,Account,Split,Amount\n"
        "01/05/2025,Check,101,Vendor A,rent,Rent Expense,Checking,\"1,200.00\"\n"
        "01/06/2025,Deposit,102,Cust B,sale,Sales,Undeposited,\"(500.00)\"\n"
        "Total Rent Expense,,,,,,,\"1,200.00\"\n"
        "\n"
        "01/07/2025,Bill,103,Vendor C,,Utilities,Accounts Payable,300.00\n",
    )
    txns = load_transactions(path)
    assert len(txns) == 3  # preamble, total, blank all skipped
    assert [t.amount for t in txns] == [1200.0, -500.0, 300.0]
    assert [t.ref for t in txns] == ["101", "102", "103"]
    assert txns[0].account == "Rent Expense"


def test_load_carries_forward_group_header_account(tmp_path):
    # Grouped layout: no per-row Account column; account is a header row.
    path = _write(
        tmp_path,
        "Company\nReport\n\n"
        "Date,Transaction Type,Num,Name,Memo/Description,Split,Amount\n"
        "Rent Expense\n"
        "01/05/2025,Check,101,V,m,Checking,100.00\n"
        "01/06/2025,Check,102,V,m,Checking,150.00\n"
        "Utilities\n"
        "01/07/2025,Bill,103,W,m,Accounts Payable,200.00\n",
    )
    txns = load_transactions(path)
    assert [t.account for t in txns] == ["Rent Expense", "Rent Expense", "Utilities"]


def test_ref_falls_back_to_row_id_when_num_missing():
    t = Transaction(txn_id=42, date=None, txn_type="Journal Entry", num="",
                    name="", memo="", account="Depreciation", split="", amount=1.0)
    assert t.ref == "row0042"


def test_ledger_metadata():
    txns = [
        Transaction(0, date(2025, 1, 5), "Check", "1", "A", "", "Rent", "", 100.0),
        Transaction(1, date(2025, 3, 20), "Bill", "2", "B", "", "Utilities", "", 50.0),
    ]
    ledger = Ledger.from_transactions(txns)
    assert ledger.period_start == date(2025, 1, 5)
    assert ledger.period_end == date(2025, 3, 20)
    assert ledger.accounts == ["Rent", "Utilities"]

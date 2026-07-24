"""Parse a QuickBooks-style CSV export into clean transaction records.

QuickBooks "Transaction Detail by Account" exports are messy: a few preamble
rows (company name, report title, date range) sit above the real header, the
account often appears as a group-header row rather than a per-row column,
there are subtotal / "Total ..." rows mixed in, blank separator lines, and
negative amounts are written as parentheses. This module absorbs those quirks
and hands the rest of the pipeline a tidy list of ``Transaction`` records.

Design note (trade-off): the kickoff suggested pandas for CSV handling, but
QuickBooks' ragged preamble (a 1-cell company-name line above an 8-column
table) fights pandas' fixed-column reader. So we read raw rows with the stdlib
``csv`` module — the boring, bulletproof choice for ragged/quoted/blank lines —
and reserve pandas for the per-account statistics in ``rules.py``, where it is
genuinely the right tool. We still do the row interpretation ourselves, because
the structure varies too much to trust header inference.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime

# Canonical field -> header synonyms we might see in a QuickBooks export.
# Matching is case-insensitive and ignores surrounding punctuation/space.
_HEADER_SYNONYMS: dict[str, set[str]] = {
    "date": {"date", "trans date", "transaction date"},
    "txn_type": {"transaction type", "type", "txn type", "trans type"},
    "num": {"num", "number", "no", "ref", "ref number", "doc num"},
    "name": {"name", "vendor", "payee", "customer", "name vendor", "name/vendor"},
    "memo": {"memo", "description", "memo description", "memo/description", "desc"},
    "account": {"account", "acct"},
    "split": {"split", "splits"},
    "amount": {"amount", "amt"},
}

# Row labels that mark structure, not transactions.
_SKIP_PREFIXES = ("total", "beginning balance", "ending balance", "net income")

# Date formats we try, in order. Deterministic on purpose (no fuzzy parsing).
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y")


@dataclass
class Transaction:
    """One normalized transaction line from the ledger."""

    txn_id: int  # stable id = original row position; used for dedup and reference
    date: date | None
    txn_type: str
    num: str
    name: str  # vendor / payee
    memo: str
    account: str
    split: str
    amount: float  # signed; parentheses in the source mean negative
    raw: dict = field(default_factory=dict)  # original cells, kept for transparency

    @property
    def ref(self) -> str:
        """Stable business id (DOMAIN.md §2): the QuickBooks Num when present,
        else a deterministic row id. Everything — issues, evidence,
        annotations — points at transactions by ref."""
        num = self.num.strip()
        return num if num else f"row{self.txn_id:04d}"


@dataclass
class Ledger:
    """A normalized set of transactions for one client/period, plus the
    metadata needed to reason about them (DOMAIN.md §1)."""

    transactions: list[Transaction]
    period_start: date | None
    period_end: date | None
    accounts: list[str]  # unique account names present, sorted

    @classmethod
    def from_transactions(cls, txns: list[Transaction]) -> "Ledger":
        dates = [t.date for t in txns if t.date is not None]
        accounts = sorted({t.account for t in txns if t.account})
        return cls(
            transactions=txns,
            period_start=min(dates) if dates else None,
            period_end=max(dates) if dates else None,
            accounts=accounts,
        )


def parse_amount(value: str) -> float | None:
    """Parse a QuickBooks money string. Parentheses and trailing '-' are negative.

    Returns None when the cell is blank / not a number, so callers can tell a
    real 0.00 apart from an empty cell.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s.endswith("-"):  # trailing-minus style, e.g. "1,200.00-"
        negative = True
        s = s[:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    if s == "":
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negative else val


def parse_date(value: str) -> date | None:
    """Parse a date using a fixed set of common formats. None if unparseable."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_header(cell: str) -> str:
    """Lowercase a header cell and collapse punctuation so synonyms match."""
    return " ".join(str(cell).lower().replace("/", " ").replace(".", " ").split())


def _find_header_row(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Locate the column-header row and map canonical fields -> column index.

    The header is the first row that contains an 'amount'-like column plus at
    least one of date/type/account. Everything above it is report preamble.
    """
    for i, row in enumerate(rows):
        mapping: dict[str, int] = {}
        for col_idx, cell in enumerate(row):
            norm = _normalize_header(cell)
            for field_name, synonyms in _HEADER_SYNONYMS.items():
                if norm in synonyms and field_name not in mapping:
                    mapping[field_name] = col_idx
        if "amount" in mapping and ({"date", "txn_type", "account"} & mapping.keys()):
            return i, mapping
    raise ValueError(
        "Could not find a header row with an 'Amount' column. "
        "Is this a QuickBooks transaction detail export?"
    )


def _cell(row: list[str], idx: int | None) -> str:
    """Safely read a cell by (optional) index, returning '' when absent."""
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def _is_structural(row: list[str]) -> bool:
    """True for subtotal / balance / blank-ish rows that aren't transactions."""
    first = ""
    for cell in row:
        if str(cell).strip():
            first = str(cell).strip().lower()
            break
    return any(first.startswith(prefix) for prefix in _SKIP_PREFIXES)


def load_transactions(path: str) -> list[Transaction]:
    """Read a QuickBooks CSV export and return normalized transactions.

    Tolerates: preamble rows, a group-header account layout (account carried
    forward from a header row), subtotal/total rows, blank lines, and
    parenthesized negatives.
    """
    # utf-8-sig strips a BOM if QuickBooks/Excel added one. csv.reader handles
    # ragged rows, quoted fields, and embedded commas without complaint.
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows: list[list[str]] = [list(r) for r in csv.reader(fh)]

    header_idx, cols = _find_header_row(rows)
    has_account_col = "account" in cols

    transactions: list[Transaction] = []
    current_account = ""  # carried forward in group-header layouts
    txn_id = 0

    for row in rows[header_idx + 1 :]:
        if all(str(c).strip() == "" for c in row):
            continue  # blank separator line
        if _is_structural(row):
            continue  # subtotal / balance row

        amount = parse_amount(_cell(row, cols.get("amount")))

        # A row with no amount is treated as an account group-header: remember
        # it as the current account and move on (it isn't a transaction).
        if amount is None:
            label = _cell(row, cols["account"]) if has_account_col else _cell(row, 0)
            if label:
                current_account = label
            continue

        account = _cell(row, cols.get("account")) or current_account

        transactions.append(
            Transaction(
                txn_id=txn_id,
                date=parse_date(_cell(row, cols.get("date"))),
                txn_type=_cell(row, cols.get("txn_type")),
                num=_cell(row, cols.get("num")),
                name=_cell(row, cols.get("name")),
                memo=_cell(row, cols.get("memo")),
                account=account,
                split=_cell(row, cols.get("split")),
                amount=amount,
                raw={str(i): str(c) for i, c in enumerate(row)},
            )
        )
        txn_id += 1

    return transactions


def load_ledger(path: str) -> Ledger:
    """Load a QuickBooks CSV export into a Ledger (transactions + metadata)."""
    return Ledger.from_transactions(load_transactions(path))

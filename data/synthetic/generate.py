"""Generate a synthetic small-business ledger — an ENGINEERING FIXTURE.

Produces two files next to this script:

  ledger.csv        A QuickBooks-style "Transaction Detail by Account" export:
                    ~500 transactions over Q1 2025, mostly boring, seeded with
                    known judgment-heavy transactions and a couple of deliberate
                    borderline cases. Includes preamble rows, subtotal/"Total"
                    rows, blank lines, comma-formatted amounts, and
                    parenthesized negatives — so it also tests the parser.

  ground_truth.csv  A synthetic annotation sheet in ReviewerAnnotation shape
                    (DOMAIN.md §6): review QUESTIONS, each with the transaction
                    refs a reviewer would examine, and a conclusion. score.py
                    matches our issues to these questions by evidence overlap.

SACRED SEPARATION (FOUNDER ruling): this is a FIXTURE, not validation. The
boring rows are authored to steer clear of the signals and the questions are
authored to match, so any score here measures the HARNESS, not the product.
Product-quality numbers only come from real ledgers + real ReviewerAnnotations.
score.py stamps every fixture report accordingly.

Run:  uv run python data/synthetic/generate.py
"""

from __future__ import annotations

import csv
import os
import random
from datetime import date, timedelta

SEED = 42
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
START = date(2025, 1, 1)
END = date(2025, 3, 31)
TARGET_BORING = 483  # boring rows; + 15 seeded + 2 borderline = ~500

COMPANY = "Bright Ledger Coffee Roasters, LLC"


def money(value: float, *, parens_if_negative: bool = True) -> str:
    """Format like QuickBooks: comma-grouped, parentheses for negatives."""
    if value < 0 and parens_if_negative:
        return f"({abs(value):,.2f})"
    return f"{value:,.2f}"


def rand_date(rng: random.Random) -> date:
    span = (END - START).days
    return START + timedelta(days=rng.randint(0, span))


def month_dates() -> list[date]:
    return [date(2025, m, 1) for m in (1, 2, 3)]


# A ledger row before it's serialized. Amount is a float (signed).
def row(num, d, ttype, name, memo, account, split, amount):
    return {
        "Num": num,
        "Date": d,
        "Transaction Type": ttype,
        "Name": name,
        "Memo/Description": memo,
        "Account": account,
        "Split": split,
        "Amount": amount,
    }


def generate_boring(rng: random.Random) -> list[dict]:
    """Recurring, unremarkable activity. Authored to avoid every detector:
    ambiguous-vendor spend stays < $2,500, one-off vendors stay < $5,000,
    nothing is an exact round $1,000 >= $5,000, no journal entries, no
    owner/related-party language, and amounts are uniform (so nothing lands
    beyond ~1.7 SD — no statistical-outlier false positives)."""
    rows: list[dict] = []
    n = 0

    def next_num() -> str:
        nonlocal n
        n += 1
        return f"T{n:04d}"

    # --- Monthly rent (3) --------------------------------------------------
    for d in month_dates():
        rows.append(
            row(next_num(), d, "Bill Payment", "Prime Property Mgmt",
                "Monthly studio rent", "Rent Expense", "Checking", 4_500.00)
        )

    # --- Biweekly payroll (~6) --------------------------------------------
    d = date(2025, 1, 3)
    while d <= END:
        amt = round(rng.uniform(11_600, 12_400), 2)  # non-round, ~6 entries
        rows.append(
            row(next_num(), d, "Expense", "Gusto Payroll", "Payroll run",
                "Payroll Expenses", "Checking", amt)
        )
        d += timedelta(days=14)

    # --- Monthly SaaS subscriptions (15) ----------------------------------
    saas = [
        ("QuickBooks Online", 90, "Dues & Subscriptions"),
        ("Slack", 120, "Dues & Subscriptions"),
        ("Google Workspace", 72, "Dues & Subscriptions"),
        ("Adobe", 55, "Dues & Subscriptions"),
        ("Shopify", 105, "Dues & Subscriptions"),
    ]
    for d in month_dates():
        for name, base, acct in saas:
            amt = round(base + rng.uniform(-5, 5), 2)
            rows.append(
                row(next_num(), d, "Credit Card Expense", name, "Monthly subscription",
                    acct, "Credit Card", amt)
            )

    # --- Monthly utilities (9) — >=8 entries, exercises outlier baseline ---
    utils = [("City Power & Light", 320, 70), ("Metro Water", 95, 25),
             ("Comcast Business", 150, 10)]
    for d in month_dates():
        for name, base, spread in utils:
            amt = round(base + rng.uniform(-spread, spread), 2)
            rows.append(
                row(next_num(), d, "Bill Payment", name, "Monthly utility",
                    "Utilities", "Checking", amt)
            )

    # --- Frequent COGS / inventory purchases ------------------------------
    suppliers = ["Andes Green Coffee", "Highland Bean Co", "Roaster's Supply"]
    for _ in range(120):
        rows.append(
            row(next_num(), rand_date(rng), "Bill", rng.choice(suppliers),
                "Green coffee purchase", "Cost of Goods Sold", "Accounts Payable",
                round(rng.uniform(400, 1_500), 2))
        )

    # --- Frequent small office supplies (ambiguous vendors, all < $2,500) --
    supply_vendors = ["Amazon", "Staples", "Costco"]
    for _ in range(90):
        rows.append(
            row(next_num(), rand_date(rng), "Credit Card Expense",
                rng.choice(supply_vendors), "Office/shop supplies",
                "Office Supplies", "Credit Card", round(rng.uniform(40, 350), 2))
        )

    # --- Merchant & bank fees ---------------------------------------------
    for _ in range(40):
        rows.append(
            row(next_num(), rand_date(rng), "Expense", "Square", "Card processing fee",
                "Merchant Account Fees", "Checking", round(rng.uniform(15, 120), 2))
        )

    # --- Sales receipts / deposits (income shown as negatives / parens) ----
    for _ in range(140):
        rows.append(
            row(next_num(), rand_date(rng), "Sales Receipt", "Cafe Sales",
                "Daily retail sales", "Sales", "Undeposited Funds",
                -round(rng.uniform(300, 2_200), 2))
        )

    # Trim/pad to roughly TARGET_BORING with extra small supply runs.
    while len(rows) < TARGET_BORING:
        rows.append(
            row(next_num(), rand_date(rng), "Credit Card Expense",
                rng.choice(supply_vendors), "Shop supplies", "Office Supplies",
                "Credit Card", round(rng.uniform(40, 350), 2))
        )
    return rows[:TARGET_BORING]


def generate_seeded(start_index: int) -> tuple[list[dict], list[dict]]:
    """The 15 known judgment-heavy transactions plus 2 deliberate borderline
    cases. Returns (ledger_rows, concerns).

    A `concern` is a synthetic ReviewerAnnotation (DOMAIN.md §6): a review
    QUESTION plus the transaction refs a reviewer would examine, plus a
    conclusion. score.py matches our issues to these by evidence overlap; issue
    wording/category never participate. Concerns are authored as a sensible
    reviewer view — several deliberately DON'T mirror the baseline so the
    harness reports non-zero misses and false positives (proving it discriminates)."""
    n = start_index
    ledger: list[dict] = []

    def add(d, ttype, name, memo, account, split, amount) -> str:
        nonlocal n
        n += 1
        num = f"T{n:04d}"
        ledger.append(row(num, d, ttype, name, memo, account, split, amount))
        return num

    # 1-3. CapEx vs OpEx: large ambiguous-vendor spend on an expense account.
    amazon_monitors = add(date(2025, 1, 14), "Credit Card Expense", "Amazon",
        "Dual 4K monitors + stands", "Office Supplies", "Credit Card", 3_200.00)
    best_buy = add(date(2025, 2, 9), "Credit Card Expense", "Best Buy",
        "Espresso-bar laptop + POS tablet", "Computer & Internet Expenses",
        "Credit Card", 4_800.00)
    home_depot = add(date(2025, 3, 3), "Credit Card Expense", "Home Depot",
        "Cafe build-out shelving & fixtures", "Repairs & Maintenance",
        "Credit Card", 2_900.00)

    # 4-5. Round-number large transactions.
    owner_capital = add(date(2025, 1, 20), "Transfer", "Owner Capital Contribution",
        "Round funding to operating acct", "Owner's Equity", "Checking", 10_000.00)
    equip_rental = add(date(2025, 2, 25), "Check", "Precision Roasting Equipment Rental",
        "Equipment rental deposit", "Equipment Rental", "Checking", 7_000.00)

    # 6-8. Owner / related-party.
    owner_draw = add(date(2025, 1, 31), "Check", "John Bright", "Owner draw - January",
        "Owner's Draw", "Checking", 6_000.00)
    shareholder_loan = add(date(2025, 2, 15), "Deposit", "Bright Family Trust",
        "Loan from shareholder to cover buildout", "Shareholder Loan", "Checking", 15_000.00)
    distribution = add(date(2025, 3, 28), "Check", "John Bright", "Q1 distribution to member",
        "Distributions", "Checking", 5_500.00)

    # 9-10. Adjusting journal entries near period end.
    je_deprec = add(date(2025, 1, 31), "Journal Entry", "", "Monthly depreciation - equipment",
        "Depreciation Expense", "Accumulated Depreciation", 12_000.00)
    je_accrual = add(date(2025, 2, 28), "Journal Entry", "", "Accrue unpaid February wages",
        "Accrued Liabilities", "Payroll Expenses", 3_500.00)

    # 11-12. Statistical outliers on otherwise-stable accounts.
    uline = add(date(2025, 2, 12), "Credit Card Expense", "ULINE",
        "Bulk packaging one-time buy", "Office Supplies", "Credit Card", 2_200.00)
    city_power_spike = add(date(2025, 3, 18), "Bill Payment", "City Power & Light",
        "HVAC surge / equipment repair charge", "Utilities", "Checking", 1_800.00)

    # 13-15. New vendor + large amount (one-off vendors).
    precision_machine = add(date(2025, 2, 6), "Bill", "Precision Machine Works",
        "Roaster drum rebuild", "Repairs & Maintenance", "Accounts Payable", 8_400.00)
    apex_legal = add(date(2025, 2, 21), "Bill", "Apex Legal LLP", "Trademark filing & counsel",
        "Professional Fees", "Accounts Payable", 6_500.00)
    harbor_labor = add(date(2025, 3, 11), "Bill", "Harbor Contract Labor",
        "Seasonal barista staffing", "Contract Labor", "Accounts Payable", 9_200.00)

    # --- Two DELIBERATE borderline transactions, intentionally NOT given a
    # concern, so the baseline surfaces issues that match no reviewer question
    # (false positives) and the harness proves it can see them. --------------
    irs = add(date(2025, 1, 16), "Check", "IRS", "Estimated federal tax payment",
              "Income Tax Expense", "Checking", 5_000.00)  # benign round tax -> extra
    add(date(2025, 3, 22), "Credit Card Expense", "Amazon",
        "Case of receipt paper + cups (bulk)", "Office Supplies", "Credit Card",
        2_650.00)  # benign bulk consumables; ABSORBED into the Amazon/Office issues

    # --- Concerns (synthetic reviewer questions). Note the deliberate
    # imperfections: `account-utilities` is a borderline outlier the baseline
    # misses (false negative), and the two transactions above have no concern
    # (false positives). Fixture, not validation.
    concerns = [
        _concern("Q01", "Are the large Amazon purchases capitalizable equipment?",
                 "capex_vs_opex", [amazon_monitors],
                 "Monitors likely meet the capitalization threshold; verify."),
        _concern("Q02", "Is the Best Buy laptop/POS capitalizable?",
                 "capex_vs_opex", [best_buy], "Computer hardware — likely capitalize."),
        _concern("Q03", "Is the Home Depot build-out capitalizable?",
                 "capex_vs_opex", [home_depot], "Leasehold improvement — likely capitalize."),
        _concern("Q04", "Why the round $10,000 owner capital contribution?",
                 "owner_personal", [owner_capital], "Confirm equity funding is documented."),
        _concern("Q05", "Owner draws / distributions to John Bright this quarter?",
                 "owner_personal", [owner_draw, distribution],
                 "Both are owner distributions; confirm classification."),
        _concern("Q06", "Shareholder loan from Bright Family Trust?",
                 "related_party", [shareholder_loan], "Related-party loan; confirm terms."),
        _concern("Q07", "Depreciation journal entry at period end?",
                 "journal_entry_review", [je_deprec], "Confirm depreciation support."),
        _concern("Q08", "Wage-accrual journal entry at period end?",
                 "journal_entry_review", [je_accrual], "Confirm accrual basis."),
        _concern("Q09", "Why is Office Supplies elevated — the big one-off buys?",
                 "account_anomaly", [amazon_monitors, uline],
                 "Several unusually large Office Supplies entries; review."),
        _concern("Q10", "Why did Utilities spike in March?",
                 "account_anomaly", [city_power_spike],
                 "One-off HVAC/repair charge in Utilities; borderline."),
        _concern("Q11", "New vendor Precision Machine Works, $8,400 to R&M?",
                 "new_vendor", [precision_machine], "First-time vendor; verify capitalization."),
        _concern("Q12", "New vendor Apex Legal, $6,500 legal fees?",
                 "new_vendor", [apex_legal], "First-time professional-fee vendor."),
        _concern("Q13", "New vendor Harbor Contract Labor, $9,200?",
                 "new_vendor", [harbor_labor], "First-time contract-labor vendor."),
    ]

    # Stable identifiers for the seeded imperfections that actually manifest in
    # scoring, so score.py can name them and flag drift. (The Amazon bulk-paper
    # consumable is deliberately ABSORBED — matched via the Amazon capex and
    # Office-Supplies anomaly issues at Jaccard >= 0.5 — so it is not an extra
    # and has no fixture id.)
    fixtures = [
        {"fixture_id": "FIXTURE-001", "kind": "expected_miss", "transaction_ref": city_power_spike,
         "description": "Utilities March variance: borderline outlier (~2.9 SD) the baseline misses."},
        {"fixture_id": "FIXTURE-002", "kind": "expected_extra", "transaction_ref": irs,
         "description": "IRS round $5,000 estimated tax: benign, surfaces as an extra."},
        {"fixture_id": "FIXTURE-003", "kind": "expected_extra", "transaction_ref": equip_rental,
         "description": "Equipment-rental round $7,000: benign new-vendor extra."},
    ]
    return ledger, concerns, fixtures


def _concern(qid: str, question: str, category: str, refs: list[str],
             conclusion: str) -> dict:
    return {"question_id": qid, "question": question, "category": category,
            "refs": refs, "conclusion": conclusion}


def serialize_ledger(rows: list[dict], path: str) -> None:
    """Write the ledger CSV with preamble, subtotal, and blank rows mixed in."""
    header = ["Date", "Transaction Type", "Num", "Name", "Memo/Description",
              "Account", "Split", "Amount"]

    # Sort by date (rows with the same date keep insertion order).
    rows = sorted(rows, key=lambda r: r["Date"])

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        # Report preamble (must be skipped by the parser).
        w.writerow([COMPANY])
        w.writerow(["Transaction Detail by Account"])
        w.writerow(["January 1 - March 31, 2025"])
        w.writerow([])  # blank line
        w.writerow(header)

        for i, r in enumerate(rows):
            w.writerow([
                r["Date"].strftime("%m/%d/%Y"),
                r["Transaction Type"],
                r["Num"],
                r["Name"],
                r["Memo/Description"],
                r["Account"],
                r["Split"],
                money(r["Amount"]),
            ])
            # Sprinkle a couple of structural rows to test tolerance.
            if i == 40:
                w.writerow([])  # blank separator
            if i == 80:
                w.writerow(["Total Rent Expense", "", "", "", "", "", "", "13,500.00"])
            if i == 160:
                w.writerow(["Total Utilities", "", "", "", "", "", "", "3,200.00"])


def serialize_concerns(concerns: list[dict], path: str) -> None:
    """Write the synthetic annotation sheet: one row per (question, ref)."""
    fields = ["question_id", "question", "category", "transaction_ref", "conclusion"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for c in concerns:
            for ref in c["refs"]:
                w.writerow({
                    "question_id": c["question_id"],
                    "question": c["question"],
                    "category": c["category"],
                    "transaction_ref": ref,
                    "conclusion": c["conclusion"],
                })


def serialize_manifest(fixtures: list[dict], path: str) -> None:
    fields = ["fixture_id", "kind", "transaction_ref", "description"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(fixtures)


def main() -> None:
    rng = random.Random(SEED)
    boring = generate_boring(rng)
    seeded, concerns, fixtures = generate_seeded(len(boring))
    all_rows = boring + seeded

    ledger_path = os.path.join(OUT_DIR, "ledger.csv")
    truth_path = os.path.join(OUT_DIR, "ground_truth.csv")
    manifest_path = os.path.join(OUT_DIR, "fixture_manifest.csv")
    serialize_ledger(all_rows, ledger_path)
    serialize_concerns(concerns, truth_path)
    serialize_manifest(fixtures, manifest_path)

    refs = sum(len(c["refs"]) for c in concerns)
    print(f"Wrote {len(all_rows)} transactions to {ledger_path}")
    print(f"Wrote {len(concerns)} reviewer questions ({refs} transaction refs) "
          f"to {truth_path}")
    print(f"Wrote {len(fixtures)} fixture markers to {manifest_path}")


if __name__ == "__main__":
    main()

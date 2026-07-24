# Design-Partner Measurement Protocol

How we turn a design-partner session into the only numbers that count for
Risk 1 (Detection) and Risk 2 (Precision). Fixtures measure the harness; **this**
measures the product (see `docs/DOMAIN.md`, fixtures-vs-validation).

## Why question-led, evidence-based

Reviewers review *patterns*, and they phrase them as questions ("why is Amazon
unusually high this month?"). So ground truth is captured as reviewer
**questions**, each with the transactions they examined — not as a list of
"bad transactions". We score by **evidence overlap**, never by wording: our
issue matches their question iff their transaction sets substantially overlap.
Two people can describe the same concern in different words; they can't examine
different transactions and mean the same thing.

## The session

1. **Give the reviewer a completed month-end ledger** (a real, anonymized
   QuickBooks export from the partner).

2. **Blind annotation first (30–60 min).** Before seeing any output from us, the
   reviewer annotates, in three layers (the `ReviewerAnnotation` shape):
   - **question** — each review question they pursued, in their words.
   - **transactions_examined** — for each question, the transactions they
     looked at. *Mandatory.* Recorded against transaction **refs**.
   - **conclusion** — what they concluded (free text, plus optional
     per-transaction outcomes).

3. **They annotate a sheet WE generate — never the raw QuickBooks file.** A
   `ref` is stable for a given normalized export but **not** across re-exports
   (row order drifts), so we generate the annotation sheet from the *same*
   normalized ledger the system reads. Annotating the raw export would break
   ref matching. (See `docs/DOMAIN.md` §2.)

4. **Run the system** on the same ledger.

5. **Score** with `src/score.py --dataset-kind validation`:
   ```bash
   uv run python -m src.score \
       --ledger <partner_ledger.csv> \
       --annotations <reviewer_annotations.csv> \
       --dataset-kind validation
   ```
   An issue matches a question iff Jaccard overlap of their transaction-ref sets
   ≥ 0.5 (tunable via `--threshold`). Wording and category never participate.

6. **Report**:
   - **Matched** — questions we surfaced (detection / Risk 1).
   - **Extra** — our issues matching no question (false positives / Risk 2).
   - **Missed** — their questions we didn't surface (false negatives / Risk 1).
   - Discussion notes on every disagreement — these are the learning.

## Constraints

- **Presentation is capped** (`--limit`, default 50). The reviewer sees the top
  ranked issues; the full issue log is always written uncapped to
  `review_queue.csv` for our analysis.
- **Early sessions are calibration, not demos.** Frame them as "help us learn
  what deserves your attention," not "look what we built."
- **Fixture numbers never appear in a validation report.** They live in CI.

## Triage vs. stored classification (surprises)

During a session, each **surprise** (an LLM issue matching neither the baseline
nor any reviewer question) gets a live **TRIAGE** reaction on the session
worksheet — `agrees immediately | disagrees immediately | needs discussion` — to
keep the session moving. Triage is **transient session data**, not the record.
The **stored** classification is the single DOMAIN.md taxonomy
(`valuable_surprise | valid_alternative_reasoning | noise`), assigned *after*
discussion. Every `needs discussion` item must resolve to a stored class before
the session ends; the taxonomy is what we keep, triage is scaffolding we discard.

## The annotation CSV

One row per (question, examined transaction):

```
question_id, question, category, transaction_ref, conclusion
Q01, "Why is Amazon spend high this month?", capex_vs_opex, T0484, "Monitors likely capitalizable"
Q01, "Why is Amazon spend high this month?", capex_vs_opex, T0611, "Monitors likely capitalizable"
Q02, ...
```

`category` is the reviewer's own bucket and is recorded for discussion only —
it does not affect scoring. `data/synthetic/ground_truth.csv` is a synthetic
example of exactly this format (a fixture, not validation).

# PROJECT BUILD — Loan-Level Portfolio Monitor (full monitoring set, Freddie Mac real data)

**For:** Claude Code
**Repo:** a NEW repo — suggested name `portfolio-monitor-loanlevel`
**Dataset:** Freddie Mac Single-Family Loan-Level Dataset (SFLLD) monthly performance — real
loan-level data with **monthly status over time** (you already have the 2007 / 2008 / 2015 samples).

This is the **full monitoring set**: monthly transition/migration matrices, roll rates, IFRS 9
stage movements, early warning, and vintage tracking. Its strength is the *monthly panel* the SBA
monitor can't provide. Keep that boundary — don't redo SBA's commercial-concentration work here.

---

## 0. Golden rules
- **Real public data only.** No synthetic data.
- **Self-contained.** Real monthly loan data in → monitoring outputs out.
- **Interpretable.** Pandas + clear aggregations; the transition logic must be easy to inspect.
- **HR-friendly.** Plain-English summary at the top of each notebook; one-line term explanations.
- **One clean results table per notebook**, saved to `outputs/`.
- **Framing:** demonstrated on real loan-level mortgage data; state plainly that the monitoring
  *mechanics* apply equally to commercial portfolios.
- If anything is ambiguous, ask me one question.

## 1. Data — Freddie Mac SFLLD
- Reuse the 2007 / 2008 / 2015 sample vintages from the mortgage project (origination + monthly
  performance files). The **monthly performance file** is the key input — it has each loan's
  delinquency status per month, which is the basis for transitions.
- **Compliance:** do NOT commit the raw Freddie Mac data (redistribution restricted). Gitignore it;
  commit only output snapshots, charts, and the report.

## 2. Key definitions (state each plainly in the notebook)
- **Delinquency buckets** from current loan delinquency status: Current (0), 30, 60, 90+, and a
  terminal Default / cure state (use the zero-balance / credit-event codes for default).
- **IFRS 9 staging:** Stage 1 = performing; **Stage 2** = significant increase in credit risk
  (e.g. 30+ days past due as the backstop trigger); **Stage 3** = default (90/180+ DPD or a
  credit-event zero-balance code). State the exact triggers.
- **Transition matrix** = the probability of moving from each bucket/stage in one period to each
  bucket/stage in the next (rows sum to 1). Choose a periodicity (monthly or quarterly) and state it.
- **Roll rate** = the share of a worse-bucket move (e.g. 30→60, 60→90).
- **Vintage** = origination-year cohort.

## 3. What it produces (the full monitoring set)
- **Transition / migration matrices** — both delinquency-bucket and IFRS 9 stage versions
  (period-over-period). A heatmap of the matrix is the headline visual.
- **Roll rates** between buckets (30→60, 60→90, etc.).
- **IFRS 9 stage movements** — period-over-period stage classification and a stage-movement summary
  (how many loans moved 1→2, 2→3, 2→1 cure, etc.).
- **Early-warning view** — loans deteriorating (entering Stage 2 / rising DPD); a watchlist table.
- **Vintage tracking** — cumulative default/delinquency by months-on-book per vintage; the
  2007/2008 (downturn) vs 2015 (calm) contrast is a strong story.
- **Concentration** — by state/geography and vintage.

## 4. Build steps (notebooks)
- **00 — Load & assemble:** join origination + monthly performance; derive delinquency bucket and
  IFRS 9 stage per loan-month; data-quality summary.
- **01 — Loan-month panel:** the base table (one row per loan per month) used for all transitions.
- **02 — Transition matrices & roll rates:** bucket-level and stage-level matrices + roll rates +
  a transition heatmap chart.
- **03 — IFRS 9 stage movements:** period-over-period stage classification + stage-movement summary.
- **04 — Early warning & watchlist:** flag deteriorating loans; watchlist table.
- **05 — Vintage tracking:** cumulative deterioration by months-on-book, 2007 vs 2008 vs 2015 + charts.
- **06 — Concentration & report:** concentration by geography/vintage; a short monitoring-pack report
  (md/html). Any APS 330-style table labelled "APS 330-style disclosure format," not regulatory.

## 5. Repo practices
- gitignore the raw Freddie Mac data; commit output snapshots + charts + report.
- README: a "See it in 30 seconds" link block; a "What this produces" section with a sample
  transition matrix + heatmap and a stage-movement summary read from real outputs; a
  "Data sources & provenance" section; and a **prominent cross-link to the mortgage repo** —
  "these are the same loans modelled there: model the portfolio → monitor it over time."
- Topics: `portfolio-monitoring`, `transition-matrix`, `ifrs9`, `early-warning`, `credit-risk`, `python`.
- Real-data-only; no synthetic-data topic.

## 6. One-line message (for the README)
> Loan-level portfolio monitoring on real Freddie Mac data — monthly transition/migration matrices,
> roll rates, IFRS 9 stage movements, early-warning watchlist, and vintage tracking (downturn vs
> calm). The monitoring layer on top of the mortgage modelling project.

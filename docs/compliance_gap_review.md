# Mortgage Portfolio Monitoring — Compliance Gap Review

**Subject project:** `mortgage-portfolio-monitoring` (the monitoring layer). The sister
model `freddie mac mortgage` (PD/LGD/EAD) is referenced only where the monitoring
framework depends on it (Layers 4–5).

**Benchmark:** *Portfolio Monitoring — How to Build a Credit Portfolio Monitoring
Programme* training guide, which consolidates APRA **APS 220 / APG 220 / APS 113 /
APG 113 / APS 330** and the **Basel Framework (CRE36, BCP 17)**.

**Date:** 2026-06-25

**Framing:** This project repeatedly and correctly states it is an *illustrative
demonstration, not a regulatory submission*. The gaps below are therefore written as
"what a production / compliant programme additionally requires," each with the exact
guidance section and a recommended fix. Items already acknowledged in the README's
Limitations are marked **(disclosed)**.

---

## What is already well covered (not re-raised as gaps)

Risk-appetite table with amber/red, owner, breach action, review cycle, leading/lagging
tag and citation (Step 1/2); RAG Board dashboard (Step 3/12); leading indicators tracked
over time — roll rates, SICR, Stage 2 (Step 4); bucket + IFRS 9 transition matrices and
vintage curves; early-warning watchlist (Step 8); geographic HHI + high-LVR concentration
(Step 9); modification cure/re-default + collections surge multiple (Step 8 / §4.6 / §4.7);
stress→limits link (Step 10); PSI + realised-default-by-grade backtest feed (Layer 4);
reporting-cadence note (Step 12); data-quality check and loan-ID masking.

---

## Gap register (summary)

| # | Area | Guidance section | Severity | Status |
|---|------|------------------|----------|--------|
| 1 | No daily monitoring of facility amounts / limits | Step 7; APS 113 Att. D EAD ¶6; Basel CRE36.92 | **Material** | **Addressed** — limit-utilisation/headroom view + daily layer documented in governance.md |
| 2 | Collateral monitored at *original* LVR only — no current/indexed LVR | §4.2; Basel CRE36.140 | **Material** | **Fixed** — current/indexed-LVR view (HPI proxy) + RAG metric |
| 3 | Investor/owner-occ, interest-only, loan-purpose product concentration absent | Step 9; APS 220 ¶35; §5.1 | **Material** | **Fixed** — investor + purpose concentration added |
| 4 | No override / exception monitoring | Step 3 (MI table); §6.2 | **Material** | **Open** — needs an origination exceptions log (not in SFLLD) |
| 5 | No provision-coverage / ECL / capital MI | Step 3; Step 4; APG 220 ¶67(b) | Moderate | **Fixed** — ECL, EL-rate & NPL coverage + RAG metrics |
| 6 | Independence of monitoring + credit-risk control unit not established | Step 6 / §4.1; Basel CRE36.57 | Moderate | **Fixed** — owners reassigned to 2nd-line + governance.md |
| 7 | Monitoring framework not actually validated (8-element) | Step 11; APG 113 ¶140 | Moderate | **Fixed** — predictiveness test + validation.md |
| 8 | Risk-appetite statement incomplete vs APS 220 ¶20 | Step 1; APS 220 ¶20 | Moderate | **Fixed** — appetite/tolerance, review date, loss-rate metric |
| 9 | Early-warning *workflow* not formalised (aggregate watchlist only) | Step 8; APS 220 ¶33; APG 220 ¶63 | Moderate | **Fixed** — per-loan triggers + workflow table |
| 10 | Third-party / broker (channel) monitoring absent | §4.5; APS 220 ¶39; APG 220 ¶307–308 | Moderate | **Fixed** — channel performance view added |
| 11 | Stress test is a flat ad-hoc 3× multiplier; stress model not validated | Step 10; APS 220 ¶73/76 | Moderate | **Fixed** — per-metric multipliers, 2 scenarios, validation note |
| 12 | Hardship-specific metrics incomplete | §4.6; APG 220 ¶68 | Minor | **Fixed** — ultimate loss + new-concession trend |
| 13 | Default definition lacks "unlikely-to-pay" | APS 220 default definition; §6.2 | Minor | **Fixed** — UTP overlay (documented as illustrative) |
| 14 | Single-name / large-exposure concentration absent | Step 9; APG 220 ¶77–80 | Minor (part-N/A) | **Fixed** — top-N view (immaterial, reported) |
| 15 | Rating-refresh cadence not addressed | Step 5; Basel CRE36.41 | Minor (cross-project) | **Addressed** — PSI→refresh trigger documented in governance.md |
| 16 | Governance/reporting cadence + country/receivables scope incomplete | Step 12; §4.3 / §4.4 | Minor | **Fixed** — cadence table + scope exclusions in governance.md |

---

## Material findings

### 1. No daily monitoring of facility amounts and limits
**Guidance:** Step 7 — APS 113 Attachment D (EAD) ¶6: *"systems and procedures … to
monitor, on a daily basis, facility amounts, outstanding amounts against committed lines
and changes in outstanding amounts…"*; Basel CRE36.92: *"monitor outstanding balances on
a daily basis."*
**Current state:** The entire engine is built on a **monthly** loan-month panel
(`src/monitor.py`, `build_panel`). There is no daily layer, no limit-excess /
available-undrawn tracking, and no top-N borrower daily exposure view.
**Fix:** State explicitly that daily facility/limit monitoring is a separate operational
layer out of scope for this monthly analytics demo, *or* add a daily limit-excess /
arrears-flow stub. At minimum, document the cadence gap against Step 7.

### 2. Collateral monitored at origination LVR only — no current / indexed LVR
**Guidance:** §4.2 — Basel CRE36.140 requires a *"continuous monitoring process … for the
specific exposures … attributable to the collateral."* For mortgages the live metric is
**current / indexed LVR**, not origination LVR.
**Current state:** Every LVR view uses `ltv` = **original** LTV
(`HIGH_LVR_CUT`, `stock_metrics`, `lvr_band`). Current UPB is available but is never
combined with an indexed property value to produce a marked-to-market LVR.
**Fix:** Add a current-LVR (indexed) concentration view — index `orig_upb`→`cur_upb`
against a house-price path (even a simple regional HPI proxy) and re-band; re-tie the
`high_lvr_share` appetite metric to current LVR or add it as a second metric.

### 3. Higher-risk *product* concentration absent — fields loaded but unused — **FIXED**
**Guidance:** Step 9 + APS 220 ¶35(b) — prudent limits on *"higher risk credit products
and activities"*; worked example §5.1 lists **investor mortgages**, **interest-only**, and
**high-LVR new flow** as standard mortgage appetite metrics.
**Original state:** `occupancy` (P/I/S), `loan_purpose` (P/C/N) and `prop_type` were
loaded but never used; no investor-mortgage or cash-out-refi concentration.
**Fix applied:** Added `category_concentration()` to the engine and wired notebook 06 to
produce **investor / owner-occupier concentration** (`06_concentration_occupancy.csv`) and
**loan-purpose concentration** (`06_concentration_purpose.csv`), each with exposure share
and point-in-time 90+ rate, surfaced in the monitoring pack §8. Interest-only is not a
field in the SFLLD sample, so it remains out of scope (noted).

### 4. No override / exception monitoring
**Guidance:** Step 3 MI table (dedicated "Override / exception" row) and §6.2 escalation
checklist ("Trend in … override rate"); worked example §5.2 carries an override-rate line.
**Current state:** No override, exception, or policy-waiver metric anywhere.
**Fix:** Add an override/exception-rate appetite metric (placeholder fed from an
exceptions log), or document that origination-override MI sits in the origination system —
but the guidance treats it as a core leading indicator, so the omission should be flagged.

---

## Moderate findings

### 5. No provision-coverage / ECL / capital MI
**Guidance:** Step 3 (Capital & provisions MI) and APG 220 ¶67(b) — **provision coverage
ratios** are a required forward-looking indicator; §6.2 flags coverage-ratio drift.
**Current state:** Stage *movements* and Stage 2/3 *exposure* are shown, but no
ECL/provision balance, no coverage ratio, no RWA/CET1.
**Fix:** Add a provision-coverage proxy (stage-weighted ECL = Stage 2/3 exposure ×
illustrative coverage rates) and a coverage-ratio trend metric.

### 6. Independence of monitoring & credit-risk control unit not established
**Guidance:** Step 6 / §4.1 — Basel CRE36.57: an *independent* credit-risk control unit,
*functionally independent from … originating exposures.*
**Current state:** The governance note covers reporting cadence but not independence; the
high-LVR appetite metric is even owned by "Head of Mortgage Lending" (the originating
function).
**Fix:** Add a governance statement placing monitoring/appetite ownership in an
independent second-line credit-risk function, and reassign the high-LVR metric owner.

### 7. Monitoring framework not actually validated
**Guidance:** Step 11 — APG 113 ¶140 eight-element validation framework; APG 220 ¶64
"robust data, subject to regular validation."
**Current state:** §11 says the framework "would be independently validated annually" —
aspirational only. No evidence any indicator is *predictive* (element 3), no threshold
back-testing.
**Fix:** Add a short validation section demonstrating element 3 (e.g. show a rising
30→60 roll rate / Stage-2 share *precedes* realised default in the vintage curves) and
element 2 (data-quality reconciliation). Keep the rest as a documented annual plan.

### 8. Risk-appetite statement incomplete vs APS 220 ¶20
**Guidance:** Step 1 — APS 220 ¶20 requires the statement to articulate **appetite *and*
tolerance** as distinct levels, the process for setting tolerances, the monitoring
process, breach action, and the **review timing of the appetite itself**.
**Current state:** `config/risk_appetite.yaml` gives per-metric amber/red + actions +
review cycle (good), but no explicit appetite-vs-tolerance distinction, no overall
loss-rate (bps) or RWA-growth appetite, and no review cadence for the appetite document.
**Fix:** Add a header block defining "appetite" vs "tolerance," an annual appetite-review
date, and a portfolio loss-rate metric.

### 9. Early-warning *workflow* not formalised
**Guidance:** Step 8 — APS 220 ¶33 / APG 220 ¶63: early-identification policies with
*criteria for identifying and reporting potential problem exposures*; the guidance gives a
6-stage workflow (watch → heightened → pre-default → default → workout → charge-off).
**Current state:** The watchlist is an aggregate Stage-2/3 count-by-vintage table; the
loan-level `04_watchlist.csv` exists but has no per-stage triggers, owners, actions, or
escalation timeframes.
**Fix:** Add a trigger→action→owner→timeframe table mirroring the Step 8 workflow and
attach the trigger to the loan-level watchlist rows.

### 10. Third-party / broker (channel) monitoring absent — **FIXED**
**Guidance:** §4.5 — APS 220 ¶39 + APG 220 ¶307–308: third-party-originated exposures need
*enhanced monitoring* (performance by introducer, application quality, early arrears).
**Original state:** The SFLLD origination record carries an acquisition-**channel** field
(Retail / Broker / Correspondent / TPO, field 13) and seller/servicer names (fields 23/24),
none of which were loaded.
**Fix applied:** Added `channel` to the loader (`ORIG_USE`), a `CHANNEL_LABEL` map, and a
`channel_performance()` engine function combining lifetime ever-90+/default with current
active-book exposure share. Wired into notebook 06 (`06_channel_performance.csv`) and
surfaced in the monitoring pack §8, contrasting retail vs broker/correspondent/TPO
performance.

### 11. Stress test is an ad-hoc flat multiplier and is itself unvalidated
**Guidance:** Step 10 — APS 220 ¶73 (stress feeds limits) and ¶76 (*"stress testing models
must be appropriately validated and checked independently"*).
**Current state:** A single `downturn_multiplier: 3.0` applied to four flow metrics; a
uniform scalar (not a scenario), no concentration/LVR stress, no validation.
**Fix:** Derive the multiplier per-metric from the realised 2007-vs-2015 ratio for *that*
metric (the data supports metric-specific severities), or add a second scenario; add an
independent-validation note per ¶76.

---

## Minor findings

### 12. Hardship-specific metrics incomplete
**Guidance:** §4.6 — APG 220 ¶68: number/$ of **new requests** by product, **approval
rates**, cure rates, **provisions on the concession book**, and **ultimate loss rates**.
**Current state:** §9 shows modification cure/re-default and exposure; missing new-request
volume, approval rate, provisions-on-concessions, ultimate loss rate.
**Fix:** Add new-modification counts per period (the `mod_flag` timeline supports this) and
a concession-book loss rate.

### 13. Default definition lacks "unlikely-to-pay"
**Guidance:** Step 8 / §6.2 — APRA default = *90 DPD **or** unlikely-to-pay*.
**Current state:** Stage 3 / default is purely 90+ DPD or a credit-event zero-balance code;
no UTP qualitative trigger.
**Fix:** Document that only the 90-DPD backstop is implemented; the `mod_flag` could seed a
basic UTP overlay.

### 14. Single-name / large-exposure concentration absent
**Guidance:** Step 9 / APG 220 ¶77–80 — single borrower and top-10/20 counterparty
concentration.
**Current state:** None. Largely **N/A** for a granular retail mortgage pool.
**Fix:** Add one sentence scoping it out as immaterial for retail mortgages, rather than
silently omitting it.

### 15. Rating-refresh cadence not addressed
**Guidance:** Step 5 — Basel CRE36.41 annual rating refresh.
**Current state:** Belongs to the sister model; the monitor provides PSI but no
rating-refresh trigger.
**Fix:** Cross-reference: state that rating refresh lives in the `freddie mac mortgage`
model and that a PSI breach should *trigger* a refresh — closing the Layer-4 loop.

### 16. Governance / reporting completeness + scope notes
**Guidance:** Step 12 (forums incl. Audit Committee; front-line daily/weekly) and §4.3/4.4
(purchased receivables; country/transfer risk).
**Current state:** Cadence covers Credit Risk Committee / Board Risk Committee / model
governance, but no Audit Committee, no front-line cadence; country-risk (US-only) and
purchased-receivables not scoped.
**Fix:** Add Audit Committee + front-line rows to the cadence table and one line each
scoping out country-risk and seller/servicer-receivables monitoring as N/A.

---

## Data-integrity caveat (not a guidance violation)

The point-in-time stock metrics (NPL ratio, Stage-2 share, top-state share) are computed
as-of the latest reporting month, at which point the book is almost entirely **surviving
2015-vintage loans** — the 2007/08 crisis cohorts have largely terminated. So the all-GREEN
dashboard reflects the calm survivors, while crisis severity only shows in the
vintage/stress sections. This is consistent with the README's "thresholds not fitted to
this sample" disclosure **(disclosed)**, but a one-line note on as-of book composition
under pack §1 would prevent misreading.

---

## Summary of fixes applied

All 16 items have now been built out except **#4** (override/exception MI), which requires
an origination exceptions log that is not present in the SFLLD dataset. Every fix is wired
through the same reproducible pipeline (engine → `make_notebooks.py` → executed notebooks →
committed tables → Board pack) and surfaced in [outputs/reports/monitoring_pack.md](../outputs/reports/monitoring_pack.md).

| # | Fix | Engine (`src/monitor.py`) | Output / location |
|---|-----|---------------------------|-------------------|
| 1 | Limit utilisation/headroom + daily layer | `limit_utilisation()` | `07_limit_utilisation.csv`; pack §1c; `governance.md` §2 |
| 2 | Current/indexed LVR | `current_lvr_series()`, `current_lvr_concentration()`, `HPI_BY_YEAR` | `06_concentration_current_lvr.csv`; `current_high_lvr_share` RAG metric; pack §8 |
| 3 | Investor/purpose product concentration | `category_concentration()` | `06_concentration_occupancy.csv`, `06_concentration_purpose.csv`; pack §8 |
| 5 | ECL / provision coverage | `ecl_provisions()`, `ecl_table()` | `07_ecl_provisions.csv`; `loss_rate` + `provision_coverage` RAG metrics; pack §1b |
| 6 | Independence / control unit | owners reassigned in config | `governance.md` §1 |
| 7 | Validation (8-element + predictiveness) | `sicr_predictiveness()` | `09_validation_predictiveness.csv`; pack §10; `validation.md` |
| 8 | Appetite vs tolerance, review date | (config) | `risk_appetite.yaml` `appetite_statement`; pack §1 |
| 9 | Early-warning workflow + triggers | (nb04 + config) | `04_watchlist_triggers.csv`, `04_watchlist_workflow.csv`; pack §6 |
| 10 | Broker/channel monitoring | `channel_performance()` | `06_channel_performance.csv`; pack §8 |
| 11 | Per-metric stress, 2 scenarios | `stress_table()` | `07_stress_vs_limits.csv`; `risk_appetite.yaml` `stress.scenarios`; pack §11 |
| 12 | Hardship ultimate loss + new-request trend | `hardship_summary()`, `new_concessions_by_year()` | `08_hardship_summary.csv`, `08_new_concessions_by_year.csv`; pack §9 |
| 13 | UTP overlay | `utp_overlay()` | `08_utp_overlay.csv`; pack §9 |
| 14 | Single-name top-N | `topn_concentration()` | `06_concentration_topn.csv`; pack §8 |
| 15 | Rating-refresh cross-reference | — | `governance.md` §4 (PSI→refresh trigger) |
| 16 | Cadence + country/receivables scope | — | `governance.md` §3, §5 |

**Selected results (illustrative, from the regenerated pack):**
- Current/indexed high-LVR share **0.0%** vs original-LVR **13.0%** — 2015 survivors de-levered as HPI rose (the point of marking collateral to market).
- ECL **$6.8m**, EL rate **39.3 bps**, NPL coverage **45.8%** — all GREEN vs appetite.
- SICR predictiveness: loans entering Stage 2 by 12 months default at **59.6%** vs **6.8%** otherwise (**~8.8× lift**) — element-3 validation evidence.
- Severe-downturn stress pushes the EL rate and both roll rates to **RED**, NPL and Stage 2 to **AMBER**.

**Only remaining open item:** #4 override/exception MI — add an origination/rating
exceptions feed, then an override-rate appetite metric, to close it.

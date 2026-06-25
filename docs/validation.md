# Monitoring Framework Validation (APG 113 para 140 — 8 elements)

Applies APRA's eight-element validation framework (APG 113 ¶140; APS 220 ¶64) to the
**monitoring framework itself** — not the PD/LGD/EAD model (that is validated in the
sister project). Closes gap #7 in [compliance_gap_review.md](compliance_gap_review.md).
Demo documentation — illustrative, **not a regulatory submission**.

---

## The 8 elements applied to this monitor

| # | Element | Monitoring-specific application | Evidence in this repo | Status |
|---|---------|--------------------------------|----------------------|--------|
| 1 | Design & construction | Logic of MI, choice of indicators, alignment to appetite | `src/monitor.py` (bucket/stage/transition logic); thresholds in `config/risk_appetite.yaml`; leading-vs-lagging tags | ✅ |
| 2 | Data quality | Source reconciliation; missing/outlier handling | `outputs/tables/00_data_quality.csv` (loan/loan-month counts, % status unknown = 0.0); pipe-layout applied by position | ✅ |
| 3 | Performance (predictiveness) | Indicators actually precede subsequent losses | **SICR predictiveness test** (pack §10 / `09_validation_predictiveness.csv`): loans entering Stage 2 by 12m default far more often than those that don't | ✅ |
| 4 | Conservative adjustments | Watch thresholds appropriately sensitive | Amber set below red as an early-warning band; 30+ DPD SICR backstop (conservative vs a PD-only trigger) | ✅ |
| 5 | Implementation | Dashboards work; feeds reliable; sign-off | Reproducible pipeline (`make_notebooks.py` → executed notebooks → committed tables); CI-style regeneration | ✅ |
| 6 | Use (use test) | Senior management actually uses the MI | Board-style pack opens with RAG dashboard + actions table; appetite breaches route to named owners/forums | ◻ Demo |
| 7 | Documentation | Policies & procedures current | README + `docs/governance.md` + this file + inline notebook narrative | ✅ |
| 8 | Management reporting | Right information reaches the Board on time | Cadence table in `docs/governance.md`; pack structured for the Board Risk Committee | ◻ Demo |

✅ = demonstrated in-repo · ◻ Demo = scaffold/illustrative (would be operationalised in production)

---

## Element 3 in detail — predictiveness (the key validation test)

A monitoring indicator earns its place only if it **leads** losses. The SICR (Stage 2)
trigger is tested directly: split loans by whether they entered Stage 2 within their
first 12 months on book, then compare eventual default (ever reaching Stage 3).

See the **`09_validation_predictiveness.csv`** table and the monitoring pack §10 for the
realised numbers (regenerated from data). The expected and observed pattern is a large
default-rate **lift** for the early-Stage-2 group — confirming the watch trigger is
forward-looking, consistent with APG 220 ¶66 (do not rely solely on lagging arrears).

Supporting evidence elsewhere in the pack:
- **Vintage curves (§7)** — cohorts with worse early roll/Stage-2 readings (2007/08) go on
  to far higher cumulative default than the calm 2015 cohort: the leading signal and the
  lagging outcome rank consistently.
- **Transition matrix (§3)** — deterioration probabilities rise monotonically with
  delinquency bucket, the mechanism behind the roll-rate early warnings.

---

## Validation cadence & independence

- The monitoring framework would be **independently validated at least annually** by Model
  Validation / Internal Audit (2nd/3rd line), separate from the team that runs it.
- **Stress multipliers** (`config/risk_appetite.yaml` → `stress`) are illustrative and
  would be independently validated before use in limit setting (APS 220 ¶76).
- Re-validation is triggered out of cycle by: a PSI breach (population drift), a material
  data-quality issue, or a structural change in the book.

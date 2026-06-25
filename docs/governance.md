# Monitoring Governance — independence, cadence, daily layer & scope

Governance wrapper for the loan-level portfolio monitor. Closes the governance gaps
in [compliance_gap_review.md](compliance_gap_review.md) (#1, #6, #15, #16). Demo
documentation — illustrative, **not a regulatory submission**.

---

## 1. Independence of monitoring from origination (Basel CRE36.57; APS 220 para 28)

Portfolio monitoring, the risk-appetite limits, and the RAG dashboard are owned by an
**independent 2nd-line Credit Risk function**, functionally separate from the mortgage
origination business. This mirrors Basel CRE36.57's requirement for an *independent
credit risk control unit … functionally independent from the personnel and management
functions responsible for originating exposures.*

| Responsibility | Owner | Line |
|---|---|---|
| Design of MI, indicators, appetite thresholds | Credit Risk (Model & Portfolio Risk) | 2nd line |
| Monthly monitoring, RAG scoring, escalation | Credit Risk (Portfolio Monitoring) | 2nd line |
| Watchlist triggers & problem-exposure classification | Credit Risk Analysts | 2nd line |
| Remediation actions (re-price, tighten LVR, collections) | Mortgage Lending / Collections | 1st line, **overseen by** 2nd line |
| Independent validation of the framework | Model Validation / Internal Audit | 2nd / 3rd line |

The appetite-metric owners in [config/risk_appetite.yaml](../config/risk_appetite.yaml)
were reassigned to *Head of Credit Risk (independent 2nd line)* for this reason; the
business executes remediation but does not own the monitoring or set its own limits.

### Credit risk control unit responsibilities (Basel CRE36.57)
1. Testing and monitoring of delinquency buckets / IFRS 9 stages and transitions.
2. Production and analysis of the monitoring pack (defaults by grade, migration, trends).
3. Verifying rating/stage definitions are applied consistently (the staging logic in `monitor.py`).
4. Reviewing and documenting changes to the monitoring process (this repo's notebooks are the record).
5. Reviewing whether indicators remain predictive — see [validation.md](validation.md) element 3.

---

## 2. The daily monitoring layer (APS 113 Att. D EAD ¶6; Basel CRE36.92)

This repo computes **monthly** analytics from the loan-month panel. APRA/Basel also
require **daily** monitoring of facility amounts and limits. That daily layer is an
operational system (not a monthly analytics notebook) and is out of scope for this
demo, but it would monitor:

| Daily check | What it watches |
|---|---|
| Drawn balance vs limit | Limit excesses / over-limit facilities |
| Available undrawn | Headroom on committed lines (redraw/offset for mortgages) |
| Material balance movements | Large day-on-day exposure changes per borrower/pool |
| Top-N borrower exposure | Concentration drift intra-month |
| New arrears flow | Accounts newly missing a payment |

The monthly **limit utilisation & headroom** table in the monitoring pack (§1c) and the
**roll-rate / trigger** views are the analytics-layer analog; they would be backed by the
daily operational feed in production. See gap #1 in the review.

---

## 3. Reporting cadence & governance forums (Step 12)

| Forum | Frequency | Content |
|---|---|---|
| Front-line management | Daily / weekly | Limit excesses, arrears flow, exceptions, new originations |
| Credit Risk Committee | Monthly | Watchlist, trigger mix, roll rates, segment performance |
| Board Risk Committee | Monthly / quarterly | Appetite RAG dashboard, concentration, provisions/ECL, stress |
| Model governance | At least annually | PSI, backtest, validation outcomes (links to sister model) |
| Audit Committee | Quarterly | Independent assurance findings, internal audit |
| Board | Quarterly | Risk appetite adherence, capital, concentration, stress, recovery |

---

## 4. Rating-refresh cadence (Step 5; Basel CRE36.41)

Annual rating refresh (and more frequent refresh for higher-risk exposures) is a
property of the **rating system**, which lives in the sister
[mortgage-credit-risk-pd-lgd-ead](https://github.com/Jane511/mortgage-credit-risk-pd-lgd-ead)
model, not this monitor. The link is closed here: a **PSI breach** in the
model-performance layer (monitoring pack §10) is a trigger to **refresh the ratings /
re-estimate the model** out of cycle. Routine refresh is at least annual per CRE36.41.

---

## 5. Scope exclusions (sections 4.3 / 4.4 of the guidance)

| Topic | Status | Reason |
|---|---|---|
| Country / transfer risk (APS 220 para 36) | **N/A** | Single-country (US) own-book portfolio; no cross-border exposures |
| Purchased-receivables seller/servicer monitoring (Basel CRE36.117) | **N/A** | Modelled as own book; no purchased-receivables programme in scope |
| Single-name / large-exposure limits | **Immaterial** | Granular retail mortgage pool — reported (pack §8) but no single borrower dominates |
| Override / exception MI (gap #4) | **Open** | Requires an origination exceptions log not present in the SFLLD dataset |

---

## 6. Open governance items

Tracked in [compliance_gap_review.md](compliance_gap_review.md): #4 override/exception MI
(needs an exceptions log). All other review items are addressed in this change set; see the
review's status column.

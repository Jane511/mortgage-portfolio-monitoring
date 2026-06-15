"""
Build the 00-06 monitoring notebooks programmatically with nbformat.

Keeping the notebook source here (instead of hand-edited JSON) makes the whole
set reproducible: run `python tools/make_notebooks.py` to regenerate, then
`jupyter nbconvert --execute` to populate outputs/. The committed artifacts are
the *executed* .ipynb files plus the tables/charts they write.
"""

from pathlib import Path

import nbformat as nbf
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks"
NB_DIR.mkdir(exist_ok=True)

# Boilerplate prepended to every notebook's first code cell: make `src` importable
# and silence noisy pandas display.
BOOT = """import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd().parent / "src"))
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
pd.set_option("display.width", 120); pd.set_option("display.max_columns", 30)
import monitor as m
print("monitor library loaded — vintages:", m.VINTAGES)"""


def nb(*cells) -> nbf.NotebookNode:
    n = new_notebook()
    n.cells = list(cells)
    n.metadata = {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    }
    return n


def save(name: str, notebook: nbf.NotebookNode):
    nbf.write(notebook, NB_DIR / name)
    print("wrote", name)


# ===========================================================================
# 00 — Load & assemble
# ===========================================================================
md00 = """# 00 — Load & assemble

**Plain English:** We read the real Freddie Mac loan files for three origination
years (2007, 2008, 2015), label every loan-month with a *delinquency bucket* and
an *IFRS 9 stage*, and run a data-quality check. This is the foundation the whole
monitor sits on.

**One-line terms**
- **Origination file** — one row per loan, its characteristics at the start.
- **Monthly performance (servicing) file** — one row per loan *per month*; carries the delinquency status that everything else is built from.
- **Delinquency bucket** — Current (0 days past due) / 30 / 60 / 90+ / Default (credit event) / Prepaid (paid off).
- **IFRS 9 stage** — Stage 1 performing · Stage 2 significant increase in credit risk (30+ days past due backstop) · Stage 3 credit-impaired/default (90+ days, or a credit event).

> Demonstrated on real loan-level mortgage data; the same monitoring *mechanics*
> apply to any commercial loan portfolio with a monthly status feed."""

c00_1 = BOOT
c00_2 = '''# Load each vintage and summarise its size + bucket mix --------------------
rows = []
for v in m.VINTAGES:
    orig = m.load_orig(v)
    svcg = m.load_svcg(v)
    svcg["bucket"] = m.dlq_to_bucket(svcg["dlq_status"])
    rows.append({
        "vintage": v,
        "loans (orig)": len(orig),
        "loan-months (svcg)": len(svcg),
        "months covered": int(svcg["loan_age"].max()) if svcg["loan_age"].notna().any() else None,
        "% ever 90+/default": round(
            100 * svcg.loc[svcg.bucket.isin(["90+", "Default"]), "loan_seq"].nunique() / len(orig), 2),
        "% rows status unknown": round(100 * svcg["bucket"].isna().mean(), 3),
    })
dq = pd.DataFrame(rows)
dq'''
c00_3 = '''# One clean results table for this notebook --------------------------------
dq.to_csv(m.OUT_TABLES / "00_data_quality.csv", index=False)
print("saved ->", m.OUT_TABLES / "00_data_quality.csv")'''

save("00_load_and_assemble.ipynb", nb(
    new_markdown_cell(md00),
    new_code_cell(c00_1), new_code_cell(c00_2), new_code_cell(c00_3),
))

# ===========================================================================
# 01 — Loan-month panel
# ===========================================================================
md01 = """# 01 — Loan-month panel (the base table)

**Plain English:** We build the single table that every later notebook reads:
**one row per loan per month**, tagged with its bucket and stage, the *next*
month's bucket and stage (so transitions are a lookup), and a few origination
attributes (state, balance, score). We cache it to `data/processed/panel.parquet`.

**One-line terms**
- **Loan-month panel** — the long table; rows = loans × the months they were observed.
- **Next-month state** — where the loan is one month later; the raw material for every transition matrix and roll rate.
- **Absorbing state** — Default and Prepaid close the loan's life; nothing rolls out of them.

The cached panel is **gitignored** — it is loan-level Freddie data and redistribution
is restricted. Only aggregated outputs are committed."""

c01_1 = BOOT
c01_2 = '''# Build the panel and attach next-month transitions ----------------------
panel = m.build_panel()                 # one row per loan per month (+ bucket, stage, mob)
panel = m.add_transitions(panel)        # + next_bucket / next_stage (terminal events folded in)
print(f"{len(panel):,} loan-months  |  {panel.loan_seq.nunique():,} loans")'''
c01_3 = '''# Join a few origination attributes used downstream ----------------------
orig = pd.concat([m.load_orig(v) for v in m.VINTAGES], ignore_index=True)
keep = ["loan_seq", "vintage", "prop_state", "orig_upb", "credit_score", "dti", "ltv", "first_pmt_date"]
panel = panel.merge(orig[keep], on=["loan_seq", "vintage"], how="left")
panel.to_parquet(m.PROC_DIR / "panel.parquet", index=False)
print("cached ->", m.PROC_DIR / "panel.parquet")'''
c01_4 = '''# One clean results table: shape of the base panel by vintage -------------
summ = (panel.groupby("vintage")
        .agg(loans=("loan_seq", "nunique"),
             loan_months=("loan_seq", "size"),
             avg_months_observed=("loan_age", lambda s: round(s.max(), 0)),
             current_share=("bucket", lambda s: round((s == "Current").mean(), 4)),
             ever_default_share=("bucket", lambda s: round(
                 panel.loc[s.index].assign(d=s.isin(["90+", "Default"]))
                      .groupby("loan_seq").d.max().mean(), 4)))
        .reset_index())
summ.to_csv(m.OUT_TABLES / "01_panel_summary.csv", index=False)
summ'''

save("01_loan_month_panel.ipynb", nb(
    new_markdown_cell(md01),
    new_code_cell(c01_1), new_code_cell(c01_2), new_code_cell(c01_3), new_code_cell(c01_4),
))

# ===========================================================================
# 02 — Transition matrices & roll rates
# ===========================================================================
md02 = """# 02 — Transition / migration matrices & roll rates

**Plain English:** The headline of the whole monitor. A **transition matrix**
reads: *of the loans in this row's state this month, what share are in each
column's state next month?* Each row sums to 1. We build it at **monthly**
periodicity, for both delinquency buckets and IFRS 9 stages, and pull the key
**roll rates** (how fast loans deteriorate) and cure rates out of it.

**One-line terms**
- **Transition matrix** — period-over-period migration probabilities; the diagonal is "stayed put", above the diagonal is cure, below is deterioration.
- **Roll rate** — the share of a worse-bucket move, e.g. 30→60, 60→90+.
- **Cure rate** — the share that improves, e.g. 30→Current.

Periodicity = **one calendar month**."""

c02_1 = BOOT + '''
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")
print(f"{len(panel):,} loan-months loaded")'''
c02_2 = '''# Pooled bucket transition matrix (all vintages) -------------------------
counts_b, probs_b = m.transition_matrix(panel, "bucket")
probs_b.round(4)'''
c02_3 = '''# IFRS 9 stage transition matrix ----------------------------------------
counts_s, probs_s = m.transition_matrix(panel, "stage")
probs_s.round(4)'''
c02_4 = '''# Headline roll rates / cure rates --------------------------------------
rr = m.roll_rates(probs_b)
rr["monthly_probability"] = rr["monthly_probability"].round(4)
rr'''
c02_5 = '''# Heatmap of the bucket transition matrix (the headline visual) ----------
fig, ax = plt.subplots(figsize=(7, 4.2))
im = ax.imshow(probs_b.values, cmap="rocket_r" if "rocket_r" in plt.colormaps() else "magma_r",
               vmin=0, vmax=1, aspect="auto")
ax.set_xticks(range(probs_b.shape[1])); ax.set_xticklabels(probs_b.columns, rotation=30, ha="right")
ax.set_yticks(range(probs_b.shape[0])); ax.set_yticklabels(probs_b.index)
for i in range(probs_b.shape[0]):
    for j in range(probs_b.shape[1]):
        val = probs_b.values[i, j]
        ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                color="white" if val > 0.5 else "black", fontsize=9)
ax.set_xlabel("next month"); ax.set_ylabel("this month")
ax.set_title("Monthly delinquency-bucket transition matrix (all vintages)")
fig.colorbar(im, ax=ax, label="probability"); fig.tight_layout()
fig.savefig(m.OUT_CHARTS / "02_bucket_transition_heatmap.png", dpi=130)
print("saved heatmap"); plt.close(fig)'''
c02_6 = '''# Save the clean tables --------------------------------------------------
probs_b.round(5).to_csv(m.OUT_TABLES / "02_bucket_transition_matrix.csv")
probs_s.round(5).to_csv(m.OUT_TABLES / "02_stage_transition_matrix.csv")
rr.to_csv(m.OUT_TABLES / "02_roll_rates.csv", index=False)
print("saved bucket + stage matrices and roll rates")'''

save("02_transition_matrices_roll_rates.ipynb", nb(
    new_markdown_cell(md02),
    new_code_cell(c02_1), new_code_cell(c02_2), new_code_cell(c02_3),
    new_code_cell(c02_4), new_code_cell(c02_5), new_code_cell(c02_6),
))

# ===========================================================================
# 03 — IFRS 9 stage movements
# ===========================================================================
md03 = """# 03 — IFRS 9 stage movements

**Plain English:** Banks report loans in three IFRS 9 stages and care a lot about
*movement* between them — every 1→2 is a new "significant increase in credit
risk" flag, every 2→3 is a new default, every 2→1 is a cure. Here we count those
month-over-month moves and show the mix.

**One-line terms**
- **Stage 1 → 2** — loan deteriorated into the watch list (SICR).
- **Stage 2 → 3** — loan defaulted.
- **Stage 2 → 1 / 3 → 2** — loan cured / partially cured.

This is the same engine as notebook 02, read through the IFRS 9 lens regulators use."""

c03_1 = BOOT + '''
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")'''
c03_2 = '''# Period-over-period stage classification (where loan-months sit) --------
stage_mix = (panel[panel.stage.notna()]
             .groupby(["vintage", "stage"]).size()
             .unstack(fill_value=0))
stage_mix = stage_mix.div(stage_mix.sum(1), axis=0).round(4)
stage_mix'''
c03_3 = '''# Stage-movement summary (the headline counts) --------------------------
moves = m.stage_movement_summary(panel)
moves["share"] = moves["share"].round(4)
moves'''
c03_4 = '''# Save the clean table ---------------------------------------------------
moves.to_csv(m.OUT_TABLES / "03_stage_movements.csv", index=False)
stage_mix.to_csv(m.OUT_TABLES / "03_stage_mix_by_vintage.csv")
print("saved stage movements + stage mix")'''

save("03_ifrs9_stage_movements.ipynb", nb(
    new_markdown_cell(md03),
    new_code_cell(c03_1), new_code_cell(c03_2), new_code_cell(c03_3), new_code_cell(c03_4),
))

# ===========================================================================
# 04 — Early warning & watchlist
# ===========================================================================
md04 = """# 04 — Early warning & watchlist

**Plain English:** Monitoring is forward-looking, so we flag loans that are
*deteriorating right now*: at each loan's latest observed month, is it in Stage 2
(or worse) and/or did its delinquency get worse versus the prior month? Those
loans form a **watchlist** — the table a credit officer would actually work.

**One-line terms**
- **Deteriorating** — this month's bucket is worse than last month's.
- **Watchlist** — currently-active loans in Stage 2/3 or freshly deteriorating.
- Loan IDs are **masked** in the committed snapshot (no loan-level redistribution).

We only look at loans still active at the end of the sample (not already prepaid/defaulted-out)."""

c04_1 = BOOT + '''
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")'''
c04_2 = '''# Take each loan's latest observed month ---------------------------------
order = {"Current": 0, "30": 1, "60": 2, "90+": 3, "Default": 4}
panel["bucket_rank"] = panel["bucket"].map(order)
panel = panel.sort_values(["loan_seq", "mob"])
panel["prev_rank"] = panel.groupby("loan_seq")["bucket_rank"].shift(1)
latest = panel.groupby("loan_seq").tail(1).copy()

# Active = not closed out by a terminal zero-balance event
latest = latest[latest["zb_code"].isna()]
latest["deteriorating"] = latest["bucket_rank"] > latest["prev_rank"]
print(f"{len(latest):,} active loans at latest observation")'''
c04_3 = '''# Watchlist: Stage 2/3 now, or deteriorating this month ------------------
watch = latest[(latest.stage.isin(["Stage 2", "Stage 3"])) | (latest.deteriorating)].copy()
watch["loan_id"] = m.mask_loan_id(watch["loan_seq"])
watch = watch.sort_values(["bucket_rank", "cur_upb"], ascending=False)
cols = ["loan_id", "vintage", "prop_state", "period", "bucket", "stage",
        "deteriorating", "loan_age", "cur_upb", "credit_score", "ltv"]
watch = watch[cols]
print(f"watchlist: {len(watch):,} loans  |  exposure on watch: ${watch.cur_upb.sum()/1e6:,.1f}m")
watch.head(15)'''
c04_4 = '''# Summary of the watchlist by vintage + stage, and save -----------------
wsumm = (watch.groupby(["vintage", "stage"])
         .agg(loans=("loan_id", "size"), exposure_upb=("cur_upb", "sum"))
         .reset_index())
wsumm["exposure_upb"] = wsumm["exposure_upb"].round(0)
watch.to_csv(m.OUT_TABLES / "04_watchlist.csv", index=False)
wsumm.to_csv(m.OUT_TABLES / "04_watchlist_summary.csv", index=False)
print("saved watchlist + summary"); wsumm'''

save("04_early_warning_watchlist.ipynb", nb(
    new_markdown_cell(md04),
    new_code_cell(c04_1), new_code_cell(c04_2), new_code_cell(c04_3), new_code_cell(c04_4),
))

# ===========================================================================
# 05 — Vintage tracking
# ===========================================================================
md05 = """# 05 — Vintage tracking (downturn vs calm)

**Plain English:** The strongest story in this data: line the vintages up by
*months on book* (not calendar time) and watch how fast each cohort goes bad.
The 2007 and 2008 cohorts originated straight into the financial crisis; 2015
originated into a calm market. The cumulative default curves separate hard — the
clearest demonstration of why vintage tracking matters.

**One-line terms**
- **Months on book** — loan age in months since first payment; puts every vintage on a common clock.
- **Cumulative default rate** — share of the cohort that has reached Stage 3 (90+/credit event) by a given age.
- **Vintage** — origination-year cohort."""

c05_1 = BOOT + '''
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")'''
c05_2 = '''# First month each loan reaches default (Stage 3), by loan age ----------
defaulted = panel[panel.stage == "Stage 3"]
first_def = defaulted.groupby(["vintage", "loan_seq"])["loan_age"].min().reset_index()
loans_per_vintage = panel.groupby("vintage")["loan_seq"].nunique()

ages = range(0, 121)  # 0..120 months on book
curves = {}
for v in m.VINTAGES:
    fv = first_def[first_def.vintage == v]
    n = loans_per_vintage[v]
    cum = [(fv.loan_age <= a).sum() / n for a in ages]
    curves[v] = cum
curve_df = pd.DataFrame(curves, index=list(ages))
curve_df.index.name = "months_on_book"
curve_df.round(4).head(13)'''
c05_3 = '''# Plot the cumulative default curves ------------------------------------
fig, ax = plt.subplots(figsize=(7.5, 4.5))
for v in m.VINTAGES:
    ax.plot(curve_df.index, 100 * curve_df[v], label=f"{v} vintage", linewidth=2)
ax.set_xlabel("months on book"); ax.set_ylabel("cumulative % ever 90+/default")
ax.set_title("Cumulative default by months on book — downturn (2007/08) vs calm (2015)")
ax.legend(); ax.grid(alpha=0.3); fig.tight_layout()
fig.savefig(m.OUT_CHARTS / "05_vintage_default_curves.png", dpi=130)
print("saved curve chart"); plt.close(fig)'''
c05_4 = '''# Clean table: cumulative default at selected ages ----------------------
marks = [12, 24, 36, 48, 60, 72]
snap = (curve_df.loc[curve_df.index.isin(marks)]
        .mul(100).round(2).reset_index())
snap.columns = ["months_on_book"] + [f"{v}_cum_default_pct" for v in m.VINTAGES]
snap.to_csv(m.OUT_TABLES / "05_vintage_cumulative_default.csv", index=False)
snap'''

save("05_vintage_tracking.ipynb", nb(
    new_markdown_cell(md05),
    new_code_cell(c05_1), new_code_cell(c05_2), new_code_cell(c05_3), new_code_cell(c05_4),
))

# ===========================================================================
# 06 — Concentration & monitoring-pack report
# ===========================================================================
md06 = """# 06 — Concentration (geography, HHI, high-LVR)

**Plain English:** Where is the risk *concentrated*? We slice exposure and
default by geography (state) and by vintage, add the **HHI** (a single
concentration number) for the state book, and a **high-LVR concentration** view
(exposure by original loan-to-value band). Concentration is a portfolio risk in
its own right (APS 220 para 35) — these tables feed both the appetite limits
(notebook 07) and the monitoring pack.

**One-line terms**
- **Concentration** — how exposure clusters in a few states / cohorts / risky products; a portfolio risk in itself.
- **Exposure (UPB)** — current unpaid principal balance, i.e. money still at risk.
- **HHI** — Herfindahl–Hirschman Index, Σ(share %)² on a 0–10,000 scale; <1500 low · 1500–2500 moderate · >2500 high.
- **High-LVR** — loans whose *original* loan-to-value was above 90% (a higher-risk product, APS 220 para 35).
- **APS 330-style** — laid out like an APRA APS 330 credit-risk disclosure table. *Format only — illustrative, not a regulatory submission.*"""

c06_1 = BOOT + '''
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")'''
c06_2 = '''# Latest snapshot per loan for a point-in-time concentration view --------
latest = panel.sort_values(["loan_seq", "mob"]).groupby("loan_seq").tail(1)
active = latest[latest.zb_code.isna()].copy()  # still on book

by_state = (active.groupby("prop_state")
            .agg(loans=("loan_seq", "size"),
                 exposure_upb=("cur_upb", "sum"),
                 pct_90plus=("bucket", lambda s: round(100 * s.isin(["90+", "Default"]).mean(), 2)))
            .sort_values("exposure_upb", ascending=False))
by_state["exposure_share_pct"] = (100 * by_state.exposure_upb / by_state.exposure_upb.sum()).round(2)
by_state.head(12)'''
c06_3 = '''# Concentration by vintage (APS 330-style layout) -----------------------
by_vintage = (active.groupby("vintage")
              .agg(loans=("loan_seq", "size"),
                   exposure_upb=("cur_upb", "sum"),
                   avg_credit_score=("credit_score", "mean"),
                   pct_stage2=("stage", lambda s: round(100 * (s == "Stage 2").mean(), 2)),
                   pct_stage3=("stage", lambda s: round(100 * (s == "Stage 3").mean(), 2)))
              .reset_index())
by_vintage["exposure_upb"] = by_vintage.exposure_upb.round(0)
by_vintage["avg_credit_score"] = by_vintage.avg_credit_score.round(0)
by_vintage.to_csv(m.OUT_TABLES / "06_concentration_vintage.csv", index=False)
by_state.round(2).to_csv(m.OUT_TABLES / "06_concentration_state.csv")
by_vintage'''
c06_4 = '''# HHI of the state book + high-LVR concentration (APS 220 para 35) -------
# HHI: one number for "how concentrated is the geography?" (companion commercial
# monitor reports the same measure). Computed on exposure shares in percent.
state_share = 100 * active.groupby("prop_state")["cur_upb"].sum() / active["cur_upb"].sum()
geo_hhi = m.hhi(state_share.values)
hhi_tbl = pd.DataFrame([{
    "dimension": "state (geography)",
    "n_buckets": int(state_share.shape[0]),
    "top_share_pct": round(float(state_share.max()), 2),
    "HHI": round(geo_hhi, 0),
    "classification": m.hhi_class(geo_hhi),
}])
hhi_tbl.to_csv(m.OUT_TABLES / "06_concentration_hhi.csv", index=False)
hhi_tbl'''
c06_5 = '''# High-LVR concentration: exposure share by ORIGINAL LVR band ------------
active = active.copy()
active["lvr_band"] = m.lvr_band(active["ltv"])
lvr = (active.groupby("lvr_band", observed=False)
       .agg(loans=("loan_seq", "size"),
            exposure_upb=("cur_upb", "sum"),
            pct_90plus=("bucket", lambda s: round(100 * s.isin(["90+", "Default"]).mean(), 2)))
       )
lvr["exposure_share_pct"] = (100 * lvr.exposure_upb / lvr.exposure_upb.sum()).round(2)
high_lvr_share = float(lvr.loc[lvr.index.isin([">95", "90-95"]), "exposure_share_pct"].sum())
print(f"High-LVR (original LVR > 90%) share of exposure: {high_lvr_share:.2f}%")
lvr.round(2).to_csv(m.OUT_TABLES / "06_concentration_lvr.csv")
lvr.round(2)'''

save("06_concentration_report.ipynb", nb(
    new_markdown_cell(md06),
    new_code_cell(c06_1), new_code_cell(c06_2), new_code_cell(c06_3),
    new_code_cell(c06_4), new_code_cell(c06_5),
))

# ===========================================================================
# 07 — Risk appetite + cascaded limits (the governance layer)   [MON-1/2/3/7]
# ===========================================================================
md07 = """# 07 — Risk appetite statement + cascaded limit framework

**Plain English:** The metrics in notebooks 02–06 tell us *what the book is
doing*; this notebook says *what we will tolerate*. We read a small **risk
appetite table** from `config/risk_appetite.yaml` — an amber and a red limit, an
owner, a breach action and a review cycle for each metric — then score the
current book **green / amber / red (RAG)** against those limits. Monitoring
without limits is just reporting (APS 220 para 20).

**One-line terms**
- **Risk appetite** — how much risk the Board is willing to run; expressed as limits.
- **Amber / Red limit** — early-warning level / hard limit; a red breach escalates to the Board.
- **RAG status** — green within appetite · amber approaching the limit · red breached.
- **Leading vs lagging** — leading indicators (SICR share, roll rates) move *before* defaults; lagging ones (NPL) confirm after (APG 220 para 66).
- **Cascade** — appetite flows Board → portfolio → segment → facility (APS 220 para 35).

> Illustrative demo thresholds — **not a regulatory submission**. Levels are set
> to plausible mortgage values, not fitted to this crisis+calm sample."""

c07_1 = BOOT + '''
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")
cfg = m.load_appetite()
print("appetite metrics:", list(cfg["metrics"]))'''
c07_2 = '''# Current vs prior reporting month, all appetite metrics as-of each --------
periods = sorted(int(p) for p in panel["period"].unique())
this_period = periods[-1]
def _prev(p):
    y, mn = divmod(p, 100)
    return (y - 1) * 100 + 12 if mn == 1 else p - 1
last_period = _prev(this_period) if _prev(this_period) in set(periods) else periods[-2]

this_vals = m.portfolio_metrics_asof(panel, this_period)
last_vals = m.portfolio_metrics_asof(panel, last_period)
appetite = m.evaluate_appetite(cfg, this_vals, last_vals)
appetite.to_csv(m.OUT_TABLES / "07_appetite_status.csv", index=False)
print(f"this period {this_period} vs last period {last_period}")
appetite[["metric", "type", "last_period", "this_period", "amber", "red (limit)", "RAG"]]'''
c07_3 = '''# Leading-indicator TREND over time (not just the pooled matrix) ----------
# APG 220 para 66: a prudent ADI uses forward-looking indicators. We track the
# trailing-12m roll rates and the SICR (Current->Stage 2) migration rate at each
# year-end, so the trend is visible, not just a single pooled number.
def sicr_rate(trans, end_period, months=12):
    end = m._period_ord(end_period)
    sub = trans[(trans["bucket"] == "Current") & trans["next_stage"].notna()].copy()
    o = m._period_ord(sub["period"])
    win = sub[(o <= end) & (o > end - months)]
    return float(100 * (win["next_stage"] == "Stage 2").mean()) if len(win) else np.nan

anchors = [p for p in periods if p % 100 == 12][-5:] or periods[-5:]
if this_period not in anchors:
    anchors = anchors + [this_period]
trend = pd.DataFrame([{
    "as_of": p,
    "roll_current_30 (leading)": round(m.roll_window(panel, p)["roll_current_30"], 3),
    "roll_30_60 (leading)": round(m.roll_window(panel, p)["roll_30_60"], 2),
    "sicr_current_to_stage2 (leading)": round(sicr_rate(panel, p), 3),
} for p in anchors])
trend.to_csv(m.OUT_TABLES / "07_leading_trends.csv", index=False)
trend'''
c07_4 = '''# Stress -> limits: would a downturn breach the limits? (MON-7) ----------
# APS 220 para 73 / APG 220 para 76. Reuse the sister model's crisis severity
# (this repo's own vintage curves: 2007 ~4x 2015 default at 72m) as a downturn
# multiplier, and re-test the stressed metrics against their red limits.
mult = cfg["stress"]["downturn_multiplier"]
rows = []
for k in cfg["stress"]["applies_to"]:
    c = cfg["metrics"][k]
    cur = this_vals.get(k, float("nan"))
    stressed = cur * mult
    rows.append({
        "metric": c["label"],
        "current": round(cur, 2),
        f"stressed (x{mult:g})": round(stressed, 2),
        "red (limit)": c["red"],
        "RAG current": m.rag(cur, c["amber"], c["red"]),
        "RAG under stress": m.rag(stressed, c["amber"], c["red"]),
    })
stress = pd.DataFrame(rows)
stress.to_csv(m.OUT_TABLES / "07_stress_vs_limits.csv", index=False)
print(f"downturn multiplier x{mult:g} — {cfg['stress']['note']}")
stress'''

save("07_risk_appetite_limits.ipynb", nb(
    new_markdown_cell(md07),
    new_code_cell(c07_1), new_code_cell(c07_2), new_code_cell(c07_3), new_code_cell(c07_4),
))

# ===========================================================================
# 08 — Problem exposures: modifications & collections scalability   [MON-5]
# ===========================================================================
md08 = """# 08 — Problem exposures: modifications & collections scalability

**Plain English:** A prudent lender takes *early remedial action* on problem
exposures and watches whether restructuring actually works (APS 220 para 79;
APG 220 para 68). We use the SFLLD **modification flag** to find every loan that
was modified or payment-deferred, then ask the only question that matters: after
the modification, did it **cure** or **re-default**? We close with a one-paragraph
**collections-scalability** note — the crisis vintages already show how big an
arrears surge the book might have to absorb.

**One-line terms**
- **Modification / restructure** — a loss-mitigation change to a struggling loan (rate/term change, or payment deferral).
- **Re-default** — a modified loan that later reached 90+/default again; the test of whether remediation held.
- **Collections scalability** — whether the workout function could cope with a stress-driven multiple of today's arrears."""

c08_1 = BOOT + '''
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")'''
c08_2 = '''# Modified / restructured-exposure outcomes by vintage -------------------
mod_view = m.modified_exposure_view(panel)
n_mod = int(mod_view["modified_loans"].sum())
print(f"{n_mod:,} loans ever modified / payment-deferred")
mod_view'''
c08_3 = '''# Collections scalability: how big an arrears surge has the book seen? ----
# Monthly 30+DPD (arrears) rate per vintage; trough vs crisis peak = the surge
# multiple the collections function would need to absorb in a downturn.
arr = panel[panel.bucket.isin(m.ACTIVE_BUCKETS)].copy()
arr["is_arrears"] = arr["bucket"].isin(["30", "60", "90+"])
mo = (arr.groupby(["vintage", "period"])
      .agg(active=("loan_seq", "size"), arrears=("is_arrears", "sum")))
mo = mo[mo["active"] >= 100]
mo["arrears_pct"] = 100 * mo["arrears"] / mo["active"]
# Baseline = the cohort's TYPICAL month (median), not the ramp-up zero — so the
# surge multiple answers "peak was how many times a normal month?".
scal = (mo.groupby("vintage")["arrears_pct"]
        .agg(typical_arrears_pct="median", peak_arrears_pct="max").reset_index())
scal["surge_multiple"] = (scal.peak_arrears_pct / scal.typical_arrears_pct).round(1)
scal[["typical_arrears_pct", "peak_arrears_pct"]] = scal[["typical_arrears_pct", "peak_arrears_pct"]].round(2)
scal.to_csv(m.OUT_TABLES / "08_collections_scalability.csv", index=False)
mod_view.to_csv(m.OUT_TABLES / "08_modified_exposure.csv", index=False)
print("saved modified-exposure + collections-scalability tables")
scal'''

save("08_problem_exposures.ipynb", nb(
    new_markdown_cell(md08),
    new_code_cell(c08_1), new_code_cell(c08_2), new_code_cell(c08_3),
))

# ===========================================================================
# 09 — Model performance / PSI (5-layer model, Layer 4)            [MON-6]
# ===========================================================================
md09 = """# 09 — Model performance: population stability (PSI) + backtest feed

**Plain English:** This monitor watches *loans*; the sister
[mortgage-credit-risk-pd-lgd-ead](https://github.com/Jane511/mortgage-credit-risk-pd-lgd-ead)
project watches the *model* that scores them. Layer 4 of a five-layer monitoring
framework is **rating-system / model performance**. We add the link: the **PSI**
(population stability index) of the score distribution across vintages — has the
population the PD model was built on drifted? — and a **realised-default-by-grade**
table that is exactly the **backtest feed** the model needs (realised vs predicted).

**One-line terms**
- **PSI** — how far one distribution has shifted from a reference; <0.10 stable · 0.10–0.25 moderate · >0.25 significant.
- **Backtest** — comparing realised outcomes (this monitor) against the model's predicted PDs.
- **Grade** — a credit-score band, the model's risk ranking.

Predicted PDs live in the sister repo; here we compute PSI on a real panel risk
feature (credit score) and produce the realised side of the backtest, documenting
the intended linkage."""

c09_1 = BOOT
c09_2 = '''# PSI of origination score & LVR across vintages (reference = calm 2015) --
orig = pd.concat([m.load_orig(v) for v in m.VINTAGES], ignore_index=True)
ref_v = "2015"
ref = orig[orig.vintage == ref_v]
rows = []
for feat in ["credit_score", "ltv"]:
    for v in [x for x in m.VINTAGES if x != ref_v]:
        val = m.psi(ref[feat], orig[orig.vintage == v][feat])
        rows.append({"feature": feat, "reference": ref_v, "vintage": v,
                     "PSI": round(val, 3), "classification": m.psi_class(val)})
psi_tbl = pd.DataFrame(rows)
psi_tbl.to_csv(m.OUT_TABLES / "09_psi.csv", index=False)
psi_tbl'''
c09_3 = '''# Realised default by credit-score grade x vintage (the backtest feed) ----
panel = pd.read_parquet(m.PROC_DIR / "panel.parquet")
ever = (panel.assign(d=panel.stage == "Stage 3")
        .groupby(["vintage", "loan_seq"])
        .agg(d=("d", "max"), score=("credit_score", "first")).reset_index())
edges = [300, 620, 660, 700, 740, 780, 850]
labels = ["<620", "620-659", "660-699", "700-739", "740-779", "780+"]
ever["grade"] = pd.cut(ever.score, bins=edges, labels=labels, right=False)
grade = (ever.groupby(["grade", "vintage"], observed=False)["d"]
         .mean().mul(100).round(2).unstack("vintage"))
grade.to_csv(m.OUT_TABLES / "09_realised_default_by_grade.csv")
print("realised cumulative default rate (%) by score grade x vintage:")
grade'''

save("09_model_performance_psi.ipynb", nb(
    new_markdown_cell(md09),
    new_code_cell(c09_1), new_code_cell(c09_2), new_code_cell(c09_3),
))

# ===========================================================================
# 10 — Monitoring pack (Board MI: RAG dashboard first)     [MON-2/3/8/9 + all]
# ===========================================================================
md10 = """# 10 — Monitoring pack (the Board-style MI dashboard)

**Plain English:** The final step assembles `outputs/report/monitoring_pack.md`
from the real outputs of every prior notebook. It is built to read as a **Board
monitoring pack**: it **opens with a RAG dashboard** tied to the appetite limits
(notebook 07) and an **actions table** for anything amber/red, then lays out the
supporting evidence — each metric labelled **leading or lagging** — and closes
with the governance, stress and disclosure notes.

> Format only — illustrative, **not a regulatory submission**."""

c10_1 = BOOT
c10_2 = r'''# Assemble the Board-style monitoring pack from real outputs -------------
T = m.OUT_TABLES
rep = m.REPO_ROOT / "outputs" / "report"
rep.mkdir(parents=True, exist_ok=True)

appetite = pd.read_csv(T / "07_appetite_status.csv")
trend    = pd.read_csv(T / "07_leading_trends.csv")
stress   = pd.read_csv(T / "07_stress_vs_limits.csv")
probs_b  = pd.read_csv(T / "02_bucket_transition_matrix.csv", index_col=0)
rr       = pd.read_csv(T / "02_roll_rates.csv")
moves    = pd.read_csv(T / "03_stage_movements.csv")
wsumm    = pd.read_csv(T / "04_watchlist_summary.csv")
vint     = pd.read_csv(T / "05_vintage_cumulative_default.csv")
state    = pd.read_csv(T / "06_concentration_state.csv", index_col=0)
hhi_tbl  = pd.read_csv(T / "06_concentration_hhi.csv")
lvr      = pd.read_csv(T / "06_concentration_lvr.csv", index_col=0)
mod_view = pd.read_csv(T / "08_modified_exposure.csv")
scal     = pd.read_csv(T / "08_collections_scalability.csv")
psi_tbl  = pd.read_csv(T / "09_psi.csv")
grade    = pd.read_csv(T / "09_realised_default_by_grade.csv", index_col=0)

worst = appetite.RAG.map({"GREEN": 0, "AMBER": 1, "RED": 2, "n/a": 0}).max()
overall = {0: "GREEN", 1: "AMBER", 2: "RED"}[int(worst)]

L = []
def add(*xs): L.extend(xs)

add("# Portfolio Monitoring Pack — loan-level (Freddie Mac SFLLD)\n")
add("_Real loan-level mortgage data. The monitoring mechanics apply equally to "
    "commercial loan portfolios with a monthly status feed._\n")
add("_Format only — illustrative, **not a regulatory submission**._\n")

# --- 1. RAG dashboard (MON-2) ------------------------------------------------
add("## 1. Risk appetite dashboard — RAG vs limits\n")
add(f"**Overall portfolio status: {overall}.** Each metric is scored against the "
    "amber (early-warning) and red (limit) thresholds in `config/risk_appetite.yaml` "
    "(APS 220 paras 20/35; APG 220 para 65). `type` flags leading vs lagging.\n")
dash = appetite[["metric", "type", "last_period", "this_period", "amber", "red (limit)", "RAG"]]
add(dash.to_markdown(index=False) + "\n")

# Actions table for anything amber/red
flagged = appetite[appetite.RAG.isin(["AMBER", "RED"])]
add("### Actions (amber/red)\n")
if len(flagged):
    act = flagged[["metric", "RAG", "breach_action", "owner", "review_cycle"]].rename(
        columns={"breach_action": "action", "review_cycle": "due"})
    add(act.to_markdown(index=False) + "\n")
else:
    add("_All metrics within appetite this period — no escalations required._\n")
add("**Forward-looking view:** leading indicators (Stage 2 share, roll rates, SICR) "
    "are read first because they move before losses; the vintage curves (section 7) show "
    "how fast a downturn cohort can deteriorate, and the stress test (section 10) shows "
    "the same metrics against their limits under a downturn multiple.\n")

# --- 2. Leading-indicator trends (MON-3) ------------------------------------
add("## 2. Leading-indicator trends (forward-looking)\n")
add("APG 220 para 66 — do not rely solely on lagging arrears. Trailing-12m roll rates "
    "and the SICR (Current->Stage 2) migration rate, tracked over time:\n")
add(trend.to_markdown(index=False) + "\n")

# --- 3. Transition matrix + roll rates (lagging/leading labelled) -----------
add("## 3. Monthly delinquency-bucket transition matrix  _(lagging)_\n")
add(probs_b.round(4).to_markdown() + "\n")
add("![heatmap](../charts/02_bucket_transition_heatmap.png)\n")
add("## 4. Headline roll rates  _(leading — deterioration moves before default)_\n")
add(rr.to_markdown(index=False) + "\n")

# --- 5. Stage movements / 6. watchlist / 7. vintage -------------------------
add("## 5. IFRS 9 stage movements (loan-months)  _(mixed)_\n")
add(moves.to_markdown(index=False) + "\n")
add("## 6. Early-warning watchlist (by vintage / stage)  _(leading)_\n")
add(wsumm.to_markdown(index=False) + "\n")
add("## 7. Vintage tracking — cumulative default by months on book  _(lagging)_\n")
add(vint.to_markdown(index=False) + "\n")
add("![vintage curves](../charts/05_vintage_default_curves.png)\n")

# --- 8. Concentration: state + HHI + LVR (MON-4) ----------------------------
add("## 8. Concentration — geography, HHI & high-LVR (APS 220 para 35)\n")
add("_Format only — illustrative, not a regulatory submission._\n")
add("**By state (top 10):**\n")
add(state.head(10).round(2).to_markdown() + "\n")
add("**Geographic HHI:**\n")
add(hhi_tbl.to_markdown(index=False) + "\n")
add("**High-LVR concentration (by original LVR band):**\n")
add(lvr.round(2).to_markdown() + "\n")

# --- 9. Problem exposures (MON-5) -------------------------------------------
add("## 9. Problem exposures — modifications & collections scalability (APS 220 para 79)\n")
add("Modified / restructured loans and whether they cured or re-defaulted:\n")
add(mod_view.to_markdown(index=False) + "\n")
add("Collections scalability — trough vs crisis-peak monthly arrears (30+DPD) rate; "
    "the surge multiple is the load the workout function must be able to absorb:\n")
add(scal.to_markdown(index=False) + "\n")

# --- 10. Model performance / PSI (MON-6) ------------------------------------
add("## 10. Model performance — population stability (PSI) & backtest feed\n")
add("Layer 4 (rating-system performance). PSI of origination features vs the calm-2015 "
    "reference, and realised default by grade — the backtest feed for the sister "
    "[mortgage-credit-risk-pd-lgd-ead](https://github.com/Jane511/mortgage-credit-risk-pd-lgd-ead) model:\n")
add(psi_tbl.to_markdown(index=False) + "\n")
add("Realised cumulative default (%) by credit-score grade x vintage:\n")
add(grade.to_markdown() + "\n")

# --- 11. Governance / stress / disclosure notes (MON-7/8/9) -----------------
add("## 11. Governance, stress & disclosure notes\n")
add("**Stress -> limits (MON-7; APS 220 para 73 / APG 220 para 76).** Applying a "
    "downturn multiple (grounded in this repo's own vintage curves — 2007 reaches ~4x "
    "2015 default) to the flow/quality metrics re-tests them against their limits:\n")
add(stress.to_markdown(index=False) + "\n")
add("**Governance & independent validation (MON-8; APS 220 paras 28/75-76; APG 113 para 140).** "
    "Reporting cadence: the watchlist and roll rates go monthly to the Credit Risk Committee; "
    "the appetite RAG dashboard and concentration go monthly to the Board Risk Committee; "
    "the PSI/model-performance layer goes at least annually to model governance. The monitoring "
    "framework itself would be **independently validated annually**. _Demo, not a production system._\n")
add("**APS 330 / Pillar 3 framing (MON-9).** The concentration and credit-quality outputs "
    "(sections 7-8) are the inputs that feed **Pillar 3 (APS 330)** credit-risk disclosure. "
    "Any APS 330-style table here is **format only — illustrative, not a regulatory submission**.\n")

(rep / "monitoring_pack.md").write_text("\n".join(L), encoding="utf-8")
print("wrote ->", rep / "monitoring_pack.md", f"| overall RAG: {overall}")'''

save("10_monitoring_pack.ipynb", nb(
    new_markdown_cell(md10),
    new_code_cell(c10_1), new_code_cell(c10_2),
))

print("\\nAll notebooks generated.")

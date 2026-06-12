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
md06 = """# 06 — Concentration & monitoring-pack report

**Plain English:** Where is the risk *concentrated*? We slice exposure and
default by geography (state) and by vintage, then assemble a short
**monitoring-pack report** that pulls the real numbers from every prior notebook.

**One-line terms**
- **Concentration** — how exposure clusters in a few states / cohorts; a portfolio risk in itself.
- **Exposure (UPB)** — current unpaid principal balance, i.e. money still at risk.
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
c06_4 = '''# Assemble the monitoring-pack report from real outputs -----------------
T = m.OUT_TABLES
rep = ROOT_REPORT = (m.REPO_ROOT / "outputs" / "report")
rep.mkdir(parents=True, exist_ok=True)

probs_b = pd.read_csv(T / "02_bucket_transition_matrix.csv", index_col=0)
rr = pd.read_csv(T / "02_roll_rates.csv")
moves = pd.read_csv(T / "03_stage_movements.csv")
vint = pd.read_csv(T / "05_vintage_cumulative_default.csv")
wsumm = pd.read_csv(T / "04_watchlist_summary.csv")

lines = []
lines.append("# Portfolio Monitoring Pack — loan-level (Freddie Mac SFLLD)\\n")
lines.append("_Real loan-level mortgage data. The monitoring mechanics apply equally to "
             "commercial loan portfolios with a monthly status feed._\\n")
lines.append("## 1. Monthly delinquency-bucket transition matrix\\n")
lines.append(probs_b.round(4).to_markdown() + "\\n")
lines.append("![heatmap](../charts/02_bucket_transition_heatmap.png)\\n")
lines.append("## 2. Headline roll rates\\n")
lines.append(rr.to_markdown(index=False) + "\\n")
lines.append("## 3. IFRS 9 stage movements (loan-months)\\n")
lines.append(moves.to_markdown(index=False) + "\\n")
lines.append("## 4. Early-warning watchlist (by vintage / stage)\\n")
lines.append(wsumm.to_markdown(index=False) + "\\n")
lines.append("## 5. Vintage tracking — cumulative default by months on book\\n")
lines.append(vint.to_markdown(index=False) + "\\n")
lines.append("![vintage curves](../charts/05_vintage_default_curves.png)\\n")
lines.append("## 6. Concentration by state (top 10) — APS 330-style format\\n")
lines.append("_Format only — illustrative, not a regulatory submission._\\n")
lines.append(by_state.head(10).round(2).to_markdown() + "\\n")

(rep / "monitoring_pack.md").write_text("\\n".join(lines), encoding="utf-8")
print("wrote ->", rep / "monitoring_pack.md")'''

save("06_concentration_report.ipynb", nb(
    new_markdown_cell(md06),
    new_code_cell(c06_1), new_code_cell(c06_2), new_code_cell(c06_3), new_code_cell(c06_4),
))

print("\\nAll notebooks generated.")

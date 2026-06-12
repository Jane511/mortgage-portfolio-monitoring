"""
reports/make_figures.py — regenerate the README charts for this repo.

Every figure is built from the committed result tables in outputs/tables/
(aggregated transition/migration metrics only — probabilities, shares, rates;
never loan-level records), so the charts regenerate reproducibly with:

    python reports/make_figures.py

Outputs PNGs into reports/figures/.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TAB = ROOT / "outputs" / "tables"
FIG = ROOT / "reports" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 130,
    "font.size": 12.5, "axes.titlesize": 15, "axes.titleweight": "bold",
    "axes.labelsize": 12.5, "axes.grid": True, "grid.alpha": 0.25,
    "axes.spines.top": False, "axes.spines.right": False,
})
RED, BLUE = "#b2182b", "#2166ac"


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name, bbox_inches="tight")
    plt.close(fig)
    print("wrote", FIG / name)


# 1. Monthly delinquency-bucket transition matrix (heatmap, the headline) ----
tm = pd.read_csv(TAB / "02_bucket_transition_matrix.csv", index_col=0)
fig, ax = plt.subplots(figsize=(7.2, 4.4))
im = ax.imshow(tm.values, cmap="magma_r", vmin=0, vmax=1, aspect="auto")
ax.set_xticks(range(tm.shape[1])); ax.set_xticklabels(tm.columns, rotation=30, ha="right")
ax.set_yticks(range(tm.shape[0])); ax.set_yticklabels(tm.index)
for i in range(tm.shape[0]):
    for j in range(tm.shape[1]):
        v = tm.values[i, j]
        ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                color="white" if v > 0.5 else "black", fontsize=9)
ax.set_xlabel("state next month"); ax.set_ylabel("state this month")
ax.set_title("Monthly delinquency-bucket transition matrix")
ax.grid(False)
fig.colorbar(im, ax=ax, label="probability")
save(fig, "transition_matrix_heatmap.png")

# 2. Headline roll rates / cure rates ----------------------------------------
rr = pd.read_csv(TAB / "02_roll_rates.csv")
is_cure = rr.roll_rate.str.contains("cure", case=False)
is_exit = rr.roll_rate.str.contains("Prepaid", case=False)
colors = [BLUE if c else ("#9e9e9e" if e else RED) for c, e in zip(is_cure, is_exit)]
rr = rr.iloc[::-1]; colors = colors[::-1]
fig, ax = plt.subplots(figsize=(8.6, 4.8))
ax.barh(rr.roll_rate, rr.monthly_probability * 100, color=colors, edgecolor="white")
for y, v in enumerate(rr.monthly_probability * 100):
    ax.text(v, y, f" {v:.1f}%", va="center", fontsize=10)
ax.set_xlabel("monthly probability (%)")
ax.set_title("Roll rates — deterioration (red) vs cure (blue)")
ax.grid(axis="y", alpha=0)
save(fig, "roll_rates.png")

# 3. IFRS 9 stage mix by vintage (stacked bar) -------------------------------
mix = pd.read_csv(TAB / "03_stage_mix_by_vintage.csv").set_index("vintage")
fig, ax = plt.subplots(figsize=(7.0, 4.8))
bottom = np.zeros(len(mix))
stage_colors = {"Stage 1": "#1a9850", "Stage 2": "#fdae61", "Stage 3": "#b2182b"}
for col in ["Stage 1", "Stage 2", "Stage 3"]:
    ax.bar(mix.index.astype(str), mix[col] * 100, bottom=bottom,
           label=col, color=stage_colors[col], width=0.6)
    if col != "Stage 1":  # Stage 1 base label would sit below the 80% axis floor
        for x, (v, b) in enumerate(zip(mix[col] * 100, bottom)):
            if v > 1.0:
                ax.text(x, b + v / 2, f"{v:.1f}%", ha="center", va="center",
                        color="white", fontsize=9, fontweight="bold")
    bottom += mix[col].values * 100
ax.set_ylabel("share of loan-months (%)")
ax.set_xlabel("origination vintage")
ax.set_title("IFRS 9 stage mix by vintage (downturn vs calm)", fontsize=13.5)
ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.02, 0.5))
ax.set_ylim(80, 100)
save(fig, "stage_mix_by_vintage.png")

# 4. Vintage cumulative default by months on book (line) ---------------------
vc = pd.read_csv(TAB / "05_vintage_cumulative_default.csv")
fig, ax = plt.subplots(figsize=(7.6, 4.6))
for col, color, lab in [("2007_cum_default_pct", RED, "2007 (crisis)"),
                        ("2008_cum_default_pct", "#ef8a62", "2008 (crisis)"),
                        ("2015_cum_default_pct", BLUE, "2015 (calm)")]:
    ax.plot(vc.months_on_book, vc[col], "o-", color=color, linewidth=2, label=lab)
ax.set_xlabel("months on book")
ax.set_ylabel("cumulative % ever 90+/default")
ax.set_title("Cumulative default by months on book — downturn vs calm")
ax.legend(frameon=False)
save(fig, "vintage_default_curves.png")

print("\nAll figures written to", FIG)

"""
monitor.py — core library for the loan-level portfolio monitor.

Everything the notebooks need to turn raw Freddie Mac Single-Family Loan-Level
Dataset (SFLLD) files into a monthly monitoring set lives here, so the notebooks
stay short and the credit-risk logic is in one inspectable place.

Data model
----------
Two raw file types per origination-year vintage (pipe-delimited, no header):
  * orig  — one row per loan  (origination characteristics)
  * svcg  — one row per loan PER MONTH (the monthly performance panel)

The svcg file is the heart of the monitor: it carries each loan's delinquency
status every month, which is what every transition / migration measure is built
from.

Key field definitions are stated in plain English next to the code that uses
them, and again at the top of each notebook.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "raw data"          # gitignored — Freddie redistribution is restricted
PROC_DIR = REPO_ROOT / "data" / "processed"  # gitignored — derived loan-level panel
OUT_TABLES = REPO_ROOT / "outputs" / "tables"
OUT_CHARTS = REPO_ROOT / "outputs" / "charts"

VINTAGES = ["2007", "2008", "2015"]

for _d in (PROC_DIR, OUT_TABLES, OUT_CHARTS):
    _d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Raw file column layouts (official Freddie Mac SFLLD layout — verified against
# the sample files). Only the columns the monitor uses are named; the rest are
# read past with usecols.
# --------------------------------------------------------------------------- #
# Origination file: 0-indexed positions -> friendly name
ORIG_USE = {
    0: "credit_score",
    1: "first_pmt_date",   # YYYYMM
    7: "occupancy",        # P owner / I investor / S second home
    9: "dti",
    10: "orig_upb",
    11: "ltv",
    12: "int_rate",
    16: "prop_state",
    17: "prop_type",       # SF / PU / CO / MH / CP
    19: "loan_seq",        # join key
    20: "loan_purpose",    # P purchase / C cash-out refi / N no-cash refi
    21: "orig_term",
}

# Servicing (monthly performance) file: 0-indexed positions -> friendly name
SVCG_USE = {
    0: "loan_seq",
    1: "period",        # YYYYMM reporting month
    2: "cur_upb",       # current actual unpaid principal balance
    3: "dlq_status",    # months past due as text: "0","1","2",... ; "RA" REO acq; "XX" unknown
    4: "loan_age",      # months on book
    5: "rem_months",    # remaining months to maturity
    7: "mod_flag",      # loss-mitigation modification flag: "Y" modified / "P" payment deferral / blank none
    8: "zb_code",       # zero-balance (termination) code — see ZB_* below
}


# --------------------------------------------------------------------------- #
# Credit definitions (stated plainly; re-stated in the notebooks)
# --------------------------------------------------------------------------- #
# Delinquency buckets, derived from the monthly delinquency status (months DPD):
#   Current = 0 DPD | "30" = 30-59 | "60" = 60-89 | "90+" = 90 or more DPD
# Two ABSORBING terminal states close the loan's life:
#   Default = a credit-event termination (see DEFAULT_ZB) or REO acquisition
#   Prepaid = a voluntary payoff / maturity (see PREPAY_ZB)
ACTIVE_BUCKETS = ["Current", "30", "60", "90+"]
BUCKET_STATES = ACTIVE_BUCKETS + ["Default", "Prepaid"]

# IFRS 9 staging triggers (backstop rules; stated exactly here):
#   Stage 1 = performing            -> Current (0 DPD)
#   Stage 2 = significant increase in credit risk (SICR) -> 30+ DPD backstop (30 or 60 bucket)
#   Stage 3 = credit-impaired / default -> 90+ DPD, or a credit-event termination / REO
STAGE_STATES = ["Stage 1", "Stage 2", "Stage 3", "Prepaid"]
BUCKET_TO_STAGE = {
    "Current": "Stage 1",
    "30": "Stage 2",
    "60": "Stage 2",
    "90+": "Stage 3",
    "Default": "Stage 3",
    "Prepaid": "Prepaid",
}

# Zero-balance (termination) codes.
#   Credit events -> Default ; voluntary payoff -> Prepaid ; the rest are
#   non-credit removals we treat as right-censored (the loan just leaves the panel).
DEFAULT_ZB = {"02", "03", "09"}   # 02 third-party sale, 03 short sale/charge-off, 09 REO disposition
PREPAY_ZB = {"01"}                # 01 prepaid or matured
# anything else in zb_code (15 note sale, 16, 96 removal, ...) -> censored exit

ZB_LABEL = {
    "01": "Prepaid / matured",
    "02": "Third-party sale",
    "03": "Short sale / charge-off",
    "06": "Repurchase",
    "09": "REO disposition",
    "15": "Note sale",
    "16": "Reperforming removal",
    "96": "Removal (other)",
}

# Loss-mitigation modification flags (SFLLD). "Y" = a loan modification (term/rate
# restructure); "P" = a payment deferral. Both are "problem-exposure" remediation
# actions (APS 220 para 79 / APG 220 para 68), so we treat either as restructured.
MODIFIED_FLAGS = {"Y", "P"}
MOD_LABEL = {"Y": "Modified", "P": "Payment deferral"}

# Original-LVR (loan-to-value at origination) bands, for the higher-risk-product
# concentration view (APS 220 para 35). "High-LVR" = original LVR above 90%.
LVR_BAND_EDGES = [0, 60, 70, 80, 90, 95, 200]
LVR_BAND_LABELS = ["<=60", "60-70", "70-80", "80-90", "90-95", ">95"]
HIGH_LVR_CUT = 90  # original LVR (%) above which a loan counts as high-LVR


def lvr_band(ltv: pd.Series) -> pd.Series:
    """Map original LTV (%) to an LVR band label (right-closed bins)."""
    return pd.cut(ltv, bins=LVR_BAND_EDGES, labels=LVR_BAND_LABELS, right=True)


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def _ordered(colmap: dict[int, str]) -> tuple[list[int], list[str]]:
    pos = sorted(colmap)
    return pos, [colmap[p] for p in pos]


def load_orig(vintage: str) -> pd.DataFrame:
    """Load one vintage's origination file (selected columns, typed)."""
    pos, names = _ordered(ORIG_USE)
    df = pd.read_csv(
        RAW_DIR / f"sample_{vintage}" / f"sample_orig_{vintage}.txt",
        sep="|", header=None, usecols=pos, names=names, dtype=str,
    )
    for c in ["dti", "orig_upb", "ltv", "int_rate", "credit_score", "orig_term"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["vintage"] = vintage
    return df


def load_svcg(vintage: str) -> pd.DataFrame:
    """Load one vintage's monthly performance file (selected columns, typed)."""
    pos, names = _ordered(SVCG_USE)
    df = pd.read_csv(
        RAW_DIR / f"sample_{vintage}" / f"sample_svcg_{vintage}.txt",
        sep="|", header=None, usecols=pos, names=names,
        dtype={"loan_seq": str, "dlq_status": str, "zb_code": str},
    )
    df["period"] = df["period"].astype("int32")
    df["loan_age"] = pd.to_numeric(df["loan_age"], errors="coerce")
    df["cur_upb"] = pd.to_numeric(df["cur_upb"], errors="coerce")
    # zb_code arrives as float-ish text ("1.0"/"9.0") or blank; normalise to 2-char codes
    df["zb_code"] = (
        df["zb_code"].str.extract(r"(\d+)")[0]
        .dropna().astype(float).astype(int).astype(str).str.zfill(2)
        .reindex(df.index)
    )
    df["vintage"] = vintage
    return df


# --------------------------------------------------------------------------- #
# Bucket / stage derivation
# --------------------------------------------------------------------------- #
def dlq_to_bucket(dlq: pd.Series) -> pd.Series:
    """Map raw monthly delinquency status -> delinquency bucket.

    "RA" (REO acquisition) is itself a credit event, so it maps straight to
    Default. "XX"/blank (status unknown) -> NaN and is dropped from transitions.
    """
    n = pd.to_numeric(dlq, errors="coerce")
    out = pd.Series(np.nan, index=dlq.index, dtype=object)
    out[n == 0] = "Current"
    out[n == 1] = "30"
    out[n == 2] = "60"
    out[n >= 3] = "90+"
    out[dlq == "RA"] = "Default"
    return out


def month_ordinal(period_yyyymm: pd.Series) -> pd.Series:
    """YYYYMM int -> absolute month index, so gaps between rows are countable."""
    y = period_yyyymm // 100
    m = period_yyyymm % 100
    return (y * 12 + m).astype("int32")


def build_panel(vintages: list[str] = VINTAGES) -> pd.DataFrame:
    """Assemble the loan-month panel: one row per loan per month, with the
    delinquency bucket and IFRS 9 stage attached. This is the base table every
    transition measure is computed from."""
    frames = []
    for v in vintages:
        s = load_svcg(v)
        s["bucket"] = dlq_to_bucket(s["dlq_status"])
        s["stage"] = s["bucket"].map(BUCKET_TO_STAGE)
        s["mob"] = month_ordinal(s["period"])
        frames.append(s)
    panel = pd.concat(frames, ignore_index=True)
    return panel


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
def add_transitions(panel: pd.DataFrame) -> pd.DataFrame:
    """For each loan-month, attach the bucket/stage in the NEXT month.

    Active->active moves only count when the two rows are exactly one month
    apart. The loan's final row is turned into a terminal transition into the
    absorbing Default/Prepaid state using its zero-balance code; non-credit
    removals are left censored (next_* = NaN) and so drop out of the matrices.
    """
    p = panel.sort_values(["loan_seq", "mob"]).copy()
    g = p.groupby("loan_seq", sort=False)
    p["next_bucket"] = g["bucket"].shift(-1)
    p["next_stage"] = g["stage"].shift(-1)
    p["next_mob"] = g["mob"].shift(-1)

    is_last = p["next_mob"].isna()
    bad_gap = (~is_last) & (p["next_mob"] - p["mob"] != 1)
    p.loc[bad_gap, ["next_bucket", "next_stage"]] = np.nan

    last_default = is_last & p["zb_code"].isin(DEFAULT_ZB)
    last_prepay = is_last & p["zb_code"].isin(PREPAY_ZB)
    p.loc[last_default, "next_bucket"] = "Default"
    p.loc[last_default, "next_stage"] = "Stage 3"
    p.loc[last_prepay, "next_bucket"] = "Prepaid"
    p.loc[last_prepay, "next_stage"] = "Prepaid"
    return p


def transition_matrix(trans: pd.DataFrame, kind: str = "bucket"):
    """Empirical one-month transition matrix.

    Returns (counts, probs). Rows are the active originating states; columns add
    the two absorbing terminal states. Each probability row sums to 1: it is the
    chance a loan in row-state this month is in column-state next month.
    """
    if kind == "bucket":
        from_col, to_col = "bucket", "next_bucket"
        rows, cols = ACTIVE_BUCKETS, BUCKET_STATES
    else:
        from_col, to_col = "stage", "next_stage"
        rows, cols = ["Stage 1", "Stage 2", "Stage 3"], STAGE_STATES

    sub = trans[trans[from_col].isin(rows) & trans[to_col].notna()]
    counts = (
        pd.crosstab(sub[from_col], sub[to_col])
        .reindex(index=rows, columns=cols, fill_value=0)
    )
    probs = counts.div(counts.sum(axis=1).replace(0, np.nan), axis=0)
    return counts, probs


def roll_rates(probs: pd.DataFrame) -> pd.DataFrame:
    """Pull the headline roll rates (deterioration) and cure rates straight out
    of a bucket transition-probability matrix."""
    def g(frm, to):
        try:
            return float(probs.loc[frm, to])
        except KeyError:
            return np.nan

    rows = [
        ("Current -> 30 (new delinquency)", g("Current", "30")),
        ("30 -> 60 (roll worse)", g("30", "60")),
        ("60 -> 90+ (roll worse)", g("60", "90+")),
        ("90+ -> Default (roll to credit event)", g("90+", "Default")),
        ("30 -> Current (cure)", g("30", "Current")),
        ("60 -> Current/30 (cure)", g("60", "Current") + g("60", "30")),
        ("Current -> Prepaid (voluntary exit)", g("Current", "Prepaid")),
    ]
    return pd.DataFrame(rows, columns=["roll_rate", "monthly_probability"])


def stage_movement_summary(trans: pd.DataFrame) -> pd.DataFrame:
    """Count period-over-period IFRS 9 stage moves (1->2, 2->3, 2->1 cure, ...)."""
    sub = trans[trans["stage"].isin(["Stage 1", "Stage 2", "Stage 3"]) & trans["next_stage"].notna()].copy()
    direction = {
        ("Stage 1", "Stage 1"): "1 -> 1  stay performing",
        ("Stage 1", "Stage 2"): "1 -> 2  deteriorate (SICR)",
        ("Stage 1", "Stage 3"): "1 -> 3  deteriorate (default)",
        ("Stage 1", "Prepaid"): "1 -> exit (prepaid)",
        ("Stage 2", "Stage 1"): "2 -> 1  cure",
        ("Stage 2", "Stage 2"): "2 -> 2  stay watch",
        ("Stage 2", "Stage 3"): "2 -> 3  deteriorate (default)",
        ("Stage 2", "Prepaid"): "2 -> exit (prepaid)",
        ("Stage 3", "Stage 1"): "3 -> 1  cure",
        ("Stage 3", "Stage 2"): "3 -> 2  partial cure",
        ("Stage 3", "Stage 3"): "3 -> 3  stay defaulted",
        ("Stage 3", "Prepaid"): "3 -> exit (prepaid)",
    }
    key = list(zip(sub["stage"], sub["next_stage"]))
    sub["move"] = [direction.get(k, f"{k[0]} -> {k[1]}") for k in key]
    out = (
        sub.groupby("move").size().rename("loan_months").reset_index()
        .sort_values("loan_months", ascending=False, ignore_index=True)
    )
    out["share"] = out["loan_months"] / out["loan_months"].sum()
    return out


def mask_loan_id(loan_seq: pd.Series) -> pd.Series:
    """Mask loan IDs for committed output snapshots (keep last 4 chars only) so
    nothing redistributes loan-level Freddie data."""
    return "****" + loan_seq.str[-4:]


# =========================================================================== #
# Governance layer — concentration (HHI), risk appetite / RAG, problem
# exposures, and model performance (PSI). This is the layer that turns the
# metrics above into a monitoring PROGRAMME: appetite -> limits -> RAG status
# -> escalation. Rule references are stated next to each function.
# =========================================================================== #
CONFIG_DIR = REPO_ROOT / "config"


# --- Concentration (APS 220 para 35) -------------------------------------- #
def hhi(shares_pct) -> float:
    """Herfindahl–Hirschman Index from exposure shares expressed in **percent**
    (0–100). Returns the standard 0–10,000 scale (Σ share²). Higher = more
    concentrated. Mirrors the companion commercial monitor's HHI."""
    s = np.asarray(shares_pct, dtype=float)
    return float(np.nansum(s ** 2))


def hhi_class(h: float) -> str:
    """Standard HHI concentration bands."""
    if np.isnan(h):
        return "n/a"
    if h < 1500:
        return "Low (<1500)"
    if h <= 2500:
        return "Moderate (1500-2500)"
    return "High (>2500)"


# --- Risk appetite / RAG (APS 220 paras 20/35; APG 220 para 65) ----------- #
def load_appetite(path: Path | None = None) -> dict:
    """Load the risk-appetite / limit thresholds from YAML. Thresholds live in
    config, not in code, so a risk owner can change appetite without touching the
    engine (APS 220 para 20)."""
    import yaml

    path = path or (CONFIG_DIR / "risk_appetite.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def rag(value: float, amber: float, red: float, higher_is_worse: bool = True) -> str:
    """Green / Amber / Red status for a metric against its amber & red limits."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    if higher_is_worse:
        return "RED" if value >= red else ("AMBER" if value >= amber else "GREEN")
    return "RED" if value <= red else ("AMBER" if value <= amber else "GREEN")


def _period_ord(period: pd.Series | int):
    """YYYYMM -> absolute month ordinal (works on a Series or a scalar)."""
    return (period // 100) * 12 + (period % 100)


def book_asof(trans: pd.DataFrame, period: int) -> pd.DataFrame:
    """The still-on-book loans observed in a given reporting month (active
    delinquency buckets only — terminated/censored rows drop out)."""
    return trans[(trans["period"] == period) & (trans["bucket"].isin(ACTIVE_BUCKETS))]


def stock_metrics(book: pd.DataFrame) -> dict:
    """Point-in-time (stock) appetite metrics for one as-of book, exposure-weighted
    on current UPB: NPL ratio, Stage 2 share, top-state share, geographic HHI,
    high-LVR share."""
    e = book["cur_upb"].sum()

    def exp_share(mask) -> float:
        return float(100 * book.loc[mask, "cur_upb"].sum() / e) if e else np.nan

    state_share = (book.groupby("prop_state")["cur_upb"].sum() / e * 100) if e else pd.Series(dtype=float)
    return {
        "npl_ratio": exp_share(book["bucket"] == "90+"),
        "stage2_share": exp_share(book["stage"] == "Stage 2"),
        "top_state_share": float(state_share.max()) if len(state_share) else np.nan,
        "geo_hhi": hhi(state_share.values) if len(state_share) else np.nan,
        "high_lvr_share": exp_share(book["ltv"] > HIGH_LVR_CUT),
    }


def roll_window(trans: pd.DataFrame, end_period: int, months: int = 12) -> dict:
    """Trailing-window (flow) roll rates ending at `end_period`: new-delinquency
    (Current->30) and the 30->60 deterioration roll. Count-based over the window,
    so a single noisy month doesn't drive the appetite trigger (APS 220 para 35)."""
    end = _period_ord(end_period)
    sub = trans[trans["bucket"].isin(["Current", "30"]) & trans["next_bucket"].notna()].copy()
    o = _period_ord(sub["period"])
    win = sub[(o <= end) & (o > end - months)]

    def roll(frm, to) -> float:
        d = win[win["bucket"] == frm]
        return float(100 * (d["next_bucket"] == to).mean()) if len(d) else np.nan

    return {"roll_current_30": roll("Current", "30"), "roll_30_60": roll("30", "60")}


def portfolio_metrics_asof(trans: pd.DataFrame, period: int, roll_months: int = 12) -> dict:
    """All appetite metric values as-of one reporting month: stock metrics at that
    month + trailing-window flow (roll) metrics ending at that month."""
    out = stock_metrics(book_asof(trans, period))
    out.update(roll_window(trans, period, months=roll_months))
    return out


def evaluate_appetite(appetite: dict, this_vals: dict, last_vals: dict) -> pd.DataFrame:
    """Join the appetite thresholds to the current/prior metric values and assign a
    RAG status to each — the data behind the Board MI dashboard (APG 220 para 65).

    Returns one row per metric: label, indicator type (leading/lagging), prior &
    current value, amber & red limits, RAG, owner, breach action, citation."""
    rows = []
    for key, c in appetite["metrics"].items():
        hw = c.get("higher_is_worse", True)
        this_v, last_v = this_vals.get(key, np.nan), last_vals.get(key, np.nan)
        rows.append({
            "metric": c["label"],
            "type": c["indicator_type"],
            "last_period": round(last_v, 2) if last_v == last_v else np.nan,
            "this_period": round(this_v, 2) if this_v == this_v else np.nan,
            "amber": c["amber"],
            "red (limit)": c["red"],
            "RAG": rag(this_v, c["amber"], c["red"], hw),
            "owner": c["owner"],
            "breach_action": c["breach_action"],
            "review_cycle": c["review_cycle"],
            "basis": c["citation"],
        })
    return pd.DataFrame(rows)


# --- Problem exposures / modifications (APS 220 para 79; APG 220 para 68) -- #
def modified_exposure_view(trans: pd.DataFrame) -> pd.DataFrame:
    """Modified / restructured-exposure outcomes by vintage. For every loan ever
    flagged modified (or payment-deferred), classify its path AFTER the first
    modification month: re-defaulted (reached 90+/Default), cured (latest state
    Current), or still delinquent. The cure-vs-re-default split is the test of
    whether remediation is working."""
    p = trans.sort_values(["loan_seq", "mob"]).copy()
    p["is_mod"] = p["mod_flag"].isin(MODIFIED_FLAGS)
    mod_loans = p.loc[p["is_mod"], "loan_seq"].unique()
    first_mod = p[p["is_mod"]].groupby("loan_seq")["mob"].min().rename("first_mod_mob")

    sub = p[p["loan_seq"].isin(mod_loans)].merge(first_mod, on="loan_seq")
    after = sub[sub["mob"] >= sub["first_mod_mob"]]

    redefault = after.groupby("loan_seq")["bucket"].apply(lambda s: s.isin(["90+", "Default"]).any())
    latest_bucket = after.sort_values("mob").groupby("loan_seq")["bucket"].last()
    vintage = sub.groupby("loan_seq")["vintage"].first()
    exposure = sub.sort_values("mob").groupby("loan_seq")["cur_upb"].last()

    status = pd.Series("still delinquent", index=redefault.index)
    status[latest_bucket == "Current"] = "cured / performing"
    status[redefault] = "re-defaulted"

    df = pd.DataFrame({"vintage": vintage, "status": status, "exposure": exposure}).dropna(subset=["vintage"])
    out = (df.groupby("vintage")
           .agg(modified_loans=("status", "size"),
                modified_exposure_upb=("exposure", "sum"),
                re_default_rate_pct=("status", lambda s: round(100 * (s == "re-defaulted").mean(), 1)),
                cure_rate_pct=("status", lambda s: round(100 * (s == "cured / performing").mean(), 1)))
           .reset_index())
    out["modified_exposure_upb"] = out["modified_exposure_upb"].round(0)
    return out


# --- Model performance / backtest feed (5-layer model, Layer 4) ----------- #
def psi(expected, actual, bins: int = 10) -> float:
    """Population Stability Index between an expected (reference) and an actual
    distribution of a continuous score. Bins on the reference's quantiles. <0.10
    stable · 0.10–0.25 moderate shift · >0.25 significant shift. This is how the
    monitor watches whether the population the PD/LGD/EAD model was built on has
    drifted (rating-system performance, the framework's Layer 4)."""
    exp = np.asarray(expected, dtype=float)
    act = np.asarray(actual, dtype=float)
    exp, act = exp[~np.isnan(exp)], act[~np.isnan(act)]
    cuts = np.unique(np.quantile(exp, np.linspace(0, 1, bins + 1)))
    cuts[0], cuts[-1] = -np.inf, np.inf
    e = np.histogram(exp, cuts)[0] / len(exp)
    a = np.histogram(act, cuts)[0] / len(act)
    e, a = np.clip(e, 1e-6, None), np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_class(p: float) -> str:
    if np.isnan(p):
        return "n/a"
    if p < 0.10:
        return "Stable (<0.10)"
    if p <= 0.25:
        return "Moderate shift (0.10-0.25)"
    return "Significant shift (>0.25)"

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
    13: "channel",         # R retail / B broker / C correspondent / T third-party (TPO)
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

# Higher-risk-product / channel dimensions (SFLLD codes -> readable labels). These
# back the product-mix concentration (APS 220 para 35 — higher-risk products) and
# the third-party-originator monitoring (APS 220 para 39 / APG 220 paras 307-308).
OCCUPANCY_LABEL = {"P": "Owner-occupier", "I": "Investor", "S": "Second home"}
PURPOSE_LABEL = {"P": "Purchase", "C": "Cash-out refi", "N": "No-cash-out refi", "R": "Refinance"}
# Acquisition channel: R is the lender's own retail desk; B/C/T are THIRD-PARTY
# originated (broker, correspondent, TPO), which APRA expects to be monitored more
# closely because the lender did not control the point of sale.
CHANNEL_LABEL = {"R": "Retail", "B": "Broker", "C": "Correspondent", "T": "Third-party (TPO)"}
THIRD_PARTY_CHANNELS = {"Broker", "Correspondent", "Third-party (TPO)"}

# Stylised US national house-price index (HPI), 2000 = 100. Used ONLY to mark
# collateral to market for the CURRENT/indexed-LVR view (Basel CRE36.140 continuous
# collateral monitoring): current value = origination value x HPI(now)/HPI(orig).
# ILLUSTRATIVE demo path (broadly FHFA/Case-Shiller-shaped — crisis dip 2008-11,
# strong 2020-22 run) — NOT a fitted series and NOT regional. Production would use a
# regional HPI feed. The shape is what matters: crisis-vintage survivors lever UP
# early, calm-vintage survivors de-lever as prices rise.
HPI_BY_YEAR = {
    2000: 100, 2001: 107, 2002: 115, 2003: 124, 2004: 135, 2005: 150, 2006: 158,
    2007: 160, 2008: 150, 2009: 138, 2010: 135, 2011: 132, 2012: 136, 2013: 146,
    2014: 154, 2015: 162, 2016: 172, 2017: 182, 2018: 193, 2019: 202, 2020: 212,
    2021: 240, 2022: 272, 2023: 280, 2024: 292, 2025: 300, 2026: 305,
}

# Illustrative IFRS 9 ECL coverage rates by stage (blended PD x LGD), used to turn
# stage exposures into a provision figure and a coverage ratio (APG 220 para 67(b)).
# Demo values — overridden by config/risk_appetite.yaml (ecl.coverage_rates) if present.
DEFAULT_ECL_COVERAGE = {"Stage 1": 0.001, "Stage 2": 0.02, "Stage 3": 0.30}


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


def category_concentration(active: pd.DataFrame, by: str, label_map: dict | None = None,
                           exposure: str = "cur_upb") -> pd.DataFrame:
    """Exposure concentration + point-in-time 90+ rate by a categorical origination
    attribute (occupancy, loan purpose, ...). Mirrors the geography view exactly:
    loans, current exposure (UPB), % of loans currently 90+/default, and exposure
    share. Backs the higher-risk-PRODUCT concentration APS 220 para 35(b) expects
    (e.g. investor vs owner-occupier, cash-out refi vs purchase) — dimensions the
    raw file already carries but the geography-only view did not surface.

    `active` is the latest-month, still-on-book snapshot (one row per loan).
    """
    df = active.copy()
    if label_map is not None:
        df[by] = df[by].map(label_map).fillna(df[by])
    g = (df.groupby(by, observed=False)
         .agg(loans=("loan_seq", "size"),
              exposure_upb=(exposure, "sum"),
              pct_90plus=("bucket", lambda s: round(100 * s.isin(["90+", "Default"]).mean(), 2)))
         .sort_values("exposure_upb", ascending=False))
    total = g["exposure_upb"].sum()
    g["exposure_share_pct"] = (100 * g["exposure_upb"] / total).round(2) if total else np.nan
    g["exposure_upb"] = g["exposure_upb"].round(0)
    return g


def channel_performance(panel: pd.DataFrame, active: pd.DataFrame) -> pd.DataFrame:
    """Acquisition-channel performance — the third-party-originator monitoring APRA
    expects (APS 220 para 39; APG 220 paras 307-308). Third-party channels (broker /
    correspondent / TPO) are originated away from the lender's own desk, so their
    performance is tracked separately from retail.

    Combines a LIFETIME view (ever reached 90+/default, over the whole panel — the
    fairest "did this channel's book go bad?" measure) with the CURRENT active-book
    exposure share, per channel.
    """
    # lifetime ever-90+/default per loan, with its origination channel
    life = (panel.assign(_bad=panel["bucket"].isin(["90+", "Default"]))
            .groupby("loan_seq")
            .agg(_bad=("_bad", "max"), channel=("channel", "first"))
            .reset_index())
    life["channel"] = life["channel"].map(CHANNEL_LABEL).fillna(life["channel"])
    life_g = (life.groupby("channel", observed=False)
              .agg(loans=("loan_seq", "size"),
                   ever_90plus_pct=("_bad", lambda s: round(100 * s.mean(), 2))))

    # current active-book exposure share per channel
    a = active.copy()
    a["channel"] = a["channel"].map(CHANNEL_LABEL).fillna(a["channel"])
    cur = (a.groupby("channel", observed=False)
           .agg(active_loans=("loan_seq", "size"), exposure_upb=("cur_upb", "sum")))

    out = life_g.join(cur, how="left")
    total = out["exposure_upb"].sum()
    out["exposure_share_pct"] = (100 * out["exposure_upb"] / total).round(2) if total else np.nan
    out["exposure_upb"] = out["exposure_upb"].round(0)
    out["third_party"] = out.index.isin(THIRD_PARTY_CHANNELS)
    return out.sort_values("exposure_upb", ascending=False).reset_index()


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


def stock_metrics(book: pd.DataFrame, coverage: dict | None = None) -> dict:
    """Point-in-time (stock) appetite metrics for one as-of book, exposure-weighted
    on current UPB: NPL ratio, Stage 2 share, top-state share, geographic HHI,
    original high-LVR share, CURRENT (indexed) high-LVR share, and — if `coverage`
    (stage ECL rates) is supplied — the expected-loss rate (bps) and NPL provision
    coverage ratio (APG 220 para 67(b))."""
    e = book["cur_upb"].sum()

    def exp_share(mask) -> float:
        return float(100 * book.loc[mask, "cur_upb"].sum() / e) if e else np.nan

    state_share = (book.groupby("prop_state")["cur_upb"].sum() / e * 100) if e else pd.Series(dtype=float)
    cur_lvr = current_lvr_series(book)
    out = {
        "npl_ratio": exp_share(book["bucket"] == "90+"),
        "stage2_share": exp_share(book["stage"] == "Stage 2"),
        "top_state_share": float(state_share.max()) if len(state_share) else np.nan,
        "geo_hhi": hhi(state_share.values) if len(state_share) else np.nan,
        "high_lvr_share": exp_share(book["ltv"] > HIGH_LVR_CUT),
        "current_high_lvr_share": exp_share(cur_lvr > HIGH_LVR_CUT),
    }
    if coverage:
        ecl = ecl_provisions(book, coverage)
        out["loss_rate"] = ecl["el_rate_bps"]
        out["provision_coverage"] = ecl["npl_coverage_pct"]
    return out


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


def portfolio_metrics_asof(trans: pd.DataFrame, period: int, roll_months: int = 12,
                           coverage: dict | None = None) -> dict:
    """All appetite metric values as-of one reporting month: stock metrics at that
    month + trailing-window flow (roll) metrics ending at that month."""
    out = stock_metrics(book_asof(trans, period), coverage=coverage)
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
            "higher_is_worse": hw,
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


# =========================================================================== #
# Extended monitoring — collateral (current/indexed LVR), provisions/ECL,
# single-name concentration, hardship/UTP, validation predictiveness, multi-
# scenario stress, and limit utilisation. These close the gaps identified in
# docs/compliance_gap_review.md. Rule references are stated next to each function.
# =========================================================================== #

# --- Current / indexed LVR — continuous collateral monitoring (CRE36.140) -- #
def current_lvr_series(book: pd.DataFrame, hpi: dict | None = None) -> pd.Series:
    """Mark-to-market (current / indexed) LVR per loan: current UPB over the property
    value re-indexed from origination to the as-of month by an HPI path. Origination
    LVR alone is a static view; APRA/Basel expect *continuous* collateral monitoring
    (Basel CRE36.140), for which the live metric is current LVR.

    value_at_orig = orig_upb / (orig_LTV/100);  value_now = value_at_orig x HPI_now/HPI_orig.
    Uses the stylised illustrative HPI_BY_YEAR unless a real path is supplied."""
    hpi = hpi or HPI_BY_YEAR
    orig_year = (pd.to_numeric(book["first_pmt_date"], errors="coerce") // 100)
    asof_year = pd.to_numeric(book["period"], errors="coerce") // 100
    ltv = pd.to_numeric(book["ltv"], errors="coerce")
    orig_value = book["orig_upb"] / (ltv / 100.0)
    factor = asof_year.map(hpi) / orig_year.map(hpi)
    cur_value = orig_value * factor
    return 100.0 * book["cur_upb"] / cur_value


def current_lvr_concentration(active: pd.DataFrame) -> pd.DataFrame:
    """Exposure by CURRENT (indexed) LVR band — the marked-to-market counterpart of
    the origination-LVR concentration view (Basel CRE36.140)."""
    a = active.copy()
    a["current_lvr"] = current_lvr_series(a)
    a["current_lvr_band"] = lvr_band(a["current_lvr"].clip(upper=LVR_BAND_EDGES[-1] - 0.01))
    g = (a.groupby("current_lvr_band", observed=False)
         .agg(loans=("loan_seq", "size"),
              exposure_upb=("cur_upb", "sum"),
              pct_90plus=("bucket", lambda s: round(100 * s.isin(["90+", "Default"]).mean(), 2))))
    total = g["exposure_upb"].sum()
    g["exposure_share_pct"] = (100 * g["exposure_upb"] / total).round(2) if total else np.nan
    g["exposure_upb"] = g["exposure_upb"].round(0)
    return g


# --- IFRS 9 ECL / provision coverage (APG 220 para 67(b)) ----------------- #
def ecl_provisions(book: pd.DataFrame, coverage: dict | None = None) -> dict:
    """Illustrative IFRS 9 expected-credit-loss provision and coverage ratios from the
    as-of book's stage exposures and config-driven stage coverage rates (blended
    PD x LGD). Returns stage exposures, total ECL, the expected-loss rate (bps of EAD)
    and the NPL (Stage 3) provision coverage ratio (%). Demo values — APG 220 para
    67(b) lists provision coverage as a required forward-looking indicator."""
    coverage = coverage or DEFAULT_ECL_COVERAGE
    e = float(book["cur_upb"].sum())
    stage_exp = book.groupby("stage")["cur_upb"].sum()
    s1, s2, s3 = (float(stage_exp.get(s, 0.0)) for s in ("Stage 1", "Stage 2", "Stage 3"))
    ecl = s1 * coverage.get("Stage 1", 0) + s2 * coverage.get("Stage 2", 0) + s3 * coverage.get("Stage 3", 0)
    return {
        "ead": e, "stage1_exp": s1, "stage2_exp": s2, "stage3_exp": s3, "ecl": ecl,
        "el_rate_bps": float(10000 * ecl / e) if e else np.nan,
        "npl_coverage_pct": float(100 * ecl / s3) if s3 else np.nan,
    }


def ecl_table(book: pd.DataFrame, coverage: dict | None = None) -> pd.DataFrame:
    """One-row ECL / coverage summary table for the monitoring pack."""
    x = ecl_provisions(book, coverage)
    return pd.DataFrame([{
        "EAD ($)": round(x["ead"], 0),
        "Stage 2 exposure ($)": round(x["stage2_exp"], 0),
        "Stage 3 / NPL exposure ($)": round(x["stage3_exp"], 0),
        "ECL provision ($)": round(x["ecl"], 0),
        "EL rate (bps of EAD)": round(x["el_rate_bps"], 1),
        "NPL coverage (%)": round(x["npl_coverage_pct"], 1),
    }])


# --- Single-name / large-exposure concentration (APG 220 paras 77-80) ----- #
def topn_concentration(active: pd.DataFrame, ns=(10, 20, 50)) -> pd.DataFrame:
    """Top-N single-loan exposure concentration. For a granular RETAIL mortgage pool
    this is expected to be immaterial (no single borrower dominates); reported for
    completeness so the large-exposure dimension (APG 220 paras 77-80) is not silently
    omitted. On a commercial book the same view would carry real signal."""
    e = float(active["cur_upb"].sum())
    s = active["cur_upb"].sort_values(ascending=False)
    rows = [{"group": f"Top {n} loans",
             "exposure_upb": round(float(s.head(n).sum()), 0),
             "share_of_book_pct": round(100 * float(s.head(n).sum()) / e, 3) if e else np.nan}
            for n in ns]
    return pd.DataFrame(rows)


# --- Hardship / restructured monitoring (APG 220 para 68) ----------------- #
def hardship_summary(panel: pd.DataFrame) -> pd.DataFrame:
    """Concession-book outcomes by vintage (APG 220 para 68): number of concessions,
    exposure, cure rate, re-default rate, and an ULTIMATE-loss proxy (share that
    reached an actual Default credit-event termination, i.e. charge-off/short-sale/REO)."""
    p = panel.sort_values(["loan_seq", "mob"]).copy()
    p["is_mod"] = p["mod_flag"].isin(MODIFIED_FLAGS)
    mod_loans = p.loc[p["is_mod"], "loan_seq"].unique()
    first_mod = p[p["is_mod"]].groupby("loan_seq")["mob"].min().rename("first_mod_mob")
    sub = p[p["loan_seq"].isin(mod_loans)].merge(first_mod, on="loan_seq")
    after = sub[sub["mob"] >= sub["first_mod_mob"]]

    redefault = after.groupby("loan_seq")["bucket"].apply(lambda s: s.isin(["90+", "Default"]).any())
    reached_default = after.groupby("loan_seq")["bucket"].apply(lambda s: (s == "Default").any())
    latest_bucket = after.sort_values("mob").groupby("loan_seq")["bucket"].last()
    vintage = sub.groupby("loan_seq")["vintage"].first()
    exposure = sub.sort_values("mob").groupby("loan_seq")["cur_upb"].last()

    status = pd.Series("still delinquent", index=redefault.index)
    status[latest_bucket == "Current"] = "cured / performing"
    status[redefault] = "re-defaulted"
    df = pd.DataFrame({"vintage": vintage, "status": status, "exposure": exposure,
                       "reached_default": reached_default}).dropna(subset=["vintage"])
    out = (df.groupby("vintage")
           .agg(concessions=("status", "size"),
                concession_exposure_upb=("exposure", "sum"),
                cure_rate_pct=("status", lambda s: round(100 * (s == "cured / performing").mean(), 1)),
                re_default_rate_pct=("status", lambda s: round(100 * (s == "re-defaulted").mean(), 1)),
                ultimate_loss_rate_pct=("reached_default", lambda s: round(100 * s.mean(), 1)))
           .reset_index())
    out["concession_exposure_upb"] = out["concession_exposure_upb"].round(0)
    return out


def new_concessions_by_year(panel: pd.DataFrame) -> pd.DataFrame:
    """Trend in NEW concession (modification/deferral) requests by calendar year and
    product (occupancy) — APG 220 para 68 asks for the number of new requests by
    product type. Note: the SFLLD records granted concessions only, so an approval
    rate is not observable (flagged in the pack)."""
    p = panel.sort_values(["loan_seq", "mob"]).copy()
    p["is_mod"] = p["mod_flag"].isin(MODIFIED_FLAGS)
    meta = p.groupby("loan_seq").agg(occupancy=("occupancy", "first")).reset_index()
    fm = (p[p["is_mod"]].groupby("loan_seq").agg(first_period=("period", "min")).reset_index()
          .merge(meta, on="loan_seq"))
    fm["year"] = fm["first_period"] // 100
    fm["occupancy"] = fm["occupancy"].map(OCCUPANCY_LABEL).fillna(fm["occupancy"])
    by_year = (fm.groupby("year")
               .agg(new_concessions=("loan_seq", "size")).reset_index())
    by_year["owner_occupier"] = (fm[fm.occupancy == "Owner-occupier"].groupby("year")["loan_seq"]
                                 .size().reindex(by_year["year"]).fillna(0).astype(int).values)
    by_year["investor"] = (fm[fm.occupancy == "Investor"].groupby("year")["loan_seq"]
                           .size().reindex(by_year["year"]).fillna(0).astype(int).values)
    return by_year


# --- Unlikely-to-pay overlay (APS 220 default definition) ------------------ #
def utp_overlay(panel: pd.DataFrame, active: pd.DataFrame) -> dict:
    """Unlikely-to-pay (UTP) overlay. APRA's default = 90 DPD *or* unlikely-to-pay;
    this monitor's Stage 3 uses only the 90-DPD/credit-event backstop. As an
    illustrative UTP signal we flag loans EVER granted a concession (modification /
    deferral — a forbearance/distress marker) that are currently still on book and NOT
    yet 90+/default. Production would fold qualitative UTP triggers into the default
    definition; here it is reported as an additional early-warning count."""
    ever_mod = (panel.assign(_m=panel["mod_flag"].isin(MODIFIED_FLAGS))
                .groupby("loan_seq")["_m"].max())
    a = active.copy()
    a["ever_mod"] = a["loan_seq"].map(ever_mod).fillna(False)
    a["utp_candidate"] = a["ever_mod"] & ~a["bucket"].isin(["90+", "Default"])
    e = float(a["cur_upb"].sum())
    exp = float(a.loc[a["utp_candidate"], "cur_upb"].sum())
    return {
        "utp_candidate_loans": int(a["utp_candidate"].sum()),
        "utp_candidate_exposure_upb": round(exp, 0),
        "utp_candidate_share_pct": round(100 * exp / e, 3) if e else np.nan,
    }


# --- Validation: predictiveness (APG 113 para 140, element 3 "Performance") - #
def sicr_predictiveness(panel: pd.DataFrame) -> pd.DataFrame:
    """Element 3 of the validation framework: show the leading indicator is actually
    PREDICTIVE. Loans that entered Stage 2 (SICR) within their first 12 months default
    (reach Stage 3 ever) at a far higher rate than those that did not — evidence the
    watch trigger leads losses rather than merely lagging them (APG 220 para 66)."""
    g = panel[["loan_seq", "loan_age", "stage"]].copy()
    early2 = set(g.loc[(g["loan_age"] <= 12) & (g["stage"] == "Stage 2"), "loan_seq"])
    loan = g.assign(d=g["stage"] == "Stage 3").groupby("loan_seq").agg(d=("d", "max"))
    loan["entered_stage2_by_12m"] = loan.index.isin(early2)
    out = (loan.groupby("entered_stage2_by_12m")
           .agg(loans=("d", "size"),
                eventual_default_pct=("d", lambda s: round(100 * s.mean(), 2)))
           .reset_index()
           .sort_values("entered_stage2_by_12m"))
    return out


# --- Multi-scenario stress (APS 220 paras 73/76) -------------------------- #
def stress_table(this_vals: dict, cfg: dict, scenario: str) -> pd.DataFrame:
    """Re-test the flow/quality metrics against their limits under a named stress
    scenario whose PER-METRIC multipliers live in config (APS 220 para 73). Per-metric
    multipliers (vs one flat scalar) reflect that watch/roll metrics move more than the
    NPL stock in a downturn — grounded in this repo's 2007-vs-2015 vintage ratios."""
    sc = cfg["stress"]["scenarios"][scenario]
    rows = []
    for k, mult in sc["multipliers"].items():
        c = cfg["metrics"][k]
        hw = c.get("higher_is_worse", True)
        cur = this_vals.get(k, np.nan)
        stressed = cur * mult
        rows.append({
            "metric": c["label"],
            "current": round(cur, 2),
            "multiplier": mult,
            "stressed": round(stressed, 2),
            "red (limit)": c["red"],
            "RAG current": rag(cur, c["amber"], c["red"], hw),
            "RAG stressed": rag(stressed, c["amber"], c["red"], hw),
        })
    return pd.DataFrame(rows)


# --- Limit utilisation / headroom (daily-layer analog; APS 113 Att.D EAD 6) #
def limit_utilisation(cfg: dict, this_vals: dict) -> pd.DataFrame:
    """How much of each limit is used, and the headroom remaining. This is the monthly
    analog of the daily facility/limit-excess monitoring APS 113 Att. D (EAD para 6)
    and Basel CRE36.92 require; the true daily layer is described in docs/governance.md."""
    rows = []
    for k, c in cfg["metrics"].items():
        v = this_vals.get(k, np.nan)
        red, amber = c["red"], c["amber"]
        hw = c.get("higher_is_worse", True)
        util = (100 * v / red) if hw else (100 * red / v if v else np.nan)
        head = (red - v) if hw else (v - red)
        rows.append({
            "metric": c["label"],
            "this_period": round(v, 2) if v == v else np.nan,
            "limit (red)": red,
            "utilisation_vs_limit_pct": round(util, 1) if util == util else np.nan,
            "headroom_to_limit": round(head, 2) if head == head else np.nan,
            "RAG": rag(v, amber, red, hw),
        })
    return pd.DataFrame(rows)

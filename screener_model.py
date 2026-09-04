#!/usr/bin/env python3
"""
screener_model.py — fundamental + technical scoring model for NSE stocks.

Input : screener_data/bundle.json.gz  +  screener_data/prices.csv.gz   (from build_dataset.py)
Output: model_output.json  (everything the HTML model needs)
        ranked_longterm.csv, ranked_swing_long.csv, ranked_swing_short.csv
        factors.csv  (every raw metric for every stock — audit trail)

    python3 screener_model.py --data ~/screener_data

Scoring philosophy
  * Every raw factor → cross-sectional percentile (0–100) inside the universe.
    Valuation factors are 50/50 blended with a within-sector percentile so a
    bank is judged against banks, a chemical company against chemicals.
  * Factors roll up into 11 pillars (6 fundamental, 5 technical), each 0–100.
  * A "mode" is a weight vector over pillars + a direction. Composites are
    recomputed live in the HTML, so weights here are only the defaults.
  * A pillar with <50% of its factors measured is null; in the composite a null
    pillar counts as neutral (50) — unknown is not good — and "coverage" shows
    how much of the weight was actually measured.
  * Lenders (banks/NBFCs/insurers) get a separate factor set — D/E, OPM, ROCE,
    CFO/PAT are meaningless for them.
"""
import os, sys, json, gzip, math, argparse
from datetime import datetime
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--data", default=os.path.expanduser("~/screener_data"))
ap.add_argument("--out", default=None)
ap.add_argument("--min-bars", type=int, default=260)
args = ap.parse_args()
DATA = args.data; OUT = args.out or DATA
os.makedirs(OUT, exist_ok=True)

def log(*a): print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)

# ----------------------------------------------------------------------------- load
log("loading bundle")
with gzip.open(os.path.join(DATA, "bundle.json.gz"), "rt") as f:
    bundle = json.load(f)
univ = pd.DataFrame(bundle["universe"])
funds = bundle["fundamentals"]
log(f"universe {len(univ)}, fundamentals {len(funds)}")
log("loading prices")
px = pd.read_csv(os.path.join(DATA, "prices.csv.gz"), parse_dates=["date"])
px = px.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"])
groups = {s: g.reset_index(drop=True) for s, g in px.groupby("symbol")}
ASOF = px["date"].max().strftime("%Y-%m-%d")
log(f"prices: {len(groups)} symbols, as of {ASOF}")

# ----------------------------------------------------------------------------- helpers
def fnum(x):
    try:
        if x is None: return None
        v = float(x)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None

def series(stmt, names):
    """Return [(date, value)] newest→oldest for the first line-item name present with ≥1 value."""
    if not stmt: return []
    for n in names:
        d = stmt.get(n)
        if d:
            items = [(k, fnum(v)) for k, v in d.items() if fnum(v) is not None]
            if items:
                return sorted(items, key=lambda kv: kv[0], reverse=True)
    return []

def vals(stmt, names, n=None):
    s = [v for _, v in series(stmt, names)]
    return s if n is None else s[:n]

def nth(stmt, names, k=0):
    s = vals(stmt, names)
    return s[k] if len(s) > k else None

def safe_div(a, b):
    a, b = fnum(a), fnum(b)
    if a is None or b is None or b == 0: return None
    return a / b

def cagr(new, old, years):
    new, old = fnum(new), fnum(old)
    if new is None or old is None or old <= 0 or new <= 0 or years <= 0: return None
    return (new / old) ** (1 / years) - 1

def growth(new, old):
    new, old = fnum(new), fnum(old)
    if new is None or old is None or old == 0: return None
    if old < 0:  # loss → profit / loss → bigger loss: use abs base, sign by direction
        return (new - old) / abs(old)
    return new / old - 1

def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def r(x, nd=4):
    return None if x is None else round(float(x), nd)

REV = ["Total Revenue", "Operating Revenue"]
EBIT = ["EBIT", "Operating Income"]
OPINC = ["Operating Income", "EBIT"]
EBITDA = ["EBITDA", "Normalized EBITDA"]
NI = ["Net Income Common Stockholders", "Net Income", "Net Income From Continuing Operation Net Minority Interest"]
PBT = ["Pretax Income"]
INT = ["Interest Expense", "Interest Expense Non Operating", "Total Other Finance Cost"]
EPS = ["Diluted EPS", "Basic EPS"]
DEP = ["Reconciled Depreciation", "Depreciation And Amortization", "Depreciation Amortization Depletion"]
OTHINC = ["Other Non Operating Income Expenses", "Other Income Expense"]
EQ = ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"]
DEBT = ["Total Debt"]
CASH = ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]
CA = ["Current Assets"]; CL = ["Current Liabilities"]
RECV = ["Accounts Receivable", "Receivables", "Gross Accounts Receivable"]
INV = ["Inventory"]
TA = ["Total Assets"]
NPPE = ["Net PPE"]; GPPE = ["Gross PPE"]
SHARES = ["Ordinary Shares Number", "Share Issued"]
CFO = ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]
CAPEX = ["Capital Expenditure"]
FCF = ["Free Cash Flow"]
DIV = ["Cash Dividends Paid", "Common Stock Dividend Paid"]
NII = ["Net Interest Income"]
PROV = ["Credit Losses Provision", "Provision For Loan Lease And Other Losses"]

LENDER_WORDS = ["bank", "credit", "mortgage", "insurance", "financial conglomerate", "lending", "finance"]

def is_lender(sector, industry):
    s = (sector or "").lower(); i = (industry or "").lower()
    if "financial" not in s: return False
    if "capital markets" in i or "asset management" in i or "exchange" in i: return False
    return any(w in i for w in LENDER_WORDS) or s == "financial services" and i == ""

# ----------------------------------------------------------------------------- fundamentals
def fundamentals(sym, rec, mcap):
    info = rec.get("info") or {}
    ia, iq, ba, bq, ca, cq = (rec.get(k) or {} for k in ["inc_a", "inc_q", "bs_a", "bs_q", "cf_a", "cf_q"])
    sector = info.get("sector") or "Unknown"; industry = info.get("industry") or "Unknown"
    lender = is_lender(sector, industry)
    F = {"sector": sector, "industry": industry, "lender": lender, "stmt_fx": 1.0}
    # Yahoo serves a few companies' statements in USD (e.g. INFY). Detect via book value per share × shares vs statement equity.
    eq0 = nth(ba, EQ); bv, sh0 = fnum(info.get("bookValue")), fnum(info.get("sharesOutstanding"))
    if eq0 and bv and sh0 and eq0 > 0:
        ratio = bv * sh0 / eq0
        if 40 < ratio < 150:
            fx = round(ratio, 2)
            def scale(stmt):
                return {k: ({d: (v * fx if isinstance(v, (int, float)) else v) for d, v in row.items()} if not any(w in k for w in ("Shares", "Tax Rate", "Share Issued")) else row) for k, row in stmt.items()}
            ia, iq, ba, bq, ca, cq = scale(ia), scale(iq), scale(ba), scale(bq), scale(ca), scale(cq)
            rec["inc_a"], rec["inc_q"], rec["bs_a"], rec["bs_q"], rec["cf_a"], rec["cf_q"] = ia, iq, ba, bq, ca, cq
            F["stmt_fx"] = fx

    rev = vals(ia, REV); ni = vals(ia, NI); ebit = vals(ia, EBIT); opinc = vals(ia, OPINC); ebitda = vals(ia, EBITDA)
    eps = vals(ia, EPS); pbt = vals(ia, PBT); intr = vals(ia, INT); dep = vals(ia, DEP); oth = vals(ia, OTHINC)
    eq = vals(ba, EQ); debt = vals(ba, DEBT); cash = vals(ba, CASH); ta = vals(ba, TA)
    recv = vals(ba, RECV); inv = vals(ba, INV); shares = vals(ba, SHARES); cur_a = vals(ba, CA); cur_l = vals(ba, CL)
    cfo = vals(ca, CFO); capex = vals(ca, CAPEX); fcf = vals(ca, FCF); divp = vals(ca, DIV)
    qrev = vals(iq, REV); qni = vals(iq, NI); qop = vals(iq, OPINC); qeps = vals(iq, EPS)
    fy_dates = [d for d, _ in series(ia, REV)] or [d for d, _ in series(ia, NI)]
    q_dates = [d for d, _ in series(iq, REV)] or [d for d, _ in series(iq, NI)]
    F["fy_latest"] = fy_dates[0] if fy_dates else None
    F["q_latest"] = q_dates[0] if q_dates else None
    F["n_fy"] = len(rev); F["n_q"] = len(qrev)

    # size — Yahoo's quarterly series often skips a quarter, so never "sum the last four":
    # TTM = latest FY + quarters after FY end − the same quarters a year earlier (all must exist).
    def qmap(names):
        return {d: v for d, v in series(iq, names)}
    def year_before(d):
        return f"{int(d[:4]) - 1}{d[4:]}"
    def ttm(a_names, q_names):
        fy = nth(ia, a_names); fyd = F["fy_latest"]
        qm = qmap(q_names)
        if fy is None or not fyd: return None, False
        after = [d for d in qm if d > fyd]
        if not after: return fy, False
        prior = [year_before(d) for d in after]
        if all(p in qm for p in prior):
            return fy + sum(qm[d] for d in after) - sum(qm[p] for p in prior), True
        return fy, False
    F["rev_fy"] = rev[0] if rev else None
    F["pat_fy"] = ni[0] if ni else None
    F["ebit_fy"] = ebit[0] if ebit else (opinc[0] if opinc else None)
    F["ebitda_fy"] = ebitda[0] if ebitda else None
    F["rev_ttm"], _ = ttm(REV, REV)
    F["pat_ttm"], F["ttm_from_q"] = ttm(NI, NI)
    def q_yoy(names, k=0):
        qm = qmap(names); ds = sorted(qm, reverse=True)
        if len(ds) <= k: return None
        d = ds[k]; p = year_before(d)
        return growth(qm[d], qm[p]) if p in qm else None

    # growth
    F["sales_g1"] = growth(rev[0], rev[1]) if len(rev) > 1 else None
    F["sales_g3"] = cagr(rev[0], rev[3], 3) if len(rev) > 3 else (cagr(rev[0], rev[2], 2) if len(rev) > 2 else None)
    F["pat_g1"] = growth(ni[0], ni[1]) if len(ni) > 1 else None
    F["pat_g3"] = cagr(ni[0], ni[3], 3) if len(ni) > 3 else (cagr(ni[0], ni[2], 2) if len(ni) > 2 else None)
    F["eps_g3"] = cagr(eps[0], eps[3], 3) if len(eps) > 3 else None
    # quarterly YoY — matched to the same quarter a year earlier by date
    F["q_rev_yoy"] = q_yoy(REV, 0)
    F["q_pat_yoy"] = q_yoy(NI, 0)
    F["q_rev_yoy_prev"] = q_yoy(REV, 1)
    F["q_pat_yoy_prev"] = q_yoy(NI, 1)
    F["q_rev_accel"] = (F["q_rev_yoy"] - F["q_rev_yoy_prev"]) if F["q_rev_yoy"] is not None and F["q_rev_yoy_prev"] is not None else None
    F["q_pat_accel"] = (F["q_pat_yoy"] - F["q_pat_yoy_prev"]) if F["q_pat_yoy"] is not None and F["q_pat_yoy_prev"] is not None else None
    F["q_pat_qoq"] = None  # not reliable with Yahoo's quarter gaps; kept for schema
    F["ttm_pat_vs_fy"] = growth(F["pat_ttm"], F["pat_fy"]) if F["ttm_from_q"] else None

    # margins
    op_series = ebit if ebit else opinc                      # for ROCE / interest cover (Screener: EBIT)
    m_series = ebitda if ebitda else (opinc if opinc else ebit)  # for OPM (Screener: operating profit before D&A)
    F["opm"] = safe_div(m_series[0], rev[0]) if m_series and rev else None
    F["opm_3y_avg"] = avg([safe_div(o, rv) for o, rv in zip(m_series[:3], rev[:3])]) if m_series and rev else None
    F["opm_delta"] = (F["opm"] - F["opm_3y_avg"]) if F["opm"] is not None and F["opm_3y_avg"] is not None else None
    F["opm_1y_delta"] = (safe_div(m_series[0], rev[0]) - safe_div(m_series[1], rev[1])) if len(m_series) > 1 and len(rev) > 1 and safe_div(m_series[1], rev[1]) is not None and safe_div(m_series[0], rev[0]) is not None else None
    F["ebit_margin"] = safe_div(op_series[0], rev[0]) if op_series and rev else None
    F["npm"] = safe_div(ni[0], rev[0]) if ni and rev else None
    F["q_opm"] = safe_div(qop[0], qrev[0]) if qop and qrev else None
    _qo, _qr = qmap(OPINC), qmap(REV)
    _d0 = q_dates[0] if q_dates else None; _p0 = year_before(_d0) if _d0 else None
    F["q_opm_yoy_delta"] = (safe_div(_qo.get(_d0), _qr.get(_d0)) - safe_div(_qo.get(_p0), _qr.get(_p0))) if _d0 and _p0 in _qo and _p0 in _qr and safe_div(_qo.get(_d0), _qr.get(_d0)) is not None and safe_div(_qo.get(_p0), _qr.get(_p0)) is not None else None

    # returns on capital
    avg_eq = avg(eq[:2]) if eq else None
    F["roe"] = safe_div(ni[0], avg_eq) if ni and avg_eq else None
    F["roa"] = safe_div(ni[0], avg(ta[:2])) if ni and ta else None
    cap_emp = [(e + (d if d is not None else 0)) for e, d in zip(eq, (debt + [None] * 4)[:len(eq)])] if eq else []
    F["roce"] = safe_div(op_series[0], cap_emp[0]) if op_series and cap_emp else None
    F["roce_3y_avg"] = avg([safe_div(o, c) for o, c in zip(op_series[:3], cap_emp[:3])]) if op_series and cap_emp else None
    F["roe_3y_avg"] = avg([safe_div(n, e) for n, e in zip(ni[:3], eq[:3])]) if ni and eq else None

    # balance sheet
    F["de"] = safe_div(debt[0], eq[0]) if debt and eq and eq[0] > 0 else (0.0 if eq and not debt else None)
    net_debt = (debt[0] - (cash[0] if cash else 0)) if debt else None
    F["net_debt_ebitda"] = safe_div(net_debt, ebitda[0]) if net_debt is not None and ebitda and ebitda[0] > 0 else None
    F["int_cover"] = safe_div(op_series[0], intr[0]) if op_series and intr and intr[0] > 0 else None
    F["current_ratio"] = safe_div(cur_a[0], cur_l[0]) if cur_a and cur_l else None
    F["dilution_3y"] = growth(shares[0], shares[min(3, len(shares) - 1)]) if len(shares) > 1 else None
    sh = rec.get("shares_hist") or {}
    if sh:
        ks = sorted(sh); first, last = fnum(sh[ks[0]]), fnum(sh[ks[-1]])
        F["dilution_hist"] = growth(last, first)
    else:
        F["dilution_hist"] = None
    F["debt_change_1y"] = growth(debt[0], debt[1]) if len(debt) > 1 and debt[1] > 0 else None

    # cash conversion
    F["cfo_pat"] = safe_div(cfo[0], ni[0]) if cfo and ni and ni[0] > 0 else None
    n3 = min(3, len(cfo), len(ni))
    F["cfo_pat_3y"] = safe_div(sum(cfo[:n3]), sum(ni[:n3])) if n3 >= 2 and sum(ni[:n3]) > 0 else None
    F["fcf"] = fcf[0] if fcf else ((cfo[0] + capex[0]) if cfo and capex else None)
    F["fcf_yield"] = safe_div(F["fcf"], mcap) if mcap else None
    F["fcf_pos_years"] = sum(1 for x in (fcf if fcf else [c + x for c, x in zip(cfo, capex)])[:3] if x > 0) if (fcf or (cfo and capex)) else None
    F["capex_dep"] = safe_div(-capex[0], dep[0]) if capex and dep and dep[0] > 0 else None
    F["capex_sales"] = safe_div(-capex[0], rev[0]) if capex and rev else None
    F["div_payout"] = safe_div(-divp[0], ni[0]) if divp and ni and ni[0] > 0 else None
    F["other_inc_pbt"] = safe_div(oth[0], pbt[0]) if oth and pbt and pbt[0] > 0 else None

    # working capital
    F["rec_days"] = safe_div(recv[0], rev[0]) * 365 if recv and rev and safe_div(recv[0], rev[0]) is not None else None
    F["rec_days_prev"] = safe_div(recv[1], rev[1]) * 365 if len(recv) > 1 and len(rev) > 1 and safe_div(recv[1], rev[1]) is not None else None
    F["rec_days_delta"] = (F["rec_days"] - F["rec_days_prev"]) if F["rec_days"] is not None and F["rec_days_prev"] is not None else None
    F["inv_days"] = safe_div(inv[0], rev[0]) * 365 if inv and rev and safe_div(inv[0], rev[0]) is not None else None
    F["inv_days_delta"] = (safe_div(inv[0], rev[0]) - safe_div(inv[1], rev[1])) * 365 if len(inv) > 1 and len(rev) > 1 and safe_div(inv[1], rev[1]) is not None and safe_div(inv[0], rev[0]) is not None else None
    F["gross_block_g1"] = growth(vals(ba, GPPE)[0], vals(ba, GPPE)[1]) if len(vals(ba, GPPE)) > 1 else None

    # ownership
    hold = rec.get("holders") or {}
    def hv(key):
        d = hold.get(key) or {}
        return fnum(next(iter(d.values()), None)) if d else None
    F["promoter"] = fnum(info.get("heldPercentInsiders")) or hv("insidersPercentHeld")
    F["institutions"] = fnum(info.get("heldPercentInstitutions")) or hv("institutionsPercentHeld")

    # valuation
    F["pe"] = safe_div(mcap, F["pat_ttm"]) if F["pat_ttm"] and F["pat_ttm"] > 0 else None
    F["pe_yahoo"] = fnum(info.get("trailingPE"))
    F["pe_fwd"] = fnum(info.get("forwardPE"))
    F["pb"] = safe_div(mcap, eq[0]) if eq and eq[0] > 0 else fnum(info.get("priceToBook"))
    ev = mcap + (debt[0] if debt else 0) - (cash[0] if cash else 0)
    F["ev_ebitda"] = safe_div(ev, ebitda[0]) if ebitda and ebitda[0] > 0 else fnum(info.get("enterpriseToEbitda"))
    F["ev_sales"] = safe_div(ev, F["rev_ttm"]) if F["rev_ttm"] else None
    F["ps"] = safe_div(mcap, F["rev_ttm"]) if F["rev_ttm"] else None
    g_for_peg = F["pat_g3"] if F["pat_g3"] is not None else F["pat_g1"]
    F["peg"] = safe_div(F["pe"], g_for_peg * 100) if F["pe"] and g_for_peg and g_for_peg > 0 else None
    F["earnings_yield"] = safe_div(1, F["pe"]) if F["pe"] else (safe_div(F["pat_ttm"], mcap) if F["pat_ttm"] is not None else None)
    dr, cp0 = fnum(info.get("dividendRate")), fnum(info.get("currentPrice"))
    F["div_yield"] = (dr / cp0) if dr and cp0 else fnum(info.get("dividendYield"))
    if F["div_yield"] is not None and F["div_yield"] > 0.25: F["div_yield"] /= 100.0  # Yahoo reports this field in percent
    F["analyst_n"] = fnum(info.get("numberOfAnalystOpinions"))
    tgt = fnum(info.get("targetMeanPrice")); cp = fnum(info.get("currentPrice"))
    F["analyst_upside"] = (tgt / cp - 1) if tgt and cp else None
    F["analyst_rec"] = info.get("recommendationKey")
    F["beta"] = fnum(info.get("beta"))

    # lender-specific
    if lender:
        nii = vals(ia, NII); prov = vals(ia, PROV)
        F["nii_g1"] = growth(nii[0], nii[1]) if len(nii) > 1 else None
        F["prov_pct_nii"] = safe_div(prov[0], nii[0]) if prov and nii else None
        F["leverage"] = safe_div(ta[0], eq[0]) if ta and eq else None
    return F

# ----------------------------------------------------------------------------- technicals
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values

def technicals(sym, g, bench):
    c = g["close"].values.astype(float); h = g["high"].values.astype(float); l = g["low"].values.astype(float)
    v = g["volume"].values.astype(float); n = len(c)
    T = {"bars": n}
    if n < 60:
        return T
    last = c[-1]
    T["price"] = last
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    T["ema20"], T["ema50"], T["ema200"] = e20[-1], e50[-1], e200[-1]
    T["vs_ema20"] = last / e20[-1] - 1; T["vs_ema50"] = last / e50[-1] - 1; T["vs_ema200"] = last / e200[-1] - 1
    T["ema_stack"] = int(e20[-1] > e50[-1] > e200[-1]) - int(e20[-1] < e50[-1] < e200[-1])  # +1 bull, -1 bear, 0 mixed
    T["ema200_slope"] = e200[-1] / e200[-21] - 1 if n > 220 else None
    T["ema50_slope"] = e50[-1] / e50[-11] - 1
    T["above_200_days"] = int(np.sum(c[-60:] > e200[-60:])) if n >= 200 else None
    def ret(k): return c[-1] / c[-1 - k] - 1 if n > k else None
    T["ret_1w"], T["ret_1m"], T["ret_3m"], T["ret_6m"], T["ret_12m"] = ret(5), ret(21), ret(63), ret(126), ret(252)
    T["mom_12_1"] = (c[-22] / c[-253] - 1) if n > 253 else None
    dr = np.diff(np.log(c))
    T["vol_20"] = float(np.std(dr[-20:]) * math.sqrt(252)); T["vol_60"] = float(np.std(dr[-60:]) * math.sqrt(252))
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values
    T["atr"] = float(atr[-1]); T["atr_pct"] = float(atr[-1] / last)
    T["sharpe_6m"] = float(np.mean(dr[-126:]) / (np.std(dr[-126:]) + 1e-12) * math.sqrt(252)) if n > 126 else None
    T["sharpe_3m"] = float(np.mean(dr[-63:]) / (np.std(dr[-63:]) + 1e-12) * math.sqrt(252))
    # relative strength vs benchmark (aligned by date)
    for k, nm in [(63, "rs_3m"), (126, "rs_6m"), (252, "rs_12m")]:
        if bench is not None and n > k:
            d0, d1 = g["date"].iloc[-1 - k], g["date"].iloc[-1]
            b = bench[(bench["date"] >= d0) & (bench["date"] <= d1)]
            if len(b) > 2:
                T[nm] = (c[-1] / c[-1 - k]) / (b["close"].iloc[-1] / b["close"].iloc[0]) - 1
            else: T[nm] = None
        else: T[nm] = None
    # RS line at new high?
    if bench is not None and n > 252:
        m = g[["date", "close"]].merge(bench[["date", "close"]], on="date", suffixes=("", "_b"))
        if len(m) > 252:
            rl = (m["close"] / m["close_b"]).values
            T["rs_line_pos_52"] = float((rl[-1] - rl[-252:].min()) / (rl[-252:].max() - rl[-252:].min() + 1e-12))
        else: T["rs_line_pos_52"] = None
    else: T["rs_line_pos_52"] = None
    w = min(252, n)
    hi52, lo52 = float(np.max(h[-w:])), float(np.min(l[-w:]))
    T["hi52"], T["lo52"] = hi52, lo52
    T["from_52h"] = last / hi52 - 1; T["from_52l"] = last / lo52 - 1
    T["pos_52"] = (last - lo52) / (hi52 - lo52 + 1e-12)
    T["hi20"], T["lo20"] = float(np.max(h[-20:])), float(np.min(l[-20:]))
    T["hi60"], T["lo60"] = float(np.max(h[-60:])), float(np.min(l[-60:]))
    T["dist_hi20"] = T["hi20"] / last - 1; T["dist_hi60"] = T["hi60"] / last - 1
    T["dist_lo20"] = 1 - T["lo20"] / last
    T["tightness_20"] = (T["hi20"] - T["lo20"]) / last
    T["tightness_atr"] = (T["hi20"] - T["lo20"]) / (atr[-1] + 1e-12)
    T["eff_ratio_60"] = float(abs(c[-1] - c[-61]) / (np.sum(np.abs(np.diff(c[-61:]))) + 1e-12)) if n > 61 else None
    # Bollinger bandwidth percentile vs own 1y
    s = pd.Series(c); ma = s.rolling(20).mean(); sd = s.rolling(20).std()
    bw = ((ma + 2 * sd) - (ma - 2 * sd)) / ma
    bwv = bw.dropna().values[-252:]
    T["bb_width"] = float(bwv[-1]) if len(bwv) else None
    T["bb_width_pctl"] = float((bwv < bwv[-1]).mean()) if len(bwv) > 20 else None
    # volume / liquidity
    turn = c * v / 1e7  # Cr
    T["turnover_20"] = float(np.mean(turn[-20:])); T["turnover_90"] = float(np.mean(turn[-90:]))
    T["vol_ratio_20_50"] = float(np.mean(v[-20:]) / (np.mean(v[-50:]) + 1e-9))
    T["vol_ratio_5_50"] = float(np.mean(v[-5:]) / (np.mean(v[-50:]) + 1e-9))
    up = dr[-20:] > 0
    T["updown_vol_20"] = float((np.sum(v[-20:][up]) + 1) / (np.sum(v[-20:][~up]) + 1))
    # structure: higher highs / higher lows across three 20-day blocks
    hh = [np.max(h[-60:-40]), np.max(h[-40:-20]), np.max(h[-20:])]; ll = [np.min(l[-60:-40]), np.min(l[-40:-20]), np.min(l[-20:])]
    T["hh_count"] = int(hh[1] > hh[0]) + int(hh[2] > hh[1]); T["hl_count"] = int(ll[1] > ll[0]) + int(ll[2] > ll[1])
    T["structure"] = T["hh_count"] + T["hl_count"]  # 0..4
    # drawdown 1y
    cc = c[-252:]; peak = np.maximum.accumulate(cc)
    T["max_dd_1y"] = float(np.min(cc / peak - 1))
    # early-momentum setup flags (event study Sep-2026: each +1.2-1.5% 10d excess vs universe, 10/10 positive years)
    early = []
    hi20y = float(np.max(c[-21:-1])) if n > 21 else None      # prior 20d close-high, excl today
    hi50y = float(np.max(c[-51:-1])) if n > 51 else None
    r5 = (c[-1] / c[-6] - 1) if n > 6 else None
    r50b = (c[-2] / c[-52] - 1) if n > 52 else None
    if T.get("bb_width_pctl") is not None and T["bb_width_pctl"] < 0.15 and hi20y and last > hi20y: early.append("squeeze release")
    if T.get("vol_ratio_5_50") and T["vol_ratio_5_50"] >= 2.0 and r5 is not None and abs(r5) < 0.03: early.append("volume wake")
    if hi50y and last >= hi50y and r50b is not None and r50b < 0.10: early.append("first thrust")
    # base-breakout patterns (Sep-2026 lab: A = uptrend+tight-base breakout, +5.7% 60d excess, 10/10 yrs;
    # B = 2y-high breakout from quiet base, +3.9% 20d excess but fades by 6m — a 1-month trade)
    T["breakout_watch"] = None
    if n > 190:
        bhi = float(np.max(c[-61:-1])); blo = float(np.min(c[-61:-1]))
        tightA = blo > 0 and (bhi / blo - 1) <= 0.25
        upA = (c[-64] / c[-190] - 1) >= 0.30
        above = (T.get("vs_ema200") or 0) > 0
        if tightA and upA and above and last > bhi and (T.get("vol_ratio_5_50") or 0) >= 1.3: early.append("base breakout")
        elif tightA and upA and above and 0.95 <= last / bhi < 1.0: T["breakout_watch"] = round(bhi, 2)
    if n > 400:
        w2 = min(500, n - 1)
        h2y = float(np.max(c[-w2 - 1:-1])); h1y = float(np.max(c[-251:-1])); l1y = float(np.min(c[-251:-1]))
        dormant = h2y > h1y * 1.001; quiet = l1y > 0 and (h1y / l1y - 1) <= 0.40
        if dormant and quiet and last > h2y and (T.get("vol_ratio_5_50") or 0) >= 1.3: early.append("multiyear breakout")
        elif dormant and quiet and 0.95 <= last / h2y < 1.0: T["breakout_watch"] = T["breakout_watch"] or round(h2y, 2)
    T["early_setup"] = ", ".join(early) if early else None
    T["gap_days_20"] = int(np.sum(abs(g["open"].values[-20:] / c[-21:-1] - 1) > 0.02)) if n > 21 else None
    T["up_days_20"] = int(np.sum(dr[-20:] > 0))
    # levels for the trade plan
    A = atr[-1]
    T["levels"] = {
        # stop 2->3 ATR + chandelier trail: 10y exit lab (Sep-2026) — 2xATR stop was the worst policy tested
        # (median MAE -8.6% shakes it out); wide stop + trail highest-close - 2.5xATR ~2.4x the per-trade profit.
        "long": {"trigger": round(T["hi20"], 2), "stop": round(T["hi20"] - 3 * A, 2), "alt_stop": round(min(T["lo20"], e50[-1]), 2),
                 "trail_mult": 2.5, "atr": round(A, 2),
                 "t1": round(T["hi20"] + 4 * A, 2), "t2": round(T["hi20"] + 6 * A, 2), "rr_at_mkt": round((T["hi20"] + 4 * A - last) / max(last - (T["hi20"] - 3 * A), 1e-9), 2) if last > T["hi20"] - 3 * A else None},
        "short": {"trigger": round(T["lo20"], 2), "stop": round(T["lo20"] + 3 * A, 2), "alt_stop": round(max(T["hi20"], e50[-1]), 2),
                  "trail_mult": 2.5, "atr": round(A, 2),
                  "t1": round(T["lo20"] - 4 * A, 2), "t2": round(T["lo20"] - 6 * A, 2)},
        "lt": {"buy_zone_lo": round(min(e50[-1], e200[-1]), 2), "buy_zone_hi": round(max(e50[-1], e200[-1]), 2),
               "stop": round(min(lo52 * 1.0, e200[-1] * 0.92), 2), "invalidation": "weekly close below 200 EMA with momentum negative"}}
    # chart series: weekly closes for 2y + last 60 daily with EMAs
    wk = g.set_index("date")["close"].resample("W-FRI").last().dropna()
    T["chart_w"] = [[d.strftime("%Y-%m-%d"), round(float(x), 2)] for d, x in wk.tail(104).items()]
    T["chart_d"] = [[g["date"].iloc[i].strftime("%Y-%m-%d"), round(float(c[i]), 2), round(float(e20[i]), 2), round(float(e50[i]), 2), round(float(e200[i]), 2), round(float(v[i]))] for i in range(max(0, n - 80), n)]
    return T

# ----------------------------------------------------------------------------- run per stock
bench = groups.get("NIFTY500") if "NIFTY500" in groups else groups.get("NIFTY50")
if bench is not None: bench = bench[["date", "close"]]
index_ret = {}
for ix in [s for s in groups if s.startswith("NIFTY") or s == "BANKNIFTY"]:
    gg = groups[ix]["close"].values
    index_ret[ix] = {k: (gg[-1] / gg[-1 - d] - 1) if len(gg) > d else None for k, d in [("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252)]}
    e = ema(gg, 200); index_ret[ix]["vs_ema200"] = gg[-1] / e[-1] - 1 if len(gg) > 200 else None

def best_shares(rec, sh_build):
    """Yahoo's share count is sometimes off by a clean multiple (unapplied split/bonus). Pick the count
    that agrees with PAT / EPS from the statements; fall back to the builder's count."""
    ia, ba = rec.get("inc_a") or {}, rec.get("bs_a") or {}
    sh_bs = nth(ba, SHARES); ni0 = nth(ia, NI); eps0 = nth(ia, EPS)
    cands = [c for c in (sh_build, sh_bs) if c and c > 0]
    if not cands: return sh_build, "build"
    if ni0 and eps0 and eps0 != 0 and ni0 / eps0 > 0:
        implied = ni0 / eps0
        best = min(cands, key=lambda c: abs(math.log(c / implied)))
        return best, ("build" if best == sh_build else "balance-sheet")
    return sh_build, "build"

rows = []
dropped = []
for _, u in univ.iterrows():
    sym = u["symbol"]; rec = funds.get(sym)
    if rec is None: continue
    price0 = fnum(u.get("price")); sh_build = fnum(u.get("shares"))
    sh, sh_src = best_shares(rec, sh_build)
    mcap = (price0 * sh) if price0 and sh else fnum(u.get("mcap"))
    if mcap is not None and bundle.get("mcap_floor_cr") and mcap < bundle["mcap_floor_cr"] * 1e7:
        dropped.append(sym); continue
    F = fundamentals(sym, rec, mcap)
    F["shares_used"] = sh; F["shares_src"] = sh_src
    if sh_src != "build" and sh_build and (sh_build / sh > 1.25 or sh_build / sh < 0.8):
        F["shares_note"] = f"Yahoo share count {sh_build/sh:.1f}x the statement count — using statements; market cap corrected"
    g = groups.get(sym)
    T = technicals(sym, g, bench) if g is not None else {"bars": 0}
    rows.append({"symbol": sym, "name": (rec.get("info") or {}).get("longName") or u.get("name") or sym, "mcap_cr": (mcap or 0) / 1e7,
                 "price": T.get("price") or fnum((rec.get("info") or {}).get("currentPrice")) or fnum(u.get("price")),
                 "summary": ((rec.get("info") or {}).get("longBusinessSummary") or "")[:600], "website": (rec.get("info") or {}).get("website"),
                 "F": F, "T": T, "rec": rec})
log(f"computed metrics for {len(rows)} stocks; dropped {len(dropped)} below the floor after share-count correction: {', '.join(dropped[:15])}")

# ----------------------------------------------------------------------------- factor table
FACTORS = {
    # key: (source, name, higher_is_better, pillar, sector_relative, lender_ok, nonlender_ok)
    "roce":          ("F", "roce", True, "quality", False, False, True),
    "roce_3y_avg":   ("F", "roce_3y_avg", True, "quality", False, False, True),
    "roe":           ("F", "roe", True, "quality", False, True, True),
    "roe_3y_avg":    ("F", "roe_3y_avg", True, "quality", False, True, True),
    "roa":           ("F", "roa", True, "quality", False, True, False),
    "opm":           ("F", "opm", True, "quality", True, False, True),
    "opm_delta":     ("F", "opm_delta", True, "quality", False, False, True),
    "npm":           ("F", "npm", True, "quality", True, True, False),
    "sales_g3":      ("F", "sales_g3", True, "growth", False, True, True),
    "sales_g1":      ("F", "sales_g1", True, "growth", False, True, True),
    "pat_g3":        ("F", "pat_g3", True, "growth", False, True, True),
    "pat_g1":        ("F", "pat_g1", True, "growth", False, True, True),
    "q_rev_yoy":     ("F", "q_rev_yoy", True, "growth", False, True, True),
    "q_pat_yoy":     ("F", "q_pat_yoy", True, "growth", False, True, True),
    "ttm_pat_vs_fy": ("F", "ttm_pat_vs_fy", True, "growth", False, True, True),
    "de":            ("F", "de", False, "balance", True, False, True),
    "net_debt_ebitda": ("F", "net_debt_ebitda", False, "balance", False, False, True),
    "int_cover":     ("F", "int_cover", True, "balance", False, False, True),
    "dilution_3y":   ("F", "dilution_3y", False, "balance", False, True, True),
    "current_ratio": ("F", "current_ratio", True, "balance", True, False, True),
    "leverage":      ("F", "leverage", False, "balance", True, True, False),
    "cfo_pat_3y":    ("F", "cfo_pat_3y", True, "cash", False, False, True),
    "cfo_pat":       ("F", "cfo_pat", True, "cash", False, False, True),
    "fcf_yield":     ("F", "fcf_yield", True, "cash", False, False, True),
    "fcf_pos_years": ("F", "fcf_pos_years", True, "cash", False, False, True),
    "other_inc_pbt": ("F", "other_inc_pbt", False, "cash", False, False, True),
    "rec_days_delta": ("F", "rec_days_delta", False, "cash", False, False, True),
    "pe":            ("F", "pe", False, "valuation", True, True, True),
    "pb":            ("F", "pb", False, "valuation", True, True, True),
    "ev_ebitda":     ("F", "ev_ebitda", False, "valuation", True, False, True),
    "peg":           ("F", "peg", False, "valuation", False, True, True),
    "earnings_yield": ("F", "earnings_yield", True, "valuation", True, True, True),
    "promoter":      ("F", "promoter", True, "ownership", False, True, True),
    "institutions":  ("F", "institutions", True, "ownership", False, True, True),
    "vs_ema200":     ("T", "vs_ema200", True, "trend", False, True, True),
    "vs_ema50":      ("T", "vs_ema50", True, "trend", False, True, True),
    "ema_stack":     ("T", "ema_stack", True, "trend", False, True, True),
    "ema200_slope":  ("T", "ema200_slope", True, "trend", False, True, True),
    "above_200_days": ("T", "above_200_days", True, "trend", False, True, True),
    "ret_3m":        ("T", "ret_3m", True, "momentum", False, True, True),
    "ret_6m":        ("T", "ret_6m", True, "momentum", False, True, True),
    "mom_12_1":      ("T", "mom_12_1", True, "momentum", False, True, True),
    "sharpe_6m":     ("T", "sharpe_6m", True, "momentum", False, True, True),
    "rs_3m":         ("T", "rs_3m", True, "rs", False, True, True),
    "rs_6m":         ("T", "rs_6m", True, "rs", False, True, True),
    "rs_12m":        ("T", "rs_12m", True, "rs", False, True, True),
    "rs_line_pos_52": ("T", "rs_line_pos_52", True, "rs", False, True, True),
    "pos_52":        ("T", "pos_52", True, "structure", False, True, True),
    "structure":     ("T", "structure", True, "structure", False, True, True),
    "dist_hi60":     ("T", "dist_hi60", False, "structure", False, True, True),
    "eff_ratio_60":  ("T", "eff_ratio_60", True, "structure", False, True, True),
    "max_dd_1y":     ("T", "max_dd_1y", True, "structure", False, True, True),
    "turnover_20":   ("T", "turnover_20", True, "volume", False, True, True),
    "updown_vol_20": ("T", "updown_vol_20", True, "volume", False, True, True),
    "vol_ratio_20_50": ("T", "vol_ratio_20_50", True, "volume", False, True, True),
}
PILLARS = ["quality", "growth", "balance", "cash", "valuation", "ownership", "trend", "momentum", "rs", "structure", "volume"]

df = pd.DataFrame([{"symbol": r_["symbol"], "sector": r_["F"]["sector"], "lender": r_["F"]["lender"],
                    **{k: (r_[src].get(nm) if src == "F" else r_["T"].get(nm)) for k, (src, nm, *_rest) in FACTORS.items()}} for r_ in rows])
# winsorise extreme ratios so one absurd print doesn't own a tail
for k in ["pe", "pb", "ev_ebitda", "peg", "int_cover", "cfo_pat", "cfo_pat_3y", "net_debt_ebitda", "de", "updown_vol_20", "vol_ratio_20_50"]:
    if k in df:
        s = pd.to_numeric(df[k], errors="coerce")
        lo, hi = s.quantile(0.01), s.quantile(0.99)
        df[k] = s.clip(lo, hi)

def pct_rank(s, higher):
    s = pd.to_numeric(s, errors="coerce")
    p = s.rank(pct=True, method="average") * 100
    return p if higher else 100 - p

pcts = pd.DataFrame(index=df.index)
for k, (src, nm, higher, pillar, sector_rel, lender_ok, non_ok) in FACTORS.items():
    mask = df["lender"].map(lambda L: lender_ok if L else non_ok)
    col = pd.to_numeric(df[k], errors="coerce").where(mask)
    p = pct_rank(col, higher)
    if sector_rel:
        ps = col.groupby(df["sector"]).transform(lambda x: (x.rank(pct=True, method="average") * 100) if len(x.dropna()) >= 8 else pd.Series(np.nan, index=x.index))
        if not higher: ps = 100 - ps
        p = np.where(ps.notna(), 0.5 * p + 0.5 * ps, p)
    pcts[k] = p

pillar_scores = pd.DataFrame(index=df.index)
pillar_cov = pd.DataFrame(index=df.index)
for pl in PILLARS:
    ks = [k for k, v in FACTORS.items() if v[3] == pl]
    sub = pcts[ks]
    cov = sub.notna().mean(axis=1)
    sc = sub.mean(axis=1)
    pillar_scores[pl] = sc.where(cov >= 0.5)
    pillar_cov[pl] = cov

MODES = {
    # Weights and gates reflect the Aug-2026 point-in-time backtest: fundamentals help over a year and are noise
    # over weeks, so the swing modes rank on technicals only and use fundamentals purely as gates.
    "longterm":   {"label": "Long-term value", "dir": "long", "review": "1 year", "hold_note": "buy in the 50–200 EMA zone, review yearly",
                   "w": {"quality": 20, "growth": 15, "balance": 10, "cash": 10, "valuation": 15, "ownership": 5, "trend": 10, "momentum": 5, "rs": 5, "structure": 3, "volume": 2},
                   "gates": {"turnover_min": 2, "pat_positive": True, "min_bars": 200, "pe_min": 3, "other_inc_max": 0.30, "dilution_max": 0.25}},
    "swing_long": {"label": "Swing — long", "dir": "long", "review": "2 weeks", "hold_note": "entries re-rank every 2 weeks; exits on the trail (3×ATR stop → 2.5×ATR chandelier), not the calendar",
                   "w": {"quality": 0, "growth": 0, "balance": 0, "cash": 0, "valuation": 0, "ownership": 0, "trend": 22, "momentum": 26, "rs": 24, "structure": 18, "volume": 10},
                   "gates": {"turnover_min": 25, "above_ema200": True, "atr_pct_min": 0.015, "min_bars": 200, "max_ext": 0.30, "pat_positive": True, "dilution_max": 0.10, "cfo_pat_min": 0.5,
                             "growth_min": 40, "quality_min": 40}},  # fundamental confirm: 3y sweep (Aug-2026) — best excess & win rate at every hold ≥ 3W, halves drawdown
    "swing_short": {"label": "Swing — short", "dir": "short", "review": "2 weeks", "hold_note": "no standalone edge in the backtest — use only with a bearish market regime",
                    "w": {"quality": 0, "growth": 0, "balance": 0, "cash": 0, "valuation": 0, "ownership": 0, "trend": 22, "momentum": 26, "rs": 24, "structure": 18, "volume": 10},
                    "gates": {"turnover_min": 25, "below_ema200": True, "atr_pct_min": 0.015, "min_bars": 200}},
}
INVERT_FOR_SHORT = ["quality", "growth", "balance", "cash", "valuation", "trend", "momentum", "rs", "structure"]  # volume & ownership stay

def composite(i, mode):
    """Weighted pillar average. A pillar with no data counts as neutral (50) — unknown is not good —
    and the share of weight actually measured is returned as coverage."""
    m = MODES[mode]; num = 0.0; measured = 0.0; wt = sum(m["w"].values())
    for pl, w in m["w"].items():
        if w == 0: continue
        s = pillar_scores.at[i, pl]
        if s is None or (isinstance(s, float) and math.isnan(s)):
            s = 50.0
        else:
            measured += w
            if m["dir"] == "short" and pl in INVERT_FOR_SHORT: s = 100 - s
        num += w * s
    return (num / wt) if wt > 0 else None, (measured / wt) if wt > 0 else 0.0

def gate_fails(F, T, mode, gates=None):
    g = gates or MODES[mode]["gates"]; out = []
    if g.get("min_bars") and (T.get("bars") or 0) < g["min_bars"]: out.append("history")
    if g.get("turnover_min") and not ((T.get("turnover_20") or 0) >= g["turnover_min"]): out.append("turnover")
    if g.get("pat_positive") and not ((F.get("pat_ttm") or 0) > 0): out.append("loss-making")
    if g.get("above_ema200") and not ((T.get("vs_ema200") or 0) > 0): out.append("below 200 EMA")
    if g.get("below_ema200") and not ((T.get("vs_ema200") or 0) < 0): out.append("above 200 EMA")
    if g.get("atr_pct_min") and not ((T.get("atr_pct") or 0) >= g["atr_pct_min"]): out.append("ATR too low")
    if g.get("max_ext") is not None and (T.get("vs_ema200") is not None) and T["vs_ema200"] > g["max_ext"]: out.append("over-extended")
    if g.get("pe_min") and F.get("pe") is not None and F["pe"] < g["pe_min"]: out.append("P/E < %g (one-off gain?)" % g["pe_min"])
    if g.get("other_inc_max") and F.get("other_inc_pbt") is not None and F["other_inc_pbt"] > g["other_inc_max"]: out.append("other income")
    if g.get("dilution_max") is not None:
        dil = F.get("dilution_3y") if F.get("dilution_3y") is not None else F.get("dilution_hist")
        if dil is not None and dil > g["dilution_max"]: out.append("dilution")
    if g.get("cfo_pat_min") is not None and not F.get("lender") and F.get("cfo_pat_3y") is not None and F["cfo_pat_3y"] < g["cfo_pat_min"]: out.append("cash conversion")
    return out

# ----------------------------------------------------------------------------- red flags
def flags_for(F, T):
    fl = []
    def add(sev, code, text): fl.append({"sev": sev, "code": code, "text": text})
    if not F["lender"]:
        if F.get("cfo_pat_3y") is not None and F["cfo_pat_3y"] < 0.5: add("red", "CFO", f"3y CFO/PAT only {F['cfo_pat_3y']:.2f} — profits not turning into cash")
        elif F.get("cfo_pat_3y") is not None and F["cfo_pat_3y"] < 0.8: add("amber", "CFO", f"3y CFO/PAT {F['cfo_pat_3y']:.2f} — below 0.8, check working capital")
        if F.get("int_cover") is not None and F["int_cover"] < 1.5: add("red", "INTCOV", f"Interest cover {F['int_cover']:.1f}x — debt service at risk")
        elif F.get("int_cover") is not None and F["int_cover"] < 3: add("amber", "INTCOV", f"Interest cover {F['int_cover']:.1f}x")
        if F.get("net_debt_ebitda") is not None and F["net_debt_ebitda"] > 4: add("red", "LEV", f"Net debt/EBITDA {F['net_debt_ebitda']:.1f}x")
        if F.get("other_inc_pbt") is not None and F["other_inc_pbt"] > 0.3: add("amber", "OTHINC", f"Other income is {F['other_inc_pbt']*100:.0f}% of PBT — core earnings weaker than headline")
        if F.get("rec_days_delta") is not None and F["rec_days_delta"] > 20 and (F.get("sales_g1") or 0) < 0.5: add("amber", "RECV", f"Receivable days up {F['rec_days_delta']:.0f} — collecting slower than it sells")
        if F.get("inv_days_delta") is not None and F["inv_days_delta"] > 25: add("amber", "INV", f"Inventory days up {F['inv_days_delta']:.0f}")
        if F.get("fcf_pos_years") is not None and F["fcf_pos_years"] == 0 and F.get("n_fy", 0) >= 3: add("amber", "FCF", "Negative free cash flow in each of the last 3 years")
        if F.get("opm_delta") is not None and F["opm_delta"] > 0.08 and (F.get("sales_g3") or 0) < 0.05: add("amber", "PEAKMGN", "Margins far above 3y average on flat sales — possible cyclical peak")
    dil = F.get("dilution_3y") if F.get("dilution_3y") is not None else F.get("dilution_hist")
    if dil is not None and dil > 0.25: add("red", "DILUTE", f"Share count up {dil*100:.0f}% — heavy dilution")
    elif dil is not None and dil > 0.10: add("amber", "DILUTE", f"Share count up {dil*100:.0f}%")
    if F.get("promoter") is not None and F["promoter"] < 0.25: add("amber", "PROM", f"Insider/promoter holding only {F['promoter']*100:.0f}%")
    if F.get("pat_ttm") is not None and F["pat_ttm"] <= 0: add("red", "LOSS", "Loss-making on a TTM basis")
    if F.get("pe") is not None and F["pe"] > 80: add("amber", "PE", f"P/E {F['pe']:.0f}x — priced for perfection")
    if F.get("q_pat_yoy") is not None and F["q_pat_yoy"] < -0.25: add("amber", "QPAT", f"Latest quarter PAT {F['q_pat_yoy']*100:.0f}% YoY")
    if F.get("shares_note"): add("grey", "DATA", F["shares_note"])
    if F.get("pe") and F.get("pe_yahoo") and (F["pe"] / F["pe_yahoo"] > 2 or F["pe"] / F["pe_yahoo"] < 0.5): add("grey", "DATA", f"Computed P/E {F['pe']:.0f}x vs Yahoo {F['pe_yahoo']:.0f}x — statements and price may be on different bases; verify")
    if F.get("stmt_fx", 1) != 1: add("grey", "DATA", f"Statements reported in USD — converted at {F['stmt_fx']:.0f}")
    if F.get("n_fy", 0) < 3: add("grey", "DATA", f"Only {F.get('n_fy', 0)} years of annual data — growth/averages unreliable")
    if T.get("bars", 0) < 260: add("grey", "DATA", f"Only {T.get('bars', 0)} price bars — 12m factors missing")
    if T.get("atr_pct") is not None and T["atr_pct"] < 0.012: add("amber", "DEAD", f"ATR {T['atr_pct']*100:.1f}% of price — may not move enough to pay for its stop")
    if T.get("turnover_20") is not None and T["turnover_20"] < 2: add("amber", "ILLIQ", f"20d turnover ₹{T['turnover_20']:.1f} Cr/day")
    if T.get("max_dd_1y") is not None and T["max_dd_1y"] < -0.5: add("amber", "DD", f"Fell {abs(T['max_dd_1y'])*100:.0f}% peak-to-trough in the last year")
    return fl

# ----------------------------------------------------------------------------- assemble
def fin_block(rec):
    ia, iq, ba, ca = rec.get("inc_a") or {}, rec.get("inc_q") or {}, rec.get("bs_a") or {}, rec.get("cf_a") or {}
    def pick(stmt, names, dates):
        d = None
        for n in names:
            if stmt.get(n): d = stmt[n]; break
        return [r(fnum((d or {}).get(x)), 0) for x in dates]
    a_dates = [d for d, _ in series(ia, REV)] or [d for d, _ in series(ia, NI)]
    a_dates = a_dates[:4]
    q_dates = ([d for d, _ in series(iq, REV)] or [d for d, _ in series(iq, NI)])[:6]
    out = {"a_dates": a_dates, "q_dates": q_dates,
           "a": {"rev": pick(ia, REV, a_dates), "ebit": pick(ia, EBIT, a_dates) if ia.get("EBIT") else pick(ia, OPINC, a_dates), "ebitda": pick(ia, EBITDA, a_dates),
                 "pat": pick(ia, NI, a_dates), "eps": [r(fnum((ia.get("Diluted EPS") or ia.get("Basic EPS") or {}).get(x)), 2) for x in a_dates],
                 "interest": pick(ia, INT, a_dates), "other_inc": pick(ia, OTHINC, a_dates),
                 "equity": pick(ba, EQ, a_dates), "debt": pick(ba, DEBT, a_dates), "cash": pick(ba, CASH, a_dates), "recv": pick(ba, RECV, a_dates), "inv": pick(ba, INV, a_dates),
                 "shares": pick(ba, SHARES, a_dates), "gross_block": pick(ba, GPPE, a_dates), "assets": pick(ba, TA, a_dates),
                 "cfo": pick(ca, CFO, a_dates), "capex": pick(ca, CAPEX, a_dates), "fcf": pick(ca, FCF, a_dates), "div": pick(ca, DIV, a_dates)},
           "q": {"rev": pick(iq, REV, q_dates), "op": pick(iq, OPINC, q_dates), "pat": pick(iq, NI, q_dates),
                 "eps": [r(fnum((iq.get("Diluted EPS") or iq.get("Basic EPS") or {}).get(x)), 2) for x in q_dates]}}
    if rec.get("F_lender"):
        out["a"]["nii"] = pick(ia, NII, a_dates); out["a"]["prov"] = pick(ia, PROV, a_dates)
    return out

stocks = []
for i, r_ in enumerate(rows):
    F, T = r_["F"], r_["T"]
    fl = flags_for(F, T)
    comps = {}
    for m in MODES:
        c, cov = composite(i, m)
        fails = gate_fails(F, T, m)
        for gk, pl, lbl in (("growth_min", "growth", "growth unconfirmed"), ("quality_min", "quality", "quality unconfirmed")):
            gv = MODES[m]["gates"].get(gk)
            pv = pillar_scores.at[i, pl]
            if gv is not None and not (isinstance(pv, (int, float)) and not math.isnan(pv) and pv >= gv): fails.append(lbl)
        comps[m] = {"score": r(c, 1), "cov": r(cov, 2), "gates": fails}
    r_["rec"]["F_lender"] = F["lender"]
    stocks.append({
        "symbol": r_["symbol"], "name": r_["name"], "sector": F["sector"], "industry": F["industry"], "lender": F["lender"],
        "mcap_cr": r(r_["mcap_cr"], 0), "price": r(r_["price"], 2), "summary": r_["summary"], "website": r_["website"],
        "F": {k: (r(v, 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in F.items()},
        "T": {k: (r(v, 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in T.items() if k not in ("chart_w", "chart_d", "levels")},
        "levels": T.get("levels"), "chart_w": T.get("chart_w"), "chart_d": T.get("chart_d"),
        "pct": {k: r(pcts.at[i, k], 0) for k in FACTORS if not (isinstance(pcts.at[i, k], float) and math.isnan(pcts.at[i, k]))},
        "pillars": {pl: r(pillar_scores.at[i, pl], 1) if not (isinstance(pillar_scores.at[i, pl], float) and math.isnan(pillar_scores.at[i, pl])) else None for pl in PILLARS},
        "pillar_cov": {pl: r(pillar_cov.at[i, pl], 2) for pl in PILLARS},
        "flags": fl, "n_red": sum(1 for x in fl if x["sev"] == "red"), "n_amber": sum(1 for x in fl if x["sev"] == "amber"),
        "scores": comps, "fin": fin_block(r_["rec"]),
    })

# ----------------------------------------------------------------------------- NSE exchange data: move quality, trifecta, filed results
def nse_exchange_block(stocks):
    """Delivery-% + futures-OI move-quality per stock, the trifecta list, and each stock's latest
    FILED quarterly numbers. All from DATA/nse (collected by the morning pull). Absent → no-op."""
    import glob as _g
    nsedir = os.path.join(DATA, "nse")
    if not os.path.isdir(nsedir): return None
    # --- delivery panel: last 80 sessions from daily bhavcopies (fallback: packed panel)
    Dfr = []
    for f in sorted(_g.glob(os.path.join(nsedir, "bhav", "sec_*.csv")))[-80:]:
        try:
            d = pd.read_csv(f, skipinitialspace=True)
            d.columns = [c.strip().upper() for c in d.columns]
            d = d[d["SERIES"].astype(str).str.strip() == "EQ"]
            Dfr.append(pd.DataFrame({"date": os.path.basename(f)[4:14], "symbol": d["SYMBOL"].str.strip(),
                                     "close": pd.to_numeric(d["CLOSE_PRICE"], errors="coerce"),
                                     "qty": pd.to_numeric(d["TTL_TRD_QNTY"], errors="coerce"),
                                     "deliv_per": pd.to_numeric(d["DELIV_PER"], errors="coerce")}))
        except Exception: pass
    if len(Dfr) < 60:
        p = os.path.join(nsedir, "delivery_panel.csv.gz")
        if os.path.exists(p):
            dp_all = pd.read_csv(p)
            keep_dates = sorted(dp_all["date"].unique())[-80:]
            Dfr = [dp_all[dp_all["date"].isin(keep_dates)]]
    if not Dfr: return None
    D = pd.concat(Dfr)
    CL = D.pivot_table(index="date", columns="symbol", values="close").sort_index()
    QT = D.pivot_table(index="date", columns="symbol", values="qty").sort_index()
    DP = D.pivot_table(index="date", columns="symbol", values="deliv_per").sort_index()
    if len(CL) < 60: return None
    nse_asof = str(CL.index[-1])
    ret5 = (CL.iloc[-1] / CL.iloc[-6] - 1) if len(CL) > 6 else None
    vr5 = QT.rolling(5).mean().iloc[-1] / QT.shift(5).rolling(50, min_periods=30).mean().iloc[-1]
    dp_now = DP.iloc[-1]
    dp_med = DP.shift(1).rolling(60, min_periods=40).median().iloc[-1]
    # --- futures OI: last 15 sessions (fallback: packed panel)
    oi5 = {}
    Ofr = []
    for f in sorted(_g.glob(os.path.join(nsedir, "fo", "fo_*.csv")))[-15:]:
        try: Ofr.append(pd.read_csv(f))
        except Exception: pass
    if not Ofr:
        p = os.path.join(nsedir, "fo_panel.csv.gz")
        if os.path.exists(p):
            fo_all = pd.read_csv(p)
            Ofr = [fo_all[fo_all["date"].isin(sorted(fo_all["date"].unique())[-15:])]]
    if Ofr:
        FO = pd.concat(Ofr)
        if "TckrSymb" in FO.columns: FO = FO.rename(columns={"TckrSymb": "symbol"})
        OIP = FO.pivot_table(index="date", columns="symbol", values="oi").sort_index()
        if len(OIP) > 6:
            oi5 = (OIP.iloc[-1] / OIP.iloc[-6] - 1).to_dict()
    # --- filed results panel
    filed = {}
    p = os.path.join(nsedir, "results_panel.csv.gz")
    if os.path.exists(p):
        try:
            R = pd.read_csv(p, parse_dates=["period_end_dt"])
            R = R[R["period_end_dt"].notna() & R["pat"].notna()]
            for sym, g in R.groupby("symbol"):
                g = g.sort_values("period_end_dt")
                last = g.iloc[-1]
                ago = g[(g["period_end_dt"] >= last["period_end_dt"] - pd.Timedelta(days=376)) &
                        (g["period_end_dt"] <= last["period_end_dt"] - pd.Timedelta(days=354))]
                d = {"pe": last["period_end_dt"].strftime("%Y-%m-%d"),
                     "sales_cr": r(last["net_sales"] / 100 if pd.notna(last.get("net_sales")) else (last["income"] / 100 if pd.notna(last.get("income")) else None), 0),
                     "pat_cr": r(last["pat"] / 100, 0), "eps": r(last.get("eps_dil") if pd.notna(last.get("eps_dil")) else last.get("eps"), 2),
                     "audited": last.get("audited") if isinstance(last.get("audited"), str) else None,
                     "filed_at": last.get("filed_at") if isinstance(last.get("filed_at"), str) else None}
                if len(ago):
                    a = ago.iloc[-1]
                    d["pat_yoy"] = r(growth(last["pat"], a["pat"]), 4)
                    sl, sa = last.get("net_sales") or last.get("income"), a.get("net_sales") or a.get("income")
                    if pd.notna(sl) and pd.notna(sa): d["sales_yoy"] = r(growth(sl, sa), 4)
                filed[sym] = {k: v for k, v in d.items() if v is not None}
        except Exception as e:
            log("nse: results panel unreadable:", str(e)[:80])
    # --- attach to stocks
    trifecta = []
    fresh = abs((pd.Timestamp(ASOF) - pd.Timestamp(nse_asof)).days) <= 7
    for s in stocks:
        sym = s["symbol"]
        if sym in filed:
            s["filed"] = filed[sym]
            ypat = None
            fq = s.get("fin", {}).get("q", {})
            qd = s.get("fin", {}).get("q_dates") or []
            pe = filed[sym].get("pe")
            if pe and pe in qd:
                v = fq.get("pat", [])[qd.index(pe)] if len(fq.get("pat", [])) > qd.index(pe) else None
                ypat = v / 1e7 if v else None   # INR → Cr
            fpat = filed[sym].get("pat_cr")
            if ypat and fpat and abs(ypat) > 5 and abs(fpat) > 5:
                ratio = fpat / ypat
                if ratio > 5 or ratio < 0.2:
                    s["flags"].append({"sev": "amber", "code": "DATA", "text": f"Filed (standalone) PAT Rs {fpat:,.0f} Cr vs Yahoo Rs {ypat:,.0f} Cr for the same quarter - consolidated/standalone gap or a data error; check the filings before trusting ratios"})
                    s["n_amber"] += 1
        if not fresh or sym not in dp_now.index: continue
        dpv, medv = dp_now.get(sym), dp_med.get(sym)
        r5 = ret5.get(sym) if ret5 is not None else None
        o5 = oi5.get(sym)
        v5 = vr5.get(sym)
        if dpv is None or (isinstance(dpv, float) and math.isnan(dpv)): continue
        spike = (medv is not None and not math.isnan(medv) and dpv >= 1.5 * medv and dpv >= 50)
        st = None
        if spike and o5 is not None and not math.isnan(o5) and o5 >= 0.10 and r5 is not None and abs(r5) < 0.05:
            st = "accumulating"; trifecta.append(sym)
        elif r5 is not None and r5 > 0.04:
            if medv is not None and not math.isnan(medv) and dpv <= 0.5 * medv: st = "hollow"
            elif o5 is not None and not math.isnan(o5) and o5 <= -0.08: st = "covering"
            elif (o5 is not None and not math.isnan(o5) and o5 > 0.02) or spike or (medv and not math.isnan(medv) and dpv >= 1.2 * medv): st = "backed"
        s["mq"] = {k: v for k, v in {"st": st, "dp": r(dpv, 1), "dp_med": r(medv, 1) if medv is not None and not math.isnan(medv) else None,
                                     "oi5": r(o5, 3) if o5 is not None and not math.isnan(o5) else None,
                                     "ret5": r(r5, 3) if r5 is not None and not math.isnan(r5) else None,
                                     "vr5": r(v5, 2) if v5 is not None and not math.isnan(v5) else None}.items() if v is not None}
    log(f"nse: move-quality for {sum(1 for s in stocks if 'mq' in s)} stocks (asof {nse_asof}, fresh={fresh}), "
        f"filed results for {sum(1 for s in stocks if 'filed' in s)}, trifecta {len(trifecta)}")
    return {"asof": nse_asof, "fresh": bool(fresh), "trifecta": sorted(trifecta), "have_oi": bool(oi5)}

nse_meta = None
try:
    nse_meta = nse_exchange_block(stocks)
except Exception as e:
    log("nse block failed (continuing without):", str(e)[:120])

# sector aggregates
sec = {}
for s in stocks:
    d = sec.setdefault(s["sector"], {"n": 0, "ret_3m": [], "ret_6m": [], "vs_ema200": [], "pe": [], "sales_g3": [], "roce": []})
    d["n"] += 1
    for k, src in [("ret_3m", "T"), ("ret_6m", "T"), ("vs_ema200", "T"), ("pe", "F"), ("sales_g3", "F"), ("roce", "F")]:
        v = s[src].get(k)
        if v is not None: d[k].append(v)
sectors = {k: {"n": v["n"], **{m: r(float(np.median(v[m])), 4) if v[m] else None for m in ["ret_3m", "ret_6m", "vs_ema200", "pe", "sales_g3", "roce"]},
               "pct_above_200": r(float(np.mean([x > 0 for x in v["vs_ema200"]])), 2) if v["vs_ema200"] else None} for k, v in sec.items()}

# sector 1-month medians too (rotation strip)
for s in stocks:
    sec[s["sector"]].setdefault("ret_1m", [])
    if s["T"].get("ret_1m") is not None: sec[s["sector"]]["ret_1m"].append(s["T"]["ret_1m"])
for k, v in sec.items():
    sectors[k]["ret_1m"] = r(float(np.median(v["ret_1m"])), 4) if v.get("ret_1m") else None

# ----------------------------------------------------------------------------- market regime
def regime_block():
    b = groups.get("NIFTY500") if "NIFTY500" in groups else groups.get("NIFTY50")
    out_ = {"index": "NIFTY500" if "NIFTY500" in groups else "NIFTY50"}
    if b is None or len(b) < 120: return out_
    c = b["close"].values.astype(float)
    er = pd.Series(c).pipe(lambda s: (s - s.shift(60)).abs() / s.diff().abs().rolling(60).sum()).dropna()
    out_["eff_ratio_60"] = r(float(er.iloc[-1]), 4)
    out_["eff_ratio_pctl"] = r(float((er < er.iloc[-1]).mean()), 2)   # vs its own history in this file (≈4y); Mihir's long-run cut is the 25th pct
    out_["regime"] = "TRENDING" if out_["eff_ratio_pctl"] > 0.25 else "CHOPPY"
    e200 = ema(c, 200); e50 = ema(c, 50)
    out_["vs_ema200"] = r(c[-1] / e200[-1] - 1, 4); out_["vs_ema50"] = r(c[-1] / e50[-1] - 1, 4)
    out_["ret_1m"] = r(c[-1] / c[-22] - 1, 4) if len(c) > 22 else None
    out_["ret_3m"] = r(c[-1] / c[-64] - 1, 4) if len(c) > 64 else None
    out_["dd_from_high_1y"] = r(float(c[-1] / np.max(c[-252:]) - 1), 4)
    # breadth from the universe
    T_ = [s["T"] for s in stocks if s["T"].get("bars", 0) >= 200]
    n = max(1, len(T_))
    out_["breadth"] = {"n": len(T_),
                       "above_200": r(sum(1 for t in T_ if (t.get("vs_ema200") or 0) > 0) / n, 3),
                       "above_50": r(sum(1 for t in T_ if (t.get("vs_ema50") or 0) > 0) / n, 3),
                       "above_20": r(sum(1 for t in T_ if (t.get("vs_ema20") or 0) > 0) / n, 3),
                       "new_52w_high": sum(1 for t in T_ if (t.get("from_52h") or -1) > -0.01),
                       "new_52w_low": sum(1 for t in T_ if (t.get("from_52l") or 1) < 0.01),
                       "median_ret_1m": r(float(np.median([t["ret_1m"] for t in T_ if t.get("ret_1m") is not None])), 4),
                       "bull_stack": r(sum(1 for t in T_ if t.get("ema_stack") == 1) / n, 3),
                       "bear_stack": r(sum(1 for t in T_ if t.get("ema_stack") == -1) / n, 3)}
    return out_
regime = regime_block()

# ----------------------------------------------------------------------------- score history → "what changed"
# kept inside fundamentals/ so the GitHub Actions cache (which already persists that folder) carries it between runs
HIST = os.path.join(OUT, "fundamentals", "_history"); os.makedirs(HIST, exist_ok=True)
hist_rows = []
for s in stocks:
    row = {"symbol": s["symbol"], "price": s["price"]}
    for m in MODES:
        row[f"score_{m}"] = s["scores"][m]["score"]; row[f"pass_{m}"] = int(not s["scores"][m]["gates"])
    hist_rows.append(row)
hist_df = pd.DataFrame(hist_rows)
hist_df.to_csv(os.path.join(HIST, f"scores_{ASOF}.csv"), index=False)
hfiles = sorted(f for f in os.listdir(HIST) if f.startswith("scores_") and f.endswith(".csv") and f != f"scores_{ASOF}.csv")
# keep the folder small: at most ~90 runs
for old in hfiles[:-90]:
    try: os.remove(os.path.join(HIST, old))
    except Exception: pass
hfiles = hfiles[-90:]
def load_hist(fname):
    d = pd.read_csv(os.path.join(HIST, fname)).set_index("symbol"); return d
prev1 = load_hist(hfiles[-1]) if hfiles else None
prev5 = load_hist(hfiles[-5]) if len(hfiles) >= 5 else (load_hist(hfiles[0]) if hfiles else None)
movers = {"prev_date": hfiles[-1][7:17] if hfiles else None, "prev5_date": (hfiles[-5] if len(hfiles) >= 5 else (hfiles[0] if hfiles else "scores_"))[7:17] or None, "modes": {}}
for m in MODES:
    cur_rank = {s["symbol"]: i for i, s in enumerate(sorted([x for x in stocks if not x["scores"][m]["gates"] and x["scores"][m]["score"] is not None], key=lambda x: -x["scores"][m]["score"]))}
    top_now = {sym for sym, i in cur_rank.items() if i < 50}
    mm = {"new_top50": [], "left_top50": [], "gainers": [], "losers": [], "newly_pass": [], "newly_fail": []}
    if prev1 is not None and f"score_{m}" in prev1:
        pv = prev1[prev1[f"pass_{m}"] == 1].sort_values(f"score_{m}", ascending=False)
        top_prev = set(pv.index[:50])
        mm["new_top50"] = sorted(top_now - top_prev, key=lambda x: cur_rank[x])[:15]
        mm["left_top50"] = sorted(top_prev - top_now)[:15]
        cur_scores = {s["symbol"]: s["scores"][m]["score"] for s in stocks if s["scores"][m]["score"] is not None}
        deltas = [(sym, cur_scores[sym] - float(prev1.at[sym, f"score_{m}"])) for sym in cur_scores if sym in prev1.index and pd.notna(prev1.at[sym, f"score_{m}"])]
        deltas.sort(key=lambda x: -x[1])
        mm["gainers"] = [{"symbol": a, "d": r(b, 1)} for a, b in deltas[:10]]
        mm["losers"] = [{"symbol": a, "d": r(b, 1)} for a, b in deltas[-10:][::-1]]
        pass_now = {s["symbol"] for s in stocks if not s["scores"][m]["gates"]}
        pass_prev = set(prev1[prev1[f"pass_{m}"] == 1].index)
        mm["newly_pass"] = sorted(pass_now - pass_prev, key=lambda x: cur_rank.get(x, 9999))[:15]
        mm["newly_fail"] = sorted(pass_prev - pass_now)[:15]
    movers["modes"][m] = mm
for s in stocks:
    h = {}
    for m in MODES:
        sc = s["scores"][m]["score"]
        d1 = (sc - float(prev1.at[s["symbol"], f"score_{m}"])) if prev1 is not None and s["symbol"] in prev1.index and sc is not None and pd.notna(prev1.at[s["symbol"], f"score_{m}"]) else None
        d5 = (sc - float(prev5.at[s["symbol"], f"score_{m}"])) if prev5 is not None and s["symbol"] in prev5.index and sc is not None and pd.notna(prev5.at[s["symbol"], f"score_{m}"]) else None
        h[m] = {"d1": r(d1, 1), "d5": r(d5, 1)}
    s["hist"] = h
# ----------------------------------------------------------------------------- live forward track record
# every past run's top-10 (gates passed) marked to today's price, plus the gated-universe average from the same day.
price_now = {s["symbol"]: s["price"] for s in stocks if s["price"]}
track = {"modes": {}, "n_runs": len(hfiles)}
for m in MODES:
    rows_ = []
    for f in hfiles:
        d = f[7:17]
        try: h = load_hist(f)
        except Exception: continue
        if f"score_{m}" not in h or "price" not in h: continue
        elig = h[(h[f"pass_{m}"] == 1) & h[f"score_{m}"].notna() & h["price"].notna()].sort_values(f"score_{m}", ascending=False)
        if len(elig) < 20: continue
        sign = -1 if MODES[m]["dir"] == "short" else 1
        def ret(sym):
            p0 = float(elig.at[sym, "price"]); p1 = price_now.get(sym)
            return sign * (p1 / p0 - 1) if p0 and p1 else None
        top = [(sym, ret(sym)) for sym in elig.index[:10]]
        top = [(a_, b_) for a_, b_ in top if b_ is not None]
        uni = [ret(sym) for sym in elig.index]; uni = [x for x in uni if x is not None]
        if not top or not uni: continue
        rows_.append({"date": d, "days": int((pd.Timestamp(ASOF) - pd.Timestamp(d)).days), "top10": r(float(np.mean([b_ for _, b_ in top])), 4),
                      "universe": r(float(np.mean(uni)), 4), "hit": r(float(np.mean([b_ > 0 for _, b_ in top])), 2),
                      "picks": [{"s": a_, "r": r(b_, 4)} for a_, b_ in top]})
    track["modes"][m] = rows_[::-1]  # newest first
log(f"history: {len(hfiles)} prior runs on file; movers computed vs {movers['prev_date']}; track record rows: {sum(len(v) for v in track['modes'].values())}")

factor_meta = {k: {"src": v[0], "higher": v[2], "pillar": v[3], "sector_rel": v[4], "lender": v[5], "nonlender": v[6]} for k, v in FACTORS.items()}
out = {"built": datetime.now().strftime("%Y-%m-%d %H:%M"), "asof": ASOF, "data_built": bundle.get("built"), "mcap_floor_cr": bundle.get("mcap_floor_cr"),
       "n": len(stocks), "index": index_ret, "sectors": sectors, "regime": regime, "movers": movers, "track": track, "nse": nse_meta, "modes": MODES, "invert_for_short": INVERT_FOR_SHORT, "pillars": PILLARS, "factors": factor_meta, "stocks": stocks}
with open(os.path.join(OUT, "model_output.json"), "w") as f:
    json.dump(out, f, separators=(",", ":"))

# On the cloud runner (no exchange-data machine), compute the Multibagger Radar from the committed
# radar_inputs/ bundle + a Yahoo price pull, so the website's Radar tab refreshes daily too. Non-fatal.
if os.environ.get("GITHUB_ACTIONS"):
    _here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(_here, "radar_inputs")) and os.path.exists(os.path.join(_here, "mb_radar.py")):
        import subprocess
        try:
            log("cloud radar: computing from radar_inputs bundle")
            subprocess.run([sys.executable, os.path.join(_here, "mb_radar.py"), "--cloud", "--data", OUT], check=True, timeout=1500)
        except Exception as e:
            log("cloud radar skipped:", str(e)[:120])
log(f"wrote model_output.json ({os.path.getsize(os.path.join(OUT, 'model_output.json'))/1e6:.1f} MB)")

# CSV audit trail + ranked lists
flat = []
for s in stocks:
    row = {"symbol": s["symbol"], "name": s["name"], "sector": s["sector"], "industry": s["industry"], "lender": s["lender"], "mcap_cr": s["mcap_cr"], "price": s["price"],
           **{f"f_{k}": v for k, v in s["F"].items() if k not in ("sector", "industry", "lender")}, **{f"t_{k}": v for k, v in s["T"].items()},
           **{f"pillar_{k}": v for k, v in s["pillars"].items()}, **{f"score_{k}": v["score"] for k, v in s["scores"].items()},
           **{f"cov_{k}": v["cov"] for k, v in s["scores"].items()}, **{f"gates_{k}": ("pass" if not v["gates"] else "; ".join(v["gates"])) for k, v in s["scores"].items()},
           "n_red": s["n_red"], "n_amber": s["n_amber"], "flags": "; ".join(x["text"] for x in s["flags"])}
    flat.append(row)
fdf = pd.DataFrame(flat)
fdf.to_csv(os.path.join(OUT, "factors.csv"), index=False)
for m in MODES:
    cols = ["symbol", "name", "sector", "mcap_cr", "price", f"score_{m}", f"cov_{m}", f"gates_{m}"] + [f"pillar_{p}" for p in PILLARS] + ["t_turnover_20", "t_atr_pct", "t_vs_ema200", "t_ret_3m", "t_rs_6m", "f_pe", "f_roce", "f_sales_g3", "f_pat_g3", "f_cfo_pat_3y", "n_red", "n_amber", "flags"]
    cols = [c for c in cols if c in fdf]
    fdf.assign(_pass=(fdf[f"gates_{m}"] == "pass")).sort_values(["_pass", f"score_{m}"], ascending=[False, False])[cols].to_csv(os.path.join(OUT, f"ranked_{m}.csv"), index=False)
log("wrote factors.csv + ranked_*.csv")
for m in MODES:
    top = fdf[fdf[f"gates_{m}"] == "pass"].sort_values(f"score_{m}", ascending=False).head(10)
    log(f"top {m}: " + ", ".join(f"{a} {b:.0f}" for a, b in zip(top["symbol"], top[f"score_{m}"])))

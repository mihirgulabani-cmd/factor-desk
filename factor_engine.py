"""factor_engine.py — the scoring engine, importable. Extracted from screener_model.py so the
backtest and the live desk share one definition of every factor, pillar, mode and gate.

score_universe(univ, funds, groups, asof=None) → DataFrame(symbol, sector, lender, price, mcap_cr,
  pillars…, cov…, score_<mode>, gates_<mode>) computed point-in-time at `asof`
  (statements only if their period end + reporting lag ≤ asof; prices only ≤ asof).
"""
import math, json
from datetime import datetime, timedelta
import numpy as np, pandas as pd

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

def technicals(sym, g, bench, light=False):
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
    T["gap_days_20"] = int(np.sum(abs(g["open"].values[-20:] / c[-21:-1] - 1) > 0.02)) if n > 21 else None
    T["up_days_20"] = int(np.sum(dr[-20:] > 0))
    if light: return T
    # levels for the trade plan
    A = atr[-1]
    T["levels"] = {
        "long": {"trigger": round(T["hi20"], 2), "stop": round(T["hi20"] - 2 * A, 2), "alt_stop": round(min(T["lo20"], e50[-1]), 2),
                 "t1": round(T["hi20"] + 4 * A, 2), "t2": round(T["hi20"] + 6 * A, 2), "rr_at_mkt": round((T["hi20"] + 4 * A - last) / max(last - (T["hi20"] - 2 * A), 1e-9), 2) if last > T["hi20"] - 2 * A else None},
        "short": {"trigger": round(T["lo20"], 2), "stop": round(T["lo20"] + 2 * A, 2), "alt_stop": round(max(T["hi20"], e50[-1]), 2),
                  "t1": round(T["lo20"] - 4 * A, 2), "t2": round(T["lo20"] - 6 * A, 2)},
        "lt": {"buy_zone_lo": round(min(e50[-1], e200[-1]), 2), "buy_zone_hi": round(max(e50[-1], e200[-1]), 2),
               "stop": round(min(lo52 * 1.0, e200[-1] * 0.92), 2), "invalidation": "weekly close below 200 EMA with momentum negative"}}
    # chart series: weekly closes for 2y + last 60 daily with EMAs
    wk = g.set_index("date")["close"].resample("W-FRI").last().dropna()
    T["chart_w"] = [[d.strftime("%Y-%m-%d"), round(float(x), 2)] for d, x in wk.tail(104).items()]
    T["chart_d"] = [[g["date"].iloc[i].strftime("%Y-%m-%d"), round(float(c[i]), 2), round(float(e20[i]), 2), round(float(e50[i]), 2), round(float(e200[i]), 2), round(float(v[i]))] for i in range(max(0, n - 80), n)]
    return T


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


MODES = {
    # Weights and gates reflect the Aug-2026 point-in-time backtest: fundamentals help over a year and are noise
    # over weeks, so the swing modes rank on technicals only and use fundamentals purely as gates.
    "longterm":   {"label": "Long-term value", "dir": "long", "review": "1 year", "hold_note": "buy in the 50–200 EMA zone, review yearly",
                   "w": {"quality": 20, "growth": 15, "balance": 10, "cash": 10, "valuation": 15, "ownership": 5, "trend": 10, "momentum": 5, "rs": 5, "structure": 3, "volume": 2},
                   "gates": {"turnover_min": 2, "pat_positive": True, "min_bars": 200, "pe_min": 3, "other_inc_max": 0.30, "dilution_max": 0.25}},
    "swing_long": {"label": "Swing — long", "dir": "long", "review": "2 weeks", "hold_note": "ride while it works; re-rank every two weeks, not monthly",
                   "w": {"quality": 0, "growth": 0, "balance": 0, "cash": 0, "valuation": 0, "ownership": 0, "trend": 22, "momentum": 26, "rs": 24, "structure": 18, "volume": 10},
                   "gates": {"turnover_min": 25, "above_ema200": True, "atr_pct_min": 0.015, "min_bars": 200, "max_ext": 0.30, "pat_positive": True, "dilution_max": 0.10, "cfo_pat_min": 0.5,
                             "growth_min": 40, "quality_min": 40}},  # fundamental confirm: 3y sweep (Aug-2026) — best excess & win rate at every hold ≥ 3W, halves drawdown
    "swing_short": {"label": "Swing — short", "dir": "short", "review": "2 weeks", "hold_note": "no standalone edge in the backtest — use only with a bearish market regime",
                    "w": {"quality": 0, "growth": 0, "balance": 0, "cash": 0, "valuation": 0, "ownership": 0, "trend": 22, "momentum": 26, "rs": 24, "structure": 18, "volume": 10},
                    "gates": {"turnover_min": 25, "below_ema200": True, "atr_pct_min": 0.015, "min_bars": 200}},
}
INVERT_FOR_SHORT = ["quality", "growth", "balance", "cash", "valuation", "trend", "momentum", "rs", "structure"]  # volume & ownership stay


def composite_row(pillars_row, mode, weights=None):
    """Weighted pillar average. A pillar with no data counts as neutral (50) — unknown is not good —
    and the share of weight actually measured is returned as coverage."""
    m = MODES[mode]; W = weights or m["w"]; num = 0.0; measured = 0.0; wt = sum(W.values())
    for pl, w in W.items():
        if w == 0: continue
        s = pillars_row.get(pl)
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


ANNUAL_LAG_DAYS = 75     # Indian FY results are out by end-May; be conservative
QUARTER_LAG_DAYS = 50    # quarterly results within 45 days

def cut_statements(rec, asof):
    """Return a copy of rec with only statement columns whose period-end + lag ≤ asof. None → unchanged."""
    if asof is None: return rec
    out = dict(rec)
    for k in ("inc_a", "bs_a", "cf_a"):
        lim = (asof - timedelta(days=ANNUAL_LAG_DAYS)).strftime("%Y-%m-%d")
        out[k] = {row: {d: v for d, v in vals.items() if d <= lim} for row, vals in (rec.get(k) or {}).items()}
        out[k] = {row: vals for row, vals in out[k].items() if vals}
    for k in ("inc_q", "bs_q", "cf_q"):
        lim = (asof - timedelta(days=QUARTER_LAG_DAYS)).strftime("%Y-%m-%d")
        out[k] = {row: {d: v for d, v in vals.items() if d <= lim} for row, vals in (rec.get(k) or {}).items()}
        out[k] = {row: vals for row, vals in out[k].items() if vals}
    return out

def pillars_table(rows):
    """rows: list of dicts with symbol, F, T → (pillar_scores df, pillar_cov df, pcts df, df)"""
    df = pd.DataFrame([{"symbol": r_["symbol"], "sector": r_["F"]["sector"], "lender": r_["F"]["lender"],
                        **{k: (r_[src].get(nm) if src == "F" else r_["T"].get(nm)) for k, (src, nm, *_rest) in FACTORS.items()}} for r_ in rows])
    for k in ["pe", "pb", "ev_ebitda", "peg", "int_cover", "cfo_pat", "cfo_pat_3y", "net_debt_ebitda", "de", "updown_vol_20", "vol_ratio_20_50"]:
        if k in df:
            s = pd.to_numeric(df[k], errors="coerce"); lo, hi = s.quantile(0.01), s.quantile(0.99); df[k] = s.clip(lo, hi)
    def pct_rank(s, higher):
        s = pd.to_numeric(s, errors="coerce"); p = s.rank(pct=True, method="average") * 100
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
    ps_ = pd.DataFrame(index=df.index); cov_ = pd.DataFrame(index=df.index)
    for pl in PILLARS:
        ks = [k for k, v in FACTORS.items() if v[3] == pl]
        sub = pcts[ks]; cov = sub.notna().mean(axis=1); sc = sub.mean(axis=1)
        ps_[pl] = sc.where(cov >= 0.5); cov_[pl] = cov
    return ps_, cov_, pcts, df

def score_universe(univ, funds, groups, asof=None, mcap_floor_cr=1000, bench_name="NIFTY500", light=True, weights=None, log=None):
    """Point-in-time scoring. asof: pandas Timestamp or None (= latest)."""
    bench = groups.get(bench_name) if bench_name in groups else groups.get("NIFTY50")
    if bench is not None:
        bench = bench[["date", "close"]]
        if asof is not None: bench = bench[bench["date"] <= asof]
    rows = []
    for _, u in univ.iterrows():
        sym = u["symbol"]; rec0 = funds.get(sym)
        if rec0 is None: continue
        g = groups.get(sym)
        if asof is not None and g is not None:
            g = g[g["date"] <= asof]
            if len(g) == 0: continue
        sh_build = fnum(u.get("shares"))
        sh, sh_src = best_shares(rec0, sh_build)
        price0 = float(g["close"].iloc[-1]) if g is not None and len(g) else fnum(u.get("price"))
        mcap = (price0 * sh) if price0 and sh else fnum(u.get("mcap"))
        if mcap is None or mcap < mcap_floor_cr * 1e7: continue
        rec = cut_statements(rec0, asof)
        F = fundamentals(sym, rec, mcap)
        F["shares_used"] = sh; F["shares_src"] = sh_src
        T = technicals(sym, g.reset_index(drop=True), bench, light=light) if g is not None and len(g) >= 60 else {"bars": 0 if g is None else len(g)}
        rows.append({"symbol": sym, "price": price0, "mcap_cr": mcap / 1e7, "F": F, "T": T})
    if not rows: return pd.DataFrame()
    ps_, cov_, pcts, df = pillars_table(rows)
    out = pd.DataFrame({"symbol": [r_["symbol"] for r_ in rows], "sector": df["sector"], "lender": df["lender"],
                        "price": [r_["price"] for r_ in rows], "mcap_cr": [r_["mcap_cr"] for r_ in rows]})
    for pl in PILLARS:
        out["p_" + pl] = ps_[pl].values; out["cov_" + pl] = cov_[pl].values
    for m in MODES:
        sc, cv, gt = [], [], []
        for i, r_ in enumerate(rows):
            prow = {pl: (None if pd.isna(ps_.at[i, pl]) else float(ps_.at[i, pl])) for pl in PILLARS}
            c, cov = composite_row(prow, m, (weights or {}).get(m))
            fails = gate_fails(r_["F"], r_["T"], m)
            g = MODES[m]["gates"]
            for gk, pl, lbl in (("growth_min", "growth", "growth unconfirmed"), ("quality_min", "quality", "quality unconfirmed")):
                if g.get(gk) is not None and not (prow.get(pl) is not None and prow[pl] >= g[gk]): fails.append(lbl)
            sc.append(c); cv.append(cov); gt.append(fails)
        out["score_" + m] = sc; out["cov_" + m] = cv; out["gates_" + m] = gt
    # a few raw fields the backtest reports on
    out["turnover_20"] = [r_["T"].get("turnover_20") for r_ in rows]
    out["atr_pct"] = [r_["T"].get("atr_pct") for r_ in rows]
    out["vs_ema200"] = [r_["T"].get("vs_ema200") for r_ in rows]
    out["ret_6m"] = [r_["T"].get("ret_6m") for r_ in rows]
    out["n_fy"] = [r_["F"].get("n_fy") for r_ in rows]
    out["pe"] = [r_["F"].get("pe") for r_ in rows]
    return out

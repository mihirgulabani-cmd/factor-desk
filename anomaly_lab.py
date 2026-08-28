#!/usr/bin/env python3
"""
anomaly_lab.py — a harness for testing "is this an inefficiency?" in minutes, honestly.

A signal is a boolean matrix (dates × symbols) built from lagged features. For each signal:
  * forward returns at 5/10/20/40 days, EXCESS over the same-day eligible-universe mean
    (this strips out the market and the bull-market drift that fools most folk backtests)
  * a CONTROL — the same signal with its key condition removed — because a signal only
    means something if it beats the trade you would have made anyway
  * split-half by date (does it hold in both halves of the sample?)
  * per-symbol de-overlap (one event per symbol per 20 sessions) so n is honest

    python3 anomaly_lab.py --prices ~/Downloads/screener_data/prices.csv.gz
    python3 anomaly_lab.py --prices fno_daily_ohlc.csv.gz --min-turnover 2   # 15y F&O file

Everything uses data ≤ t for signals and close_t → close_{t+h} for returns. No look-ahead.
"""
import os, argparse
from datetime import datetime
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--prices", default=os.path.expanduser("~/Downloads/screener_data/prices.csv.gz"))
ap.add_argument("--out", default=None)
ap.add_argument("--min-turnover", type=float, default=5.0, help="₹Cr/day 20d avg for eligibility")
ap.add_argument("--dedup-days", type=int, default=20)
ap.add_argument("--tag", default="")
args = ap.parse_args()
def log(*a): print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)

log("loading", args.prices)
px = pd.read_csv(args.prices, parse_dates=["date"]).sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"])
idx_syms = [s for s in px["symbol"].unique() if s.startswith(("NIFTY", "BANKNIFTY", "^"))]
bench_sym = "NIFTY500" if "NIFTY500" in idx_syms else ("NIFTY50" if "NIFTY50" in idx_syms else None)
stk = px[~px["symbol"].isin(idx_syms)]
def wide(col): return stk.pivot(index="date", columns="symbol", values=col).sort_index()
C, H, L, V = wide("close"), wide("high"), wide("low"), wide("volume")
O = wide("open") if "open" in stk.columns else C.shift(1)
DATES = C.index; N = len(DATES)
log(f"{C.shape[1]} symbols × {N} days  ({DATES[0].date()} → {DATES[-1].date()})")

# ---------------------------------------------------------------- features (all as of day t)
def ema(df, n): return df.ewm(span=n, adjust=False, min_periods=n).mean()
R1 = C.pct_change()
RET = {k: C.pct_change(k) for k in (3, 5, 10, 20, 60, 120, 250)}
E20, E50, E200 = ema(C, 20), ema(C, 50), ema(C, 200)
VS200 = C / E200 - 1
V5, V20, V50 = V.rolling(5).mean(), V.rolling(20).mean(), V.rolling(50).mean()
VR5 = V5 / V50; VR1 = V / V50
TURN20 = (C * V).rolling(20).mean() / 1e7
TR = pd.concat([(H - L), (H - C.shift(1)).abs(), (L - C.shift(1)).abs()]).groupby(level=0).max() if False else np.maximum(H - L, np.maximum((H - C.shift(1)).abs(), (L - C.shift(1)).abs()))
ATRP = (pd.DataFrame(TR, index=DATES, columns=C.columns).ewm(alpha=1/14, adjust=False).mean() / C)
HI20, LO20 = H.rolling(20).max(), L.rolling(20).min()
HI252 = H.rolling(252).max()
POS52 = (C - L.rolling(252).min()) / (HI252 - L.rolling(252).min() + 1e-9)
RANGE = H - L
NR7 = RANGE.le(RANGE.rolling(7).min() + 1e-12)
MA20, SD20 = C.rolling(20).mean(), C.rolling(20).std()
BBW = (4 * SD20) / MA20
BBW_PCTL = BBW.rolling(252, min_periods=60).rank(pct=True)
DOWN3 = (R1 < 0).rolling(3).sum().eq(3)
GAP = O / C.shift(1) - 1
if bench_sym:
    b = px[px["symbol"] == bench_sym].set_index("date")["close"].reindex(DATES).ffill()
    RS20 = C.pct_change(20).sub(b.pct_change(20), axis=0)
    RSLINE = C.div(b, axis=0); RS_HI60 = RSLINE.ge(RSLINE.rolling(60).max() - 1e-12)
else:
    RS20 = C.pct_change(20) * np.nan; RS_HI60 = C * np.nan
ELIG = (TURN20 >= args.min_turnover) & C.notna() & (C.pct_change(250).notna())
BARS_OK = C.notna().rolling(260, min_periods=1).sum() >= 250

# sector map (only available when the desk's model_output.json sits next to the prices file)
SEC = None
mo = os.path.join(os.path.dirname(os.path.abspath(args.prices)), "model_output.json")
if os.path.exists(mo):
    import json
    d = json.load(open(mo)); SEC = {s["symbol"]: s["sector"] for s in d["stocks"]}
    log(f"sector map loaded for {len(SEC)} symbols")

# forward EXCESS returns: close_t → close_{t+h}, minus the same-day mean over the eligible universe
FWD, XS = {}, {}
for h in (5, 10, 20, 40):
    f = C.shift(-h) / C - 1
    base = f.where(ELIG).mean(axis=1)
    FWD[h] = f; XS[h] = f.sub(base, axis=0)

def dedup(sig):
    """One event per symbol per dedup-days sessions."""
    s = sig.fillna(False).astype(bool)
    arr = np.ascontiguousarray(s.values).copy()
    for j in range(arr.shape[1]):
        last = -10**9
        col = arr[:, j]
        for i in np.nonzero(col)[0]:
            if i - last < args.dedup_days: col[i] = False
            else: last = i
    return pd.DataFrame(arr, index=s.index, columns=s.columns)

RESULTS = []
def test(name, sig, control=None, note=""):
    sig = dedup(sig & ELIG & BARS_OK)
    n = int(sig.values.sum())
    row = {"signal": name, "n": n, "note": note}
    if n < 150:
        row["verdict"] = f"too few events ({n})"; RESULTS.append(row); log(f"{name:48s} n={n} — too few"); return
    mid = DATES[N // 2]
    for h in (10, 20, 40):
        x = XS[h].where(sig).stack().dropna()
        row[f"x{h}"] = float(x.mean()); row[f"w{h}"] = float((x > 0).mean())
    xa = XS[20].where(sig)
    row["h1"] = float(xa.loc[:mid].stack().dropna().mean()); row["h2"] = float(xa.loc[mid:].stack().dropna().mean())
    if control is not None:
        c = dedup(control & ELIG & BARS_OK)
        row["ctrl_n"] = int(c.values.sum())
        row["ctrl20"] = float(XS[20].where(c).stack().dropna().mean()) if row["ctrl_n"] > 50 else None
    edge = row["x20"] - (row.get("ctrl20") if row.get("ctrl20") is not None else 0)
    robust = (row["h1"] > 0) == (row["h2"] > 0) and row["h1"] * row["h2"] >= 0
    if row.get("ctrl20") is not None and row["x20"] <= row["ctrl20"]:
        row["verdict"] = "no edge vs control"
    elif row["x20"] <= 0:
        row["verdict"] = "negative"
    elif not robust:
        row["verdict"] = "not robust (halves disagree)"
    elif row["x20"] > 0.01 and row["w20"] > 0.5:
        row["verdict"] = "SURVIVOR"
    else:
        row["verdict"] = "weak positive"
    RESULTS.append(row)
    log(f"{name:48s} n={n:5d}  x10={row['x10']*100:+.2f}%  x20={row['x20']*100:+.2f}% (win {row['w20']*100:.0f}%)  x40={row['x40']*100:+.2f}%  halves {row['h1']*100:+.1f}/{row['h2']*100:+.1f}  ctrl20={('%+.2f%%' % (row['ctrl20']*100)) if row.get('ctrl20') is not None else '–'}  → {row['verdict']}")

log("=== batch ===")
prior_hi = HI252.shift(1)
bo52 = (C > prior_hi) & (C.shift(1) <= prior_hi.shift(1))
test("52w-high breakout + vol≥2x", bo52 & (VR1 >= 2), control=bo52 & (VR1 < 2), note="classic momentum breakout; control = same breakout on normal volume")
test("52w-high breakout + vol≥2x + not extended", bo52 & (VR1 >= 2) & (VS200 <= 0.5), control=bo52 & (VR1 >= 2))
tight = BBW_PCTL.shift(1) < 0.2
bo20 = (C > HI20.shift(1))
test("tight-base (BBW<20th pctl) 20d breakout", tight & bo20 & (VR1 >= 1.5), control=(~tight.fillna(False)) & bo20 & (VR1 >= 1.5), note="control = same breakout without the tight base")
test("NR7 then break up", NR7.shift(1) & (C > H.shift(1)), control=NR7.shift(1) & (C < L.shift(1)), note="control = NR7 break DOWN")
test("3 down days, still above 200EMA", DOWN3 & (VS200 > 0), control=DOWN3 & (VS200 < 0), note="pullback-buy; control = same in a downtrend")
test("3 down days above 200EMA + touch 20d low", DOWN3 & (VS200 > 0) & (L <= LO20.shift(1) * 1.005), control=DOWN3 & (VS200 > 0))
gapup = (GAP > 0.04)
test("gap up >4% on vol≥3x (drift proxy)", gapup & (VR1 >= 3), control=gapup & (VR1 < 1.5), note="results-day drift proxy without dates; control = gap on thin volume")
test("gap DOWN >4% on vol≥3x (short check)", (GAP < -0.04) & (VR1 >= 3), note="positive x20 here = gap-downs bounce; negative = they keep falling")
dry = (VR5 <= 0.5)
test("volume dry-up near 20d low, above 200EMA", dry & (C <= LO20.shift(1) * 1.01) & (VS200 > 0), control=dry & (C <= LO20.shift(1) * 1.01) & (VS200 < 0), note="Wyckoff-ish; control = same below 200EMA")
test("20d-high break + RS line 60d high", bo20 & RS_HI60 & (VR1 >= 1.2), control=bo20 & (~RS_HI60.fillna(False)) & (VR1 >= 1.2), note="leadership breakout; control = breakout with weak RS")
test("over-extended >60% above 200EMA (short check)", (VS200 > 0.6) & (RET[20] > 0.15), note="x20 NEGATIVE would mean extension mean-reverts tradably")
test("vol surge 2x, price flat (Mihir's)", (VR5 >= 2) & (RET[5].abs() < 0.02), control=(RET[5].abs() < 0.02), note="re-run in the harness; control = flat week, any volume")
if SEC is not None:
    sec_ser = pd.Series({s: SEC.get(s) for s in C.columns})
    R20 = C.pct_change(20)
    sec_med = R20.T.groupby(sec_ser).transform("median").T
    test("sector hot (+5% 20d), stock flat, above 200EMA", (sec_med > 0.05) & (R20 < 0.0) & (VS200 > 0), control=(sec_med > 0.05) & (R20 > 0.05), note="laggard catch-up; control = chasing the sector leader")

out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.prices)), f"anomaly_report{args.tag}.md")
df = pd.DataFrame(RESULTS)
lines = [f"# Anomaly lab — {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
         f"Data: `{os.path.basename(args.prices)}`, {C.shape[1]} symbols, {DATES[0].date()} → {DATES[-1].date()}. "
         f"Eligibility: 20d turnover ≥ ₹{args.min_turnover:g} Cr, ≥250 bars. One event per symbol per {args.dedup_days} sessions. "
         "All returns are EXCESS over the same-day eligible-universe mean (market and drift stripped out).", "",
         "| Signal | n | 10d | 20d | win20 | 40d | half1/half2 (20d) | control 20d | verdict |", "|---|---|---|---|---|---|---|---|---|"]
def pc(v): return "–" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v*100:+.2f}%"
for r in RESULTS:
    lines.append(f"| {r['signal']} | {r['n']} | {pc(r.get('x10'))} | **{pc(r.get('x20'))}** | {pc(r.get('w20'))} | {pc(r.get('x40'))} | {pc(r.get('h1'))} / {pc(r.get('h2'))} | {pc(r.get('ctrl20'))} | {r['verdict']} |")
lines += ["", "Notes: " + " · ".join(f"**{r['signal']}** — {r['note']}" for r in RESULTS if r.get("note")), "",
          "A signal only matters if its 20d column beats BOTH zero and its control, with the same sign in both halves. "
          "Everything else is the market, the filter, or luck."]
open(out, "w").write("\n".join(lines))
log("wrote", out)

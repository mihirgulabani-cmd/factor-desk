#!/usr/bin/env python3
"""edge_events.py — event lab on the NSE panels: delivery % + futures OI.

Method mirrors anomaly_lab.py: forward EXCESS returns vs the same-day eligible-universe mean,
per-symbol dedup (20 sessions), every signal judged against a CONTROL, halves consistency.
Window: whatever the panels hold (currently ~2y). Cash closes used for returns everywhere
(futures closes carry roll noise).
"""
import argparse, os
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--nse", default="/mnt/user-data/uploads/Downloads/screener_data/nse")
ap.add_argument("--min-turnover", type=float, default=5.0, help="Cr, 20d avg")
ap.add_argument("--dedup-days", type=int, default=20)
ap.add_argument("--out", default="data/edge_events_report.md")
a = ap.parse_args()

print("loading panels")
D = pd.read_csv(os.path.join(a.nse, "delivery_panel.csv.gz"), parse_dates=["date"])
F = pd.read_csv(os.path.join(a.nse, "fo_panel.csv.gz"), parse_dates=["date"])

CLOSE = D.pivot_table(index="date", columns="symbol", values="close")
QTY   = D.pivot_table(index="date", columns="symbol", values="qty")
DP    = D.pivot_table(index="date", columns="symbol", values="deliv_per")
OI    = F.pivot_table(index="date", columns="symbol", values="oi").reindex(CLOSE.index)
print(f"panel: {CLOSE.shape[0]} days × {CLOSE.shape[1]} symbols, {CLOSE.index[0].date()} → {CLOSE.index[-1].date()}; OI names: {OI.shape[1]}")

TURN = (CLOSE * QTY / 1e7).rolling(20, min_periods=10).mean()          # Cr
ELIG = (TURN >= a.min_turnover) & CLOSE.notna() & (CLOSE.rolling(60, min_periods=60).count() >= 60)

RET5  = CLOSE / CLOSE.shift(5) - 1
VR5   = QTY.rolling(5).mean() / QTY.shift(5).rolling(50, min_periods=30).mean()
DPMED = DP.shift(1).rolling(60, min_periods=30).median()
DPSPK = (DP >= 1.5 * DPMED) & (DP >= 50)
OI5   = OI / OI.shift(5) - 1

XS = {}
for h in (5, 10, 20):
    fwd = CLOSE.shift(-h) / CLOSE - 1
    fwd = fwd.where(ELIG)
    XS[h] = fwd.sub(fwd.mean(axis=1), axis=0)

def dedup(sig):
    m = np.ascontiguousarray(sig.fillna(False).to_numpy()).copy()
    n = a.dedup_days
    for j in range(m.shape[1]):
        col = m[:, j]; last = -10**9
        for i in range(len(col)):
            if col[i]:
                if i - last < n: col[i] = False
                else: last = i
    return pd.DataFrame(m, index=sig.index, columns=sig.columns)

def stats(sig):
    s = dedup(sig & ELIG)
    out = {}
    for h in (5, 10, 20):
        v = XS[h].where(s).stack().dropna()
        out[h] = (len(v), v.mean(), (v > 0).mean())
    ix = s.any(axis=1)
    days = s.index[ix]
    if len(days) > 4:
        mid = days[len(days)//2]
        h1 = XS[20].where(s.loc[:mid]).stack().dropna().mean()
        h2 = XS[20].where(s.loc[mid:]).stack().dropna().mean()
    else:
        h1 = h2 = np.nan
    return out, h1, h2

def test(name, sig, ctrl=None, note=""):
    st, h1, h2 = stats(sig)
    n, x20, win = st[20][0], st[20][1], st[20][2]
    if n < 60:
        print(f"{name:48s} n={n:5d} — too few"); return None
    c20 = None
    if ctrl is not None:
        cst, _, _ = stats(ctrl)
        c20 = cst[20][1] if cst[20][0] >= 40 else None
    halves_ok = (not np.isnan(h1)) and (not np.isnan(h2)) and (np.sign(h1) == np.sign(h2))
    if x20 is None or np.isnan(x20): verdict = "?"
    elif c20 is not None and x20 - c20 < 0.002 and x20 < c20 + 0.002: verdict = "no edge vs control"
    elif x20 <= 0: verdict = "negative"
    elif not halves_ok: verdict = "not robust (halves disagree)"
    elif x20 >= 0.005 and (c20 is None or x20 - c20 >= 0.004): verdict = "SURVIVOR"
    else: verdict = "weak positive"
    cs = f"ctrl20={cst[20][1]*100:+.2f}%" if c20 is not None else "ctrl20=–"
    line = (f"{name:48s} n={n:5d}  x5={st[5][1]*100:+.2f}%  x10={st[10][1]*100:+.2f}%  "
            f"x20={x20*100:+.2f}% (win {win*100:.0f}%)  halves {h1*100:+.2f}/{h2*100:+.2f}  {cs}  → {verdict}")
    print(line)
    return {"name": name, "n": n, "x5": st[5][1], "x10": st[10][1], "x20": x20, "win": win,
            "h1": h1, "h2": h2, "ctrl20": c20, "verdict": verdict, "note": note}

print("=== delivery % ===")
R = []
flat = RET5.abs() < 0.03
up5  = RET5 > 0.05
dn5  = RET5 < -0.05
R.append(test("delivery spike, price flat", DPSPK & flat, ctrl=DPSPK & up5,
              note="deliv% >=1.5x own 60d median and >=50, |5d ret|<3% — accumulation without price. Control: same spike after a >5% rally (chased)."))
R.append(test("vol surge 2x + delivery spike, price flat", (VR5 >= 2) & DPSPK & flat, ctrl=(VR5 >= 2) & flat & ~DPSPK.fillna(False),
              note="Mihir's volume-surge-flat WITH delivery confirmation vs WITHOUT — does delivery % rescue it?"))
R.append(test("fall >5% absorbed on high delivery (>=75%)", dn5 & (DP >= 75), ctrl=dn5 & (DP <= 40),
              note="strong hands taking delivery into a fall vs speculative selling"))
R.append(test("delivery drought (<0.5x median) after rally", up5 & (DP <= 0.5 * DPMED), ctrl=up5 & DPSPK,
              note="rally on LOW delivery (intraday churn) — should fade vs delivery-backed rally"))

print("=== futures OI (cash closes for returns) ===")
oiflat = RET5.abs() < 0.02
R.append(test("OI build >=15% in 5d, price flat", (OI5 >= 0.15) & oiflat, ctrl=(OI5 >= 0.15) & (RET5 > 0.03),
              note="positioning without price vs confirmed build-up"))
R.append(test("OI unwind <=-10%, price flat", (OI5 <= -0.10) & oiflat, ctrl=None, note="quiet unwind"))
R.append(test("rally >4% on OI unwind (short covering)", (RET5 > 0.04) & (OI5 <= -0.08), ctrl=(RET5 > 0.04) & (OI5 >= 0.08),
              note="short-covering rally vs fresh-longs rally — which continues?"))

print("=== combined ===")
R.append(test("delivery spike + OI build, price flat", DPSPK & (OI5 >= 0.10) & (RET5.abs() < 0.03),
              ctrl=(OI5 >= 0.10) & (RET5.abs() < 0.03) & ~DPSPK.fillna(False),
              note="the trifecta: cash delivery + derivative build-up + no price move yet"))

lines = [f"# Edge events — delivery % + OI ({CLOSE.index[0].date()} → {CLOSE.index[-1].date()}, {CLOSE.shape[1]} symbols, turnover ≥ {a.min_turnover:g} Cr)", "",
         "Excess forward returns vs same-day eligible-universe mean; per-symbol dedup 20 sessions; halves = first/second half of the window.", ""]
for r in R:
    if r is None: continue
    lines += [f"## {r['name']}  →  **{r['verdict']}**",
              f"- n={r['n']}, excess +5d {r['x5']*100:+.2f}% · +10d {r['x10']*100:+.2f}% · +20d {r['x20']*100:+.2f}% (win {r['win']*100:.0f}%), halves {r['h1']*100:+.2f}/{r['h2']*100:+.2f}"
              + (f", control 20d {r['ctrl20']*100:+.2f}%" if r['ctrl20'] is not None else ""),
              f"- {r['note']}", ""]
os.makedirs(os.path.dirname(a.out), exist_ok=True)
open(a.out, "w").write("\n".join(lines))
print("wrote", a.out)

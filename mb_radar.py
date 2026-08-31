#!/usr/bin/env python3
"""mb_radar.py — the Multibagger Radar: today's 0–5 precursor score for every NSE name ≥ ₹50 Cr.

Score (one point each, validated in the Aug-2026 odds study, 123k stock-months 2017-2025):
  fallen angel (≥60% below ATH) · volume regime shift (3m ≥ 1.5× 12m turnover) · small (< ₹500 Cr)
  · 6m momentum ≥ +30% · promoter added ≥ 1pp in 2 qtrs · hot sector (top-tercile 3m sector return)
  (−1 if promoter sold ≥ 1pp; −1 if cold sector)
Flags: ASM/GSM surveillance, thin liquidity. Sector heat tercile when the industry map exists.

    python3 mb_radar.py                    # scores from the shard snapshot (+ --refresh to top up prices first, Mac only)
    python3 mb_radar.py --refresh          # tail-refresh last ~15 sessions for all names via Yahoo (~4 min), then score

Writes radar.json next to the other outputs; build_html.py embeds it into the desk's Radar tab.
"""
import os, sys, json, glob, argparse
from datetime import datetime
import pandas as pd, numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--data", default=os.path.expanduser("~/screener_data"))
ap.add_argument("--refresh", action="store_true", help="Yahoo tail top-up of the price shards first (Mac only)")
ap.add_argument("--min-mcap", type=float, default=50.0)
ap.add_argument("--cloud", action="store_true", help="no shards: pull ~14 months of prices from Yahoo, read slow-changing inputs from radar_inputs/")
a = ap.parse_args()
MB = os.path.join(a.data, "mb"); NSE_D = os.path.join(a.data, "nse")
HERE = os.path.dirname(os.path.abspath(__file__))
INP = next((d for d in (os.path.join(a.data, "radar_inputs"), os.path.join(HERE, "radar_inputs")) if os.path.isdir(d)), None)

def log(*x): print(datetime.now().strftime("%H:%M:%S"), *x, flush=True)

shard_files = sorted(glob.glob(os.path.join(MB, "prices10y_shard*.csv.gz")))
if not shard_files and not a.cloud: sys.exit("no price shards — run mb_pull.py first (or use --cloud with a radar_inputs bundle)")
ATH_PRIOR = None
if a.cloud:
    if not INP: sys.exit("--cloud needs a radar_inputs/ bundle (made by a normal desk run of mb_radar.py)")
    import yfinance as yf
    from datetime import timedelta
    athf = pd.read_csv(os.path.join(INP, "ath.csv"))
    ATH_PRIOR = dict(zip(athf["symbol"].astype(str), pd.to_numeric(athf["ath"], errors="coerce")))
    syms = sorted(ATH_PRIOR)
    start = (datetime.now() - timedelta(days=430)).strftime("%Y-%m-%d")
    frames = []
    log(f"cloud mode: pulling ~14 months for {len(syms)} symbols")
    B = 80
    if os.environ.get("RADAR_FAKE_PULL") and shard_files:   # sandbox test of the cloud path without network
        _px = pd.concat([pd.read_csv(f) for f in shard_files]); _px["date"] = pd.to_datetime(_px["date"])
        frames = [_px[_px["date"] >= pd.Timestamp(start)][["symbol", "date", "close", "volume"]]]; syms = []
    for i in range(0, len(syms), B):
        tick = [s + ".NS" for s in syms[i:i+B]]
        try: d = yf.download(tick, start=start, group_by="ticker", auto_adjust=False, threads=True, progress=False, timeout=60)
        except Exception as e: log("batch fail", str(e)[:50]); continue
        for tk in tick:
            try: sub = d[tk] if len(tick) > 1 else d
            except KeyError: continue
            if isinstance(sub.columns, pd.MultiIndex): sub.columns = sub.columns.get_level_values(-1)
            sub = sub.loc[:, ~sub.columns.duplicated()].dropna(subset=["Close"])
            if sub.empty: continue
            frames.append(pd.DataFrame({"symbol": tk[:-3], "date": sub.index.strftime("%Y-%m-%d"),
                                        "close": sub["Close"].values, "volume": sub["Volume"].values}))
    if not frames: sys.exit("cloud radar: Yahoo returned nothing")
    px = pd.concat(frames)
    shard_files = []

if a.refresh and not a.cloud:
    import yfinance as yf
    from datetime import timedelta
    for f in shard_files:
        old = pd.read_csv(f)
        syms = sorted(set(old["symbol"]))
        start = (datetime.now() - timedelta(days=25)).strftime("%Y-%m-%d")
        frames = [old]
        B = 80
        for i in range(0, len(syms), B):
            tick = [s + ".NS" for s in syms[i:i+B]]
            try: d = yf.download(tick, start=start, group_by="ticker", auto_adjust=False, threads=True, progress=False, timeout=60)
            except Exception as e: log("batch fail", str(e)[:50]); continue
            for tk in tick:
                try: sub = d[tk] if len(tick) > 1 else d
                except KeyError: continue
                if isinstance(sub.columns, pd.MultiIndex): sub.columns = sub.columns.get_level_values(-1)
                sub = sub.loc[:, ~sub.columns.duplicated()].dropna(subset=["Close"])
                if sub.empty: continue
                frames.append(pd.DataFrame({"symbol": tk[:-3], "date": sub.index.strftime("%Y-%m-%d"),
                                            "open": sub["Open"].values, "high": sub["High"].values, "low": sub["Low"].values,
                                            "close": sub["Close"].values, "volume": sub["Volume"].values}))
        allp = pd.concat(frames).drop_duplicates(["symbol", "date"], keep="last")
        allp.to_csv(f, index=False, compression="gzip")
        log(f"refreshed {os.path.basename(f)} → through {allp['date'].max()}")

if not a.cloud:
    log("loading shards")
    px = pd.concat([pd.read_csv(f) for f in shard_files])
px["date"] = pd.to_datetime(px["date"])
C = px.pivot_table(index="date", columns="symbol", values="close")
V = px.pivot_table(index="date", columns="symbol", values="volume")
ASOF = C.index[-1]
log(f"{C.shape[1]} symbols through {ASOF.date()}")

TURN = (C * V)
turn20 = TURN.rolling(20, min_periods=10).mean().iloc[-1] / 1e5          # lakhs/day
volreg = (TURN.rolling(63).mean().iloc[-1] / TURN.rolling(252, min_periods=150).mean().iloc[-1])
last = C.ffill().iloc[-1]
ret6 = C.ffill().iloc[-1] / C.ffill().iloc[-126] - 1 if len(C) > 126 else pd.Series(dtype=float)
ret3 = C.ffill().iloc[-1] / C.ffill().iloc[-63] - 1 if len(C) > 63 else pd.Series(dtype=float)
ret12 = C.ffill().iloc[-1] / C.ffill().iloc[-252] - 1 if len(C) > 252 else pd.Series(dtype=float)
ath_now = C.max()
if ATH_PRIOR:
    ath_now = pd.Series({s: max(ath_now.get(s, np.nan), ATH_PRIOR.get(s, np.nan)) if np.isfinite(ATH_PRIOR.get(s, np.nan)) else ath_now.get(s, np.nan) for s in C.columns})
dd_ath = last / ath_now - 1
bars = C.notna().sum() if not ATH_PRIOR else pd.Series({s: 300 for s in C.columns})  # history proven by the bundle

def inp(name, *fallbacks):
    for p_ in ([os.path.join(INP, name)] if INP else []) + list(fallbacks):
        if p_ and os.path.exists(p_): return p_
    return None
mcapf = pd.read_csv(inp("mcap.csv", os.path.join(a.data, "mcap.csv")))[["symbol", "shares"]]
sh = dict(zip(mcapf["symbol"], pd.to_numeric(mcapf["shares"], errors="coerce")))
mcap = pd.Series({s: last.get(s, np.nan) * sh.get(s, np.nan) / 1e7 for s in C.columns})

names = {}
up = inp("universe_all.csv", os.path.join(a.data, "universe_all.csv"))
if up and os.path.exists(up):
    u = pd.read_csv(up); names = dict(zip(u["symbol"].astype(str), u["name"].astype(str)))

# promoter trend (latest vs 2 quarters earlier)
prom_chg, prom_now = {}, {}
sp = inp("shareholding_panel.csv.gz", os.path.join(MB, "shareholding_panel.csv.gz"))
if sp and os.path.exists(sp):
    S = pd.read_csv(sp, parse_dates=["date_dt"]).dropna(subset=["date_dt"]).sort_values(["symbol", "date_dt"])
    for sym, g in S.groupby("symbol"):
        if len(g) >= 3 and pd.notna(g.iloc[-1]["promoter_pct"]) and pd.notna(g.iloc[-3]["promoter_pct"]):
            prom_now[sym] = float(g.iloc[-1]["promoter_pct"])
            prom_chg[sym] = float(g.iloc[-1]["promoter_pct"] - g.iloc[-3]["promoter_pct"])

# surveillance lists
asm_syms = set()
ap_ = inp("asm.json", os.path.join(NSE_D, "asm.json"))
if ap_ and os.path.exists(ap_):
    def harvest(x):
        if isinstance(x, dict):
            if x.get("symbol"): asm_syms.add(str(x["symbol"]))
            for v in x.values(): harvest(v)
        elif isinstance(x, list):
            for v in x: harvest(v)
    try:
        harvest(json.load(open(ap_)))
    except Exception as e:
        log("asm parse:", str(e)[:60])

# sector map + heat
smap = {}
imp = inp("industry_map.csv", os.path.join(MB, "industry_map.csv"))
if imp and os.path.exists(imp):
    im = pd.read_csv(imp)
    smap = dict(zip(im["symbol"].astype(str), im["sector"]))
sec_heat = {}
if smap:
    tmp = pd.DataFrame({"symbol": list(C.columns)})
    tmp["sector"] = tmp["symbol"].map(smap)
    tmp["ret3"] = tmp["symbol"].map(ret3)
    med = tmp.dropna(subset=["sector", "ret3"]).groupby("sector")["ret3"].median()
    if len(med) >= 5:
        pct = med.rank(pct=True)
        sec_heat = {s: ("hot" if p >= 0.67 else "cold" if p <= 0.33 else "mid") for s, p in pct.items()}

rows = []
for s in C.columns:
    mc = mcap.get(s)
    if not (isinstance(mc, float) and np.isfinite(mc) and mc >= a.min_mcap): continue
    if not (turn20.get(s, 0) >= 5 and bars.get(s, 0) >= 100): continue
    r6, dd, vr = ret6.get(s), dd_ath.get(s), volreg.get(s)
    pc = prom_chg.get(s)
    score = 0
    parts = []
    if isinstance(dd, float) and np.isfinite(dd) and dd <= -0.6: score += 1; parts.append("fallen angel")
    if isinstance(vr, float) and np.isfinite(vr) and vr >= 1.5: score += 1; parts.append("volume waking")
    if mc < 500: score += 1; parts.append("small")
    if isinstance(r6, float) and np.isfinite(r6) and r6 >= 0.3: score += 1; parts.append("6m momentum")
    if pc is not None and pc >= 1.0: score += 1; parts.append("promoter adding")
    if pc is not None and pc <= -1.0: score -= 1; parts.append("promoter SELLING")
    _sec = smap.get(s); _heat = sec_heat.get(_sec) if isinstance(_sec, str) else None
    if _heat == "hot": score += 1; parts.append("hot sector")
    if _heat == "cold": score -= 1; parts.append("COLD sector")
    if score < 2: continue
    flags = []
    if s in asm_syms: flags.append("SURVEILLANCE")
    if turn20.get(s, 0) < 50: flags.append("thin")
    # tie-breakers from the winners-vs-losers study (inside the flagged set): deeper, quieter, not already run
    r12 = ret12.get(s) if len(ret12) else None
    tags = []
    if isinstance(dd, float) and np.isfinite(dd) and dd <= -0.5: tags.append("deep")
    if turn20.get(s, 0) < 100 and (r12 is None or not np.isfinite(r12) or r12 < 0.4): tags.append("quiet")
    if isinstance(r12, float) and np.isfinite(r12) and r12 >= 0.6: tags.append("late")
    sec = smap.get(s)
    rows.append({"symbol": s, "name": (names.get(s) or "")[:40], "score": score, "why": ", ".join(parts),
                 "mcap_cr": round(mc), "price": round(float(last.get(s)), 1), "turn_l": round(float(turn20.get(s, 0))),
                 "ret6": round(float(r6), 3) if isinstance(r6, float) and np.isfinite(r6) else None,
                 "dd_ath": round(float(dd), 2) if isinstance(dd, float) and np.isfinite(dd) else None,
                 "volreg": round(float(vr), 2) if isinstance(vr, float) and np.isfinite(vr) else None,
                 "prom_chg": round(pc, 2) if pc is not None else None,
                 "sector": sec if isinstance(sec, str) else None,
                 "heat": sec_heat.get(sec) if isinstance(sec, str) else None,
                 "flags": flags, "tags": tags,
                 "ret12": round(float(r12), 3) if isinstance(r12, float) and np.isfinite(r12) else None})
rows.sort(key=lambda r: (-r["score"], -(r["volreg"] or 0)))
# market context: median distance-from-ATH across the eligible universe (the study's strongest payoff conditioner:
# score>=3 wins 37% when the market median is >35% off highs, 21% at 22-35%, 11% when the market is near highs)
elig_mask = (mcap >= a.min_mcap) & (turn20 >= 5) & (bars >= 100)
mkt_dd = float(dd_ath[elig_mask.reindex(dd_ath.index).fillna(False)].median())
context = "CRUSHED" if mkt_dd <= -0.35 else "MODERATE" if mkt_dd <= -0.22 else "NEAR HIGHS"
ctx_win = {"CRUSHED": 36.8, "MODERATE": 20.6, "NEAR HIGHS": 11.1}[context]
ctx_deploy = {"CRUSHED": "full sleeve (10%)", "MODERATE": "half sleeve (5%)", "NEAR HIGHS": "minimal (2-3%) — flags here are mostly idiosyncratic wrecks"}[context]
out = {"asof": str(ASOF.date()), "built": datetime.now().strftime("%Y-%m-%d %H:%M"), "n_universe": int((mcap >= a.min_mcap).sum()),
       "mkt_dd": round(mkt_dd, 3), "context": context, "ctx_win": ctx_win, "ctx_deploy": ctx_deploy,
       "n_listed": len(rows), "counts": {str(k): sum(1 for r in rows if r["score"] == k) for k in (2, 3, 4, 5)},
       "odds": {"2": 13.9, "3": 18.5, "4": 24.0, "5": 27.4, "base": 10.2}, "rows": rows[:400]}
p = os.path.join(a.data, "radar.json")
json.dump(out, open(p, "w"))
log(f"radar: {len(rows)} names score>=2 ({out['counts']}) → {p}")
if not a.cloud:
    import shutil
    bdir = os.path.join(a.data, "radar_inputs"); os.makedirs(bdir, exist_ok=True)
    pd.DataFrame({"symbol": ath_now.index, "ath": ath_now.values}).dropna().to_csv(os.path.join(bdir, "ath.csv"), index=False)
    for src_p, nm in ((os.path.join(a.data, "mcap.csv"), "mcap.csv"), (os.path.join(MB, "shareholding_panel.csv.gz"), "shareholding_panel.csv.gz"),
                      (os.path.join(NSE_D, "asm.json"), "asm.json"), (os.path.join(MB, "industry_map.csv"), "industry_map.csv"),
                      (os.path.join(a.data, "universe_all.csv"), "universe_all.csv")):
        if os.path.exists(src_p): shutil.copy(src_p, os.path.join(bdir, nm))
    log(f"radar_inputs bundle refreshed → {bdir}  (copy this folder into the GitHub repo so the website computes the radar daily)")

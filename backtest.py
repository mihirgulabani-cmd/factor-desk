#!/usr/bin/env python3
"""
backtest.py — point-in-time test of the Factor Desk rankings.

For every cohort date the universe is re-scored using ONLY statements whose period end + reporting
lag is before the date, and ONLY prices up to the date. The top-10 (gates passed) is bought equal
weight and held to the next cohort date. Reported against Nifty 500 and against the equal-weight
average of every eligible name (the honest benchmark: it strips out market beta).

    python3 backtest.py --data ~/Downloads/screener_data [--out ...]

Outputs: backtest_results.json, backtest_picks.csv, backtest_summary.md
"""
import os, json, gzip, math, argparse
from datetime import datetime
import numpy as np, pandas as pd
import factor_engine as E

ap = argparse.ArgumentParser()
ap.add_argument("--data", default=os.path.expanduser("~/Downloads/screener_data"))
ap.add_argument("--out", default=None)
ap.add_argument("--top", type=int, default=10)
ap.add_argument("--cost", type=float, default=0.003, help="round-trip cost per rebalance (swing)")
ap.add_argument("--lt-cost", type=float, default=0.002)
ap.add_argument("--min-cov", type=float, default=0.6)
ap.add_argument("--start", default="2023-07-01")
ap.add_argument("--tech-only", action="store_true", help="zero all fundamental weights (for long price histories)")
ap.add_argument("--swing-hold", default="1M", help="swing cohort spacing: 2W, 1M, 3M")
ap.add_argument("--tag", default="")
ap.add_argument("--max-ext", type=float, default=None, help="swing long: exclude names more than this far above the 200 EMA (e.g. 0.30)")
ap.add_argument("--modes", default="longterm,swing_long,swing_short")
ap.add_argument("--prices", default=None, help="alternative prices file (e.g. prices_11y.csv.gz)")
args = ap.parse_args()
DATA = args.data; OUT = args.out or DATA
CACHE = os.path.join(OUT, "bt_cache" + ("_11y" if args.prices else "")); os.makedirs(CACHE, exist_ok=True)
def log(*a): print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)

log("loading")
with gzip.open(os.path.join(DATA, "bundle.json.gz"), "rt") as f: bundle = json.load(f)
univ = pd.DataFrame(bundle["universe"]); funds = bundle["fundamentals"]
px = pd.read_csv(args.prices or os.path.join(DATA, "prices.csv.gz"), parse_dates=["date"]).sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"])
groups = {s: g.reset_index(drop=True) for s, g in px.groupby("symbol")}
close = px.pivot(index="date", columns="symbol", values="close").sort_index()
DATES = close.index
LAST = DATES[-1]
bench_col = "NIFTY500" if "NIFTY500" in close else "NIFTY50"
log(f"{len(groups)} symbols, {DATES[0].date()} → {LAST.date()}, bench {bench_col}")

FUND_P = ["quality", "growth", "balance", "cash", "valuation", "ownership"]
WEIGHTS = {}
for m, spec in E.MODES.items():
    w = dict(spec["w"]); w["ownership"] = 0  # current holdings only — no history → excluded from the test
    if args.tech_only:
        for p in FUND_P: w[p] = 0
    WEIGHTS[m] = w

def first_trading_on_or_after(d):
    i = DATES.searchsorted(pd.Timestamp(d))
    return DATES[min(i, len(DATES) - 1)]

def px_at(sym, d):
    s = close[sym] if sym in close else None
    if s is None: return None
    s = s.loc[:d].dropna()
    return float(s.iloc[-1]) if len(s) else None

def fwd_return(sym, d0, d1):
    a, b = px_at(sym, d0), px_at(sym, d1)
    return (b / a - 1) if a and b else None

def run_cohorts(mode, dates, label, direction="long", cost=0.003):
    rows, picks = [], []
    scol, gcol, ccol = f"score_{mode}", f"gates_{mode}", f"cov_{mode}"
    for i, d in enumerate(dates[:-1]):
        d1 = dates[i + 1]
        ck = os.path.join(CACHE, f"{d.date()}_{'tech' if args.tech_only else 'full'}.pkl")
        if os.path.exists(ck):
            sc = pd.read_pickle(ck)
        else:
            sc = E.score_universe(univ, funds, groups, asof=d, weights=WEIGHTS)
            sc.to_pickle(ck)
        if sc.empty: continue
        sc["fwd"] = [fwd_return(s, d, d1) for s in sc["symbol"]]
        elig = sc[(sc[gcol].map(len) == 0) & (sc[ccol] >= args.min_cov) & sc["fwd"].notna() & sc[scol].notna()].copy()
        if args.max_ext is not None and mode == "swing_long":
            elig = elig[pd.to_numeric(elig["vs_ema200"], errors="coerce") <= args.max_ext]
        if len(elig) < 30: continue
        elig = elig.sort_values(scol, ascending=False)
        top = elig.head(args.top); bot = elig.tail(args.top)
        sign = 1 if direction == "long" else -1
        r_top = sign * top["fwd"].mean() - cost
        r_bot = sign * bot["fwd"].mean() - cost
        r_univ = sign * elig["fwd"].mean()
        r_bench = fwd_return(bench_col, d, d1) or 0.0
        # deciles + rank IC on the eligible set
        elig["dec"] = pd.qcut(elig[scol].rank(method="first"), 10, labels=False) + 1
        dec = elig.groupby("dec")["fwd"].mean()
        ic = elig[[scol, "fwd"]].corr(method="spearman").iloc[0, 1]
        hit = (sign * top["fwd"] > 0).mean(); beat = (sign * top["fwd"] > sign * r_bench).mean()
        rows.append({"cohort": d.strftime("%Y-%m-%d"), "exit": d1.strftime("%Y-%m-%d"), "n_elig": len(elig), "top": r_top, "bottom": r_bot, "universe": r_univ, "bench": sign * r_bench,
                     "hit": hit, "beat_bench": beat, "ic": ic, "d1": dec.get(10), "d10": dec.get(1), "spread": (dec.get(10) or 0) - (dec.get(1) or 0),
                     "picks": list(top["symbol"]), "pick_returns": [round(x, 4) for x in top["fwd"]]})
        for _, p in top.iterrows():
            picks.append({"mode": label, "cohort": d.strftime("%Y-%m-%d"), "symbol": p["symbol"], "sector": p["sector"], "score": round(p[scol], 1), "entry": round(px_at(p["symbol"], d), 2), "exit_px": round(px_at(p["symbol"], d1), 2), "return": round(p["fwd"], 4), "bench": round(r_bench, 4)})
        log(f"{label} {d.date()}→{d1.date()}  elig={len(elig)}  top={r_top*100:+.1f}%  univ={r_univ*100:+.1f}%  bench={sign*r_bench*100:+.1f}%  hit={hit*100:.0f}%  IC={ic:+.2f}  D10−D1={((dec.get(10) or 0)-(dec.get(1) or 0))*100:+.1f}pp")
    return rows, picks

def summarise(rows, periods_per_year):
    if not rows: return {}
    df = pd.DataFrame(rows)
    def chain(col): return float(np.prod(1 + df[col]) - 1)
    def cagr(col):
        n = len(df) / periods_per_year
        return (1 + chain(col)) ** (1 / n) - 1 if n > 0 else None
    def maxdd(col):
        eq = np.cumprod(1 + df[col].values); peak = np.maximum.accumulate(eq); return float(np.min(eq / peak - 1))
    out = {"cohorts": len(df), "first": df["cohort"].iloc[0], "last_exit": df["exit"].iloc[-1]}
    for c in ["top", "bottom", "universe", "bench"]:
        out[c] = {"total": chain(c), "cagr": cagr(c), "avg": float(df[c].mean()), "median": float(df[c].median()), "max_dd": maxdd(c), "pos_periods": float((df[c] > 0).mean())}
    out["excess_vs_universe"] = {"avg": float((df["top"] - df["universe"]).mean()), "pos": float(((df["top"] - df["universe"]) > 0).mean())}
    out["excess_vs_bench"] = {"avg": float((df["top"] - df["bench"]).mean()), "pos": float(((df["top"] - df["bench"]) > 0).mean())}
    out["hit_rate"] = float(df["hit"].mean()); out["beat_bench_rate"] = float(df["beat_bench"].mean())
    out["ic_mean"] = float(df["ic"].mean()); out["ic_pos"] = float((df["ic"] > 0).mean())
    out["decile_spread_avg"] = float(df["spread"].mean()); out["decile_spread_pos"] = float((df["spread"] > 0).mean())
    out["top_minus_bottom_avg"] = float((df["top"] - df["bottom"]).mean())
    return out

results, all_picks = {}, []
start = pd.Timestamp(args.start)

# ---- Long-term: annual cohorts on the first trading day on/after 1 July (FY statements are public by then)
years = list(range(start.year, LAST.year + 1))
lt_dates = [first_trading_on_or_after(f"{y}-07-01") for y in years if pd.Timestamp(f"{y}-07-01") <= LAST]
lt_dates = [d for d in lt_dates if d >= start]
if (LAST - lt_dates[-1]).days < 60: lt_dates = lt_dates[:-1]
lt_dates.append(LAST)
log(f"long-term cohorts: {[d.date().isoformat() for d in lt_dates]}")
MODES_RUN = args.modes.split(",")
if "longterm" in MODES_RUN:
    rows, picks = run_cohorts("longterm", lt_dates, "longterm", "long", args.lt_cost)
    results["longterm"] = {"rows": rows, "summary": summarise(rows, 1.0)}; all_picks += picks

# ---- Swing: monthly cohorts, first trading day of each month, hold to the next
freq = {"2W": "2W-MON", "1M": "MS", "3M": "QS"}[args.swing_hold.upper()]
months = pd.date_range(start, LAST, freq=freq)
sw_dates = [first_trading_on_or_after(d) for d in months]
sw_dates = sorted(set(sw_dates))
if (LAST - sw_dates[-1]).days >= 10: sw_dates.append(LAST)
log(f"swing cohorts: {len(sw_dates)-1} months from {sw_dates[0].date()}")
ppy = {"2W": 26.0, "1M": 12.0, "3M": 4.0}[args.swing_hold.upper()]
if "swing_long" in MODES_RUN:
    rows, picks = run_cohorts("swing_long", sw_dates, "swing_long", "long", args.cost)
    results["swing_long"] = {"rows": rows, "summary": summarise(rows, ppy)}; all_picks += picks
if "swing_short" in MODES_RUN:
    rows, picks = run_cohorts("swing_short", sw_dates, "swing_short", "short", args.cost)
    results["swing_short"] = {"rows": rows, "summary": summarise(rows, ppy)}; all_picks += picks

results["meta"] = {"built": datetime.now().strftime("%Y-%m-%d %H:%M"), "data_to": LAST.strftime("%Y-%m-%d"), "top": args.top, "cost": args.cost, "lt_cost": args.lt_cost,
                   "min_cov": args.min_cov, "tech_only": args.tech_only, "weights": WEIGHTS, "bench": bench_col,
                   "notes": ["ownership pillar excluded (no holdings history)", "returns are price-only (no dividends)", "universe = today's listed names ≥ ₹1,000 Cr → survivorship bias, favours the whole universe not the ranking",
                             "statements available only after a 75-day (annual) / 50-day (quarterly) lag", "Yahoo gives 4 fiscal years → fundamental factors are thin before mid-2025"]}
tag = ("_tech" if args.tech_only else "") + (f"_{args.swing_hold}" if args.swing_hold.upper() != "1M" else "") + args.tag
with open(os.path.join(OUT, f"backtest_results{tag}.json"), "w") as f: json.dump(results, f, indent=1, default=float)
pd.DataFrame(all_picks).to_csv(os.path.join(OUT, f"backtest_picks{tag}.csv"), index=False)

# ---- summary
def pct(x): return "–" if x is None else f"{x*100:+.1f}%"
lines = [f"# Factor Desk backtest — {results['meta']['built']} (data to {results['meta']['data_to']}){' — TECHNICAL PILLARS ONLY' if args.tech_only else ''}", ""]
for m, _ppy in [("longterm", 1), ("swing_long", ppy), ("swing_short", ppy)]:
    if m not in results: continue
    s = results[m]["summary"]
    if not s: continue
    lines += [f"## {m}  ({s['cohorts']} cohorts, {s['first']} → {s['last_exit']})", "",
              "| | Top-10 | Bottom-10 | Eligible universe (EW) | Nifty 500 |", "|---|---|---|---|---|",
              f"| Total return | {pct(s['top']['total'])} | {pct(s['bottom']['total'])} | {pct(s['universe']['total'])} | {pct(s['bench']['total'])} |",
              f"| CAGR | {pct(s['top']['cagr'])} | {pct(s['bottom']['cagr'])} | {pct(s['universe']['cagr'])} | {pct(s['bench']['cagr'])} |",
              f"| Avg per period | {pct(s['top']['avg'])} | {pct(s['bottom']['avg'])} | {pct(s['universe']['avg'])} | {pct(s['bench']['avg'])} |",
              f"| Max drawdown | {pct(s['top']['max_dd'])} | {pct(s['bottom']['max_dd'])} | {pct(s['universe']['max_dd'])} | {pct(s['bench']['max_dd'])} |",
              f"| Positive periods | {s['top']['pos_periods']*100:.0f}% | {s['bottom']['pos_periods']*100:.0f}% | {s['universe']['pos_periods']*100:.0f}% | {s['bench']['pos_periods']*100:.0f}% |", "",
              f"- Pick hit rate (a top-10 name finishes up): **{s['hit_rate']*100:.0f}%** · beats Nifty 500: **{s['beat_bench_rate']*100:.0f}%**",
              f"- Top-10 minus eligible universe: avg {pct(s['excess_vs_universe']['avg'])} per period, positive in {s['excess_vs_universe']['pos']*100:.0f}% of periods",
              f"- Top-10 minus Nifty 500: avg {pct(s['excess_vs_bench']['avg'])}, positive in {s['excess_vs_bench']['pos']*100:.0f}% of periods",
              f"- Top-10 minus bottom-10: avg {pct(s['top_minus_bottom_avg'])} per period",
              f"- Rank IC (Spearman score vs forward return, all eligible names): mean {s['ic_mean']:+.3f}, positive in {s['ic_pos']*100:.0f}% of periods",
              f"- Decile spread (D10 − D1): avg {pct(s['decile_spread_avg'])}, positive in {s['decile_spread_pos']*100:.0f}% of periods", ""]
    lines += ["| Cohort | Exit | Elig | Top-10 | Bottom-10 | Universe | Nifty 500 | Hit | IC | D10−D1 | Picks |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results[m]["rows"]:
        lines.append(f"| {r['cohort']} | {r['exit']} | {r['n_elig']} | {pct(r['top'])} | {pct(r['bottom'])} | {pct(r['universe'])} | {pct(r['bench'])} | {r['hit']*100:.0f}% | {r['ic']:+.2f} | {pct(r['spread'])} | {' '.join(r['picks'])} |")
    lines.append("")
lines += ["## Caveats", ""] + [f"- {n}" for n in results["meta"]["notes"]]
open(os.path.join(OUT, f"backtest_summary{tag}.md"), "w").write("\n".join(lines))
log("wrote backtest_results.json, backtest_picks.csv, backtest_summary.md")
print("\n".join(lines[:40]))

#!/usr/bin/env python3
"""build_html.py — embed model_output.json (+ backtest summaries, if present) into the template → NSE-Factor-Desk.html

    python3 build_html.py --data ~/Downloads/screener_data [--template model_template.html]
"""
import os, json, glob, argparse
ap = argparse.ArgumentParser()
ap.add_argument("--data", default=os.path.expanduser("~/Downloads/screener_data"))
ap.add_argument("--template", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_template.html"))
ap.add_argument("--out", default=None)
a = ap.parse_args()

def safe(txt):  # guard against </script> inside embedded strings
    return txt.replace("</", "<\\/")

with open(os.path.join(a.data, "model_output.json")) as f:
    data = f.read()

# ---- backtest summaries → compact block for the Backtest tab
VARIANTS = [("backtest_results.json", "longterm", "Long-term, buy 1 Jul, hold 1 yr — full model"),
            ("backtest_results_tech.json", "longterm", "Long-term — technicals only"),
            ("backtest_results_2W.json", "swing_long", "Swing long, 2-week hold — full model"),
            ("backtest_results.json", "swing_long", "Swing long, 1-month hold — full model"),
            ("backtest_results_tech.json", "swing_long", "Swing long, 1-month — technicals only"),
            ("backtest_results_ext30.json", "swing_long", "Swing long, 1-month — ≤30% above 200 EMA"),
            ("backtest_results_3M.json", "swing_long", "Swing long, 3-month hold — full model"),
            ("backtest_results.json", "swing_short", "Swing short, 1-month (short the top-10 short scores)"),
            ("backtest_results_3M.json", "swing_short", "Swing short, 3-month")]
FINDINGS = [
    "The ranking orders stocks correctly more often than not, but weakly: rank IC is positive in about two-thirds of two-week periods and in every annual cohort. A real but modest signal — about what published factor models show — not a crystal ball.",
    "At the annual horizon the top-10 beat both benchmarks on three data points (38% CAGR vs 22% for the gated universe and 12% for Nifty 500), with the top decile beating the bottom decile in every cohort. Three cohorts, one of them a mania year, is not proof — keep running the test every July.",
    "For swing, the holding period decides everything: two weeks beat the gated universe, one month matched it, three months lost to it. The composite is momentum-heavy and momentum leaders bought at the top of the list are extended and mean-revert on a 1–3 month clock. Ride it while it works; re-rank every two weeks.",
    "Fundamentals help over a year and do nothing over a month. Technicals-only matched the full model at a year and beat it at one month, so the swing modes now rank on technicals and use fundamentals as gates only.",
    "The gate is worth more than the rank: 'above the 200 EMA, liquid, ATR ≥ 1.5%' as a plain equal-weight basket compounded at ~21% against 12% for the index in every test, with no ranking at all.",
    "The short side has no edge: shorting the ten worst-scored names lost the same as shorting anything in a rising market. Do not short off this ranking without a bearish market regime.",
    "Concentration is the risk: ten names, monthly rebalanced, drew down 34–40% in Jan–Feb 2025 against 25% for the gated universe. The extension cap cut that to 29%.",
    "Every number is price-only (no dividends) on today's ≥ ₹1,000 Cr universe, which is survivorship-biased upward for every row in the table equally.",
]
bt = None
rows = []
HERE = os.path.dirname(os.path.abspath(__file__))
def find(fname):
    for d in (a.data, HERE, os.path.join(HERE, "backtest")):
        p = os.path.join(d, fname)
        if os.path.exists(p): return p
    return None
for fname, mode, label in VARIANTS:
    p = find(fname)
    if not p: continue
    try:
        R = json.load(open(p))
    except Exception:
        continue
    sm = (R.get(mode) or {}).get("summary")
    if not sm: continue
    rows.append({"label": label, "cohorts": sm["cohorts"], "top_cagr": sm["top"]["cagr"], "univ_cagr": sm["universe"]["cagr"], "bench_cagr": sm["bench"]["cagr"],
                 "top_dd": sm["top"]["max_dd"], "hit": sm["hit_rate"], "ex_univ": sm["excess_vs_universe"]["avg"], "ex_univ_pos": sm["excess_vs_universe"]["pos"],
                 "ic": sm["ic_mean"], "ic_pos": sm["ic_pos"], "spread": sm["decile_spread_avg"], "spread_pos": sm["decile_spread_pos"]})
    if bt is None:
        bt = {"built": R.get("meta", {}).get("built"), "data_to": R.get("meta", {}).get("data_to")}
if rows:
    bt["variants"] = rows
    base = find("backtest_results.json")
    if base:
        R = json.load(open(base))
        bt["lt_cohorts"] = [{k: r[k] for k in ("cohort", "exit", "n_elig", "top", "bottom", "universe", "bench", "hit", "ic", "spread", "picks")} for r in (R.get("longterm") or {}).get("rows", [])]
    bt["findings"] = FINDINGS
bt_json = json.dumps(bt) if bt else "null"

with open(a.template) as f:
    tpl = f.read()
html = tpl.replace("__DATA__", safe(data)).replace("__BT__", safe(bt_json))
out = a.out or os.path.join(a.data, "NSE-Factor-Desk.html")
with open(out, "w") as f:
    f.write(html)
print(f"wrote {out} ({os.path.getsize(out)/1e6:.1f} MB){' + backtest tab (%d variants)' % len(rows) if rows else ' (no backtest results found)'}")

#!/usr/bin/env python3
"""
nse_results.py — the quarterly results every company FILED with NSE, two sources merged:
  results/        2017 → Dec-2024 history (comparison API — static, pulled once)
  results_fresh/  the last ~5 quarters incl. the newest (stock-page API — post-Dec-2024
                  Integrated Filing era; refreshed on a rolling cycle)
All figures in Rs LAKHS. The exchange's numbers, not Yahoo's approximation.

Run on the Mac (NSE blocks datacenter IPs):

    python3 ~/Documents/GitHub/factor-desk/nse_results.py --all        # fresh quarters, whole universe (~20 min)
    python3 ~/Documents/GitHub/factor-desk/nse_results.py              # daily: 150 stalest symbols (~3 min)
    python3 ~/Documents/GitHub/factor-desk/nse_results.py --pack       # merge both → results_panel.csv.gz
"""
import os, sys, json, time, argparse, random
from datetime import datetime, date, timedelta

import requests
try:
    import pandas as pd
except ImportError:
    sys.exit("pip3 install pandas requests")

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/screener_data/nse"))
ap.add_argument("--universe", default=os.path.expanduser("~/screener_data/universe_kept.csv"))
ap.add_argument("--per-run", type=int, default=150, help="symbols to refresh per run (stalest first)")
ap.add_argument("--all", action="store_true", help="do the whole universe in one run (first backfill)")
ap.add_argument("--pack", action="store_true", help="just consolidate what's on disk into results_panel.csv.gz")
ap.add_argument("--probe", action="store_true", help="30s test of the fresh-results endpoints → probe_results.json")
args = ap.parse_args()
RES = os.path.join(args.out, "results")          # 2017–2024 archive (comparison API; static)
FRESH = os.path.join(args.out, "results_fresh")  # latest ~5 quarters (stock-page API; refreshed)
os.makedirs(RES, exist_ok=True); os.makedirs(FRESH, exist_ok=True)

def log(*a): print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}

class NSE:
    def __init__(self):
        self.s = requests.Session(); self.s.headers.update(UA); self.warm = 0
    def _warmup(self):
        if time.time() - self.warm < 300: return
        try:
            self.s.get("https://www.nseindia.com", timeout=15)
            self.s.get("https://www.nseindia.com/companies-listing/corporate-filings-financial-results", timeout=15)
            self.warm = time.time()
        except Exception as e:
            log("warmup failed (will still try):", str(e)[:80])
    def get_json(self, url, tries=3):
        self._warmup()
        for i in range(tries):
            try:
                r = self.s.get(url, timeout=25)
                if r.status_code in (401, 403):
                    self.warm = 0; self._warmup(); continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if i == tries - 1: raise
                time.sleep(2 * (i + 1) + random.random())

# ---------------------------------------------------------------- pull
def rows_from(j):
    """NSE wraps the list differently across endpoints — dig out the list of result rows."""
    if isinstance(j, list): return [r for r in j if isinstance(r, dict)]
    if isinstance(j, dict):
        for k in ("resCmpData", "data", "rows", "results"):
            v = j.get(k)
            if isinstance(v, list): return [r for r in v if isinstance(r, dict)]
    return []

def pull(symbols):
    """FRESH results via the stock-page API (top-corp-info → financial_results): the last ~5 filed
    quarters incl. the newest (post-Dec-2024 Integrated Filing era). The 2017–2024 history from the
    comparison API already sits in results/ and never changes — this only refreshes the fresh side."""
    nse = NSE()
    got = 0
    for i, s in enumerate(symbols, 1):
        q = requests.utils.quote(s)
        rows = []
        try:
            j = nse.get_json(f"https://www.nseindia.com/api/top-corp-info?symbol={q}&market=equities")
            fr = (j or {}).get("financial_results") or {}
            rows = fr.get("data") if isinstance(fr, dict) else (fr if isinstance(fr, list) else [])
            rows = [r for r in (rows or []) if isinstance(r, dict)]
        except Exception as e:
            log(f"{s}: {str(e)[:60]}")
        if rows:
            json.dump({"symbol": s, "_fetched": datetime.now().strftime("%Y-%m-%d %H:%M"), "rows": rows},
                      open(os.path.join(FRESH, f"{s}.json"), "w"))
            got += 1
        else:
            log(f"{s}: no fresh rows")
        if i % 50 == 0: log(f"{i}/{len(symbols)}  ({got} with data)")
        time.sleep(0.7 + random.random() * 0.3)
    log(f"done: {got}/{len(symbols)} refreshed (fresh files on disk: {len(os.listdir(FRESH))})")

# ---------------------------------------------------------------- pack
NORM = {  # mapped against the real key inventory from the first pull (results_keys.txt, 30 Aug 2026)
    # figures are in Rs LAKHS (divide by 100 for Cr)
    "period_end": ["re_to_dt", "toDate", "to_date"],
    "period_start": ["re_from_dt", "fromDate", "from_date"],
    "filed_at": ["re_create_dt", "broadCastDate"],
    "res_type": ["re_res_type"],
    "seq": ["re_seq_num"],
    "net_sales": ["re_net_sale"],
    "other_income": ["re_oth_inc", "re_oth_inc_new"],
    "income": ["re_total_inc", "re_tot_inc"],
    "expenditure": ["re_tot_exp_exc_pro_cont", "re_oth_tot_exp"],
    "pbt": ["re_pro_loss_bef_tax", "re_pro_loss_bef_tax_sum"],
    "pat": ["re_net_profit", "re_proloss_ord_act"],
    "pat_cons": ["re_con_pro_loss"],
    "tax": ["re_tax", "re_curr_tax"],
    "eps": ["re_basic_eps", "re_bsc_eps_bfr_exi"],
    "eps_dil": ["re_diluted_eps", "re_dil_eps_bfr_exi"],
    "except_items": ["re_excepn_items", "re_excepn_items_new"],
    # banks / NBFCs
    "int_earned": ["re_int_earned"],
    "int_expended": ["re_int_expd"],
    "gnpa": ["re_grs_npa"],
    "gnpa_pct": ["re_grs_npa_per", "re_per_grs_npa"],
    "capital_adequacy": ["re_cap_ade_rat"],
    "return_on_assets": ["re_ret_asset"],
}
NUM_COLS = ["net_sales", "other_income", "income", "expenditure", "pbt", "pat", "pat_cons", "tax",
            "eps", "eps_dil", "except_items", "int_earned", "int_expended", "gnpa", "gnpa_pct",
            "capital_adequacy", "return_on_assets"]
def pick(r, names):
    for n in names:
        if n in r and r[n] not in (None, "", "-"): return r[n]
    lower = {k.lower(): v for k, v in r.items()}
    for n in names:
        v = lower.get(n.lower())
        if v not in (None, "", "-"): return v
    return None

FRESH_NORM = {  # keys of the stock-page financial_results rows (all Rs LAKHS)
    "period_end": ["to_date"], "period_start": ["from_date"], "filed_at": ["re_broadcast_timestamp"],
    "income": ["income"], "expenditure": ["expenditure"], "pbt": ["reProLossBefTax"],
    "pat": ["proLossAftTax"], "eps_dil": ["reDilEPS"], "audited": ["audited"],
    "consolidated": ["consolidated"], "xbrl": ["xbrl_attachment"],
}

def pack():
    out, keys = [], set()
    for d, norm, src in ((RES, NORM, "archive"), (FRESH, FRESH_NORM, "fresh")):
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json"): continue
            try: j = json.load(open(os.path.join(d, f)))
            except Exception: continue
            for r in j.get("rows", []):
                keys |= set(r.keys())
                row = {"symbol": j["symbol"], "source": src}
                for col, aliases in norm.items():
                    row[col] = pick(r, aliases)
                out.append(row)
    if not out:
        log("pack: nothing on disk yet — run the pull first"); return
    df = pd.DataFrame(out)
    for c in NUM_COLS:
        if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
    try:
        df["period_end_dt"] = pd.to_datetime(df["period_end"], format="mixed", dayfirst=True, errors="coerce")
    except (TypeError, ValueError):
        df["period_end_dt"] = pd.to_datetime(df["period_end"], dayfirst=True, errors="coerce")
    # where a quarter appears in both sources, the fresh (integrated-filing era) row wins
    df["cons_n"] = df.get("consolidated", pd.Series(index=df.index, dtype=object)).astype(str).str.lower().str[:4]
    df = df.sort_values(["symbol", "period_end_dt", "source"])  # archive < fresh alphabetically
    df = df.drop_duplicates(["symbol", "period_end_dt", "cons_n"], keep="last").drop(columns=["cons_n"])
    p = os.path.join(args.out, "results_panel.csv.gz")
    df.to_csv(p, index=False, compression="gzip")
    kp = os.path.join(args.out, "results_keys.txt")
    open(kp, "w").write("\n".join(sorted(keys)))
    n_norm = df["pat"].notna().mean()
    log(f"pack: {len(df)} result rows, {df['symbol'].nunique()} symbols → {p} ({os.path.getsize(p)/1e6:.1f} MB)")
    log(f"pack: PAT normalised for {n_norm*100:.0f}% of rows; key inventory → {kp}")
    log("Drag results_panel.csv.gz (and results_keys.txt if PAT% is low) into the chat.")

def probe():
    """Round 2: find where post-Dec-2024 results live (SEBI's Integrated Filing switch).
    ~45s; writes probe_results.json — say 'done' in the chat."""
    nse = NSE()
    to = date.today(); frm = to - timedelta(days=60)
    dr = f"from_date={frm.strftime('%d-%m-%Y')}&to_date={to.strftime('%d-%m-%Y')}"
    tests = {
        "integrated_window": f"https://www.nseindia.com/api/integrated-filing-results?index=equities&{dr}&period=Quarterly",
        "integrated_symbol": "https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol=RELIANCE&period=Quarterly",
        "corp_integrated": f"https://www.nseindia.com/api/corporate-integrated-filing?index=equities&{dr}",
        "integrated_typed": f"https://www.nseindia.com/api/integrated-filing-results?index=equities&type=Integrated%20Filing-%20Financials&{dr}",
        "top_corp_info": "https://www.nseindia.com/api/top-corp-info?symbol=RELIANCE&market=equities",
        "quote_corpinfo": "https://www.nseindia.com/api/quote-equity?symbol=RELIANCE&section=corp_info",
    }
    out = {}
    for name, url in tests.items():
        try:
            j = nse.get_json(url)
            rows = rows_from(j)
            info = {"rows": len(rows), "resp_type": type(j).__name__}
            if isinstance(j, dict):
                info["top_level_keys"] = sorted(j.keys())[:25]
                # dig one level for nested row lists (e.g. financial_results)
                for k, v in j.items():
                    if isinstance(v, dict):
                        for k2, v2 in v.items():
                            if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                                info[f"nested_{k}.{k2}"] = {"rows": len(v2), "keys": sorted(v2[0].keys()),
                                                           "first3": v2[:3]}
                    elif isinstance(v, list) and v and isinstance(v[0], dict):
                        info[f"nested_{k}"] = {"rows": len(v), "keys": sorted(v[0].keys()), "first3": v[:3]}
            if rows:
                info["keys"] = sorted(rows[0].keys())
                info["first3"] = rows[:3]
                for k in ("toDate", "qe_date", "period_ended", "relatingTo", "creation_Date", "symbol"):
                    vals = [r.get(k) for r in rows if isinstance(r, dict) and r.get(k)]
                    if vals: info[f"sample_{k}"] = vals[:4]
            out[name] = {"url": url, **info}
            log(f"{name}: {info.get('rows', 0)} rows, resp {info['resp_type']}")
        except Exception as e:
            out[name] = {"url": url, "error": str(e)[:120]}
            log(f"{name}: FAILED {str(e)[:80]}")
        time.sleep(1)
    p = os.path.join(args.out, "probe_results.json")
    json.dump(out, open(p, "w"), indent=1, default=str)
    log(f"wrote {p} — say 'done' in the chat")

if __name__ == "__main__":
    if getattr(args, "probe", False):
        probe(); sys.exit(0)
    if args.pack:
        pack(); sys.exit(0)
    if not os.path.exists(args.universe):
        sys.exit(f"universe file not found: {args.universe}")
    syms = list(pd.read_csv(args.universe)["symbol"].astype(str))
    if args.all:
        todo = syms
    else:
        def age(s):
            p = os.path.join(FRESH, f"{s}.json")
            return os.path.getmtime(p) if os.path.exists(p) else 0
        todo = sorted(syms, key=age)[:args.per_run]
    log(f"pulling filed results for {len(todo)} symbols (~{len(todo)*0.85/60:.0f} min)")
    pull(todo)
    log("Tip: run with --pack after a big pull to build results_panel.csv.gz")

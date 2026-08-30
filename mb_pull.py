#!/usr/bin/env python3
"""
mb_pull.py — the multibagger-study data pull: the WHOLE NSE list (~2,500 names, ₹50 Cr+ analysed later),
ten years back. One overnight run, fully resumable — re-running only fetches what's missing.

    python3 ~/Documents/GitHub/factor-desk/mb_pull.py            # everything (~3h first time; minutes after)
    python3 ~/Documents/GitHub/factor-desk/mb_pull.py --pack     # just rebuild the packed panels

Collects into ~/screener_data/mb/ (and shares the nse/ stores with the daily pull):
  prices10y_shard{0..7}.csv.gz   10y daily OHLCV, all symbols, 8 shards (~10-15 MB each, bridge-sized)
  results_panel_all.csv.gz       filed quarterly results 2017→now for EVERY symbol (nse/results*, extended)
  shareholding_panel.csv.gz      ~5y quarterly promoter % + public % for EVERY symbol (nse/shareholding, extended)
Figures in filed results are ₹ lakhs.
"""
import os, sys, json, time, argparse, random, hashlib
from datetime import datetime, timedelta

import requests
try:
    import pandas as pd
    import yfinance as yf
except ImportError:
    sys.exit("pip3 install pandas requests yfinance")

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/screener_data"))
ap.add_argument("--years", type=int, default=10)
ap.add_argument("--pack", action="store_true", help="only rebuild the packed panels from what's on disk")
ap.add_argument("--skip-prices", action="store_true")
ap.add_argument("--skip-results", action="store_true")
ap.add_argument("--skip-shareholding", action="store_true")
args = ap.parse_args()
MB = os.path.join(args.out, "mb"); NSE_D = os.path.join(args.out, "nse")
RES = os.path.join(NSE_D, "results"); SH = os.path.join(NSE_D, "shareholding")
for d in (MB, RES, SH): os.makedirs(d, exist_ok=True)

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

def universe():
    p = os.path.join(args.out, "universe_all.csv")
    if os.path.exists(p):
        syms = list(pd.read_csv(p)["symbol"].astype(str))
        log(f"universe: {len(syms)} symbols from universe_all.csv"); return syms
    sys.exit("universe_all.csv not found — run build_dataset.py once first")

def shard_of(sym, n=8):
    return int(hashlib.md5(sym.encode()).hexdigest(), 16) % n

# ---------------------------------------------------------------- A. prices, 10y, sharded
def stage_prices(syms):
    start = (datetime.now() - timedelta(days=int(args.years * 365.25) + 10)).strftime("%Y-%m-%d")
    for sh in range(8):
        path = os.path.join(MB, f"prices10y_shard{sh}.csv.gz")
        batch_syms = [s for s in syms if shard_of(s) == sh]
        have = set()
        frames = []
        if os.path.exists(path):
            old = pd.read_csv(path)
            have = set(old["symbol"]); frames.append(old)
        todo = [s for s in batch_syms if s not in have]
        if not todo:
            log(f"prices shard {sh}: complete ({len(have)} symbols)"); continue
        log(f"prices shard {sh}: fetching {len(todo)} of {len(batch_syms)} (have {len(have)})")
        B = 60
        for i in range(0, len(todo), B):
            tick = [s + ".NS" for s in todo[i:i + B]]
            try:
                d = yf.download(tick, start=start, group_by="ticker", auto_adjust=False, threads=True, progress=False, timeout=60)
            except Exception as e:
                log(f"  batch {i}: {str(e)[:60]}"); continue
            for tk in tick:
                try:
                    sub = d[tk] if len(tick) > 1 else d
                except KeyError:
                    continue
                if isinstance(sub.columns, pd.MultiIndex): sub.columns = sub.columns.get_level_values(-1)
                sub = sub.loc[:, ~sub.columns.duplicated()].dropna(subset=["Close"])
                if sub.empty: continue
                frames.append(pd.DataFrame({"symbol": tk[:-3], "date": sub.index.strftime("%Y-%m-%d"),
                                            "open": sub["Open"].values, "high": sub["High"].values, "low": sub["Low"].values,
                                            "close": sub["Close"].values, "volume": sub["Volume"].values}))
            if (i // B) % 2 == 1 or i + B >= len(todo):
                pd.concat(frames).drop_duplicates(["symbol", "date"]).to_csv(path, index=False, compression="gzip")
                log(f"  shard {sh}: {min(i+B,len(todo))}/{len(todo)} saved")
            time.sleep(1)
        if frames:
            allp = pd.concat(frames).drop_duplicates(["symbol", "date"])
            allp.to_csv(path, index=False, compression="gzip")
            log(f"prices shard {sh}: {allp['symbol'].nunique()} symbols, {len(allp):,} rows ({os.path.getsize(path)/1e6:.0f} MB)")

# ---------------------------------------------------------------- B. filed-results archive, all symbols
def rows_from(j):
    if isinstance(j, list): return [r for r in j if isinstance(r, dict)]
    if isinstance(j, dict):
        for k in ("resCmpData", "data", "rows", "results"):
            v = j.get(k)
            if isinstance(v, list): return [r for r in v if isinstance(r, dict)]
    return []

def stage_results(syms):
    nse = NSE()
    todo = [s for s in syms if not os.path.exists(os.path.join(RES, f"{s}.json"))]
    log(f"filed results: {len(syms)-len(todo)} already on disk, fetching {len(todo)} (~{len(todo)*0.85/60:.0f} min)")
    got = 0
    for i, s in enumerate(todo, 1):
        q = requests.utils.quote(s)
        try:
            rows = rows_from(nse.get_json(f"https://www.nseindia.com/api/results-comparision?symbol={q}"))
        except Exception:
            rows = []
        if rows:
            json.dump({"symbol": s, "_fetched": datetime.now().strftime("%Y-%m-%d %H:%M"), "rows": rows},
                      open(os.path.join(RES, f"{s}.json"), "w"))
            got += 1
        if i % 100 == 0: log(f"  results {i}/{len(todo)} ({got} with data)")
        time.sleep(0.6 + random.random() * 0.3)
    log(f"filed results: +{got}; total on disk {len(os.listdir(RES))}")

# ---------------------------------------------------------------- C. shareholding history, all symbols
def stage_shareholding(syms):
    nse = NSE()
    todo = [s for s in syms if not os.path.exists(os.path.join(SH, f"{s}.json"))]
    log(f"shareholding: {len(syms)-len(todo)} already on disk, fetching {len(todo)} (~{len(todo)*0.85/60:.0f} min)")
    got = 0
    for i, s in enumerate(todo, 1):
        try:
            j = nse.get_json(f"https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={requests.utils.quote(s)}")
            json.dump({"symbol": s, "_fetched": datetime.now().strftime("%Y-%m-%d"), "data": j}, open(os.path.join(SH, f"{s}.json"), "w"))
            got += 1
        except Exception:
            pass
        if i % 100 == 0: log(f"  shareholding {i}/{len(todo)} ({got} ok)")
        time.sleep(0.6 + random.random() * 0.3)
    log(f"shareholding: +{got}; total on disk {len(os.listdir(SH))}")

# ---------------------------------------------------------------- D. industry map (sector-rotation features need it)
def stage_meta(syms):
    META = os.path.join(NSE_D, "meta"); os.makedirs(META, exist_ok=True)
    nse = NSE()
    todo = [s for s in syms if not os.path.exists(os.path.join(META, f"{s}.json"))]
    log(f"industry map: {len(syms)-len(todo)} on disk, fetching {len(todo)} (~{len(todo)*0.75/60:.0f} min)")
    got = 0
    for i, s in enumerate(todo, 1):
        try:
            j = nse.get_json(f"https://www.nseindia.com/api/quote-equity?symbol={requests.utils.quote(s)}")
            info = (j or {}).get("industryInfo") or {}
            meta = {"symbol": s, "macro": info.get("macro"), "sector": info.get("sector"),
                    "industry": info.get("industry"), "basic": info.get("basicIndustry"),
                    "listed": ((j or {}).get("metadata") or {}).get("listingDate"),
                    "_fetched": datetime.now().strftime("%Y-%m-%d")}
            json.dump(meta, open(os.path.join(META, f"{s}.json"), "w"))
            got += 1
        except Exception:
            pass
        if i % 150 == 0: log(f"  meta {i}/{len(todo)} ({got} ok)")
        time.sleep(0.5 + random.random() * 0.25)
    log(f"industry map: +{got}; total {len(os.listdir(META))}")

def pack_meta():
    META = os.path.join(NSE_D, "meta")
    if not os.path.isdir(META): return
    rows = []
    for f in sorted(os.listdir(META)):
        if not f.endswith(".json"): continue
        try: rows.append(json.load(open(os.path.join(META, f))))
        except Exception: pass
    if rows:
        p = os.path.join(MB, "industry_map.csv")
        pd.DataFrame(rows).to_csv(p, index=False)
        log(f"pack: industry map {len(rows)} symbols → {p}")

# ---------------------------------------------------------------- E. pack
def pack():
    # shareholding panel: symbol, date, promoter %, public %
    rows = []
    for f in sorted(os.listdir(SH)):
        if not f.endswith(".json"): continue
        try: j = json.load(open(os.path.join(SH, f)))
        except Exception: continue
        d = j.get("data")
        if isinstance(d, dict): d = d.get("data") or []
        for rrow in (d or []):
            if not isinstance(rrow, dict): continue
            rows.append({"symbol": j["symbol"], "date": rrow.get("date"),
                         "promoter_pct": pd.to_numeric(rrow.get("pr_and_prgrp"), errors="coerce"),
                         "public_pct": pd.to_numeric(rrow.get("public_val"), errors="coerce"),
                         "submitted": rrow.get("submissionDate")})
    if rows:
        df = pd.DataFrame(rows).dropna(subset=["date"])
        df["date_dt"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
        df = df.sort_values(["symbol", "date_dt"]).drop_duplicates(["symbol", "date"], keep="last")
        p = os.path.join(MB, "shareholding_panel.csv.gz")
        df.to_csv(p, index=False, compression="gzip")
        log(f"pack: shareholding {len(df)} rows, {df['symbol'].nunique()} symbols → {p} ({os.path.getsize(p)/1e6:.1f} MB)")
    # filed-results panel for ALL symbols: reuse nse_results.py's pack (same stores) if importable, else minimal
    try:
        sys.argv = [sys.argv[0]]
        import importlib.util
        spec = importlib.util.spec_from_file_location("nse_results", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nse_results.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)  # runs its argparse with defaults; pack() writes nse/results_panel.csv.gz
        m.pack()
        src = os.path.join(NSE_D, "results_panel.csv.gz")
        if os.path.exists(src):
            import shutil; shutil.copy(src, os.path.join(MB, "results_panel_all.csv.gz"))
            log("pack: results_panel_all.csv.gz copied to mb/")
    except SystemExit:
        pass
    except Exception as e:
        log("pack: results reuse failed:", str(e)[:80])

if __name__ == "__main__":
    t0 = time.time()
    syms = universe()
    if not args.pack:
        if not args.skip_results: stage_results(syms)
        if not args.skip_shareholding: stage_shareholding(syms)
        stage_meta(syms)
        if not args.skip_prices: stage_prices(syms)
    pack(); pack_meta()
    log(f"done in {(time.time()-t0)/60:.0f} min → {MB}")
    log("Tell the chat it's done — the shards move over the bridge one by one.")

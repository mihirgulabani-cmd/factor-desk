#!/usr/bin/env python3
"""
build_dataset.py — data builder for the NSE fundamental + technical screener.

Run on your Mac (the cloud sandbox can't reach Yahoo/NSE):

    pip3 install --upgrade yfinance pandas requests
    python3 ~/Downloads/build_dataset.py

Resumable and idempotent: every stage caches to ~/Downloads/screener_data/.
Re-run any time; only missing pieces are fetched. To force a full refresh
of one stage, delete its cache file/dir and re-run.

Stages
  1. universe   → all NSE-listed equities (EQUITY_L.csv from NSE archives;
                  falls back to Nifty Total Market + Nifty 500 lists).
  2. mcap pass  → market cap for every symbol via Yahoo fast_info; keep
                  those > MCAP_FLOOR_CR (default 1,000 Cr).
  3. fundamentals → per-symbol: profile/ratios (info), annual + quarterly
                  income statement, balance sheet, cash flow, holders.
                  Cached one JSON per symbol in fundamentals/.
  4. prices     → ~4 years of daily OHLCV for the kept universe + NIFTY 50,
                  NIFTY 500, NIFTY MIDCAP 150, NIFTY SMALLCAP 250 and
                  sector indices (for relative strength).
  5. pack       → screener_data/bundle.json.gz  (single file to hand back)

Takes ~40–70 min the first time for ~1,000+ names (Yahoo rate limits),
seconds on re-run. Ctrl-C and re-run to resume.
"""
import os, sys, json, time, gzip, io, math, random, argparse, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

try:
    import yfinance as yf
except ImportError:
    sys.exit("pip3 install yfinance  (then re-run)")

# ----------------------------------------------------------------------------
ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/Downloads/screener_data"))
ap.add_argument("--mcap-floor-cr", type=float, default=1000.0)
ap.add_argument("--threads", type=int, default=6)
ap.add_argument("--years", type=int, default=4, help="years of daily prices")
ap.add_argument("--limit", type=int, default=0, help="debug: only first N symbols")
ap.add_argument("--stage", default="all", choices=["all", "universe", "mcap", "fund", "prices", "pack"])
ap.add_argument("--refresh-prices", action="store_true", help="ignore the price cache and refetch (use with --years N)")
ap.add_argument("--max-age-days", type=int, default=30, help="re-fetch a stock's fundamentals when its cached file is older than this (0 = never)")
ap.add_argument("--price-file", default=None, help="write prices to this file instead of prices.csv.gz")
ap.add_argument("--allow-stale", action="store_true", help="don't fail when the latest session's bar is missing (e.g. NSE holiday)")
args = ap.parse_args()

OUT = args.out
FUND_DIR = os.path.join(OUT, "fundamentals")
os.makedirs(FUND_DIR, exist_ok=True)
MCAP_FLOOR = args.mcap_floor_cr * 1e7  # Cr → INR

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/csv,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9"}

def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)

def jsonable(x):
    """Make pandas/numpy things JSON-safe."""
    if x is None:
        return None
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, (int, str, bool)):
        return x
    if hasattr(x, "item"):
        try:
            v = x.item()
            return jsonable(v)
        except Exception:
            pass
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.strftime("%Y-%m-%d")
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    try:
        return str(x)
    except Exception:
        return None

def df_to_dict(df):
    """Statement DataFrame (rows=line items, cols=period dates) → {item: {date: value}}."""
    if df is None or len(df) == 0:
        return {}
    out = {}
    for item, row in df.iterrows():
        d = {}
        for col, val in row.items():
            key = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
            v = jsonable(val)
            if v is not None:
                d[key] = v
        if d:
            out[str(item)] = d
    return out

def retry(fn, tries=4, base=1.5, what=""):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            if i == tries - 1:
                raise
            wait = base * (2 ** i) + random.random()
            if "Too Many Requests" in msg or "429" in msg or "Rate limited" in msg:
                wait = max(wait, 30 * (i + 1))
                log(f"  rate-limited{(' on ' + what) if what else ''}; sleeping {wait:.0f}s")
            time.sleep(wait)

# ----------------------------------------------------------------------------
# 1. UNIVERSE
# ----------------------------------------------------------------------------
UNIV_FILE = os.path.join(OUT, "universe_all.csv")

def fetch_csv(url, **kw):
    r = requests.get(url, headers=UA, timeout=30, **kw)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))

def stage_universe():
    if os.path.exists(UNIV_FILE):
        df = pd.read_csv(UNIV_FILE)
        log(f"universe: cached {len(df)} symbols")
        return df
    df = None
    manual = os.path.join(OUT, "EQUITY_L.csv")
    if os.path.exists(manual):
        raw = pd.read_csv(manual); raw.columns = [c.strip().upper() for c in raw.columns]
        raw = raw[raw["SERIES"].astype(str).str.strip().isin(["EQ", "BE"])]
        df = pd.DataFrame({"symbol": raw["SYMBOL"].str.strip(), "name": raw["NAME OF COMPANY"].str.strip(),
                           "series": raw["SERIES"].str.strip(), "listed": raw.get("DATE OF LISTING", ""), "source": "EQUITY_L_manual"})
        log(f"universe: manual EQUITY_L.csv gave {len(df)} EQ/BE symbols")
    for url in ([] if df is not None else ["https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
                "https://archives.nseindia.com/content/equities/EQUITY_L.csv"]):
        try:
            # NSE wants a warm cookie first
            s = requests.Session(); s.headers.update(UA)
            try: s.get("https://www.nseindia.com", timeout=15)
            except Exception: pass
            r = s.get(url, timeout=30); r.raise_for_status()
            raw = pd.read_csv(io.StringIO(r.text))
            raw.columns = [c.strip().upper() for c in raw.columns]
            raw = raw[raw["SERIES"].astype(str).str.strip().isin(["EQ", "BE"])]
            df = pd.DataFrame({"symbol": raw["SYMBOL"].str.strip(),
                               "name": raw["NAME OF COMPANY"].str.strip(),
                               "series": raw["SERIES"].str.strip(),
                               "listed": raw.get("DATE OF LISTING", ""),
                               "source": "EQUITY_L"})
            log(f"universe: EQUITY_L gave {len(df)} EQ/BE symbols")
            break
        except Exception as e:
            log(f"universe: {url} failed: {e}")
    if df is None or (len(df) < 500 and not os.path.exists(manual)):
        log("universe: falling back to NSE index constituent lists")
        frames = []
        for u in ["https://niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
                  "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
                  "https://niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv",
                  "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
                  "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"]:
            try:
                x = fetch_csv(u)
                x.columns = [c.strip().lower() for c in x.columns]
                frames.append(pd.DataFrame({"symbol": x["symbol"].str.strip(),
                                            "name": x["company name"].str.strip(),
                                            "series": x.get("series", "EQ"),
                                            "listed": "", "source": u.split("/")[-1]}))
                log(f"universe: {u.split('/')[-1]} gave {len(x)}")
            except Exception as e:
                log(f"universe: {u} failed: {e}")
        if not frames:
            sys.exit("Could not fetch any universe list. Download EQUITY_L.csv from "
                     "https://www.nseindia.com/market-data/securities-available-for-trading "
                     f"and save it as {UNIV_FILE.replace('universe_all','EQUITY_L')} then re-run.")
        df = pd.concat(frames).drop_duplicates("symbol")
    df = df.sort_values("symbol").reset_index(drop=True)
    df.to_csv(UNIV_FILE, index=False)
    return df

def ysym(sym):
    return f"{sym}.NS"

# ----------------------------------------------------------------------------
# 2. MARKET-CAP PASS
# ----------------------------------------------------------------------------
MCAP_FILE = os.path.join(OUT, "mcap.csv")

def fetch_shares(sym):
    def go():
        sh = yf.Ticker(ysym(sym)).get_shares_full(start=(datetime.now() - timedelta(days=548)).strftime("%Y-%m-%d"))
        if sh is None or len(sh) == 0:
            return None
        if isinstance(sh, pd.DataFrame): sh = sh[sh.columns[0]]
        return float(sh.iloc[-1])
    try:
        return sym, retry(go, tries=3), ""
    except Exception as e:
        return sym, None, str(e)[:120]

def batch_last_close(tickers, B=100):
    """Last close for many tickers via yf.download (one request per batch, not per ticker)."""
    out = {}
    for i in range(0, len(tickers), B):
        batch = tickers[i:i + B]
        def go():
            return yf.download(batch, period="5d", group_by="ticker", auto_adjust=False, threads=True, progress=False, timeout=60)
        try:
            d = retry(go, tries=3)
        except Exception as e:
            log(f"mcap: price batch {i} failed: {e}"); continue
        for tk in batch:
            try:
                sub = d[tk] if len(batch) > 1 else d
                c = sub["Close"].dropna()
                if len(c): out[tk] = float(c.iloc[-1])
            except Exception:
                pass
        log(f"mcap: prices {min(i + B, len(tickers))}/{len(tickers)}  got {len(out)}")
    return out

def stage_mcap(univ):
    # plain dicts throughout — no pandas cell assignment (pandas 3 is strict about dtypes)
    rec = {}
    if os.path.exists(MCAP_FILE):
        old = pd.read_csv(MCAP_FILE, dtype=str, keep_default_na=False)
        for _, r in old.iterrows():
            sym = str(r.get("symbol", "")).strip()
            if not sym: continue
            try: sh = float(r.get("shares", "") or "nan")
            except Exception: sh = float("nan")
            rec[sym] = {"shares": sh, "err": str(r.get("err", "") or "")}
    syms = list(univ["symbol"].astype(str))
    if args.limit: syms = syms[:args.limit]
    def has_shares(s):
        v = rec.get(s, {}).get("shares", float("nan"))
        return isinstance(v, float) and v == v and v > 0
    need_sh = [s for s in syms if not has_shares(s)]
    log(f"mcap: shares cached for {len(syms) - len(need_sh)}, fetching {len(need_sh)}")
    def save():
        rows = [{"symbol": s, "mcap": rec[s].get("mcap", ""), "price": rec[s].get("price", ""), "shares": rec[s].get("shares", ""),
                 "ok": rec[s].get("ok", ""), "err": rec[s].get("err", "")} for s in rec]
        pd.DataFrame(rows).to_csv(MCAP_FILE, index=False)
    if need_sh:
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futs = [ex.submit(fetch_shares, s) for s in need_sh]
            for i, f in enumerate(as_completed(futs), 1):
                sym, sh, err = f.result()
                rec.setdefault(sym, {})["shares"] = float(sh) if sh else float("nan")
                rec[sym]["err"] = err or ""
                if i % 100 == 0 or i == len(need_sh):
                    save(); log(f"mcap: shares {i}/{len(need_sh)}")
    with_sh = [s for s in syms if has_shares(s)]
    prices = batch_last_close([ysym(s) for s in with_sh])
    kept = []
    for s in with_sh:
        px = prices.get(ysym(s)); sh = rec[s]["shares"]
        rec[s]["price"] = px if px else ""
        rec[s]["mcap"] = (px * sh) if px else ""
        rec[s]["ok"] = bool(px)
        if px and px * sh >= MCAP_FLOOR:
            kept.append({"symbol": s, "mcap": px * sh, "price": px, "shares": sh, "ok": True})
    save()
    names = dict(zip(univ["symbol"].astype(str), univ["name"].astype(str)))
    keep = pd.DataFrame(kept)
    n_ok = sum(1 for s in with_sh if rec[s].get("ok"))
    if len(keep) == 0:
        sys.exit("mcap: nothing kept — Yahoo price fetch failed. Wait a few minutes and re-run.")
    keep["name"] = keep["symbol"].map(names)
    keep["mcap_cr"] = (keep["mcap"] / 1e7).round(1)
    keep = keep.sort_values("mcap", ascending=False).reset_index(drop=True)
    keep.to_csv(os.path.join(OUT, "universe_kept.csv"), index=False)
    log(f"mcap: {len(keep)} symbols >= {args.mcap_floor_cr:.0f} Cr  (of {n_ok} with a market cap; {len(syms) - n_ok} without)")
    return keep

# ----------------------------------------------------------------------------
# 3. FUNDAMENTALS
# ----------------------------------------------------------------------------
INFO_KEYS = [
    "longName", "shortName", "sector", "industry", "industryKey", "sectorKey", "longBusinessSummary", "website",
    "marketCap", "enterpriseValue", "currentPrice", "previousClose", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "beta", "sharesOutstanding", "floatShares", "impliedSharesOutstanding", "bookValue", "priceToBook",
    "trailingPE", "forwardPE", "trailingEps", "forwardEps", "pegRatio", "priceToSalesTrailing12Months",
    "enterpriseToRevenue", "enterpriseToEbitda", "dividendYield", "dividendRate", "payoutRatio",
    "returnOnEquity", "returnOnAssets", "grossMargins", "operatingMargins", "ebitdaMargins", "profitMargins",
    "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth", "revenuePerShare",
    "totalRevenue", "ebitda", "netIncomeToCommon", "totalCash", "totalDebt", "debtToEquity",
    "currentRatio", "quickRatio", "operatingCashflow", "freeCashflow",
    "heldPercentInsiders", "heldPercentInstitutions",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions", "recommendationKey", "recommendationMean",
    "lastFiscalYearEnd", "mostRecentQuarter", "fullTimeEmployees",
    "fiftyDayAverage", "twoHundredDayAverage", "averageVolume", "averageVolume10days",
]

def fetch_fund(sym):
    path = os.path.join(FUND_DIR, f"{sym}.json")
    if os.path.exists(path):
        return sym, "cached"
    try:
        t = yf.Ticker(ysym(sym))
        rec = {"symbol": sym, "fetched": datetime.now().strftime("%Y-%m-%d")}
        info = retry(lambda: t.info, tries=4, what=sym) or {}
        time.sleep(0.2)
        rec["info"] = {k: jsonable(info.get(k)) for k in INFO_KEYS}
        rec["inc_a"] = df_to_dict(retry(lambda: t.financials, tries=2))
        rec["inc_q"] = df_to_dict(retry(lambda: t.quarterly_financials, tries=2))
        rec["bs_a"] = df_to_dict(retry(lambda: t.balance_sheet, tries=2))
        rec["bs_q"] = df_to_dict(retry(lambda: t.quarterly_balance_sheet, tries=2))
        rec["cf_a"] = df_to_dict(retry(lambda: t.cashflow, tries=2))
        rec["cf_q"] = df_to_dict(retry(lambda: t.quarterly_cashflow, tries=2))
        try:
            mh = t.major_holders
            rec["holders"] = df_to_dict(mh) if mh is not None else {}
        except Exception:
            rec["holders"] = {}
        try:
            sh = t.get_shares_full(start=(datetime.now() - timedelta(days=4 * 365)).strftime("%Y-%m-%d"))
            if sh is not None and len(sh):
                sh = sh[~sh.index.duplicated(keep="last")]
                rec["shares_hist"] = {d.strftime("%Y-%m-%d"): jsonable(v) for d, v in sh.items()}
        except Exception:
            rec["shares_hist"] = {}
        got_info = bool(info.get("longName") or info.get("sector") or info.get("marketCap"))
        got_stmt = any(len(rec[k]) for k in ("inc_a", "inc_q", "bs_a", "cf_a"))
        if not got_info and not got_stmt:
            raise RuntimeError("empty response (network/rate limit) — not caching")
        with open(path, "w") as f:
            json.dump(rec, f)
        return sym, "ok" if got_stmt else "ok-nostatements"
    except Exception as e:
        return sym, f"ERR {str(e)[:100]}"

def is_empty_shell(path):
    try:
        with open(path) as f: rec = json.load(f)
        info = rec.get("info") or {}
        got_info = bool(info.get("longName") or info.get("sector") or info.get("marketCap"))
        got_stmt = any(len(rec.get(k) or {}) for k in ("inc_a", "inc_q", "bs_a", "cf_a"))
        return not got_info and not got_stmt
    except Exception:
        return True

def stage_fund(keep):
    syms = list(keep["symbol"])
    purged = 0
    for s in syms:
        p = os.path.join(FUND_DIR, f"{s}.json")
        if os.path.exists(p) and is_empty_shell(p):
            os.remove(p); purged += 1
    if purged: log(f"fundamentals: purged {purged} empty cached files (fetched while offline) — will refetch")
    if args.max_age_days > 0:
        cutoff = time.time() - args.max_age_days * 86400
        stale = [s for s in syms if os.path.exists(os.path.join(FUND_DIR, f"{s}.json")) and os.path.getmtime(os.path.join(FUND_DIR, f"{s}.json")) < cutoff]
        # cap the daily refresh so a weekday run stays short; the rest roll over to following days
        stale = sorted(stale, key=lambda s: os.path.getmtime(os.path.join(FUND_DIR, f"{s}.json")))[:80]
        for s in stale: os.remove(os.path.join(FUND_DIR, f"{s}.json"))
        if stale: log(f"fundamentals: {len(stale)} cached files older than {args.max_age_days} days — refetching (≤80 per run)")
    todo = [s for s in syms if not os.path.exists(os.path.join(FUND_DIR, f"{s}.json"))]
    log(f"fundamentals: {len(syms) - len(todo)} cached, {len(todo)} to fetch (~{len(todo) * 4 / 60 / max(1, args.threads / 2):.0f} min)")
    errs = 0
    with ThreadPoolExecutor(max_workers=max(1, min(3, args.threads // 2))) as ex:  # gentler: 7 calls per symbol
        futs = [ex.submit(fetch_fund, s) for s in todo]
        for i, f in enumerate(as_completed(futs), 1):
            s, st = f.result()
            if st.startswith("ERR"):
                errs += 1
                if errs <= 20: log(f"  {s}: {st}")
                if errs >= 30 and errs >= 0.9 * i:
                    log("fundamentals: almost everything is failing — internet down or Yahoo blocking. Stop (Ctrl-C), check connection, wait 10 min, re-run.")
            if i % 25 == 0 or i == len(todo):
                log(f"fundamentals: {i}/{len(todo)}  errors={errs}")
    log("fundamentals: done")

# ----------------------------------------------------------------------------
# 4. PRICES
# ----------------------------------------------------------------------------
PRICE_FILE = args.price_file or os.path.join(OUT, "prices.csv.gz")
INDEXES = {"^NSEI": "NIFTY50", "^CRSLDX": "NIFTY500", "^NSEMDCP50": "NIFTYMIDCAP50", "^CNXSC": "NIFTYSMALLCAP",
           "^NSEBANK": "BANKNIFTY", "^CNXIT": "NIFTYIT", "^CNXPHARMA": "NIFTYPHARMA", "^CNXAUTO": "NIFTYAUTO",
           "^CNXFMCG": "NIFTYFMCG", "^CNXMETAL": "NIFTYMETAL", "^CNXENERGY": "NIFTYENERGY", "^CNXREALTY": "NIFTYREALTY",
           "^CNXINFRA": "NIFTYINFRA", "^CNXPSUBANK": "NIFTYPSUBANK", "^CNXFIN": "NIFTYFIN"}

def _fetch_prices(tickers, start, tag):
    """Batched yf.download → list of tidy frames. Never raises; skips failed batches."""
    frames = []
    B = 80
    for i in range(0, len(tickers), B):
        batch = tickers[i:i + B]
        def go():
            return yf.download(batch, start=start, group_by="ticker", auto_adjust=False, threads=True, progress=False, timeout=60)
        try:
            d = retry(go, tries=3)
        except Exception as e:
            log(f"prices[{tag}]: batch {i} failed: {e}"); continue
        for tk in batch:
            try:
                sub = d[tk] if len(batch) > 1 else d
            except KeyError:
                continue
            if isinstance(sub.columns, pd.MultiIndex): sub.columns = sub.columns.get_level_values(-1)
            sub = sub.loc[:, ~sub.columns.duplicated()]
            sub = sub.dropna(subset=["Close"])
            if sub.empty: continue
            sym = INDEXES.get(tk, tk.replace(".NS", ""))
            frames.append(pd.DataFrame({"symbol": sym, "date": sub.index.strftime("%Y-%m-%d"),
                                        "open": sub["Open"].values, "high": sub["High"].values, "low": sub["Low"].values,
                                        "close": sub["Close"].values, "volume": sub["Volume"].values}))
        if (i // B) % 3 == 2: log(f"prices[{tag}]: {min(i + B, len(tickers))}/{len(tickers)}")
    return frames

def nse_bhav_patch(dates_needed, want_syms):
    """Patch missing sessions from NSE's bhavcopy archive (static CDN) — the authoritative closes,
    reachable in some places where Yahoo serves lagged bars. Returns tidy frames like _fetch_prices."""
    import io as _io
    ua = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
          "Referer": "https://www.nseindia.com/"}
    frames = []
    for d in dates_needed:
        url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        try:
            r = requests.get(url, headers=ua, timeout=30)
            if r.status_code != 200 or len(r.content) < 1000:
                log(f"prices: bhav patch {d}: HTTP {r.status_code}"); continue
            b = pd.read_csv(_io.BytesIO(r.content), skipinitialspace=True)
            b.columns = [c.strip().upper() for c in b.columns]
            b = b[b["SERIES"].astype(str).str.strip().isin(["EQ", "BE"])]
            b = b[b["SYMBOL"].str.strip().isin(want_syms)]
            f = pd.DataFrame({"symbol": b["SYMBOL"].str.strip(), "date": d.strftime("%Y-%m-%d"),
                              "open": pd.to_numeric(b["OPEN_PRICE"], errors="coerce"),
                              "high": pd.to_numeric(b["HIGH_PRICE"], errors="coerce"),
                              "low": pd.to_numeric(b["LOW_PRICE"], errors="coerce"),
                              "close": pd.to_numeric(b["CLOSE_PRICE"], errors="coerce"),
                              "volume": pd.to_numeric(b["TTL_TRD_QNTY"], errors="coerce")}).dropna(subset=["close"])
            if len(f): frames.append(f); log(f"prices: NSE bhavcopy patch {d} — {len(f)} symbols")
        except Exception as e:
            log(f"prices: bhav patch {d} failed: {str(e)[:70]}")
    return frames

def expected_last_session():
    """The most recent NSE session that should have a bar by now (weekday, 16:00 IST cutoff). Ignores holidays."""
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    d = now_ist.date()
    if now_ist.hour < 16: d -= timedelta(days=1)
    while d.weekday() >= 5: d -= timedelta(days=1)
    return d

def stage_prices(keep):
    start = (datetime.now() - timedelta(days=int(args.years * 365.25) + 10)).strftime("%Y-%m-%d")
    have = set()
    frames = []
    if os.path.exists(PRICE_FILE) and not args.refresh_prices:
        old = pd.read_csv(PRICE_FILE)
        last = pd.to_datetime(old["date"]).max()
        if (datetime.now() - last).days <= 14:
            have = set(old["symbol"]); frames.append(old)
            log(f"prices: history cached through {last.date()} for {len(have)} symbols")
        else:
            log(f"prices: cache too old (last {last.date()}), full refetch")
    all_tickers = list(dict.fromkeys([ysym(s) for s in keep["symbol"]] + list(INDEXES)))
    if args.limit: all_tickers = list(dict.fromkeys([ysym(s) for s in keep["symbol"]][:args.limit] + list(INDEXES)))
    # 1) full history only for symbols we don't have yet
    new_tickers = [t for t in all_tickers if INDEXES.get(t, t.replace(".NS", "")) not in have]
    if new_tickers:
        log(f"prices: full history for {len(new_tickers)} new tickers from {start}")
        frames += _fetch_prices(new_tickers, start, "full")
    # 2) ALWAYS top up the last days for every symbol — this is what makes each run carry the latest close
    tail_start = (datetime.now() - timedelta(days=9)).strftime("%Y-%m-%d")
    log(f"prices: topping up all {len(all_tickers)} tickers from {tail_start}")
    frames += _fetch_prices(all_tickers, tail_start, "tail")

    def write():
        allp = pd.concat(frames).drop_duplicates(["symbol", "date"], keep="last")
        allp.to_csv(PRICE_FILE, index=False, compression="gzip")
        return allp
    allp = write() if frames else None
    if allp is None:
        sys.exit("prices: nothing fetched at all — Yahoo unreachable")
    def freshness(allp, exp):
        per = pd.to_datetime(allp.groupby("symbol")["date"].max())
        return per.max().date(), float((per >= pd.Timestamp(exp)).mean())
    # freshness gate: never let the pipeline silently ship yesterday's prices
    exp = expected_last_session()
    last, cov = freshness(allp, exp)
    if last < exp or cov < 0.6:
        log(f"prices: last bar {last} (coverage {cov:.0%}) < expected session {exp} — waiting 90s and retrying the top-up once")
        time.sleep(90)
        frames += _fetch_prices(all_tickers, tail_start, "tail-retry")
        allp = write()
        last, cov = freshness(allp, exp)
    if last < exp or cov < 0.6:
        log(f"prices: still {cov:.0%} — patching the latest session from the NSE bhavcopy archive")
        want = set(keep["symbol"].astype(str))
        frames += nse_bhav_patch([exp], want)
        allp = write()
        last, cov = freshness(allp, exp)
    with open(os.path.join(OUT, "prices_through.txt"), "w") as f:
        f.write(str(last))
    log(f"prices: {len(allp)} rows, {allp['symbol'].nunique()} symbols, through {last} (coverage {cov:.0%}) → {PRICE_FILE}")
    if last < exp or cov < 0.6:
        msg = f"prices: STALE — last bar {last}, {cov:.0%} of symbols have the {exp} bar. NSE holiday, or Yahoo hasn't published yet."
        if os.environ.get("GITHUB_ACTIONS") and not args.allow_stale:
            sys.exit(msg + " Failing the run so the site never silently serves old prices. "
                           "(If today was a market holiday, this failure is expected — ignore it.)")
        log(msg)

# ----------------------------------------------------------------------------
# 5. PACK
# ----------------------------------------------------------------------------
def stage_pack(keep):
    funds = {}
    for s in keep["symbol"]:
        p = os.path.join(FUND_DIR, f"{s}.json")
        if os.path.exists(p):
            with open(p) as f: funds[s] = json.load(f)
    bundle = {"built": datetime.now().strftime("%Y-%m-%d %H:%M"), "mcap_floor_cr": args.mcap_floor_cr,
              "universe": keep.to_dict(orient="records"), "fundamentals": funds}
    out = os.path.join(OUT, "bundle.json.gz")
    with gzip.open(out, "wt") as f:
        json.dump(bundle, f)
    log(f"pack: {len(funds)} fundamentals + {len(keep)} universe rows → {out}  ({os.path.getsize(out) / 1e6:.1f} MB)")
    log(f"HAND BACK: {out}  and  {PRICE_FILE}")

# ----------------------------------------------------------------------------
if __name__ == "__main__":
    t0 = time.time()
    univ = stage_universe()
    if args.stage in ("all", "mcap", "fund", "prices", "pack"):
        keep = stage_mcap(univ) if args.stage in ("all", "mcap") or not os.path.exists(os.path.join(OUT, "universe_kept.csv")) \
            else pd.read_csv(os.path.join(OUT, "universe_kept.csv"))
    if args.stage in ("all", "fund"):
        stage_fund(keep)
    if args.stage in ("all", "prices"):
        stage_prices(keep)
    if args.stage in ("all", "pack"):
        stage_pack(keep)
    log(f"total {(time.time() - t0) / 60:.1f} min")

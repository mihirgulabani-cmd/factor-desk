#!/usr/bin/env python3
"""
nse_pull.py — the exchange-data puller: everything NSE publishes that Yahoo doesn't have.

Run on the Mac (NSE blocks datacenter IPs; a home connection works):

    python3 ~/Downloads/factor-desk/nse_pull.py --backfill-days 730      # first run: ~2y of daily files
    python3 ~/Downloads/factor-desk/nse_pull.py                          # daily incremental (~1-2 min)

What it collects, into ~/Downloads/screener_data/nse/:

  bhav/sec_YYYY-MM-DD.csv      full bhavcopy incl. DELIV_PER (delivery %)        [daily, backfillable]
  fo/fo_YYYY-MM-DD.csv         F&O bhavcopy → per-stock futures OI + change      [daily, backfillable]
  announcements.jsonl          corporate announcements (timestamped, w/ subject) [rolling, kept forever]
  insider.jsonl                insider/SAST trades                               [rolling]
  bulk_block.jsonl             bulk & block deals w/ buyer names                 [rolling]
  events.jsonl                 results/board-meeting calendar                    [rolling]
  asm.json                     current ASM/GSM surveillance lists                [snapshot]
  shareholding/SYMBOL.json     quarterly shareholding pattern incl. pledge       [top names, slow-cycled]

Everything is resumable and append-only; re-running never loses data. JSONL files carry an
`_fetched` stamp per row and are de-duplicated on a natural key.
"""
import os, sys, json, time, gzip, io, csv, argparse, random, zipfile
from datetime import datetime, timedelta, date

import requests
try:
    import pandas as pd
except ImportError:
    sys.exit("pip3 install pandas requests")

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/Downloads/screener_data/nse"))
ap.add_argument("--backfill-days", type=int, default=10, help="how far back to fill daily files (bhav/fo)")
ap.add_argument("--shareholding", type=int, default=25, help="how many symbols' shareholding to refresh per run (slow-cycled)")
ap.add_argument("--universe", default=os.path.expanduser("~/Downloads/screener_data/universe_kept.csv"))
args = ap.parse_args()
OUT = args.out
for d in ("bhav", "fo", "shareholding"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

def log(*a): print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}

class NSE:
    """Session with the cookie warm-up NSE's JSON APIs require."""
    def __init__(self):
        self.s = requests.Session(); self.s.headers.update(UA); self.warm = 0
    def _warmup(self):
        if time.time() - self.warm < 300: return
        try:
            self.s.get("https://www.nseindia.com", timeout=15)
            self.s.get("https://www.nseindia.com/companies-listing/corporate-filings-announcements", timeout=15)
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
    def get_raw(self, url, tries=3):
        for i in range(tries):
            try:
                r = self.s.get(url, timeout=40, headers=UA)
                if r.status_code == 404: return None
                r.raise_for_status()
                return r.content
            except Exception as e:
                if i == tries - 1: raise
                time.sleep(2 * (i + 1))

nse = NSE()

def jsonl_append(fname, rows, key):
    """Append rows to a JSONL file, de-duplicated on key(row)."""
    path = os.path.join(OUT, fname)
    seen = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try: seen.add(key(json.loads(line)))
                except Exception: pass
    new = [r for r in rows if key(r) not in seen]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path, "a") as f:
        for r in new:
            r["_fetched"] = stamp
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new)

# ---------------------------------------------------------------- daily files (backfillable)
def trading_days(n):
    d = date.today(); out = []
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= timedelta(days=1)
    return out

def pull_bhav():
    """Full bhavcopy with delivery % — sec_bhavdata_full_DDMMYYYY.csv"""
    got = miss = 0
    for d in trading_days(max(2, args.backfill_days * 5 // 7)):
        path = os.path.join(OUT, "bhav", f"sec_{d.isoformat()}.csv")
        if os.path.exists(path): continue
        url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
        try:
            raw = nse.get_raw(url)
        except Exception as e:
            log(f"bhav {d}: {str(e)[:60]}"); miss += 1; continue
        if raw is None or len(raw) < 1000: miss += 1; continue  # holiday
        open(path, "wb").write(raw); got += 1
        if got % 25 == 0: log(f"bhav: {got} days fetched")
        time.sleep(0.4)
    log(f"bhav: +{got} days (misses/holidays {miss}); total {len(os.listdir(os.path.join(OUT,'bhav')))}")

def pull_fo():
    """F&O bhavcopy (UDiFF zip) → per-stock futures OI summary."""
    got = 0
    for d in trading_days(max(2, args.backfill_days * 5 // 7)):
        path = os.path.join(OUT, "fo", f"fo_{d.isoformat()}.csv")
        if os.path.exists(path): continue
        url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip"
        try:
            raw = nse.get_raw(url)
        except Exception as e:
            log(f"fo {d}: {str(e)[:60]}"); continue
        if raw is None or len(raw) < 1000: continue
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                inner = z.read(z.namelist()[0])
            df = pd.read_csv(io.BytesIO(inner), low_memory=False)
            # UDiFF columns: TckrSymb, FinInstrmTp (STF = stock futures), OpnIntrst, ChngInOpnIntrst, TtlTradgVol, ClsPric, XpryDt
            fut = df[df["FinInstrmTp"].isin(["STF", "IDF"])]
            agg = fut.groupby("TckrSymb").agg(oi=("OpnIntrst", "sum"), oi_chg=("ChngInOpnIntrst", "sum"),
                                              vol=("TtlTradgVol", "sum"), close=("ClsPric", "first")).reset_index()
            agg.insert(0, "date", d.isoformat())
            agg.to_csv(path, index=False); got += 1
        except Exception as e:
            log(f"fo {d} parse: {str(e)[:60]}")
        time.sleep(0.4)
    log(f"fo: +{got} days; total {len(os.listdir(os.path.join(OUT,'fo')))}")

# ---------------------------------------------------------------- rolling JSON feeds
def pull_announcements():
    try:
        j = nse.get_json("https://www.nseindia.com/api/corporate-announcements?index=equities")
        rows = j if isinstance(j, list) else j.get("rows", j.get("data", []))
        n = jsonl_append("announcements.jsonl", rows, lambda r: (r.get("symbol"), r.get("an_dt") or r.get("sort_date"), (r.get("desc") or r.get("subject") or "")[:60]))
        log(f"announcements: +{n}")
    except Exception as e:
        log("announcements FAILED:", str(e)[:100])

def pull_insider():
    try:
        j = nse.get_json("https://www.nseindia.com/api/corporates-pit?index=equities")
        rows = j.get("data", []) if isinstance(j, dict) else j
        n = jsonl_append("insider.jsonl", rows, lambda r: (r.get("symbol"), r.get("date") or r.get("intimDt"), r.get("acqName"), r.get("secAcq")))
        log(f"insider: +{n}")
    except Exception as e:
        log("insider FAILED:", str(e)[:100])

def pull_bulk_block():
    try:
        j = nse.get_json("https://www.nseindia.com/api/snapshot-capital-market-largedeal")
        rows = []
        for k in ("BULK_DEALS_DATA", "BLOCK_DEALS_DATA", "SHORT_DEALS_DATA"):
            for r in (j.get(k) or []):
                r["deal_type"] = k.split("_")[0].lower(); rows.append(r)
        n = jsonl_append("bulk_block.jsonl", rows, lambda r: (r.get("symbol"), r.get("date"), r.get("clientName"), r.get("qty"), r.get("deal_type")))
        log(f"bulk/block: +{n}")
    except Exception as e:
        log("bulk/block FAILED:", str(e)[:100])

def pull_events():
    try:
        j = nse.get_json("https://www.nseindia.com/api/event-calendar")
        rows = j if isinstance(j, list) else j.get("data", [])
        n = jsonl_append("events.jsonl", rows, lambda r: (r.get("symbol"), r.get("date"), (r.get("purpose") or "")[:40]))
        log(f"events: +{n}")
    except Exception as e:
        log("events FAILED:", str(e)[:100])

def pull_asm():
    out = {}
    for name, url in [("asm", "https://www.nseindia.com/api/reportASM"), ("gsm", "https://www.nseindia.com/api/reportGSM")]:
        try:
            out[name] = nse.get_json(url)
        except Exception as e:
            log(f"{name} FAILED:", str(e)[:80])
    if out:
        out["_fetched"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(out, open(os.path.join(OUT, "asm.json"), "w"))
        log("asm/gsm: saved")

def pull_shareholding():
    """Quarterly shareholding incl. promoter pledge — slow-cycled through the universe."""
    if not os.path.exists(args.universe):
        log("shareholding: universe_kept.csv not found — skipped"); return
    syms = list(pd.read_csv(args.universe)["symbol"].astype(str))
    def age(s):
        p = os.path.join(OUT, "shareholding", f"{s}.json")
        return os.path.getmtime(p) if os.path.exists(p) else 0
    todo = sorted(syms, key=age)[:args.shareholding]
    got = 0
    for s in todo:
        try:
            j = nse.get_json(f"https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={requests.utils.quote(s)}")
            json.dump({"symbol": s, "_fetched": datetime.now().strftime("%Y-%m-%d"), "data": j}, open(os.path.join(OUT, "shareholding", f"{s}.json"), "w"))
            got += 1
        except Exception as e:
            log(f"shareholding {s}: {str(e)[:60]}")
        time.sleep(0.8)
    log(f"shareholding: refreshed {got}/{len(todo)} (oldest-first; full cycle ≈ {max(1, len(syms)//max(1,args.shareholding))} runs)")

if __name__ == "__main__":
    t0 = time.time()
    pull_bhav()
    pull_fo()
    pull_announcements()
    pull_insider()
    pull_bulk_block()
    pull_events()
    pull_asm()
    pull_shareholding()
    log(f"done in {(time.time()-t0)/60:.1f} min → {OUT}")
    log("If the JSON feeds FAILED but bhav/fo worked, open nseindia.com once in a browser and re-run (cookie warm-up).")

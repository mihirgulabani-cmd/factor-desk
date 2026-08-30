#!/usr/bin/env python3
"""pack_nse.py — consolidate the daily NSE files into two compact files for analysis.

    python3 ~/Documents/GitHub/factor-desk/pack_nse.py

Writes into ~/screener_data/nse/:
  delivery_panel.csv.gz   date, symbol, close, qty, deliv_qty, deliv_per   (EQ series only)
  fo_panel.csv.gz         date, symbol, oi, oi_chg, vol, close
Then drag those two (plus announcements.jsonl, insider.jsonl, bulk_block.jsonl, events.jsonl) into the chat.
"""
import os, glob, gzip
import pandas as pd
NSE = os.path.expanduser("~/screener_data/nse")

# ---- delivery panel from bhavcopies
rows = []
files = sorted(glob.glob(os.path.join(NSE, "bhav", "sec_*.csv")))
print(f"bhav files: {len(files)}")
for i, f in enumerate(files):
    try:
        d = pd.read_csv(f, skipinitialspace=True)
        d.columns = [c.strip().upper() for c in d.columns]
        d = d[d["SERIES"].astype(str).str.strip() == "EQ"]
        date = os.path.basename(f)[4:14]
        out = pd.DataFrame({"date": date, "symbol": d["SYMBOL"].str.strip(),
                            "close": pd.to_numeric(d["CLOSE_PRICE"], errors="coerce"),
                            "qty": pd.to_numeric(d["TTL_TRD_QNTY"], errors="coerce"),
                            "deliv_qty": pd.to_numeric(d["DELIV_QTY"], errors="coerce"),
                            "deliv_per": pd.to_numeric(d["DELIV_PER"], errors="coerce")})
        rows.append(out)
    except Exception as e:
        print(f"  {os.path.basename(f)}: {str(e)[:60]}")
    if (i + 1) % 100 == 0: print(f"  {i+1}/{len(files)}")
if rows:
    allr = pd.concat(rows)
    p = os.path.join(NSE, "delivery_panel.csv.gz")
    allr.to_csv(p, index=False, compression="gzip")
    print(f"delivery_panel: {len(allr)} rows, {allr['symbol'].nunique()} symbols → {p} ({os.path.getsize(p)/1e6:.1f} MB)")

# ---- F&O OI panel
fo_files = sorted(glob.glob(os.path.join(NSE, "fo", "fo_*.csv")))
print(f"fo files: {len(fo_files)}")
if fo_files:
    fo = pd.concat([pd.read_csv(f) for f in fo_files])
    fo = fo.rename(columns={"TckrSymb": "symbol"})
    p = os.path.join(NSE, "fo_panel.csv.gz")
    fo.to_csv(p, index=False, compression="gzip")
    print(f"fo_panel: {len(fo)} rows, {fo['symbol'].nunique()} symbols → {p} ({os.path.getsize(p)/1e6:.1f} MB)")
print("\nDrag into the chat: delivery_panel.csv.gz, fo_panel.csv.gz, announcements.jsonl, insider.jsonl, bulk_block.jsonl, events.jsonl")

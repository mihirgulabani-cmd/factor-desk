# Factor Desk — point-in-time backtest, 27 Aug 2026

**Question asked:** if this ranking had been run in the past and the top 10 bought each period, what would it have made, and how often was it right?

**What could and could not be tested.** Yahoo supplies four fiscal years of statements (FY23–FY26). A fundamental score is only honest once statements existed *at the time*, so the full model can be tested from **July 2023** — 3 annual cohorts, 38 monthly, 82 two-week. A ten-year test of the full model is not possible from this data and nobody should claim one. The technical pillars alone can be pushed to 2015 once the 11-year price file is in (pending).

**Method (no look-ahead).** At each cohort date the entire universe is re-scored using only statements whose period end plus a reporting lag (75 days annual, 50 days quarterly) is before the date, and only prices up to that date. Names must pass the mode's gates and have ≥ 60 % of the score weight actually measured. The top-10 by score is bought equal-weight and held to the next cohort date; 0.3 % round-trip cost per swing rebalance, 0.2 % for annual. The **ownership pillar is excluded** (no holdings history). Two benchmarks: Nifty 500, and — the one that matters — the **equal-weight average of every name that passed the same gates**. If the top-10 does not beat that, the ranking is adding nothing to the gate.

Every number below is price-only (no dividends) and on today's ≥ ₹1,000 Cr universe, which is survivorship-biased upward for *everything* in the table equally.

## Results

| Test | Cohorts | Top-10 CAGR | Eligible universe CAGR | Nifty 500 CAGR | Top-10 max DD | Pick hit rate | Top-10 − universe, avg / % periods | Rank IC, mean / % positive | Decile spread D10−D1 |
|---|---|---|---|---|---|---|---|---|---|
| **Long-term, buy 1 Jul, hold 1 yr** — full model | 3 | **38.1 %** | 21.6 % | 12.4 % | −5.7 % | 67 % | +24.9 pp / 67 % | +0.09 / 100 % | +19.4 pp / 100 % |
| Long-term — technicals only | 3 | 37.3 % | 21.8 % | 12.4 % | −0.9 % | 63 % | +20.3 pp / 67 % | +0.10 / 100 % | +26.3 pp / 100 % |
| **Swing long, 2-week hold** — full model | 82 | **28.6 %** | 20.7 % | 11.8 % | −33.8 % | 53 % | +0.31 % / 55 % | +0.026 / 63 % | +0.7 pp / 61 % |
| Swing long, 1-month hold — full model | 38 | 18.0 % | 21.0 % | 11.7 % | −40.3 % | 53 % | −0.06 % / 55 % | +0.017 / 63 % | +0.9 pp / 58 % |
| Swing long, 1-month — technicals only | 38 | 29.6 % | 21.0 % | 11.7 % | −36.7 % | 55 % | +0.84 % / 50 % | +0.014 / 58 % | +1.0 pp / 58 % |
| Swing long, 1-month — cap at ≤ 30 % above 200 EMA | 38 | 15.8 % | 19.6 % | 11.7 % | −28.8 % | 54 % | −0.20 % / 37 % | +0.036 / 66 % | +1.3 pp / 66 % |
| Swing long, 3-month hold — full model | 13 | 12.6 % | 20.3 % | 11.4 % | −32.2 % | 51 % | −1.5 % / 38 % | +0.022 / 54 % | +3.4 pp / 62 % |
| **Swing short, 1-month** (short the top-10 short scores) | 35 | −13.9 % | −11.1 % (short everything eligible) | −10.0 % (short index) | −39.9 % | 52 % | −0.10 % / 46 % | −0.001 / 54 % | +0.3 pp / 46 % |
| Swing short, 3-month | 12 | −25.9 % | −17.8 % | −12.1 % | −56.5 % | 42 % | −1.7 % / 33 % | +0.03 / 58 % | +1.5 pp / 58 % |

*Rank IC* = Spearman correlation between score and next-period return across all eligible names — the cleanest measure of whether the ranking orders stocks correctly. Professional multi-factor models typically show 0.02–0.06 at monthly horizons. *Decile spread* = average return of the top-scoring tenth minus the bottom tenth.

### Long-term, year by year

| Bought | Sold | Eligible | Top-10 | Bottom-10 | Universe | Nifty 500 | Picks up | IC | D10−D1 | The ten |
|---|---|---|---|---|---|---|---|---|---|---|
| 3 Jul 2023 | 1 Jul 2024 | 623 | **+133.4 %** | +43.7 % | +61.8 % | +37.5 % | 10/10 | +0.20 | +47.3 pp | LGBBROSLTD CHENNPETRO WELENT RECLTD MSTCLTD SHRIPISTON MANINFRA ALIVUS HAL MAZDOCK |
| 1 Jul 2024 | 1 Jul 2025 | 727 | **−5.7 %** | +8.0 % | +3.2 % | +3.9 % | 4/10 | +0.04 | +6.3 pp | BOMDYEING GANESHHOU RTNPOWER MGL INDSWFTLAB KSCL HAL CHAMBLFERT ALLDIGI INDUSTOWER |
| 1 Jul 2025 | 27 Aug 2026 | 772 | **+19.8 %** | +12.4 % | +7.7 % | −0.6 % | 6/10 | +0.04 | +4.5 pp | FORCEMOT SRM MCX INDUSTOWER RPGLIFE INDIANHUME AIIL BSE IIFLCAPS NATIONALUM |

Read the middle row before the headline. 2023 was a small-cap mania in which the ranking had only one year of statements to work with; it caught PSU/defence/rail (RECLTD, HAL, MAZDOCK, MSTCLTD) and doubled. 2024's ten included BOMDYEING and RTNPOWER at P/E 1–1.5 — one-off-gain "value" that the red-flag engine now catches but the score did not — and lost money in a flat year. 2025 is the first cohort with three years of statements and it beat the universe by 12 points with a normal-looking list (MCX, BSE, FORCEMOT, NATIONALUM).

## What this says

1. **The ranking orders stocks correctly more often than not, but weakly.** IC is positive in 63 % of two-week periods and 100 % of annual ones; decile spreads are positive in 58–66 % of periods. That is a real but modest signal — about what published factor models show — not a crystal ball.

2. **At the annual horizon the top-10 beat both benchmarks, on three data points.** 38 % CAGR against 22 % for the gated universe and 12 % for Nifty 500, with the top decile beating the bottom decile in every cohort. Three cohorts, one of them a mania year, is not proof. It is enough to keep running the test each July.

3. **For swing, the holding period decides everything.** Two weeks: top-10 beat the gated universe (28.6 % vs 20.7 %). One month: no better than the universe. Three months: worse. The composite is momentum-heavy; momentum names bought at the top of the ranking are extended, and extension mean-reverts on a 1–3 month clock. Your own instinct — ride it while it works, exit when it dies, do not sit through a month on a fixed calendar — is what the data supports.

4. **Fundamentals help at a year, do nothing at a month.** Technicals-only matched the full model over a year (37.3 % vs 38.1 %) and beat it at one month (29.6 % vs 18.0 %). With only 1–3 years of statements in the window, the fundamental pillars were mostly noise at short horizons. This is consistent with everything known about how fundamentals price in — over quarters, not weeks. For swing the fundamental weight should be a *gate* (no losses, no dilution, cash converts), not a rank driver.

5. **The gate is worth more than the rank.** "Above the 200 EMA, liquid, ATR ≥ 1.5 %" as an equal-weight basket compounded at ~21 % against 12 % for the index across every test — without any ranking at all. Most of the money is in the filter.

6. **The short side has no edge.** Shorting the ten worst-scored names lost the same as shorting anything in a rising market; IC ≈ 0. Do not short off this ranking without a market-regime filter on top.

7. **Concentration is the risk.** Ten names, monthly rebalanced, drew down 34–40 % in Jan–Feb 2025 when the gated universe fell 25 % and the index 18 %. The extension cap (≤ 30 % above 200 EMA) cut the drawdown to 29 % at some cost to return.

## What to change in the desk (done or queued)

- Swing mode: keep the fundamentals as gates, rank on technicals; default review cadence two weeks, not a month. *(queued — one-line weight change in the HTML, plus a "no red flags" gate default)*
- Long-term mode: "one-off gain" trap — exclude names whose other income > 30 % of PBT or whose P/E < 3 from the top list by default. *(queued as a default gate)*
- Add the extension cap as a visible gate slider. *(queued)*
- Re-run this backtest every quarter as statements accumulate; the fundamental side becomes honest only from mid-2025 onward.

## Files

`backtest.py` + `factor_engine.py` (point-in-time engine shared with the desk), `backtest_summary*.md` (every cohort, every pick), `backtest_picks*.csv` (entry/exit price of every pick), `backtest_results*.json`.

`python3 backtest.py --data ~/Downloads/screener_data [--swing-hold 2W|1M|3M] [--tech-only] [--max-ext 0.30] [--prices prices_11y.csv.gz --tech-only --start 2016-07-01]`

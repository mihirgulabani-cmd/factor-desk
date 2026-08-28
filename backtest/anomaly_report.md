# Anomaly lab — 2026-08-27 21:29

Data: `prices.csv.gz`, 1293 symbols, 2022-08-17 → 2026-08-27. Eligibility: 20d turnover ≥ ₹5 Cr, ≥250 bars. One event per symbol per 20 sessions. All returns are EXCESS over the same-day eligible-universe mean (market and drift stripped out).

| Signal | n | 10d | 20d | win20 | 40d | half1/half2 (20d) | control 20d | verdict |
|---|---|---|---|---|---|---|---|---|
| 52w-high breakout + vol≥2x | 3269 | +0.30% | **+0.63%** | +46.49% | +0.93% | +0.31% / +1.16% | +0.67% | no edge vs control |
| 52w-high breakout + vol≥2x + not extended | 2770 | +0.19% | **+0.53%** | +46.74% | +0.72% | +0.17% / +1.10% | +0.63% | no edge vs control |
| tight-base (BBW<20th pctl) 20d breakout | 3002 | +0.38% | **+0.44%** | +44.85% | +0.30% | +0.13% / +0.58% | +0.21% | weak positive |
| NR7 then break up | 15753 | -0.13% | **-0.04%** | +43.70% | +0.01% | +0.09% / -0.11% | -0.04% | no edge vs control |
| 3 down days, still above 200EMA | 12684 | +0.24% | **+0.31%** | +44.73% | +0.35% | +0.35% / +0.28% | -0.27% | weak positive |
| 3 down days above 200EMA + touch 20d low | 4713 | +0.07% | **-0.01%** | +43.68% | +0.21% | -0.14% / +0.08% | +0.31% | no edge vs control |
| gap up >4% on vol≥3x (drift proxy) | 1786 | +0.34% | **+0.76%** | +45.36% | +1.02% | +1.26% / +0.40% | +0.83% | no edge vs control |
| gap DOWN >4% on vol≥3x (short check) | 675 | -0.85% | **-0.46%** | +41.12% | -0.26% | -1.70% / +0.06% | – | negative |
| volume dry-up near 20d low, above 200EMA | 2284 | -0.17% | **-0.14%** | +42.99% | +0.13% | -0.69% / +0.19% | -0.64% | negative |
| 20d-high break + RS line 60d high | 6732 | +0.22% | **+0.37%** | +44.60% | +0.60% | +0.56% / +0.25% | -0.03% | weak positive |
| over-extended >60% above 200EMA (short check) | 1004 | +0.47% | **+1.31%** | +45.16% | +1.93% | +0.83% / +2.38% | – | weak positive |
| vol surge 2x, price flat (Mihir's) | 3678 | +0.03% | **+0.05%** | +43.12% | -0.27% | -0.20% / +0.16% | -0.06% | not robust (halves disagree) |
| sector hot (+5% 20d), stock flat, above 200EMA | 2053 | +0.11% | **-0.04%** | +43.35% | -0.03% | +0.10% / -0.21% | +0.22% | no edge vs control |

Notes: **52w-high breakout + vol≥2x** — classic momentum breakout; control = same breakout on normal volume · **tight-base (BBW<20th pctl) 20d breakout** — control = same breakout without the tight base · **NR7 then break up** — control = NR7 break DOWN · **3 down days, still above 200EMA** — pullback-buy; control = same in a downtrend · **gap up >4% on vol≥3x (drift proxy)** — results-day drift proxy without dates; control = gap on thin volume · **gap DOWN >4% on vol≥3x (short check)** — positive x20 here = gap-downs bounce; negative = they keep falling · **volume dry-up near 20d low, above 200EMA** — Wyckoff-ish; control = same below 200EMA · **20d-high break + RS line 60d high** — leadership breakout; control = breakout with weak RS · **over-extended >60% above 200EMA (short check)** — x20 NEGATIVE would mean extension mean-reverts tradably · **vol surge 2x, price flat (Mihir's)** — re-run in the harness; control = flat week, any volume · **sector hot (+5% 20d), stock flat, above 200EMA** — laggard catch-up; control = chasing the sector leader

A signal only matters if its 20d column beats BOTH zero and its control, with the same sign in both halves. Everything else is the market, the filter, or luck.
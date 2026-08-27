# NSE Factor Desk — cloud refresh

Runs the fundamental + technical screener every weekday at 18:30 IST on GitHub's servers and
publishes the desk to a GitHub Pages URL you can open from anywhere.

## One-time setup (≈5 minutes)

1. **Create the repo.** On github.com → New repository → name `factor-desk`, **Public**
   (GitHub Pages on a *private* repo needs a paid plan; the desk contains only public market data —
   your notes and watchlist live in your own browser, never in the file). Do not add a README.

2. **Push these files.** In Terminal:
   ```
   cd ~/Downloads/factor-desk
   git init && git add . && git commit -m "factor desk"
   git branch -M main
   git remote add origin https://github.com/<your-username>/factor-desk.git
   git push -u origin main
   ```
   (If git asks you to log in, use a personal access token as the password: GitHub → Settings →
   Developer settings → Personal access tokens → Tokens (classic) → scope `repo`.)

3. **Turn on Pages.** Repo → Settings → Pages → *Build and deployment* → Source: **GitHub Actions**.

4. **Run it once by hand.** Repo → Actions → *refresh-desk* → *Run workflow*. The first run pulls
   every stock's statements (~45–60 min). After that, daily runs take ~5–8 min.

5. **Open the desk.** `https://<your-username>.github.io/factor-desk/` — bookmark it on your phone.
   `…/factor-desk/built.txt` shows when it last refreshed; `…/ranked_swing_long.csv` etc. are the lists.

## What runs

| step | what |
|---|---|
| `build_dataset.py` | universe (NSE list, refreshed monthly) → share counts (cached) → last prices → mcap ≥ ₹1,000 Cr → fundamentals (cached; each name re-pulled after 30 days, ≤80 per run) → 4y daily prices (every run) |
| `screener_model.py` | 58 factors → 11 pillars → 3 modes, red flags, trade levels |
| `build_html.py` | embeds the JSON into `model_template.html` → `NSE-Factor-Desk.html` |

Fundamentals and share counts persist between runs in the Actions cache. If a run fails (Yahoo
throttling, GitHub outage) the previous desk stays published and the next run fills the gaps.

## Changing the schedule

Edit `.github/workflows/refresh.yml` — the `cron` line is UTC (`0 13` = 18:30 IST). Push the change.

## Local use still works

`bash refresh.sh` on the Mac does the same thing into `~/Downloads/screener_data/`.
`bash install_daily.sh` installs the laptop-side schedule (runs on wake if the 18:30 slot was missed).
`python3 backtest.py` re-runs the point-in-time backtest.

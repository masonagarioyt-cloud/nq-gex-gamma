# NQ Gamma Exposure (GEX) Auto-Updater

Generates a fresh, ready-to-paste TradingView Pine Script every weekday morning
with estimated NQ gamma levels (call wall, put wall, gamma flip), derived from
free QQQ options data.

## What this actually is (read this first)

- **Free data**, not a licensed feed. Source is Yahoo Finance's public/unofficial
  data, pulled via the `yfinance` Python library. It can be delayed, occasionally
  incomplete, or break if Yahoo changes something. That's the tradeoff for $0.
- **Estimated levels**, not SpotGamma's or MenthorQ's proprietary numbers. This
  uses the standard public GEX formula (dealers assumed long calls / short puts) —
  the same simplification every free GEX calculator online uses. It will be in
  the right neighborhood but won't match a paid provider exactly.
- **QQQ as a proxy for NQ.** There's no free NDX/NQ options feed, so QQQ options
  (highly correlated, very liquid) are used and then scaled to NQ price terms
  using the live NQ/QQQ ratio at run time.
- **One manual step remains forever**: pasting the fresh code into TradingView.
  TradingView has no API — nothing, free or paid, can push code into your chart
  for you. This setup automates everything up to that point.

## One-time setup (about 10 minutes)

1. **Create a free GitHub account** at github.com if you don't have one.
2. **Create a new repository**: click the "+" in the top right → "New repository".
   Name it anything (e.g. `nq-gex`). Keep it **Public** (needed for the free raw-file
   link to work simply) or Private if you don't mind an extra step later. Click
   "Create repository".
3. **Upload these 4 files/folders** into that repo (drag-and-drop works on GitHub's
   web page, under "Add file" → "Upload files"), keeping the folder structure:
   - `generate_gex.py`
   - `requirements.txt`
   - `.github/workflows/daily_gex.yml`
   - `README.md` (optional, just for your reference)
4. **Enable Actions**: go to the "Actions" tab of your repo. GitHub may ask you to
   confirm you want to enable workflows — click the button to enable them.
5. **Run it once manually** to make sure it works: in the "Actions" tab, click
   "Daily NQ GEX Update" on the left, then click "Run workflow" (top right) → "Run
   workflow" again to confirm. Wait ~1 minute, refresh, and you should see a green
   checkmark. This creates the first `output.pine` file in your repo.

## Your daily bookmark

Once step 5 has run at least once, bookmark this link (replace the placeholders
with your actual GitHub username and repo name):

```
https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO_NAME/main/output.pine
```

Every weekday at 8:15 AM ET, the Action re-runs automatically and updates that
file. Each morning:

1. Open your bookmarked link.
2. Select all the text (Ctrl/Cmd+A), copy it.
3. Open TradingView → Pine Editor → paste over your existing script (replace
   everything).
4. Click "Save," then "Add to Chart" (or just refresh the chart if it's already
   added).

That's the entire daily routine — no digging for numbers, no manual typing.

## If the Action ever shows a red X (failed run)

Click into the failed run to see the error. Most common causes:
- Yahoo Finance changed something and `yfinance` needs updating — try bumping
  the version in `requirements.txt`.
- Market holiday with no fresh options data yet that day — usually resolves
  itself the next trading day.

## Customizing

- Change the cron time in `.github/workflows/daily_gex.yml` if you want the
  update earlier/later (the schedule is in UTC).
- The script currently uses the nearest available QQQ expiration. If you want
  a specific expiration cycle (e.g. only monthly), that's a small change to
  `pick_expiration()` in `generate_gex.py` — ask and I can adjust it.

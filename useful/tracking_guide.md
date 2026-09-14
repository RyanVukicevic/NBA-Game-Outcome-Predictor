# Pregame Tracking And Paper Betting

This is an NBA moneyline research workflow, not an execution service. It does
not place bets, scrape sportsbook accounts, purchase data, or run in the
background. No live-game features or odds are used. SQLite is included with
Python, so no additional database installation is necessary.

## Quick Start

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe src/main.py upcoming
.\.venv\Scripts\python.exe src/track.py status
```

Upcoming forecasts now automatically enter `data/tracking/predictions.sqlite3`.
The existing JSON artifacts and model archives remain available. Back up the
database while the collector is stopped, along with models and data snapshots;
these local files are deliberately not committed to GitHub. Avoid simultaneous
writers across different PCs through OneDrive; SQLite is a local database, not
a shared network service.

Old forecast JSON can be imported using `track.py import-forecasts PATH ...`.
An import is recorded as received now, never backdated to manufacture a live
track record. Files without stable game IDs or timezone-aware tipoffs are not
eligible. Production forecasts and archived synthetic examples are separate.

## Odds Setup

The adapter uses The Odds API v4, not a DraftKings login. Obtain your own API key
at https://the-odds-api.com/ after checking current costs and NBA/bookmaker
coverage. No key was available during implementation; collection is tested
against mocked responses, not a paid live account.

Set `ODDS_API_KEY` locally in the terminal session. To avoid typing it into
PowerShell command history:

```powershell
$secret = Read-Host 'The Odds API key' -AsSecureString
$env:ODDS_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
.\.venv\Scripts\python.exe src/track.py collect-odds --bookmakers draftkings fanduel
Remove-Item Env:ODDS_API_KEY
```

No key belongs in source code, notebook output, Git, or chat. `.env` files are
ignored but are not automatically loaded. Request exceptions omit secret URLs.
The collector makes one NBA request, requests decimal two-way moneyline odds,
prints quota headers, and returns. Missing books/events invalidate older quotes;
network failures do not invent a successful response. Full-feed ingestion is
not suitable for a manually filtered event subset.

Provider events must match the NBA home/away abbreviations and exact UTC tipoff
uniquely. Unmatched events are counted, not guessed. Refresh/reconcile schedules
when times disagree. A quoted market does not prove a wager would be accepted.

Provider reference checked during implementation:
https://the-odds-api.com/liveapi/guides/v4/

## Fixed Policy

Copy a model ID displayed by `track.py status` into the command below:

```powershell
.\.venv\Scripts\python.exe src/track.py decide --model-id MODEL_ID
```

Defaults: DraftKings, T-60 minutes, $10 flat stake, odds no older than 10 minutes,
prediction no older than 24 hours, estimated EV strictly above 3% for model-value.
The EV threshold is an unvalidated research setting, not a proven recommendation.
All settings, including the pinned model ID, form the immutable policy ID. A new
model or setting creates a separate experiment; do not cherry-pick the best one
after looking at results. Run the command at or after the cutoff. It never uses
predictions or quotes issued/received after the cutoff, even if run much later.
Both source and receipt times must qualify; stale/newly unavailable latest quotes
are not replaced with an older favorable price. Skips are final for that policy.

The collector must actually run before cutoffs. Refresh predictions during the
day, collect odds within the freshness window before each cutoff, then decide.
Repeating commands is safe. No Windows scheduled task was installed; until a
scheduler is configured these commands only run when invoked. Do not expect
continuous data while the PC is asleep or this process is stopped.

The strategies share the same eligible prediction/odds cohort:

- Favorite: lower decimal price; equal prices skip.
- Home: always home.
- Model winner: probability above 50%; exactly 50% skips.
- Model value: side with highest `probability * decimal_price - 1`, provided it
  exceeds the threshold; otherwise skip.

Prices retain the bookmaker margin for payouts. Market calibration uses a
separately normalized two-way no-vig probability. Bonuses/promotions and variable
staking are excluded. The favorite benchmark is conditional on our common
tracked cohort, not a claim to include every NBA game ever played.

## Results And Reports

```powershell
.\.venv\Scripts\python.exe src/track.py settle --seasons 2026-27 --refresh
.\.venv\Scripts\python.exe src/track.py report --policy-id POLICY_ID
```

Use a season with actual completed games. Without `--refresh`, settlement uses
cached logs. Same-day results are conservatively excluded by the existing daily
data policy. Unknown/uncompleted games remain pending. Corrected official scores
append result revisions; reports use the latest revision, never erase history.

`reports/tracking/` contains per-game paper bets, strategy and monthly/season
summaries, calibration, probability metrics, actual receipts, report metadata,
and `paper_profit.png`. Re-exporting a directory replaces that report, not the
underlying ledger. Use different output directories to preserve report versions.

ROI divides net profit by settled nonvoid stake. Pending stake is not a loss.
Void bets return stake, contribute zero profit, and are excluded from that ROI
denominator. NBA two-way final moneylines cannot push on a tied final score;
actual receipts support operator-confirmed pushes separately. Average odds and
win rate use settled nonvoid bets. Drawdown uses settlement-observation order.
ROI intervals resample whole UTC game-days (2,000 replicates, fixed seed), require
10 settled days, and are descriptive, not a guarantee or correction for trying
many strategies. Model/market probability metrics use the same completed eligible
games. Small samples can be misleading even with intervals.

## Schedule Changes

Forecast refresh records newly observed tipoff changes. Absence from a schedule
feed alone does not prove cancellation. Record verified changes explicitly:

```powershell
.\.venv\Scripts\python.exe src/track.py schedule-status --game-id GAME_ID --tipoff 2026-10-22T23:00:00Z --status postponed --source 'NBA schedule'
```

A change before cutoff requires a matching new forecast and quote. A subsequent
postponement, cancellation, or tipoff change voids that paper bet permanently;
there is no second bet on that game within the policy. This conservative research
rule is not a statement of any sportsbook's actual settlement rules. Automated
postponement detection and a managed scheduler remain follow-on work.

## Actual Receipts

`track.py import-actual PATH.json` accepts a JSON object with `wagers` and/or
`settlements` lists. Wager fields: game_id, bookmaker, reference, selection
(`home`/`away`), decimal odds, stake, accepted (timezone-aware timestamp).
The command prints a wager ID. Settlement fields: wager_id, status
(`win`/`loss`/`void`/`push`), payout (gross returned amount, including returned
stake), reference. These are manual operator-confirmed records, never bets
generated or placed by this project. Receipt IDs are idempotent and conflicting
edits fail; settlement corrections append. Store receipt files outside tracked
source (for example `data/tracking/receipts/`). Reports keep actual results in a
separate file; no simulated fills are presented as actual profits.

## Demo

The end of `src/demo.ipynb` shows real ledger counts and a separate, explicitly
synthetic 30-game accounting example. Its generated profit chart demonstrates
the reporting code only; it is not model evidence or a real betting result.

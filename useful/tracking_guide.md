# Pregame Tracking And Paper Betting

This is an NBA moneyline research workflow, not an execution service. It does
not place bets, scrape sportsbook accounts, or purchase data. A local scheduler
is available but runs only when explicitly started. No live-game features or odds are used. SQLite is included with
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
coverage. Live authentication and NBA collection have now been verified, alongside
mocked tests. The key supplied during verification was used only in temporary
process environments, not saved to the repository or notebook.

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

All requests require the quota guard to be configured first. For a new database,
run `track.py quota-init --used N`, replacing N with current dashboard usage.
This PC was initialized with last verified usage of 2 credits. An active cycle
cannot be reinitialized. Monthly resets are on the first at 00:00 UTC, as
confirmed by the user; rollover is automatic. Provider headers reconcile other
observed account usage. See the scheduler section below for the full limits.

Provider events must match the NBA home/away abbreviations uniquely within
15 minutes of the NBA tipoff. The live feed lists many expected starts 10 minutes
later. Both times and their offset are retained, but NBA time remains the policy
cutoff. Quotes are excluded after either start time. Larger or ambiguous
differences fail closed. A quoted market does not prove a wager would be accepted.

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
Repeating commands is safe. No Windows scheduled task was installed; the local
scheduler below must be started explicitly. Do not expect
continuous data while the PC is asleep or this process is stopped.

New policies use version 2 and require next-game eligibility at the decision
cutoff. Version-1 historical policies and the isolated accounting demo retain
their original rules; do not combine their scores with version 2.

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
rule is not a statement of any sportsbook's actual settlement rules. Explicit
NBA postponed/canceled labels are now imported during schedule refresh; absence
alone still does not establish a cancellation.

## Eligibility

`track.py eligibility --model-id MODEL_ID` displays and records readiness:

- `waiting`: either team has an earlier unresolved future/postponed in-scope game.
- `awaiting_data`: a preceding game lacks a final result or its result is absent
  from the forecast's exact feature snapshot; also used for missing forecasts.
- `eligible`: both teams are ready, with recorded history and matching tipoff.
- `closed`: started, finished, canceled, or postponed target game.

Each record includes blockers, snapshot/forecast identity, latest incorporated
game per team, and first observed eligibility for this schedule version/model.
Eligibility is not a betting recommendation. Schedule import includes past and
ongoing games so a new installation can identify preceding-game blockers.
Checks cover configured regular-season/playoff games, not excluded preseason,
play-in, or all-star games. Same-day results remain excluded by the daily policy.
Missing or erroneous upstream schedule data remains a limitation.

Real verification exposed five 2025-26 games with both raw team rows labeled
away: 0022500147, 0022501229, 0022501230, 0022500578, 0022500602. The existing
home/away training pipeline excludes these ambiguous/neutral-site rows, so they
currently block affected teams' eligibility. This is an explicit conservative
skip, not evidence that those games remain unplayed. Correct neutral-site
feature/Elo handling is follow-on model/data work; do not bypass the guard.

## Free-Plan Scheduler

Preview (no NBA or odds requests):

```powershell
.\.venv\Scripts\python.exe src/track.py schedule --model-path models/demo.joblib
```

After setting the rotated key locally, run one pass with `--execute`, or keep
checking due work every 60 seconds with:

```powershell
.\.venv\Scripts\python.exe src/track.py schedule --model-path models/demo.joblib --execute --watch
```

Ctrl+C stops the loop. Prefer a specific `models/versions/MODEL_ID.joblib` archive
for longer experiments. Model identity is pinned: the scheduler refreshes feature
history without refitting weights, includes the new current season when needed,
and stops if the chosen file is replaced or code/runtime compatibility changes.
Train and select a new policy intentionally after such a change.

NBA schedules/results/forecasts refresh once per Eastern day and near due
collection windows, with at least 15 minutes between successful refreshes.
These are NBA calls, not Odds API credits. Raw data snapshots and exact forecast
vectors are archived. Ingestion times are observed now, not backdated estimates.

Primary collection is T-65 through T-60 minutes, decided at T-60. Secondary
research collects T-365 through T-360, decided at T-6h. Only eligible games
trigger requests. One request covers simultaneously due games and both books;
a qualifying saved quote can cover another slot without a request. The full
response can incidentally archive later/ineligible games, but those do not
trigger requests or bets. Optional opening snapshots are not enabled yet.

Manual CLI calls and scheduler requests share one guard in this database:
12 credits per Eastern day, 450 per UTC calendar month, and a 50-credit reserve
from the 500 allowance. Secondary requests yield to remaining primary slots
that day. Atomic reservations protect concurrent local processes. Every attempt
counts unless response headers report otherwise; unknown timeout/crash costs
remain charged. Each slot permits one automatic attempt, with no retry loop.
Headers reconcile observed external usage; unknown use by another client cannot
be detected before a response, so use one ledger/key workflow for reliable limits.

Missed windows, unavailable odds, and failures remain in the ledger. Sleeping or
stopping the PC can miss opportunities; gaps never get post-cutoff prices.
No paid upgrade, OS startup task, hosted service, or actual betting is enabled.
Export performance using the existing `report` command.

American odds are derived locally in the demo and paper-bet CSV; decimal odds
remain canonical. Rounding may differ slightly from original sportsbook displays.

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

The end of `src/demo.ipynb` shows real counts, American/decimal odds, readiness,
blockers, first eligibility, quota status, and a separate, explicitly
synthetic 30-game accounting example. Its generated profit chart demonstrates
the reporting code only; it is not model evidence or a real betting result.

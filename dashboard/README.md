# Courtside: NBA Game Predictor Dashboard

A local, read-only website over the existing production logistic model and
append-only SQLite ledger. Research models are never promoted by the website.

## Run

From the project root:

```powershell
.venv\Scripts\python.exe -B -m dashboard.server --port 8765
```

Open http://127.0.0.1:8765. Keep that terminal running; Ctrl+C stops it.
If the port is occupied, choose another port. The server binds only to loopback.
No Node.js installation or frontend build step is required.
Existing project requirements provide the runtime dependencies.

The configured artifact is resolved from `production_config.txt` using the existing
production model-path function. The default ledger is
`data/tracking/predictions.sqlite3`. Optional `--model PATH` and `--db PATH` are
for explicit local inspection; there is no user-uploaded model loading endpoint.
Only load trusted local joblib artifacts.

## Views

- **Games:** next 90 days in card or compact table view, searchable by team, with
  date/bookmaker/status filters, American odds, win probabilities, freshness and
  eligibility reasons. Qualifying rows/cards are green; no-bet rows/cards are red.
- **Matchup details:** latest production explanation, home/away blockers, first
  observed eligibility, forecast and market timeline, all model-version snapshots,
  American/decimal odds history, saved official decisions, CSV export.
- **Teams & Elo:** exact saved production Elo, rank, recent movement, and a
  color-coded multi-team time-series chart with top/bottom presets, custom groups,
  and one-, two-, or three-season windows. Matchup details compare both teams.
- **Performance:** policy-isolated prospective accuracy, log loss, Brier,
  calibration, paper stake/profit/ROI/drawdown, baselines, value selection, and
  model-confidence strategies from 55% through 95% and stricter 5%/7% EV
  variants. Select a strategy name for its exact rule and purpose.
- **History:** immutable official decisions versus upcoming forecast snapshots.
- **Model:** deployment-estimator standardized coefficients, exact recipe and
  identity, plus a separate historical research-results tab.
- **System:** ledger counts, data timestamps, local quota accounting, recorded
  collector activity and read-only paper-policy settings.

Times can be displayed in Eastern, browser-local or UTC. Snapshot tables retain
raw UTC fields at the end. Filters remain selected while navigating views. The
theme and Games card/table preference persist in the browser. Dark mode is the
default for a new browser; a user-selected light theme persists afterward.

## Card Contract

Green means the current paper policy qualifies a side, not a safe or guaranteed
bet. Red means the data is eligible and fresh but neither side qualifies. Gray
means pending, missing, stale, closed, incompatible or past cutoff without an
official decision. Expected return is `probability * decimal_odds - 1` and must
strictly exceed the existing default policy threshold of 3%.

The default version-2 policy requires both-teams-next-game eligibility, odds no
older than ten minutes, and forecasts no older than 24 hours. The official cutoff
is 60 minutes before tipoff. There is no new staking or automated wagering logic.

Before cutoff, colors are **previews**, not ledger writes. After cutoff, a valid
stored decision is labeled **Locked** and its card uses the original forecast and
odds IDs, even if newer snapshots exist. Missing decisions are not backfilled by
the UI. Schedule-invalidated decisions cannot become green locked cards.

Performance defaults to the current production-model/DraftKings/T-60/v2 policy.
Older policies are available explicitly in the selector. Confidence strategies
are derived from the immutable model-winner decision at the same cutoff, so they
can be compared from the start of the season without extra API requests. Never
combine policies to manufacture a track record. The ledger calculates probability
metrics on settled valid policy decisions, not every forecast ever generated.
The data currently has no locked decisions, so that view is genuinely empty.

## Safety And Limits

The dashboard opens SQLite with `mode=ro` and `PRAGMA query_only=ON`. It never
creates or migrates the production database, calls the Odds API, updates history,
trains a model, changes configuration, or places a wager. Refresh means reread
saved project data. Existing forecasts may be stale; no sample results are mixed
into the live views. The only synthetic UI data is isolated inside browser tests.

The dashboard lives outside `src/` so it does not change the model implementation
identity. The underlying production recipe and artifact remain unchanged.
Elo is the saved postgame snapshot, not a live-game estimate. Offseason regression
still happens in the production forecast function and is labeled separately.
Coefficients come from the deployment estimator, not its holdout estimator.
Matchup contributions are shown only when the saved model ID and reconstructed
probability match the configured artifact exactly.

This is **not yet an always-on hosted service**. The local scheduler remains a
separate process, and page refresh does not run it. There is no authenticated admin
editor or public write API. Do not expose this development server publicly.
Before hosting: choose a free host with durable SQLite-compatible storage or
migrate the ledger, add a real scheduled worker and heartbeat, secure deployment,
backups and monitoring. Free hosting must be checked against those requirements;
static frontend hosting alone cannot run the Python collector.

## Assets And Verification

NBA team logos are local PNGs sourced from ESPN's public image CDN. Team marks
belong to their respective owners; no affiliation is claimed. Asset source URLs
and SHA256 hashes are in `static/assets/manifest.json`. Lucide 0.468.0 is vendored
under its included license so the browser does not depend on a remote CDN.

To restore missing assets:

```powershell
.venv\Scripts\python.exe -B -m dashboard.fetch_assets
```

Tests:

```powershell
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_dashboard.py -v
.venv\Scripts\python.exe -m pip install -r dashboard/requirements-test.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -B -m dashboard.browser_check
```

The browser check expects the local server at port 8765 and the existing saved
project data. It checks desktop/tablet/mobile layouts, rendered logos, chart pixels,
navigation, dialogs, filtering, color states and export; screenshots go to
`reports/dashboard/`. Unit tests use temporary ledgers and include read-only
integrity, publication cutoffs, stale-input guards and immutable locked-card prices.

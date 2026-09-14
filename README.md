# NBA Game Predictor

Starter project for building an NBA game predictor with [`nba_api`](https://github.com/swar/nba_api).

The current predictor uses team game logs, builds rolling pre-game team features, trains a logistic regression model, and predicts the home team's win probability for a matchup.

## Setup

Install Python 3.10+ (this PC uses Python 3.12), then create a project environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

In VS Code, open `src/demo.ipynb`, select the `.venv` or Python 3.12 kernel,
and choose **Run All**. The tour covers API lookups, cached data, rolling features,
Elo, training, holdout and walk-forward evaluation, calibration plots, small tuning
runs, earlier experiment reports, model save/load, odds conversion, and live predictions.
It writes its outputs to `reports/demo/` and its model to `models/demo.joblib`.
The final live lookup requires internet; set `RUN_LIVE=False` to skip it.
Set `RUN_SMALL_TUNING=False` for a shorter tour. Restart the kernel after editing
imported source modules.

The same dependencies are also installed in this PC's normal Python 3.12. To run
without a virtual environment, select that kernel, or use this terminal command:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" src/main.py upcoming
```

To install/update that interpreter's dependencies:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pip install -r requirements-dev.txt
```

To run commands without activating the environment (or changing PowerShell policy):

```powershell
.\.venv\Scripts\python.exe src/main.py upcoming
```

Use `.\.venv\Scripts\python.exe` in place of `python` in the commands below.
NBA data downloads and schedule lookups need internet; existing cached seasons
can be used offline.

## Inspect DataFrames

Export the stages so you can see what the data looks like before modeling:

```powershell
python src/main.py inspect --seasons 2023-24 2024-25 --feature-set deltas
```

This writes:

- `data/processed/01_raw_game_logs.csv`
- `data/processed/02_team_rolling_features.csv`
- `data/processed/03_matchups_deltas_only.csv`
- `data/processed/04_matchups_full_home_away_diff.csv`
- `data/processed/05_training_features.csv`

## Train And Evaluate

```powershell
python src/main.py train --seasons 2022-23 2023-24 2024-25 --cv-splits 5
```

Training reports accuracy, log loss, Brier score, and ROC/AUC. It also writes a calibration table to `reports/calibration_curve.csv`, feature coefficients to `reports/feature_importance.csv`, and temporal cross-validation scores to `reports/temporal_cv_scores.csv` when `--cv-splits` is used.

Add pre-game Elo features with:

```powershell
python src/main.py train --seasons 2022-23 2023-24 2024-25 --feature-set deltas --rolling-window 20 --min-periods 7 --use-elo --cv-splits 5
```

Elo defaults:

- `--elo-k 20`
- `--elo-playoff-k`, optional; defaults to the regular Elo K when omitted
- `--elo-home-advantage 65`
- `--elo-carryover 0.75`

Training with `--use-elo` also writes `reports/elo_leaderboard.csv`.

To include playoff games, pass both season types. Quote `Regular Season` because it contains a space:

```powershell
python src/main.py train --seasons 2023-24 2024-25 2025-26 --season-types "Regular Season" Playoffs --feature-set deltas --rolling-window 20 --min-periods 7 --use-elo --elo-playoff-k 30 --cv-splits 5
```

Feature modes:

- `--feature-mode base`: original model-style features plus `IS_PLAYOFFS`
- `--feature-mode full`: base plus all engineered interaction features
- `--feature-mode lean`: a smaller handpicked interaction subset

For early-season predictions, you can warm up features with previous seasons:

```powershell
python src/main.py train --seasons 2023-24 2024-25 2025-26 --warmup-seasons 2022-23 --season-types "Regular Season" Playoffs --feature-set deltas --feature-mode lean --rolling-window 10 --min-periods 1 --rolling-history carryover --use-prior-season-features --prior-decay-games 30 --use-elo --elo-k 15 --elo-playoff-k 15 --elo-carryover 0.85 --cv-splits 5 --cv-method walk-forward
```

- `--warmup-seasons` loads earlier games for feature/Elo history.
- `--rolling-history carryover` lets rolling stats cross season boundaries.
- `--use-prior-season-features` adds previous-season win rate and plus-minus summaries.
- `--prior-decay-games` fades prior-season summary features as current-season games accumulate.
- `--cv-method walk-forward` tests date-based future chunks instead of equal row-count chunks.

## Tune Rolling Settings

Compare rolling-window and min-period combinations:

```powershell
python src/main.py tune --seasons 2022-23 2023-24 2024-25 --feature-set deltas --rolling-windows 5 10 15 20 --min-periods-grid 3 5 8 10
```

This writes `reports/tuning_results.csv`.

To tune the same rolling settings with Elo features included:

```powershell
python src/main.py tune --seasons 2022-23 2023-24 2024-25 --feature-set deltas --use-elo --rolling-windows 15 20 25 --min-periods-grid 5 7 10 --cv-splits 5
```

To tune Elo parameters while holding the rolling settings fixed:

```powershell
python src/main.py tune-elo --seasons 2022-23 2023-24 2024-25 --feature-set deltas --rolling-window 20 --min-periods 7 --elo-k-grid 10 15 20 25 30 --elo-home-advantage-grid 40 55 65 75 90 --elo-carryover-grid 0.5 0.75 0.9 --cv-splits 5
```

This writes `reports/elo_tuning_results.csv`.

To grid feature set, feature mode, rolling settings, regular-season Elo K, and playoff Elo K:

```powershell
python src/main.py tune-grid --seasons 2023-24 2024-25 2025-26 --season-types "Regular Season" Playoffs --feature-sets deltas full --feature-modes base lean full --rolling-windows 10 15 20 25 --min-periods-grid 5 7 10 --elo-k-grid 15 20 25 --elo-playoff-k-grid 25 30 35 40 --cv-splits 5
```

This writes `reports/model_grid_results.csv`.

To export the Elo leaderboard without training a model:

```powershell
python src/main.py elo-leaderboard --seasons 2022-23 2023-24 2024-25 --elo-k 20 --elo-home-advantage 65 --elo-carryover 0.75
```

To export a playoff-aware Elo leaderboard:

```powershell
python src/main.py elo-leaderboard --seasons 2023-24 2024-25 2025-26 --season-types "Regular Season" Playoffs --rolling-window 20 --min-periods 7 --feature-set deltas --elo-k 20 --elo-playoff-k 30 --elo-home-advantage 65 --elo-carryover 0.75 --output reports\elo_leaderboard_2023_24_2025_26_with_playoffs.csv
```

## Predict A Game

```powershell
python src/main.py predict --seasons 2022-23 2023-24 2024-25 --home BOS --away NYK
```

The older `python src/nba_game_predictor.py ...` command still works as a wrapper.

The first run calls NBA.com through `nba_api` and writes cached CSVs under `data/raw/`. Later runs reuse those files unless you pass `--refresh`.
Engineered team and matchup DataFrames are also cached under `data/processed/cache/`, so rerunning the same seasons/window with `--feature-set deltas` and then `--feature-set full` should be faster after the first run.

## Production Upcoming Predictions

To run the production predictor with the saved settings in `production_config.txt`:

```powershell
python src/main.py upcoming
```

The `upcoming` command predicts upcoming NBA games using the production config. `seasons=auto_last_3` automatically picks the current NBA season plus the two prior seasons from today's date, so the backend does not need yearly season edits.

The default search is 90 days ahead (`upcoming_days=90`). Output prints the current
date, horizon, and end date before fetching schedules. Searches crossing October
query both seasons. Preseason, exhibitions, completed games, and opponents outside
the NBA are excluded. All returned matchups use the same latest model snapshot;
future team form and roster changes are not projected.

Production reloads completed history from cached logs on every run. Set
`refresh=true` to download updated logs and advance the prediction snapshot
without changing compatible model weights. Set `retrain=true` to refit weights
as well. Return both to `false` for cached runs. Scheduled ingestion is a later
milestone; cached runs do not imply that NBA.com was checked for new results.

## Reproducible Forecasts (Milestone 1)

Evaluation uses an approximately 80/20 holdout with whole dates kept together.
`result.model` remains the evaluation estimator. `result.deployment_model` is a
separate fit on all eligible matchups and is used for future predictions.
Holdout metrics always belong to the evaluation fit, never the deployment refit.

Daily logs must contain both teams with final W/L results. Only games strictly
before the cutoff's Eastern calendar date are eligible. These older CSVs have
no historical publication/ingestion timestamps, so this is a conservative
event-date replay, not a verified intraday information-availability backtest.
Same-day results are deliberately excluded until richer ingestion is implemented.

Prediction rolling windows include the latest eligible completed game. Rest is
computed from the target date and the preceding known/scheduled game (capped at
five days, matching training). New-season/sparse history uses a labeled rolling
fallback; Elo regresses at season rollover. Future results are never simulated.
Elo training includes games excluded from rolling-feature training by min periods.

Each model has a JSON manifest recording data/settings/source/runtime identities,
evaluation and deployment cutoffs, and training counts. `code_commit` is the Git
HEAD at fit time; `implementation_id` hashes the actual Python working tree.
Content-addressed processed caches invalidate when source data, settings, code,
or relevant package versions change. Production uses new `_v2` artifacts and
preserves legacy models; incompatible source/runtime artifacts are retrained.

Upcoming runs save forecast JSON (including actual model inputs) under
`reports/forecasts/`, immutable model versions under `models/versions/`, and data
snapshots under `data/processed/snapshots/`. These generated files remain local.
This is the reproducibility foundation, not yet the SQL history/scheduler milestone.

To produce a historical-cutoff model and a fully specified example forecast:

```powershell
.\.venv\Scripts\python.exe src/main.py train --seasons 2023-24 2024-25 --as-of 2025-07-01 --use-elo --model-out models/cutoff_demo.joblib --reports-dir reports/cutoff_demo
.\.venv\Scripts\python.exe src/main.py predict --model-in models/cutoff_demo.joblib --home BOS --away NYK --as-of 2025-07-01 --game-date 2025-07-02 --output reports/cutoff_demo/prediction.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The example matchup is hypothetical. A model trained after the requested cutoff
is rejected. Legacy prediction models without a manifest require retraining.

The current production settings are:

- `feature-set=deltas`
- `feature-mode=lean`
- `rolling-window=10`
- `min-periods=1`
- `use-elo=true`
- `elo-k=15`
- `elo-playoff-k=15`
- `elo-home-advantage=65`
- `elo-carryover=0.65`

Prediction confidence is always shown for the predicted winner, so it is always at least 50%. Optional betting-line comparison can be added with a CSV path in `production_config.txt`; use columns `home_team`, `away_team`, `home_odds`, and `away_odds` with American odds.

To preview the output format during the offseason, run predictions against already-played games:

```powershell
python src/main.py sample --season 2025-26 --season-type Playoffs --limit 20
```

This is useful for seeing the table shape, but it is not a leakage-free historical backtest because the production model may already include those games in its training data.

## Model Notes

This is a clean baseline, not a finished betting model. It only uses team-level box-score trends available before the game:

- rolling points, shooting, rebounds, assists, turnovers, fouls, and plus-minus
- recent win rate
- rest days
- home-court context

Use `--feature-set deltas` to train on home-minus-away feature differences only. Use `--feature-set full` to train on home features, away features, and their deltas.
Default model filenames include the feature set and rolling settings, for example `models/game_predictor_deltas_rw10_min5.joblib`.
With `--use-elo`, the model gets `diff_elo_pre`, `diff_elo_change_last_3`, and `diff_elo_change_last_5` for `deltas`, or home/away/diff Elo columns for `full`. When playoffs are included, the model also gets `IS_PLAYOFFS`. The feature pipeline also adds a small set of interpretable interaction features for rest advantage, possession control, playoff-specific Elo/margin/rest effects, and Elo-by-plus-minus agreement.

Good next upgrades are injuries/lineups, betting lines, opponent-adjusted ratings, back-to-back flags, and a stricter walk-forward validation split.

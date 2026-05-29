# NBA Game Predictor

Starter project for building an NBA game predictor with [`nba_api`](https://github.com/swar/nba_api).

The current predictor uses team game logs, builds rolling pre-game team features, trains a logistic regression model, and predicts the home team's win probability for a matchup.

## Setup

Install Python 3.10+, then:

```powershell
pip install -r requirements.txt
```

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
- `--elo-home-advantage 65`
- `--elo-carryover 0.75`

Training with `--use-elo` also writes `reports/elo_leaderboard.csv`.

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

To export the Elo leaderboard without training a model:

```powershell
python src/main.py elo-leaderboard --seasons 2022-23 2023-24 2024-25 --elo-k 20 --elo-home-advantage 65 --elo-carryover 0.75
```

## Predict A Game

```powershell
python src/main.py predict --seasons 2022-23 2023-24 2024-25 --home BOS --away NYK
```

The older `python src/nba_game_predictor.py ...` command still works as a wrapper.

The first run calls NBA.com through `nba_api` and writes cached CSVs under `data/raw/`. Later runs reuse those files unless you pass `--refresh`.
Engineered team and matchup DataFrames are also cached under `data/processed/cache/`, so rerunning the same seasons/window with `--feature-set deltas` and then `--feature-set full` should be faster after the first run.

## Model Notes

This is a clean baseline, not a finished betting model. It only uses team-level box-score trends available before the game:

- rolling points, shooting, rebounds, assists, turnovers, fouls, and plus-minus
- recent win rate
- rest days
- home-court context

Use `--feature-set deltas` to train on home-minus-away feature differences only. Use `--feature-set full` to train on home features, away features, and their deltas.
Default model filenames include the feature set and rolling settings, for example `models/game_predictor_deltas_rw10_min5.joblib`.
With `--use-elo`, the model gets `diff_elo_pre`, `diff_elo_change_last_3`, and `diff_elo_change_last_5` for `deltas`, or home/away/diff Elo columns for `full`.

Good next upgrades are injuries/lineups, betting lines, opponent-adjusted ratings, back-to-back flags, and a stricter walk-forward validation split.

# Baseline Before Playoff Support

Captured on 2026-05-29 before adding season-type/playoff support.

## Current Data Scope

- Raw cached seasons: 2022-23, 2023-24, 2024-25, 2025-26
- Existing fetch path uses regular season only.
- Main updated leaderboard command tested:

```powershell
python src/main.py elo-leaderboard --seasons 2023-24 2024-25 2025-26 --rolling-window 20 --min-periods 7 --feature-set deltas --elo-k 20 --elo-home-advantage 65 --elo-carryover 0.75 --output reports\elo_leaderboard_2023_24_2025_26.csv
```

## Regular-Season Elo Snapshot

From `reports/elo_leaderboard_2023_24_2025_26.csv`:

| Rank | Team | Elo |
| ---: | --- | ---: |
| 1 | OKC | 1723.61 |
| 2 | SAS | 1694.26 |
| 3 | BOS | 1680.46 |
| 4 | DET | 1653.72 |
| 5 | DEN | 1643.82 |
| 6 | CLE | 1621.64 |
| 7 | NYK | 1614.80 |

## Prior Saved Temporal CV Snapshot

From `reports/temporal_cv_scores.csv`:

| Fold | Accuracy | Log Loss | Brier | ROC/AUC |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.6047 | 0.6642 | 0.2354 | 0.6424 |
| 2 | 0.6655 | 0.6108 | 0.2113 | 0.7322 |
| 3 | 0.6297 | 0.6324 | 0.2201 | 0.7068 |
| 4 | 0.6601 | 0.6219 | 0.2165 | 0.7091 |
| 5 | 0.6798 | 0.5840 | 0.1998 | 0.7624 |

## Prior Top Feature Importances

From `reports/feature_importance.csv`:

| Feature | Coefficient |
| --- | ---: |
| diff_rolling_20_plus_minus | 0.4688 |
| diff_elo_pre | 0.4222 |
| HOME_REST_DAYS | 0.1307 |
| AWAY_REST_DAYS | -0.1207 |
| diff_rolling_20_win | -0.1118 |

# Methodology

## Forecast

The production model is standardized logistic regression over lagged rolling
team-form differences, Elo, rest, and the small set of configured interaction
terms. The output is a home-win probability. Odds are not model inputs; they are
used afterward to compare the forecast with a price.

The deployment estimator uses all eligible completed games available at its
training cutoff. Its historical score is not computed on those training rows.
Evaluation uses whole-date holdouts and forward-only season folds so later games
cannot enter earlier features, preprocessing, fitting, selection, or calibration.

## Probability Quality

- **Accuracy** scores the selected winner, but ignores confidence and price.
- **Log loss** heavily penalizes confident wrong probabilities.
- **Brier score** is the mean squared probability error; lower is better.
- **Calibration** compares mean forecast probability with observed frequency in
  probability bins. A calibrated 70% group should win about 70% of the time.

Historical calibration experiments found a small beta-calibration improvement,
but its paired uncertainty interval crossed zero. Raw logistic probabilities
remain production; the beta result is a frozen challenger for future validation.

## Official Prospective Policy

The primary paper policy is DraftKings moneyline at T-60 minutes:

1. Both teams' earlier games must be final and incorporated into the forecast.
2. The forecast must be no more than 24 hours old and match the current tipoff.
3. The quote must be observed by cutoff and updated within ten minutes.
4. The higher-EV side qualifies only when estimated EV is strictly above 3%.
5. The decision stores its forecast, odds, schedule revision, policy, and cutoff.
6. Later line movement or forecasts never overwrite the official decision.

`EV = model probability * decimal odds - 1`. This calculation is standard, but
its usefulness depends on calibration, data timing, executable prices, and enough
independent forward observations. Positive estimated EV does not imply a win.

Flat $10 paper stakes are the primary comparison. Favorite, always-home,
model-winner, confidence-threshold, and stricter EV strategies reuse the same
locked cohort. They are research comparisons, not retrospective strategy mining.

## Current Evidence

The controlled expanded historical study evaluated 3,884 games across three
outer seasons. Best observed full-coverage accuracy was 67.33%; the current
reference scored 65.81% in that exact comparison. More complex tree and neural
models did not improve probability quality convincingly enough to replace
logistic regression. High-confidence subsets exceeded 75% while excluding about
half the games, which is not 75% accuracy across the full schedule.

No completed prospective season or profitable live track record is claimed.

## Research Boundary

Player availability, expected minutes, lineup strength, alternate calibration,
and fractional Kelly staking remain separate research. They require timestamped
sources and forward ablations. Unknown availability must remain distinct from
available, and scenario assumptions must never overwrite an official forecast.

from __future__ import annotations

import math

import pandas as pd
from sklearn.base import clone
from sklearn.calibration import calibration_curve
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit


def evaluate_probabilities(y_true: pd.Series, probabilities) -> dict[str, float]:
    predictions = (probabilities >= 0.5).astype(int)
    metrics = {
        "accuracy": accuracy_score(y_true, predictions),
        "log_loss": log_loss(y_true, probabilities),
        "brier_score": brier_score_loss(y_true, probabilities),
    }

    if len(set(y_true)) == 2:
        metrics["roc_auc"] = roc_auc_score(y_true, probabilities)
    else:
        metrics["roc_auc"] = math.nan

    return metrics


def calibration_table(y_true: pd.Series, probabilities, bins: int = 10) -> pd.DataFrame:
    prob_true, prob_pred = calibration_curve(y_true, probabilities, n_bins=bins, strategy="uniform")
    return pd.DataFrame(
        {
            "predicted_probability_bin": prob_pred,
            "actual_win_rate": prob_true,
        }
    )


def temporal_cv_scores(model, x: pd.DataFrame, y: pd.Series, splits: int = 5) -> pd.DataFrame:
    splitter = TimeSeriesSplit(n_splits=splits)
    rows = []

    for fold, (train_index, test_index) in enumerate(splitter.split(x), start=1):
        fold_model = clone(model)
        x_train, x_test = x.iloc[train_index], x.iloc[test_index]
        y_train, y_test = y.iloc[train_index], y.iloc[test_index]
        fold_model.fit(x_train, y_train)

        probabilities = fold_model.predict_proba(x_test)[:, 1]
        metrics = evaluate_probabilities(y_test, probabilities)
        metrics["fold"] = float(fold)
        metrics["train_games"] = float(len(x_train))
        metrics["test_games"] = float(len(x_test))
        rows.append(metrics)

    return pd.DataFrame(rows)

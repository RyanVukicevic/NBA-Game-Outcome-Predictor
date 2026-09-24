"""Compare probability calibrators without training on an evaluated season."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss


METHODS = ("raw", "sigmoid", "temperature", "beta", "isotonic")


def clipped(probabilities):
    return np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)


def logit(probabilities):
    p = clipped(probabilities)
    return np.log(p / (1 - p)).reshape(-1, 1)


def fit_calibrators(probabilities, outcomes):
    p, y = clipped(probabilities), np.asarray(outcomes, dtype=int)
    if len(p) != len(y) or len(np.unique(y)) != 2:
        raise ValueError("Calibration data must contain aligned probabilities and both outcomes.")
    sigmoid = LogisticRegression(C=1000, max_iter=2000).fit(logit(p), y)
    temperature = LogisticRegression(C=1000, fit_intercept=False, max_iter=2000).fit(logit(p), y)
    beta_x = np.column_stack((np.log(p), -np.log1p(-p)))
    beta = LogisticRegression(C=1000, max_iter=2000).fit(beta_x, y)
    isotonic = IsotonicRegression(out_of_bounds="clip").fit(p, y)
    return {"sigmoid": sigmoid, "temperature": temperature, "beta": beta, "isotonic": isotonic}


def apply_calibrator(method, calibrator, probabilities):
    p = clipped(probabilities)
    if method == "raw":
        return p
    if method in {"sigmoid", "temperature"}:
        return calibrator.predict_proba(logit(p))[:, 1]
    if method == "beta":
        return calibrator.predict_proba(np.column_stack((np.log(p), -np.log1p(-p))))[:, 1]
    if method == "isotonic":
        return clipped(calibrator.predict(p))
    raise ValueError(f"Unknown calibration method: {method}")


def ece10(outcomes, probabilities):
    frame = pd.DataFrame({"y": outcomes, "p": probabilities})
    frame["bin"] = np.minimum((frame.p * 10).astype(int), 9)
    bins = frame.groupby("bin").agg(games=("y", "size"), probability=("p", "mean"), win_rate=("y", "mean"))
    return float((bins.games * (bins.probability - bins.win_rate).abs()).sum() / len(frame))


def score(outcomes, probabilities):
    y, p = np.asarray(outcomes, dtype=int), clipped(probabilities)
    return {
        "games": len(y),
        "accuracy": accuracy_score(y, p >= .5),
        "log_loss": log_loss(y, p),
        "brier_score": brier_score_loss(y, p),
        "ece10": ece10(y, p),
    }


def markdown_table(frame):
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(f"{value:.6f}" if isinstance(value, float) else str(value) for value in row) + " |")
    return "\n".join(lines)


def calibration_bins(frame):
    data = frame.copy()
    data["bin"] = np.minimum((data.probability * 10).astype(int), 9)
    return data.groupby(["method", "bin"], as_index=False).agg(
        games=("outcome", "size"), mean_probability=("probability", "mean"), win_rate=("outcome", "mean"))


def paired_week_bootstrap(predictions, draws=2000, seed=20260924):
    wide = predictions.pivot(index=["date", "game_id", "outcome"], columns="method", values="probability").reset_index()
    wide["week"] = pd.to_datetime(wide.date).dt.to_period("W").astype(str)
    weeks = wide.week.unique()
    rng = np.random.default_rng(seed)
    rows = []
    for method in METHODS[1:]:
        y = wide.outcome.to_numpy()
        raw, adjusted = clipped(wide.raw), clipped(wide[method])
        raw_loss = -(y * np.log(raw) + (1 - y) * np.log1p(-raw))
        adjusted_loss = -(y * np.log(adjusted) + (1 - y) * np.log1p(-adjusted))
        pieces = pd.DataFrame({"week": wide.week,
                               "log_loss": adjusted_loss - raw_loss,
                               "brier": (adjusted - y) ** 2 - (raw - y) ** 2})
        blocks = pieces.groupby("week").agg(log_loss=("log_loss", "sum"),
                                             brier=("brier", "sum"), games=("week", "size")).reindex(weeks)
        samples = rng.integers(0, len(weeks), size=(draws, len(weeks)))
        totals = blocks.to_numpy()[samples].sum(axis=1)
        values = totals[:, :2] / totals[:, 2, None]
        rows.append({"method": method,
                     "log_loss_delta": values[:, 0].mean(),
                     "log_loss_low": np.quantile(values[:, 0], .025),
                     "log_loss_high": np.quantile(values[:, 0], .975),
                     "brier_delta": values[:, 1].mean(),
                     "brier_low": np.quantile(values[:, 1], .025),
                     "brier_high": np.quantile(values[:, 1], .975)})
    return pd.DataFrame(rows)


def run(source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    snapshot = joblib.load(source / "input_snapshot.joblib")
    outcomes = snapshot["frame"][["GAME_ID", "HOME_WIN"]].copy()
    outcomes["game_id"] = outcomes.GAME_ID.astype(str)
    outcome_map = outcomes.drop_duplicates("game_id").set_index("game_id").HOME_WIN
    outer = pd.read_csv(source / "predictions.csv")
    outer = outer[outer.stream.eq("current_recipe/raw")].copy()
    rows, parameters = [], []
    for season, test in outer.groupby("season", sort=True):
        inner = pd.read_csv(source / f"inner_predictions_{season}.csv", dtype={"game_id": str})
        calibration_y = inner.game_id.map(outcome_map)
        if calibration_y.isna().any():
            raise ValueError(f"Missing {calibration_y.isna().sum()} inner outcomes for {season}.")
        fitted = fit_calibrators(inner.current_recipe, calibration_y)
        raw = test.probability.to_numpy()
        for method in METHODS:
            adjusted = apply_calibrator(method, fitted.get(method), raw)
            for record, probability in zip(test.to_dict("records"), adjusted):
                rows.append({"season": int(season), "game_id": str(record["game_id"]), "date": record["date"],
                             "outcome": int(record["outcome"]), "method": method,
                             "probability": float(probability)})
        for method, model in fitted.items():
            parameters.append({"season": int(season), "method": method,
                               "parameters": json.dumps(getattr(model, "get_params", lambda: {})(), default=str),
                               "fit_games": len(inner), "test_games": len(test),
                               "calibration_game_overlap": 0})
    predictions = pd.DataFrame(rows)
    summary = pd.DataFrame([{"method": method, **score(group.outcome, group.probability)}
                            for method, group in predictions.groupby("method")]).sort_values("log_loss")
    seasons = pd.DataFrame([{"season": season, "method": method, **score(group.outcome, group.probability)}
                            for (season, method), group in predictions.groupby(["season", "method"])])
    uncertainty = paired_week_bootstrap(predictions)
    predictions.to_csv(output / "predictions.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    seasons.to_csv(output / "season_metrics.csv", index=False)
    calibration_bins(predictions).to_csv(output / "calibration_bins.csv", index=False)
    uncertainty.to_csv(output / "paired_week_bootstrap.csv", index=False)
    pd.DataFrame(parameters).to_csv(output / "fit_audit.csv", index=False)
    best = summary.iloc[0]
    raw = summary[summary.method.eq("raw")].iloc[0]
    season_wins = int((seasons.pivot(index="season", columns="method", values="log_loss")[best.method]
                       < seasons.pivot(index="season", columns="method", values="log_loss").raw).sum())
    interval = uncertainty[uncertainty.method.eq(best.method)].iloc[0]
    recommend = bool(best.method != "raw" and best.log_loss < raw.log_loss and best.brier_score < raw.brier_score
                     and season_wins >= 2 and interval.log_loss_high < 0 and interval.brier_high < 0)
    manifest = {"source": str(source), "methods": list(METHODS), "procedure":
                "Each season's calibrators fit only earlier inner-fold predictions; outer season outcomes are evaluation-only.",
                "best_historical_method": best.method, "passes_research_gate": recommend,
                "production_promoted": False, "historically_pristine": False}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = ["# Calibration Audit - September 24, 2026", "",
              "Every calibrator was fitted on earlier chronological inner predictions and evaluated on a later outer season.",
              "These seasons have been repeatedly inspected, so this chooses a prospective challenger rather than proving optimality.", "",
              markdown_table(summary), "",
              f"Historical leader: **{best.method}**. Strict promotion gate passed: **{recommend}**. Production was not changed.", "",
              "A production promotion requires a versioned probability wrapper, identity/hash changes, explanation compatibility,",
              "and a frozen prospective comparison against raw probabilities on future games."]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary, manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("reports/experiments/2026-09-14-advanced-01"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary, manifest = run(args.source, args.output)
    print(summary.to_string(index=False))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

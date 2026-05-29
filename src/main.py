from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import PROCESSED_DIR, REPORTS_DIR, default_model_path
from inspection import export_model_stages
from modeling import build_elo_leaderboard, train_model, save_training_result, load_training_result
from prediction import predict_matchup
from tuning import save_tuning_results, tune_elo_settings, tune_model_grid, tune_rolling_settings


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seasons", nargs="+", default=["2022-23", "2023-24", "2024-25"])
    parser.add_argument("--warmup-seasons", nargs="*", default=[])
    parser.add_argument("--season-types", nargs="+", choices=["Regular Season", "Playoffs"], default=["Regular Season"])
    parser.add_argument("--rolling-window", type=int, default=10)
    parser.add_argument("--min-periods", type=int, default=5)
    parser.add_argument("--rolling-history", choices=["same-season", "carryover"], default="same-season")
    parser.add_argument("--use-prior-season-features", action="store_true")
    parser.add_argument("--feature-set", choices=["deltas", "full"], default="deltas")
    parser.add_argument("--feature-mode", choices=["base", "full", "lean"], default="full")
    parser.add_argument("--use-elo", action="store_true")
    parser.add_argument("--elo-k", type=float, default=20)
    parser.add_argument("--elo-playoff-k", type=float, default=None)
    parser.add_argument("--elo-home-advantage", type=float, default=65)
    parser.add_argument("--elo-carryover", type=float, default=0.75)
    parser.add_argument("--refresh", action="store_true", help="Ignore cached nba_api CSVs.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and use an NBA game predictor.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train", help="Train and evaluate the model.")
    add_common_args(train_parser)
    train_parser.add_argument("--model-out", type=Path)
    train_parser.add_argument("--cv-splits", type=int, default=0)
    train_parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)

    predict_parser = subparsers.add_parser("predict", help="Train/load and predict one matchup.")
    add_common_args(predict_parser)
    predict_parser.add_argument("--home", required=True, help="Home team abbreviation, e.g. BOS")
    predict_parser.add_argument("--away", required=True, help="Away team abbreviation, e.g. NYK")
    predict_parser.add_argument("--is-playoffs", action="store_true")
    predict_parser.add_argument("--model-in", type=Path)
    predict_parser.add_argument("--retrain", action="store_true", help="Retrain before predicting.")

    inspect_parser = subparsers.add_parser("inspect", help="Export intermediate DataFrames to CSV.")
    add_common_args(inspect_parser)
    inspect_parser.add_argument("--output-dir", type=Path, default=PROCESSED_DIR)

    cv_parser = subparsers.add_parser("cv", help="Run temporal cross-validation only.")
    add_common_args(cv_parser)
    cv_parser.add_argument("--cv-splits", type=int, default=5)
    cv_parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)

    tune_parser = subparsers.add_parser("tune", help="Compare rolling-window/min-period settings.")
    tune_parser.add_argument("--seasons", nargs="+", default=["2022-23", "2023-24", "2024-25"])
    tune_parser.add_argument("--season-types", nargs="+", choices=["Regular Season", "Playoffs"], default=["Regular Season"])
    tune_parser.add_argument("--rolling-windows", nargs="+", type=int, default=[5, 10, 15, 20])
    tune_parser.add_argument("--min-periods-grid", nargs="+", type=int, default=[3, 5, 8, 10])
    tune_parser.add_argument("--feature-set", choices=["deltas", "full"], default="deltas")
    tune_parser.add_argument("--feature-mode", choices=["base", "full", "lean"], default="full")
    tune_parser.add_argument("--use-elo", action="store_true")
    tune_parser.add_argument("--elo-k", type=float, default=20)
    tune_parser.add_argument("--elo-playoff-k", type=float, default=None)
    tune_parser.add_argument("--elo-home-advantage", type=float, default=65)
    tune_parser.add_argument("--elo-carryover", type=float, default=0.75)
    tune_parser.add_argument("--cv-splits", type=int, default=0)
    tune_parser.add_argument("--refresh", action="store_true", help="Ignore cached nba_api and processed CSVs.")
    tune_parser.add_argument("--output", type=Path, default=REPORTS_DIR / "tuning_results.csv")

    tune_elo_parser = subparsers.add_parser("tune-elo", help="Compare Elo K/home-advantage/carryover settings.")
    tune_elo_parser.add_argument("--seasons", nargs="+", default=["2022-23", "2023-24", "2024-25"])
    tune_elo_parser.add_argument("--season-types", nargs="+", choices=["Regular Season", "Playoffs"], default=["Regular Season"])
    tune_elo_parser.add_argument("--rolling-window", type=int, default=20)
    tune_elo_parser.add_argument("--min-periods", type=int, default=7)
    tune_elo_parser.add_argument("--feature-set", choices=["deltas", "full"], default="deltas")
    tune_elo_parser.add_argument("--feature-mode", choices=["base", "full", "lean"], default="full")
    tune_elo_parser.add_argument("--elo-k-grid", nargs="+", type=float, default=[10, 15, 20, 25, 30])
    tune_elo_parser.add_argument("--elo-playoff-k", type=float, default=None)
    tune_elo_parser.add_argument("--elo-home-advantage-grid", nargs="+", type=float, default=[40, 55, 65, 75, 90])
    tune_elo_parser.add_argument("--elo-carryover-grid", nargs="+", type=float, default=[0.5, 0.75, 0.9])
    tune_elo_parser.add_argument("--cv-splits", type=int, default=0)
    tune_elo_parser.add_argument("--refresh", action="store_true", help="Ignore cached nba_api and processed CSVs.")
    tune_elo_parser.add_argument("--output", type=Path, default=REPORTS_DIR / "elo_tuning_results.csv")

    tune_grid_parser = subparsers.add_parser("tune-grid", help="Grid feature sets, feature modes, rolling settings, and Elo K values.")
    tune_grid_parser.add_argument("--seasons", nargs="+", default=["2023-24", "2024-25", "2025-26"])
    tune_grid_parser.add_argument("--warmup-seasons", nargs="*", default=[])
    tune_grid_parser.add_argument("--season-types", nargs="+", choices=["Regular Season", "Playoffs"], default=["Regular Season", "Playoffs"])
    tune_grid_parser.add_argument("--feature-sets", nargs="+", choices=["deltas", "full"], default=["deltas"])
    tune_grid_parser.add_argument("--feature-modes", nargs="+", choices=["base", "full", "lean"], default=["base", "lean", "full"])
    tune_grid_parser.add_argument("--rolling-windows", nargs="+", type=int, default=[10, 15, 20, 25])
    tune_grid_parser.add_argument("--min-periods-grid", nargs="+", type=int, default=[5, 7, 10])
    tune_grid_parser.add_argument("--rolling-histories", nargs="+", choices=["same-season", "carryover"], default=["same-season"])
    tune_grid_parser.add_argument("--prior-season-features-grid", nargs="+", type=int, choices=[0, 1], default=[0])
    tune_grid_parser.add_argument("--elo-k-grid", nargs="+", type=float, default=[15, 20, 25])
    tune_grid_parser.add_argument("--elo-playoff-k-grid", nargs="+", type=float, default=[25, 30, 35, 40])
    tune_grid_parser.add_argument("--elo-home-advantage", type=float, default=65)
    tune_grid_parser.add_argument("--elo-carryover-grid", nargs="+", type=float, default=[0.75])
    tune_grid_parser.add_argument("--cv-splits", type=int, default=0)
    tune_grid_parser.add_argument("--refresh", action="store_true", help="Ignore cached nba_api and processed CSVs.")
    tune_grid_parser.add_argument("--output", type=Path, default=REPORTS_DIR / "model_grid_results.csv")

    elo_board_parser = subparsers.add_parser("elo-leaderboard", help="Export current Elo ratings after selected seasons.")
    elo_board_parser.add_argument("--seasons", nargs="+", default=["2022-23", "2023-24", "2024-25"])
    elo_board_parser.add_argument("--warmup-seasons", nargs="*", default=[])
    elo_board_parser.add_argument("--season-types", nargs="+", choices=["Regular Season", "Playoffs"], default=["Regular Season"])
    elo_board_parser.add_argument("--rolling-window", type=int, default=20)
    elo_board_parser.add_argument("--min-periods", type=int, default=7)
    elo_board_parser.add_argument("--rolling-history", choices=["same-season", "carryover"], default="same-season")
    elo_board_parser.add_argument("--use-prior-season-features", action="store_true")
    elo_board_parser.add_argument("--feature-set", choices=["deltas", "full"], default="deltas")
    elo_board_parser.add_argument("--feature-mode", choices=["base", "full", "lean"], default="full")
    elo_board_parser.add_argument("--elo-k", type=float, default=20)
    elo_board_parser.add_argument("--elo-playoff-k", type=float, default=None)
    elo_board_parser.add_argument("--elo-home-advantage", type=float, default=65)
    elo_board_parser.add_argument("--elo-carryover", type=float, default=0.75)
    elo_board_parser.add_argument("--refresh", action="store_true", help="Ignore cached nba_api and processed CSVs.")
    elo_board_parser.add_argument("--output", type=Path, default=REPORTS_DIR / "elo_leaderboard.csv")

    if len(sys.argv) == 1:
        parser.print_help()
        print()
        print("Examples:")
        print("  python src/main.py inspect --seasons 2023-24 2024-25")
        print("  python src/main.py train --seasons 2022-23 2023-24 2024-25 --cv-splits 5")
        print("  python src/main.py tune --seasons 2022-23 2023-24 2024-25")
        print("  python src/main.py tune-elo --seasons 2022-23 2023-24 2024-25")
        print("  python src/main.py elo-leaderboard --seasons 2022-23 2023-24 2024-25")
        print("  python src/main.py predict --home BOS --away NYK")
        sys.exit(0)

    return parser.parse_args()


def print_metrics(metrics: dict[str, float]) -> None:
    for name, value in metrics.items():
        print(f"{name}: {value:.4f}")


def main() -> None:
    args = parse_args()

    if args.command == "inspect":
        paths = export_model_stages(
            seasons=args.seasons,
            output_dir=args.output_dir,
            rolling_window=args.rolling_window,
            min_periods=args.min_periods,
            feature_set=args.feature_set,
            feature_mode=args.feature_mode,
            season_types=args.season_types,
            warmup_seasons=args.warmup_seasons,
            rolling_history=args.rolling_history,
            use_prior_season_features=args.use_prior_season_features,
            use_elo=args.use_elo,
            elo_k=args.elo_k,
            elo_playoff_k=args.elo_playoff_k,
            elo_home_advantage=args.elo_home_advantage,
            elo_carryover=args.elo_carryover,
            refresh=args.refresh,
        )
        print("Exported inspection CSVs:")
        for name, path in paths.items():
            print(f"{name}: {path}")
        return

    if args.command == "train":
        model_out = args.model_out or default_model_path(
            args.feature_set,
            args.rolling_window,
            args.min_periods,
            use_elo=args.use_elo,
            season_types=args.season_types,
            feature_mode=args.feature_mode,
            rolling_history=args.rolling_history,
            use_prior_season_features=args.use_prior_season_features,
        )
        result = train_model(
            seasons=args.seasons,
            rolling_window=args.rolling_window,
            min_periods=args.min_periods,
            feature_set=args.feature_set,
            feature_mode=args.feature_mode,
            season_types=args.season_types,
            warmup_seasons=args.warmup_seasons,
            rolling_history=args.rolling_history,
            use_prior_season_features=args.use_prior_season_features,
            use_elo=args.use_elo,
            elo_k=args.elo_k,
            elo_playoff_k=args.elo_playoff_k,
            elo_home_advantage=args.elo_home_advantage,
            elo_carryover=args.elo_carryover,
            cv_splits=args.cv_splits,
            refresh=args.refresh,
        )
        save_training_result(result, model_out)
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        result.calibration.to_csv(args.reports_dir / "calibration_curve.csv", index=False)
        result.feature_importance.to_csv(args.reports_dir / "feature_importance.csv", index=False)
        if result.latest_elos is not None:
            result.latest_elos.to_csv(args.reports_dir / "elo_leaderboard.csv")
        if result.cv_scores is not None:
            result.cv_scores.to_csv(args.reports_dir / "temporal_cv_scores.csv", index=False)

        print("Holdout evaluation")
        print_metrics(result.metrics)
        print(f"Saved model: {model_out}")
        print(f"Saved calibration curve: {args.reports_dir / 'calibration_curve.csv'}")
        print(f"Saved feature importance: {args.reports_dir / 'feature_importance.csv'}")
        if result.latest_elos is not None:
            print(f"Saved Elo leaderboard: {args.reports_dir / 'elo_leaderboard.csv'}")
        if result.cv_scores is not None:
            print(f"Saved temporal CV scores: {args.reports_dir / 'temporal_cv_scores.csv'}")
        return

    if args.command == "cv":
        result = train_model(
            seasons=args.seasons,
            rolling_window=args.rolling_window,
            min_periods=args.min_periods,
            feature_set=args.feature_set,
            feature_mode=args.feature_mode,
            season_types=args.season_types,
            warmup_seasons=args.warmup_seasons,
            rolling_history=args.rolling_history,
            use_prior_season_features=args.use_prior_season_features,
            use_elo=args.use_elo,
            elo_k=args.elo_k,
            elo_playoff_k=args.elo_playoff_k,
            elo_home_advantage=args.elo_home_advantage,
            elo_carryover=args.elo_carryover,
            cv_splits=args.cv_splits,
            refresh=args.refresh,
        )
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        path = args.reports_dir / "temporal_cv_scores.csv"
        result.cv_scores.to_csv(path, index=False)
        print(result.cv_scores)
        print(f"Saved temporal CV scores: {path}")
        return

    if args.command == "tune":
        results = tune_rolling_settings(
            seasons=args.seasons,
            rolling_windows=args.rolling_windows,
            min_periods_values=args.min_periods_grid,
            feature_set=args.feature_set,
            feature_mode=args.feature_mode,
            season_types=args.season_types,
            use_elo=args.use_elo,
            elo_k=args.elo_k,
            elo_playoff_k=args.elo_playoff_k,
            elo_home_advantage=args.elo_home_advantage,
            elo_carryover=args.elo_carryover,
            cv_splits=args.cv_splits,
            refresh=args.refresh,
        )
        save_tuning_results(results, args.output)
        print(results)
        print(f"Saved tuning results: {args.output}")
        return

    if args.command == "tune-elo":
        results = tune_elo_settings(
            seasons=args.seasons,
            rolling_window=args.rolling_window,
            min_periods=args.min_periods,
            elo_k_values=args.elo_k_grid,
            elo_home_advantages=args.elo_home_advantage_grid,
            elo_carryovers=args.elo_carryover_grid,
            elo_playoff_k=args.elo_playoff_k,
            feature_set=args.feature_set,
            feature_mode=args.feature_mode,
            season_types=args.season_types,
            cv_splits=args.cv_splits,
            refresh=args.refresh,
        )
        save_tuning_results(results, args.output)
        print(results)
        print(f"Saved Elo tuning results: {args.output}")
        return

    if args.command == "tune-grid":
        results = tune_model_grid(
            seasons=args.seasons,
            feature_sets=args.feature_sets,
            feature_modes=args.feature_modes,
            rolling_windows=args.rolling_windows,
            min_periods_values=args.min_periods_grid,
            elo_k_values=args.elo_k_grid,
            elo_playoff_k_values=args.elo_playoff_k_grid,
            elo_carryover_values=args.elo_carryover_grid,
            season_types=args.season_types,
            warmup_seasons=args.warmup_seasons,
            rolling_histories=args.rolling_histories,
            use_prior_season_features_values=[bool(value) for value in args.prior_season_features_grid],
            elo_home_advantage=args.elo_home_advantage,
            cv_splits=args.cv_splits,
            refresh=args.refresh,
        )
        save_tuning_results(results, args.output)
        print(results)
        print(f"Saved model grid results: {args.output}")
        return

    if args.command == "elo-leaderboard":
        leaderboard = build_elo_leaderboard(
            seasons=args.seasons,
            rolling_window=args.rolling_window,
            min_periods=args.min_periods,
            feature_set=args.feature_set,
            feature_mode=args.feature_mode,
            season_types=args.season_types,
            warmup_seasons=args.warmup_seasons,
            rolling_history=args.rolling_history,
            use_prior_season_features=args.use_prior_season_features,
            elo_k=args.elo_k,
            elo_playoff_k=args.elo_playoff_k,
            elo_home_advantage=args.elo_home_advantage,
            elo_carryover=args.elo_carryover,
            refresh=args.refresh,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        leaderboard.to_csv(args.output)
        print(leaderboard)
        print(f"Saved Elo leaderboard: {args.output}")
        return

    if args.command == "predict":
        model_in = args.model_in or default_model_path(
            args.feature_set,
            args.rolling_window,
            args.min_periods,
            use_elo=args.use_elo,
            season_types=args.season_types,
            feature_mode=args.feature_mode,
            rolling_history=args.rolling_history,
            use_prior_season_features=args.use_prior_season_features,
        )
        if args.retrain or not model_in.exists():
            result = train_model(
                seasons=args.seasons,
                rolling_window=args.rolling_window,
                min_periods=args.min_periods,
                feature_set=args.feature_set,
                feature_mode=args.feature_mode,
                season_types=args.season_types,
                warmup_seasons=args.warmup_seasons,
                rolling_history=args.rolling_history,
                use_prior_season_features=args.use_prior_season_features,
                use_elo=args.use_elo,
                elo_k=args.elo_k,
                elo_playoff_k=args.elo_playoff_k,
                elo_home_advantage=args.elo_home_advantage,
                elo_carryover=args.elo_carryover,
                refresh=args.refresh,
            )
            save_training_result(result, model_in)
        else:
            result = load_training_result(model_in)

        probability = predict_matchup(result, args.home, args.away, is_playoffs=args.is_playoffs)
        print(f"{args.home.upper()} home win probability vs {args.away.upper()}: {probability:.1%}")


if __name__ == "__main__":
    main()

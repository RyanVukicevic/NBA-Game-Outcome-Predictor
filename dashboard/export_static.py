"""Export a read-only dashboard snapshot for static HTTPS hosting."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import zipfile

import pandas as pd

from dashboard.service import Dashboard, clean, utc

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
DEFAULT_OUTPUT = Path(__file__).parent / "public"
DEFAULT_ARCHIVE = Path(__file__).parent / "courtside-cloudflare.zip"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), ensure_ascii=True, separators=(",", ":")), encoding="utf-8")


def build_archive(source: Path, archive: Path = DEFAULT_ARCHIVE) -> Path:
    """Create a portable Pages upload; POSIX member paths matter on Windows."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(source).as_posix())
    return archive


def export_snapshot(output: Path = DEFAULT_OUTPUT, service=None, days: int = 90) -> dict:
    """Build the public shell and bounded JSON snapshot without copying secrets."""
    service = service or Dashboard()
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(STATIC, output)
    (output / "deployment.js").write_text("window.COURTSIDE_STATIC = true;\n", encoding="ascii")
    (output / ".nojekyll").write_text("", encoding="ascii")
    (output / "_headers").write_text(
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: no-referrer\n"
        "  Permissions-Policy: camera=(), microphone=(), geolocation=()\n"
        "  Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'\n"
        "/data/*\n"
        "  Cache-Control: no-cache\n",
        encoding="ascii",
    )

    books = ("draftkings", "fanduel")
    overviews = {}
    game_ids = set()
    generated = utc()
    cutoff = pd.Timestamp(generated) + pd.Timedelta(days=days)
    for book in books:
        overview = service.overview(book, now=generated)
        overview["games"] = [game for game in overview["games"] if pd.Timestamp(game["tipoff"]) <= cutoff]
        overview["as_of"] = generated
        overview["system"]["hosting"] = "Cloudflare Pages / published snapshot"
        overview["system"]["scheduler"] = f"Snapshot generated {generated}; collection runs separately"
        overviews[book] = overview
        game_ids.update(game["id"] for game in overview["games"])
        write_json(output / "data" / "overview" / f"{book}.json", overview)

    game_details = {book: {} for book in books}
    for game_id in sorted(game_ids):
        for book in books:
            try:
                payload = service.game(game_id, book, now=generated)
            except KeyError:
                continue
            game_details[book][game_id] = payload
    for book, payloads in game_details.items():
        write_json(output / "data" / "games" / f"{book}.json", payloads)

    teams = sorted(overviews["draftkings"]["teams"])
    all_series = []
    for start in range(0, len(teams), 10):
        all_series.extend(service.elo(teams[start:start + 10])["series"])
    write_json(output / "data" / "elo.json", {"series": all_series})
    for team in teams:
        write_json(output / "data" / "team" / f"{team}.json", service.team(team))

    policy_ids = {policy["id"] for overview in overviews.values() for policy in overview.get("policies", [])}
    default_performance = service.performance(now=generated)
    write_json(output / "data" / "performance" / "default.json", default_performance)
    policy_ids.add(default_performance["policy_id"])
    for policy_id in sorted(policy_ids):
        write_json(output / "data" / "performance" / f"{policy_id}.json", service.performance(policy_id, now=generated))
    write_json(output / "data" / "research.json", service.research())
    manifest = {"generated_at": generated, "days": days, "games": len(game_ids), "books": list(books)}
    write_json(output / "data" / "manifest.json", manifest)
    return clean(manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.days <= 180:
        parser.error("--days must be between 1 and 180")
    summary = export_snapshot(args.output, days=args.days)
    archive = build_archive(args.output, args.archive)
    print(f"Exported {summary['games']} games to {args.output} and {archive} at {summary['generated_at']}")


if __name__ == "__main__":
    main()

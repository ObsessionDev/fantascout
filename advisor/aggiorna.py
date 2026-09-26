"""Bring a matchday into the model: import its votes, then regenerate the projections.

    python -m advisor.aggiorna --profile config/profiles/lega-2026-27.json \\
        --importa voti_giornata_5.xlsx --giornata 5

`--importa` accepts a canonical CSV (see advisor.inseason) or a workbook in
the layout of the Fantacalcio "voti" download. Without `--importa` the command
only regenerates, which is what to run after dropping a season-to-date
`statistiche_<stagione>.xlsx` into `data/raw`. `--verifica` prints the
matchday-by-matchday check over the files present.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import LeagueConfig
from .inseason import import_votes_workbook, matchday_backtest, read_matchdays, validate_matchday, votes_dir, write_matchday
from .league_profile import LeagueProfile
from .pipeline import PROCESSED, RAW, build_projections


def import_file(path: Path, raw: Path, season: str, matchday: int | None) -> Path:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        if matchday is None:
            raise SystemExit("--giornata è obbligatorio per importare un file .xlsx")
        frame = import_votes_workbook(path, matchday)
    else:
        frame = pd.read_csv(path)
        if matchday is not None:
            frame["giornata"] = matchday
        frame = validate_matchday(frame, str(path))
    return write_matchday(frame, votes_dir(raw, season))


def _points(dataset: Path) -> dict[int, tuple[str, float]]:
    if not dataset.exists():
        return {}
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    return {p["id"]: (p["nome"], p["mercato"]["fp_per_giornata"]) for p in payload["players"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa una giornata e rigenera le proiezioni.")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    parser.add_argument("--output-dir", type=Path, default=PROCESSED)
    parser.add_argument("--importa", type=Path, nargs="*", default=[])
    parser.add_argument("--giornata", type=int)
    parser.add_argument("--solo-importa", action="store_true", help="Importa senza rigenerare.")
    parser.add_argument("--verifica", action="store_true", help="Verifica giornata per giornata sui file presenti.")
    args = parser.parse_args(argv)
    profile = LeagueProfile.load_json(args.profile)
    season = profile.season.season
    for path in args.importa:
        written = import_file(path, args.raw_dir, season, args.giornata)
        print(f"Importata {path} -> {written}")
    if args.solo_importa:
        return 0
    dataset = args.output_dir / profile.profile_id / season.replace("/", "-") / "auction_data.json"
    before = _points(dataset)
    payload = build_projections(args.raw_dir, args.output_dir, profile=profile)
    players = payload["players"]
    observed = max((p["proiezione"].get("giornate_osservate", 0) for p in players), default=0)
    print(f"Proiezioni rigenerate: {len(players)} giocatori, modello {payload['model_version']}, giornate osservate fino a {observed}")
    if before:
        moves = sorted(((p["mercato"]["fp_per_giornata"] - before[p["id"]][1], p["nome"]) for p in players if p["id"] in before), reverse=True)
        if moves:
            print("Salgono di più:", ", ".join(f"{name} {delta:+.2f}" for delta, name in moves[:5]))
            print("Scendono di più:", ", ".join(f"{name} {delta:+.2f}" for delta, name in moves[-5:][::-1]))
    if args.verifica:
        from .projection import project_season
        frames = read_matchdays(votes_dir(args.raw_dir, season))
        if not frames:
            print("Verifica giornata per giornata: nessun file di giornata.")
            return 0
        listone = pd.read_excel(next(s for s in profile.current_sources if s.name == "player_list").path, sheet_name="Tutti", header=1)
        league = LeagueConfig.from_profile(profile)
        # The pre-season projection is the prior: matchday files are hidden from it.
        result = project_season(args.raw_dir, listone, season, league.scoring, profile.season.serie_a_matchdays, in_season=False)
        if result is None:
            print("Verifica non possibile: storico insufficiente.")
            return 0
        check = matchday_backtest(result[0], frames, result[1], league.scoring)
        print(f"Verifica su {check.matchdays} giornate: log loss presenza {check.presence_logloss:.4f}, MAE fantavoto {check.fantavoto_mae:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

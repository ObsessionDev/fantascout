"""The formation for one matchday: who starts, who is on the bench, in order.

The auction asks "what is the best use of a credit"; a matchday asks something
narrower — "given the twenty-five I already own, who plays this week". The
model is the same one the auction uses (`optimize.best_lineup`, which is
`optimize.lineup_value` with the winning selection kept instead of discarded):
restricting it to a single matchday is only a matter of feeding it that
matchday's own numbers, via `matchday_snapshot`, instead of the season means.

Availability near the deadline — injuries, suspensions, a coach's doubt — is
what the season-start priors cannot know. `unavailable` drops a player from
consideration entirely; `doubtful` overrides his probability of playing for
this matchday only, everything else about him unchanged.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import LeagueConfig
from .league_profile import LeagueProfile
from .optimize import ROLES, best_lineup, load_players, parse_formations, resolve_player_token


class LineupError(Exception):
    """No allowed formation can be filled from the players still available."""


def matchday_snapshot(player: dict, day_index: int, probability_override: float | None = None) -> dict:
    """A copy of `player` with `mercato` figures swapped for a single matchday.

    `optimize.best_lineup` reads `mercato.p_gioca_medio` and
    `mercato.fantavoto_medio`, season means over the fixture window. Restricting
    the same model to one matchday means feeding it that matchday's own numbers
    instead — nothing about the coverage or formation logic changes.
    """
    play = player.get("p_gioca_per_giornata") or []
    vote = player.get("voto_puro_mean_per_giornata") or []
    bonus = player.get("bonus_atteso_per_giornata") or []
    if day_index < len(play) and day_index < len(vote) and day_index < len(bonus):
        probability = float(play[day_index])
        fantavoto = float(vote[day_index]) + float(bonus[day_index])
    else:
        projection = player.get("proiezione", {})
        probability = float(projection.get("p_gioca", 0.0))
        fantavoto = float(projection.get("fantavoto", 0.0))
    if probability_override is not None:
        probability = float(probability_override)
    snapshot = dict(player)
    snapshot["mercato"] = {
        **player.get("mercato", {}),
        "p_gioca_medio": round(probability, 4),
        "fantavoto_medio": round(fantavoto, 4),
    }
    return snapshot


def team_fixture(teams: list[dict] | None, team_name: str, giornata: int) -> dict | None:
    """The league fixture `team_name` plays on Serie A matchday `giornata`, if known."""
    if not teams:
        return None
    team = next((t for t in teams if t.get("squadra") == team_name), None)
    if not team:
        return None
    return next((f for f in team.get("fixtures", []) if f.get("matchday") == giornata), None)


def penalty_priority(set_pieces: list[dict] | None, team_name: str, player_id: int) -> int | None:
    """1 for the designated penalty taker of `team_name`, 2/3 for backups, `None` otherwise."""
    if not set_pieces:
        return None
    for entry in set_pieces:
        if entry.get("squadra") == team_name and entry.get("tipo") == "RIGORI":
            for taker in entry.get("takers", []):
                if taker.get("player_id") == player_id:
                    return taker.get("priorita")
    return None


def _formation_shortfall(available: list[dict], league: LeagueConfig) -> str:
    """Why no allowed formation could be filled, in terms Mattia can act on."""
    counts = {role: sum(1 for p in available if p["ruolo"] == role) for role in ROLES}
    if counts["P"] < 1:
        return "nessun portiere disponibile: nessuna formazione è legale senza portiere."
    best_gap = None
    for defenders, midfielders, forwards in parse_formations(league):
        need = {"D": defenders, "C": midfielders, "A": forwards}
        gaps = {role: max(0, count - counts[role]) for role, count in need.items()}
        total_gap = sum(gaps.values())
        if best_gap is None or total_gap < best_gap[0]:
            best_gap = (total_gap, (defenders, midfielders, forwards), gaps)
    total_gap, formation, gaps = best_gap
    short_roles = ", ".join(f"{role} (mancano {gap})" for role, gap in gaps.items() if gap > 0)
    formation_label = "-".join(str(n) for n in formation)
    return (f"nessuna formazione legale con i disponibili: la più vicina è {formation_label}, "
            f"manca copertura in {short_roles}.")


@dataclass
class LineupReport:
    giornata: int
    formazione: str | None
    valore_atteso: float
    titolari: list[dict] = field(default_factory=list)
    panchina: list[dict] = field(default_factory=list)
    fuori_lista: list[dict] = field(default_factory=list)
    indisponibili: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "giornata": self.giornata,
            "formazione": self.formazione,
            "valore_atteso": round(self.valore_atteso, 3),
            "titolari": self.titolari,
            "panchina": self.panchina,
            "fuori_lista": self.fuori_lista,
            "indisponibili": self.indisponibili,
        }


def build_lineup(roster: list[dict], league: LeagueConfig, day_index: int,
                 unavailable: set[int] | None = None,
                 doubtful: dict[int, float] | None = None,
                 teams: list[dict] | None = None,
                 set_pieces: list[dict] | None = None) -> LineupReport:
    """The XI, ordered bench and notes for one matchday of the given roster.

    `day_index` is 0-based (Serie A matchday N is index N-1), matching every
    other `_per_giornata` consumer in the codebase (`simulation.py`,
    `pipeline.py`). Raises `LineupError` when no allowed formation can be
    filled from the players left after `unavailable` is removed — that is a
    roster problem, not a value of zero.

    `teams` and `set_pieces` are the dataset's own top-level lists (unrelated
    to the roster): passing them adds the opponent, venue and penalty-taker
    standing to every starter and bench row, the "why" behind each choice.
    Omitting them leaves those fields `None`, which keeps the CLI usage above
    (no full dataset in hand, only the roster) unchanged.
    """
    if day_index < 0:
        raise ValueError("la giornata deve essere un numero positivo")
    unavailable = set(unavailable or [])
    doubtful = dict(doubtful or {})
    excluded = [p for p in roster if p["id"] in unavailable]
    eligible = [p for p in roster if p["id"] not in unavailable]
    snapshots = {p["id"]: matchday_snapshot(p, day_index, doubtful.get(p["id"])) for p in eligible}
    plan = best_lineup(list(snapshots.values()), league)
    if plan.formation is None:
        raise LineupError(_formation_shortfall(eligible, league))

    by_id = {p["id"]: p for p in eligible}

    giornata_1based = day_index + 1

    def row(snapshot: dict) -> dict:
        original = by_id[snapshot["id"]]
        mercato = snapshot["mercato"]
        std_per_giornata = original.get("voto_puro_std_per_giornata") or []
        incertezza = (round(float(std_per_giornata[day_index]), 3) if day_index < len(std_per_giornata)
                     else original.get("proiezione", {}).get("deviazione"))
        fixture = team_fixture(teams, original["squadra"], giornata_1based)
        return {
            "id": original["id"], "nome": original["nome"], "ruolo": original["ruolo"],
            "squadra": original["squadra"],
            "p_gioca": round(float(mercato["p_gioca_medio"]), 3),
            "fantavoto_atteso": round(float(mercato["fantavoto_medio"]), 3),
            "dubbio": original["id"] in doubtful,
            "incertezza": incertezza,
            "avversario": fixture.get("opponent") if fixture else None,
            "trasferta": (fixture.get("venue") == "TRASFERTA") if fixture else None,
            "rigorista_priorita": penalty_priority(set_pieces, original["squadra"], original["id"]),
        }

    def bare(player: dict) -> dict:
        return {"id": player["id"], "nome": player["nome"], "ruolo": player["ruolo"], "squadra": player["squadra"]}

    titolari = [row(p) for role in ROLES for p in plan.starters.get(role, [])]
    panchina = [row(p) for role in ROLES for p in plan.bench.get(role, [])]
    fuori_lista = [bare(p) for p in plan.unused]
    indisponibili = [bare(p) for p in excluded]
    formazione = "-".join(str(n) for n in plan.formation)
    return LineupReport(giornata=day_index + 1, formazione=formazione, valore_atteso=plan.value,
                        titolari=titolari, panchina=panchina, fuori_lista=fuori_lista,
                        indisponibili=indisponibili)


def load_roster(path: Path, players_by_id: dict[int, dict]) -> list[dict]:
    """The exported roster (dashboard JSON with a `giocatori` list, or a bare
    list of ids) resolved against the full dataset, which is where the
    per-matchday projections actually live."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = payload.get("giocatori", []) if isinstance(payload, dict) else payload
    roster, missing = [], []
    for entry in entries:
        raw_id = entry["id"] if isinstance(entry, dict) else entry
        try:
            player_id = int(raw_id)
        except (TypeError, ValueError):
            raise SystemExit(f"id non valido nella rosa: {raw_id!r}") from None
        player = players_by_id.get(player_id)
        (roster if player is not None else missing).append(player if player is not None else raw_id)
    if missing:
        raise SystemExit(f"giocatori della rosa non presenti nel dataset: {missing}")
    return roster


def resolve_unavailable(players: list[dict], spec: str) -> set[int]:
    """`"Nome,Nome"` or `"#id,#id"`, players excluded entirely from the giornata."""
    return {resolve_player_token(players, token)["id"]
            for token in filter(None, (piece.strip() for piece in spec.split(",")))}


def resolve_doubtful(players: list[dict], spec: str) -> dict[int, float]:
    """`"Nome:probabilità,..."`, a probability of playing that overrides the model's own."""
    doubtful: dict[int, float] = {}
    for chunk in filter(None, (piece.strip() for piece in spec.split(","))):
        token, _, probability_text = chunk.rpartition(":")
        if not token or not probability_text:
            raise SystemExit(f'formato non valido per un dubbio, atteso "Nome:probabilità": {chunk!r}')
        try:
            probability = float(probability_text)
        except ValueError:
            raise SystemExit(f"probabilità non numerica per {token!r}: {probability_text!r}")
        if not 0.0 <= probability <= 1.0:
            raise SystemExit(f"probabilità fuori range [0,1] per {token!r}: {probability}")
        doubtful[resolve_player_token(players, token)["id"]] = probability
    return doubtful


def _print_report(report: LineupReport) -> None:
    print(f"Giornata {report.giornata} — formazione {report.formazione} — "
          f"{report.valore_atteso:.2f} FP attesi")
    print()
    print("TITOLARI")
    for row in report.titolari:
        flag = "  (dubbio)" if row["dubbio"] else ""
        print(f"  {row['ruolo']} {row['nome'][:24]:24s} {row['squadra'][:12]:12s} "
              f"p.gioca {row['p_gioca'] * 100:4.0f}%  atteso {row['fantavoto_atteso']:5.2f}{flag}")
    print()
    print("PANCHINA (in ordine)")
    if not report.panchina:
        print("  (nessuno)")
    for row in report.panchina:
        flag = "  (dubbio)" if row["dubbio"] else ""
        print(f"  {row['ruolo']} {row['nome'][:24]:24s} {row['squadra'][:12]:12s} "
              f"p.gioca {row['p_gioca'] * 100:4.0f}%  atteso {row['fantavoto_atteso']:5.2f}{flag}")
    if report.indisponibili:
        print()
        print("INDISPONIBILI (esclusi dalla giornata)")
        for row in report.indisponibili:
            print(f"  {row['ruolo']} {row['nome']}")
    if report.fuori_lista:
        print()
        print(f"Fuori lista per questa giornata: {', '.join(r['nome'] for r in report.fuori_lista)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Formazione consigliata per una giornata, dati la rosa e le indisponibilità")
    parser.add_argument("--data", required=True, help="auction_data.json generato dalla pipeline")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--roster", required=True,
                        help="rosa esportata dalla dashboard (JSON) o elenco di id")
    parser.add_argument("--giornata", type=int, required=True, help="giornata di Serie A (1-based)")
    parser.add_argument("--indisponibili", default="", help='infortunati/squalificati: "Nome,Nome" o "#id,#id"')
    parser.add_argument("--dubbi", default="",
                        help='in ballottaggio, con probabilità di giocare: "Nome:0.6,..."')
    parser.add_argument("--json", action="store_true", help="stampa JSON invece del testo leggibile")
    args = parser.parse_args(argv)

    if args.giornata < 1:
        raise SystemExit("--giornata deve essere un numero di giornata Serie A, a partire da 1")

    profile = LeagueProfile.load_json(args.profile)
    league = LeagueConfig.from_profile(profile)
    players = load_players(Path(args.data))
    players_by_id = {p["id"]: p for p in players}
    roster = load_roster(Path(args.roster), players_by_id)

    unavailable = resolve_unavailable(roster, args.indisponibili) if args.indisponibili else set()
    doubtful = resolve_doubtful(roster, args.dubbi) if args.dubbi else {}

    try:
        report = build_lineup(roster, league, args.giornata - 1, unavailable=unavailable, doubtful=doubtful)
    except LineupError as error:
        raise SystemExit(str(error))

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Stable fingerprints and compatibility metadata for generated artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIMULATOR_VERSION = "1.1"


def _canonical(value: Any) -> bytes:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")


def source_fingerprints(profile: Any, raw: Path) -> list[dict[str, Any]]:
    result = []
    for group in ("current_sources", "history_sources"):
        for source in getattr(profile, group, ()):
            declared = Path(source.path)
            candidates = [declared] if declared.is_absolute() else [raw / declared, declared, Path.cwd() / declared, Path(__file__).resolve().parents[1] / declared]
            path = next((candidate for candidate in candidates if candidate.is_file()), None)
            item: dict[str, Any] = {"group": group, "name": source.name, "path": source.path}
            if path is None:
                item["exists"] = False
            else:
                stat = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                item.update({"exists": True, "size_bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "sha256": digest})
            result.append(item)
    in_season = _in_season_fingerprint(profile, raw)
    if in_season:
        result.append(in_season)
    return result


def _in_season_fingerprint(profile: Any, raw: Path) -> dict[str, Any] | None:
    """One entry for the matchdays of the season in progress, so a new one makes the dataset stale.

    The files live next to the player list (see advisor.inseason). The entry
    always exists, with the digest of whatever is there, empty included: a
    missing entry would read as a missing required source.
    """
    player_list = next((source for source in getattr(profile, "current_sources", ()) if source.name == "player_list"), None)
    season = getattr(getattr(profile, "season", None), "season", None)
    if player_list is None or not season:
        return None
    declared = Path(player_list.path)
    candidates = [declared] if declared.is_absolute() else [raw / declared, declared, Path.cwd() / declared, Path(__file__).resolve().parents[1] / declared]
    listone = next((candidate for candidate in candidates if candidate.is_file()), None)
    if listone is None:
        return None
    from .inseason import season_stats_path, votes_dir
    files = sorted(votes_dir(listone.parent, season).glob("giornata_*.csv"))
    stats = season_stats_path(listone.parent, season)
    if stats.is_file():
        files.append(stats)
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return {"group": "in_season", "name": "giornate_giocate", "path": str(votes_dir(Path("data/raw"), season)),
            "exists": True, "files": len(files), "sha256": digest.hexdigest()}


def dataset_configuration_hash(profile: Any) -> str:
    payload = {
        "season": profile.season,
        "current_sources": profile.current_sources,
        "history_sources": profile.history_sources,
        "scoring": profile.scoring,
        "participants": profile.participants,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def dataset_input_hash(profile: Any, fingerprints: list[dict[str, Any]]) -> str:
    # mtimes are useful diagnostics, but content-identical files are equivalent
    # inputs and must not force a regeneration merely because they were touched.
    stable_fingerprints = [
        {key: value for key, value in fingerprint.items() if key != "modified_at"}
        for fingerprint in fingerprints
    ]
    payload = {
        "configuration_hash": dataset_configuration_hash(profile),
        "sources": stable_fingerprints,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def simulation_configuration_hash(profile: Any) -> str:
    payload = {"simulation_version": SIMULATOR_VERSION, "defense_modifier": profile.defense_modifier, "formations": profile.formations, "bench_switch": profile.bench_switch, "virtual_goals": profile.virtual_goals, "standings": profile.standings, "payouts": profile.payouts, "entry_fee_eur": profile.credits.entry_fee_eur, "incomplete_lineup": profile.incomplete_lineup, "roster_slots": profile.roster_slots}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def roster_input_hash(rosters: dict[str, list[int]]) -> str:
    """Fingerprint ownership independently of JSON and auction transaction order."""
    normalized = [sorted(player_ids) for _, player_ids in sorted(rosters.items())]
    return hashlib.sha256(_canonical(normalized)).hexdigest()


def simulation_input_hash(dataset_hash: str, profile: Any, roster_mode: str = "sample", roster_hash: str | None = None) -> str:
    payload = {
        "dataset_input_hash": dataset_hash,
        "configuration_hash": simulation_configuration_hash(profile),
        "roster_mode": roster_mode,
        "roster_input_hash": roster_hash,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()

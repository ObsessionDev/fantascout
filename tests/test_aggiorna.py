"""Import a matchday's votes, then regenerate — the pieces the GUI's "Aggiorna
giornate" reuses (`advisor.server`), exercised here without the HTTP layer."""

import json
from pathlib import Path

import pandas as pd
import pytest

from advisor.aggiorna import import_and_regenerate, import_file, regenerate_with_diff
from advisor.inseason import votes_dir


class FakeProfile:
    profile_id = "test-league"

    class season:
        season = "2026/27"


def _canonical_row(giornata=5, player_id=1, voto=6.5):
    return pd.DataFrame([{
        "giornata": giornata, "id_fantacalcio": player_id, "nome": "Alpha", "squadra": "Roma", "ruolo": "A",
        "voto": voto, "gol": 1, "assist": 0, "ammonizioni": 0, "espulsioni": 0, "autogol": 0,
        "gol_subiti": 0, "rigori_parati": 0, "rigori_sbagliati": 0, "rigori_segnati": 0,
    }])


def test_import_file_writes_the_canonical_csv(tmp_path):
    csv_path = tmp_path / "voti.csv"
    _canonical_row().to_csv(csv_path, index=False)
    written = import_file(csv_path, tmp_path / "raw", "2026/27", matchday=None)
    assert written == votes_dir(tmp_path / "raw", "2026/27") / "giornata_05.csv"
    assert written.exists()


def test_regenerate_with_diff_uses_an_injected_generator_and_reports_movers(tmp_path):
    output = tmp_path / "processed"
    dataset = output / FakeProfile.profile_id / "2026-27" / "auction_data.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(json.dumps({
        "players": [{"id": 1, "nome": "Alpha", "mercato": {"fp_per_giornata": 5.0}}],
        "model_version": "2.0",
    }), encoding="utf-8")

    def generator(profile, output_dir):
        path = output_dir / profile.profile_id / "2026-27" / "auction_data.json"
        path.write_text(json.dumps({
            "players": [
                {"id": 1, "nome": "Alpha", "mercato": {"fp_per_giornata": 6.0}, "proiezione": {"giornate_osservate": 5}},
                {"id": 2, "nome": "Beta", "mercato": {"fp_per_giornata": 3.0}, "proiezione": {"giornate_osservate": 5}},
            ],
            "model_version": "2.0",
        }), encoding="utf-8")

    result = regenerate_with_diff(tmp_path / "raw", output, FakeProfile(), generator=generator)
    assert result["giocatori"] == 2
    assert result["model_version"] == "2.0"
    assert result["giornate_osservate"] == 5
    assert result["sale"] == [{"nome": "Alpha", "delta": 1.0}]
    assert result["scende"] == []


def test_import_and_regenerate_imports_then_regenerates(tmp_path):
    raw = tmp_path / "raw"
    output = tmp_path / "processed"
    candidate = tmp_path / "candidate.csv"
    _canonical_row(giornata=7).to_csv(candidate, index=False)

    calls = []

    def generator(profile, output_dir):
        calls.append(profile)
        path = output_dir / profile.profile_id / "2026-27" / "auction_data.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"players": [], "model_version": "2.0"}), encoding="utf-8")

    result = import_and_regenerate(candidate, raw, output, FakeProfile(), giornata=7, generator=generator)
    assert result["giornata"] == 7
    assert Path(result["file_importato"]) == votes_dir(raw, "2026/27") / "giornata_07.csv"
    assert Path(result["file_importato"]).exists()
    assert len(calls) == 1


def test_import_and_regenerate_rejects_a_malformed_candidate(tmp_path):
    candidate = tmp_path / "bad.csv"
    pd.DataFrame([{"nome": "Alpha"}]).to_csv(candidate, index=False)
    with pytest.raises(ValueError):
        import_and_regenerate(candidate, tmp_path / "raw", tmp_path / "processed", FakeProfile(), giornata=1,
                              generator=lambda profile, output_dir: None)

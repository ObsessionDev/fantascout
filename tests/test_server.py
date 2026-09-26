import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

import pandas as pd

from advisor.config import LeagueConfig
from advisor.league_profile import LeagueProfile
from advisor.league_calendar import build_legacy_calendar_template, parse_legacy_two_block_frame
from advisor.pipeline import LISTONE_COLUMNS
from advisor.server import create_server, profile_response


class LocalApiServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.calls = []
        self.profile = json.loads((Path(__file__).parents[1] / "config/default_profile.json").read_text(encoding="utf-8"))
        self.profile["profile_id"] = "my-team"
        calendar_source = next(source for source in self.profile["current_sources"] if source["name"] == "league_calendar")
        calendar_source["path"] = str(root / "missing-calendar.xlsx")
        self.profile = json.loads(LeagueProfile.from_dict(self.profile).canonical_json())

        def generator(profile, datasets_dir):
            self.calls.append(profile)
            path = datasets_dir / profile.profile_id / profile.season.season.replace("/", "-") / "auction_data.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"generated":true}', encoding="utf-8")

        def simulator(profile, output_dir, iterations, seed, rosters):
            self.calls.append((profile, output_dir, iterations, seed, rosters))
            return {"iterations": iterations, "diagnostics": {"seed": seed}, "teams": {}, "scenarios": {}, "rosters": {}}

        def update_fetcher(url):
            page = 1 if url.endswith("chi-prendere/") else int(url.rstrip("/").rsplit("/", 1)[1])
            role = {1: "PORTIERI", 2: "DIFENSORI", 3: "CENTROCAMPISTI", 4: "ATTACCANTI"}[page]
            return f'<article><h2 class="article-page-subtitle">{role}</h2><p><strong>TOP</strong> - Alpha</p><p>{self.update_prose}</p></article>'

        self.update_prose = "Alpha is the starter."

        def formations_fetcher(_url):
            return self.formations_html

        self.formations_html = ""

        def player_list_fetcher(url):
            return self.player_list_html

        self.player_list_html = ""

        def set_piece_fetcher(url):
            return self.penalty_html if "rigoristi" in url else self.set_piece_html

        self.set_piece_html = ""
        self.penalty_html = ""

        self.server = create_server(
            ("127.0.0.1", 0),
            profiles_dir=root / "config/profiles",
            datasets_dir=root / "data/processed",
            uploads_dir=root / "data/uploads",
            updates_dir=root / "data/updates",
            raw_dir=root / "data/raw",
            generator=generator,
            simulator=simulator,
            update_fetcher=update_fetcher,
            formations_fetcher=formations_fetcher,
            set_piece_fetcher=set_piece_fetcher,
            player_list_fetcher=player_list_fetcher,
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp_dir.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        if not payload:
            parsed = None
        elif response.getheader("Content-Type", "").startswith("application/json"):
            parsed = json.loads(payload)
        elif response.getheader("Content-Type", "").startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
            parsed = payload
        else:
            parsed = payload.decode("utf-8")
        return response, parsed

    def player_workbook(self, players, ceduti=()):
        defaults = {column: 0 for column in LISTONE_COLUMNS}
        rows = [{**defaults, "RM": "Por", "Nome": "Player", "Squadra": "AAA", **player} for player in players]
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            pd.DataFrame([["Quotazioni Fantacalcio Stagione 2026 27"]]).to_excel(writer, sheet_name="Tutti", index=False, header=False)
            pd.DataFrame(rows, columns=sorted(LISTONE_COLUMNS)).to_excel(writer, sheet_name="Tutti", index=False, startrow=1)
            pd.DataFrame([["Quotazioni Fantacalcio Stagione 2026 27"]]).to_excel(writer, sheet_name="Ceduti", index=False, header=False)
            pd.DataFrame({"Id": list(ceduti)}).to_excel(writer, sheet_name="Ceduti", index=False, startrow=1)
        return output.getvalue()

    def set_piece_article(self, first="Alpha, Beta"):
        teams = [
            "ATALANTA", "BOLOGNA", "CAGLIARI", "COMO", "FIORENTINA", "FROSINONE", "GENOA",
            "INTER", "JUVENTUS", "LAZIO", "LECCE", "MILAN", "MONZA", "NAPOLI", "PARMA",
            "ROMA", "SASSUOLO", "TORINO", "UDINESE", "VENEZIA",
        ]
        sections = "".join(
            f"<p>✅ <strong>{team}</strong></p><p><em>Punizioni</em>: {first if index == 0 else f'Free {index}, Reserve {index}'}</p>"
            f"<p><em>Corner</em>: Corner {index}, Wide {index}</p><p>Evidence {index}.</p>"
            for index, team in enumerate(teams)
        )
        return f'<h1>Tiratori 2026/27</h1><div id="article-content">{sections}</div>'

    def penalty_article(self, first="Penalty", reserve="Reserve"):
        teams = [
            "ATALANTA", "BOLOGNA", "CAGLIARI", "COMO", "FIORENTINA", "FROSINONE", "GENOA",
            "INTER", "JUVENTUS", "LAZIO", "LECCE", "MILAN", "MONZA", "NAPOLI", "PARMA",
            "ROMA", "SASSUOLO", "TORINO", "UDINESE", "VENEZIA",
        ]
        sections = "".join(
            f"<p>🎯 <strong>{team}</strong></p><p><em>Primo</em>: <strong>{first if index == 0 else 'Penalty Player'}</strong>.</p>"
            f"<p><em>Note</em>: <strong>{reserve if index == 0 else 'Reserve Player'}</strong>.</p>"
            for index, team in enumerate(teams)
        )
        return f'<h1>Tutti i rigoristi 2026/27</h1><div id="article-content">{sections}</div>'

    def formations_article(self, first_ballot="Player 0A remains first choice."):
        teams = [
            "ATALANTA", "BOLOGNA", "CAGLIARI", "COMO", "FIORENTINA", "FROSINONE", "GENOA",
            "INTER", "JUVENTUS", "LAZIO", "LECCE", "MILAN", "MONZA", "NAPOLI", "PARMA",
            "ROMA", "SASSUOLO", "TORINO", "UDINESE", "VENEZIA",
        ]
        sections = []
        for index, team in enumerate(teams):
            names = [f"Player {index}{letter}" for letter in "ABCDEFGHIJK"]
            formation = f"{names[0]}; {', '.join(names[1:5])}; {', '.join(names[5:8])}; {', '.join(names[8:])}"
            ballot = first_ballot if index == 0 else f"Stable hierarchy for {team}."
            sections.append(
                f"<p><strong>{team}</strong></p>"
                f"<p><em>Formazione-tipo:</em> {formation}.</p>"
                f"<p><em>I ballottaggi:</em> {ballot}</p>"
            )
        return '<h1>Formazioni-tipo Serie A 2026/27</h1><div id="article-content">' + "".join(sections) + "</div>"

    def test_profiles_round_trip_and_index(self):
        expected = json.loads(json.dumps(profile_response(LeagueProfile.from_dict(self.profile))))
        body = json.dumps(self.profile).encode("utf-8")
        response, payload = self.request("PUT", "/api/profiles/my-team", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, expected)

        response, payload = self.request("GET", "/api/profiles")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"profiles": ["my-team"]})

        response, payload = self.request("GET", "/api/profiles/my-team")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, expected)

    def test_profile_responses_carry_the_hash_the_dataset_metadata_uses(self):
        """The UI compares meta.profile.profile_hash with the profile's own hash to
        flag a stale dataset, so the API has to expose it and stay stable when the
        browser sends the annotated profile back."""
        body = json.dumps(self.profile).encode("utf-8")
        _, saved = self.request("PUT", "/api/profiles/my-team", body, {"Content-Type": "application/json"})
        self.assertEqual(saved["configuration_hash"], LeagueProfile.from_dict(self.profile).configuration_hash)
        _, fetched = self.request("GET", "/api/profiles/my-team")
        self.assertEqual(fetched["configuration_hash"], saved["configuration_hash"])
        self.assertEqual(fetched["dataset_configuration_hash"], saved["dataset_configuration_hash"])
        self.assertEqual(fetched["simulation_configuration_hash"], saved["simulation_configuration_hash"])
        _, resaved = self.request("PUT", "/api/profiles/my-team", json.dumps(saved).encode("utf-8"), {"Content-Type": "application/json"})
        self.assertEqual(resaved, saved)
        stored = json.loads((self.server.profiles_dir / "my-team.json").read_text(encoding="utf-8"))
        self.assertNotIn("configuration_hash", stored)

    def test_rejects_unsafe_names_and_invalid_json_boundaries(self):
        response, payload = self.request("PUT", "/api/profiles/%2E%2E", b'{}', {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_profile_name")

        response, payload = self.request("PUT", "/api/profiles/team", b'[]', {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

        response, payload = self.request("PUT", "/api/profiles/team", b'{"value":NaN}', {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

        response, payload = self.request("PUT", "/api/profiles/team", b'{}')
        self.assertEqual(response.status, 415)
        self.assertEqual(payload["error"]["code"], "invalid_content_type")

        response, payload = self.request("PUT", "/api/profiles/team", b'{}', {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_profile")

    def test_manifest_generation_and_vite_cors(self):
        dataset = Path(self.temp_dir.name) / "data/processed/auction_data.json"
        dataset.parent.mkdir(parents=True)
        dataset.write_text("{}", encoding="utf-8")

        response, payload = self.request("GET", "/api/datasets/manifest", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["datasets"][0]["path"], "auction_data.json")
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "http://localhost:5173")

        response, _ = self.request("PUT", "/api/profiles/my-team", json.dumps(self.profile).encode("utf-8"), {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)

        response, payload = self.request("POST", "/api/generate", b'{"profile_id":"my-team"}', {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["profile_id"], "my-team")
        self.assertEqual(payload["profile_hash"], LeagueProfile.from_dict(self.profile).configuration_hash)
        self.assertEqual(payload["dataset_path"], "my-team/2026-27/auction_data.json")
        self.assertEqual(payload["dataset_manifest"]["datasets"][1]["path"], "my-team/2026-27/auction_data.json")
        self.assertEqual(self.calls[0].profile_id, "my-team")

        response, dataset_payload = self.request("GET", f"/api/datasets/{payload['dataset_path']}", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.status, 200)
        self.assertEqual(dataset_payload, {"generated": True})
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "http://localhost:5173")

        inline = self.profile.copy()
        inline["profile_id"] = "inline-team"
        response, payload = self.request("POST", "/api/generate", json.dumps({"profile": inline}).encode("utf-8"), {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["profile_id"], "inline-team")
        self.assertEqual(self.calls[1].profile_id, "inline-team")

    def test_options_and_invalid_generation_profile_are_structured(self):
        response, payload = self.request("OPTIONS", "/api/generate", headers={"Origin": "http://127.0.0.1:5173"})
        self.assertEqual(response.status, 204)
        self.assertIsNone(payload)
        self.assertEqual(response.getheader("Access-Control-Allow-Methods"), "GET, PUT, POST, DELETE, OPTIONS")

        response, payload = self.request("POST", "/api/generate", b'{}', {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_profile")

    def test_profiles_can_be_deleted_and_report_missing_names(self):
        profile = {**self.profile, "profile_id": "throwaway"}
        response, _ = self.request("PUT", "/api/profiles/throwaway", json.dumps(profile).encode("utf-8"), {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)

        response, payload = self.request("GET", "/api/profiles")
        self.assertIn("throwaway", payload["profiles"])

        response, payload = self.request("DELETE", "/api/profiles/throwaway")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"profile_id": "throwaway", "deleted": True})

        response, payload = self.request("GET", "/api/profiles")
        self.assertNotIn("throwaway", payload["profiles"])

        response, payload = self.request("DELETE", "/api/profiles/throwaway")
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "profile_not_found")

        response, payload = self.request("DELETE", "/api/profiles/not%20a%20name")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_profile_name")

        response, payload = self.request("DELETE", "/api/datasets/anything.json")
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_generation_reports_invalid_source_data(self):
        def invalid_generator(profile, datasets_dir):
            raise ValueError("league calendar teams must match profile participants")

        self.server.generator = invalid_generator
        response, payload = self.request(
            "POST",
            "/api/generate",
            json.dumps({"profile": self.profile}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )

        self.assertEqual(response.status, 422)
        self.assertEqual(payload["error"]["code"], "invalid_source_data")
        self.assertEqual(
            payload["error"]["message"],
            "league calendar teams must match profile participants",
        )

    def test_generation_derives_participants_from_calendar(self):
        calendar = {
            "schema_version": "1.0",
            "league_id": "my-team",
            "teams": ["Alpha", "Beta", "Gamma"],
            "participants_count": 3,
            "matchdays": [
                {
                    "number": 1,
                    "serie_a_matchday": 1,
                    "fixtures": [{"home": "Alpha", "away": "Beta"}],
                }
            ],
        }
        calendar_path = Path(self.temp_dir.name) / "calendar.json"
        calendar_path.write_text(json.dumps(calendar), encoding="utf-8")
        source = next(source for source in self.profile["current_sources"] if source["name"] == "league_calendar")
        source.update(path=str(calendar_path), format="json")

        response, payload = self.request(
            "POST",
            "/api/generate",
            json.dumps({"profile": self.profile}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(self.calls[-1].participants.team_names, ("Alpha", "Beta", "Gamma"))
        self.assertEqual(self.calls[-1].participants.user_team, "Alpha")
        self.assertEqual(payload["profile_hash"], self.calls[-1].configuration_hash)

        response, payload = self.request("POST", "/api/generate", b'{"profile":{}}', {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_profile")

    def test_simulation_overwrites_the_current_report(self):
        response, payload = self.request("POST", "/api/simulate", json.dumps({"profile": self.profile, "iterations": 2000, "seed": 42}).encode("utf-8"), {"Content-Type": "application/json"})

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["iterations"], 2000)
        self.assertEqual(payload["diagnostics"]["seed"], 42)
        self.assertEqual(self.calls[-1][2:], (2000, 42, None))

        response, payload = self.request("POST", "/api/simulate", json.dumps({"profile": self.profile, "iterations": 99}).encode("utf-8"), {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_iterations")

    def test_simulation_forwards_real_auction_rosters_and_rejects_malformed_values(self):
        rosters = {"Alpha": [1, 2], "Beta": [3, 4]}
        response, payload = self.request(
            "POST",
            "/api/simulate",
            json.dumps({"profile": self.profile, "roster_mode": "auction", "rosters": rosters}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(self.calls[-1][-1], rosters)

        response, payload = self.request(
            "POST",
            "/api/simulate",
            json.dumps({"profile": self.profile, "roster_mode": "auction", "rosters": {"Alpha": [True]}}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_rosters")

    def test_dataset_read_rejects_unsafe_or_missing_paths(self):
        response, payload = self.request("GET", "/api/datasets/%2E%2E/secret.json")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_dataset_path")

        response, payload = self.request("GET", "/api/datasets/auction_data.csv")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_dataset_path")

        response, payload = self.request("GET", "/api/datasets/missing.json")
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "dataset_not_found")

    def test_uploads_fixed_sources_and_reports_missing_files(self):
        self.profile["current_sources"][0]["path"] = str(
            Path(self.temp_dir.name) / "missing.xlsx"
        )
        response, payload = self.request(
            "POST",
            "/api/sources/status",
            json.dumps(self.profile).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 200)
        player_list = next(
            source for source in payload["sources"] if source["name"] == "player_list"
        )
        self.assertFalse(player_list["exists"])

        response, payload = self.request(
            "PUT",
            "/api/uploads/my-team/current_sources/player_list",
            b"workbook contents",
            {
                "Content-Type": "application/octet-stream",
                "X-Filename": "listone.xlsx",
            },
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(Path(payload["path"]).is_file())
        self.profile["current_sources"][0]["path"] = payload["path"]

        response, payload = self.request(
            "POST",
            "/api/sources/status",
            json.dumps(self.profile).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        player_list = next(
            source for source in payload["sources"] if source["name"] == "player_list"
        )
        self.assertTrue(player_list["exists"])

    def test_sosfanta_update_check_and_accept_flow(self):
        body = json.dumps({"profile": self.profile}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        response, payload = self.request("POST", "/api/updates/sosfanta/check", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "baseline_missing")
        first_hash = payload["content_hash"]

        reviewed_body = json.dumps({"profile": self.profile, "content_hash": first_hash}).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/sosfanta/accept", reviewed_body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "unchanged")

        response, payload = self.request("POST", "/api/updates/sosfanta/check", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "unchanged")

        self.update_prose = "Beta now challenges Alpha."
        response, payload = self.request("POST", "/api/updates/sosfanta/check", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "changed")
        self.assertEqual(payload["change_count"], 4)
        changed_hash = payload["content_hash"]

        reviewed_body = json.dumps({"profile": self.profile, "content_hash": changed_hash}).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/sosfanta/bundle", reviewed_body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/plain; charset=utf-8")
        self.assertIn("current_titolari_csv", payload)
        self.assertIn("Beta now challenges Alpha", payload)

        response, payload = self.request("POST", "/api/updates/sosfanta/status", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "changed")

    def test_sosfanta_formations_full_audit_and_monitoring_flow(self):
        root = Path(self.temp_dir.name)
        starters_path = root / "titolari.csv"
        listone_path = root / "listone.xlsx"
        players = []
        starters = []
        player_id = 1
        teams = [
            "Atalanta", "Bologna", "Cagliari", "Como", "Fiorentina", "Frosinone", "Genoa",
            "Inter", "Juventus", "Lazio", "Lecce", "Milan", "Monza", "Napoli", "Parma",
            "Roma", "Sassuolo", "Torino", "Udinese", "Venezia",
        ]
        for index, team in enumerate(teams):
            for letter in "ABCDEFGHIJK":
                name = f"Player {index}{letter}"
                players.append({"Id": player_id, "Nome": name, "Squadra": team})
                starters.append({
                    "squadra": team,
                    "nome": name,
                    "id_fantacalcio": player_id,
                    "status": "TITOLARE",
                    "note": "",
                })
                player_id += 1
        listone_path.write_bytes(self.player_workbook(players))
        pd.DataFrame(starters).to_csv(starters_path, index=False)
        next(item for item in self.profile["current_sources"] if item["name"] == "starters")["path"] = str(starters_path)
        next(item for item in self.profile["current_sources"] if item["name"] == "player_list")["path"] = str(listone_path)
        self.formations_html = self.formations_article()
        body = json.dumps({"profile": self.profile}).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        response, initial = self.request("POST", "/api/updates/sosfanta-formations/check", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(initial["state"], "baseline_missing")
        self.assertEqual(initial["audit"]["summary"]["issue_count"], 0)
        self.assertEqual(initial["audit"]["sources"]["starters"]["path"], str(starters_path.resolve()))
        self.assertEqual(len(initial["audit"]["sources"]["player_list"]["sha256"]), 64)
        self.assertTrue(initial["bundle_available"])

        stale = json.dumps({
            "profile": self.profile,
            "content_hash": initial["content_hash"],
            "audit_hash": "stale",
        }).encode("utf-8")
        response, _ = self.request("POST", "/api/updates/sosfanta-formations/bundle", stale, headers)
        self.assertEqual(response.status, 422)

        reviewed = json.dumps({
            "profile": self.profile,
            "content_hash": initial["content_hash"],
            "audit_hash": initial["audit_hash"],
        }).encode("utf-8")
        response, bundle = self.request("POST", "/api/updates/sosfanta-formations/bundle", reviewed, headers)
        self.assertEqual(response.status, 200)
        self.assertIn("Slash order is not a hierarchy", bundle)

        response, _ = self.request("POST", "/api/updates/sosfanta-formations/accept", reviewed, headers)
        self.assertEqual(response.status, 200)
        response, status = self.request("POST", "/api/updates/sosfanta-formations/status", body, headers)
        self.assertEqual(response.status, 200)
        self.assertFalse(status["bundle_available"])

        self.formations_html = self.formations_article("Player 0B now challenges Player 0A.")
        response, changed = self.request("POST", "/api/updates/sosfanta-formations/check", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(changed["state"], "changed")
        self.assertEqual(changed["change_count"], 1)
        self.assertTrue(changed["bundle_available"])

    def test_sosfanta_set_piece_check_accept_and_bundle_flow(self):
        root = Path(self.temp_dir.name)
        set_pieces = root / "piazzati.csv"
        set_pieces.write_text(
            "squadra,nome,tipo,priorita\nAtalanta,Penalty,RIGORI,1\nAtalanta,Alpha,PUNIZIONI,1\n",
            encoding="utf-8",
        )
        next(item for item in self.profile["current_sources"] if item["name"] == "set_pieces")["path"] = str(set_pieces)
        self.set_piece_html = self.set_piece_article()
        self.penalty_html = self.penalty_article()
        body = json.dumps({"profile": self.profile}).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        response, first = self.request("POST", "/api/updates/sosfanta-set-pieces/check", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(first["state"], "baseline_missing")
        reviewed = json.dumps({"profile": self.profile, "content_hash": first["content_hash"]}).encode("utf-8")
        response, _ = self.request("POST", "/api/updates/sosfanta-set-pieces/accept", reviewed, headers)
        self.assertEqual(response.status, 200)

        self.set_piece_html = self.set_piece_article("Beta, Alpha")
        self.penalty_html = self.penalty_article("Reserve", "Penalty")
        response, changed = self.request("POST", "/api/updates/sosfanta-set-pieces/check", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(changed["change_count"], 1)
        reviewed = json.dumps({"profile": self.profile, "content_hash": changed["content_hash"]}).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/sosfanta-set-pieces/bundle", reviewed, headers)
        self.assertEqual(response.status, 200)
        self.assertIn("current_piazzati_csv", payload)
        self.assertIn("A RIGORI operation must be supported", payload)

        response, status = self.request("POST", "/api/updates/sosfanta-set-pieces/status", body, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(status["state"], "changed")

    def test_upload_rejects_unsafe_paths_and_file_types(self):
        response, payload = self.request(
            "PUT",
            "/api/uploads/my-team/current_sources/%2E%2E",
            b"value",
            {"X-Filename": "file.xlsx"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_upload_path")

        response, payload = self.request(
            "PUT",
            "/api/uploads/my-team/current_sources/player_list",
            b"value",
            {"X-Filename": "script.exe"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_upload_type")

    def test_league_calendar_upload_validates_before_replacing_existing_file(self):
        headers = {"Content-Type": "application/octet-stream", "X-Filename": "calendar.xlsx"}
        response, uploaded = self.request(
            "PUT",
            "/api/uploads/my-team/current_sources/league_calendar",
            build_legacy_calendar_template(),
            headers,
        )
        self.assertEqual(response.status, 200)
        target = Path(uploaded["path"])
        original = target.read_bytes()

        response, payload = self.request(
            "PUT",
            "/api/uploads/my-team/current_sources/league_calendar",
            b"not an xlsx workbook",
            headers,
        )

        self.assertEqual(response.status, 422)
        self.assertEqual(payload["error"]["code"], "invalid_league_calendar")
        self.assertIn("Download the calendar template", payload["error"]["message"])
        self.assertEqual(target.read_bytes(), original)

    def test_invalid_first_league_calendar_upload_is_not_stored(self):
        response, payload = self.request(
            "PUT",
            "/api/uploads/my-team/current_sources/league_calendar",
            b"not an xlsx workbook",
            {"Content-Type": "application/octet-stream", "X-Filename": "calendar.xlsx"},
        )

        self.assertEqual(response.status, 422)
        self.assertEqual(payload["error"]["code"], "invalid_league_calendar")
        target = Path(self.temp_dir.name) / "data/uploads/my-team/current_sources/league_calendar.xlsx"
        self.assertFalse(target.exists())

    def test_league_calendar_template_endpoint_returns_parseable_workbook(self):
        response, workbook = self.request("GET", "/api/templates/league-calendar.xlsx")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Disposition"), 'attachment; filename="calendario_lega_template.xlsx"')
        frame = pd.read_excel(io.BytesIO(workbook), sheet_name="Calendario", header=None)
        calendar = parse_legacy_two_block_frame(frame, "example")
        self.assertEqual(len(calendar["matchdays"]), 36)

    def test_player_list_check_upload_status_and_apply(self):
        root = Path(self.temp_dir.name)
        active = root / "active.xlsx"
        starters = root / "titolari.csv"
        pd.DataFrame([
            {"squadra": "AAA", "nome": "One", "id_fantacalcio": 1, "status": "TITOLARE", "note": ""},
            {"squadra": "CCC", "nome": "Departed", "id_fantacalcio": 3, "status": "RISERVA", "note": ""},
        ]).to_csv(starters, index=False)
        active_bytes = self.player_workbook([
            {"Id": 1, "R": "P", "Nome": "One", "Squadra": "AAA", "Qt.A": 10, "FVM": 20},
        ])
        active.write_bytes(active_bytes)
        source = next(item for item in self.profile["current_sources"] if item["name"] == "player_list")
        source["path"] = str(active)
        next(item for item in self.profile["current_sources"] if item["name"] == "starters")["path"] = str(starters)
        self.player_list_html = """<h1>Quotazioni 2026/27</h1><table><tr class="player-row">
            <td class="role">P</td><td><a class="name" href="/calciatori/1/one">One</a></td>
            <td class="team">AAA</td><td class="quotation">10</td><td class="fvm">20</td></tr></table>"""
        body = json.dumps({"profile": self.profile}).encode("utf-8")
        json_headers = {"Content-Type": "application/json"}

        response, payload = self.request("POST", "/api/updates/player-list/check", body, json_headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "unchanged")
        self.assertTrue(payload["download_url"].endswith("/21/1"))

        response, payload = self.request("POST", "/api/updates/player-list/status", body, json_headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "never_uploaded")

        candidate = self.player_workbook([
            {"Id": 1, "R": "P", "Nome": "One", "Squadra": "AAA", "Qt.A": 11, "FVM": 21},
            {"Id": 2, "R": "A", "Nome": "Two", "Squadra": "BBB", "Qt.A": 8, "FVM": 15},
        ], ceduti=[3])
        response, uploaded = self.request(
            "PUT", "/api/updates/player-list/candidate/my-team/2026-27", candidate,
            {"Content-Type": "application/octet-stream", "X-Filename": "official.xlsx"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(uploaded["state"], "candidate_ready")

        response, status = self.request("POST", "/api/updates/player-list/status", body, json_headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(status["summary"]["added"], 1)
        self.assertEqual(status["candidate_hash"], uploaded["candidate_hash"])
        self.assertEqual(status["profile_hash"], LeagueProfile.from_dict(self.profile).configuration_hash)
        self.assertEqual(len(status["active_hash"]), 64)
        self.assertEqual(len(status["starters_hash"]), 64)
        self.assertEqual(status["summary"]["starters_removed"], 1)
        self.assertEqual(status["details"]["starters_removed"][0]["id"], 3)

        stale = json.dumps({
            "profile": self.profile, "candidate_hash": "0" * 64,
            "profile_hash": status["profile_hash"], "active_hash": status["active_hash"], "starters_hash": status["starters_hash"],
        }).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/player-list/apply", stale, json_headers)
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"]["code"], "stale_candidate")

        changed_profile = json.loads(json.dumps(self.profile))
        changed_profile["name"] = "Changed while reviewing"
        response, _ = self.request("PUT", "/api/profiles/my-team", json.dumps(changed_profile).encode("utf-8"), json_headers)
        self.assertEqual(response.status, 200)
        stale_profile_body = json.dumps({
            "profile": self.profile, "candidate_hash": uploaded["candidate_hash"],
            "profile_hash": status["profile_hash"], "active_hash": status["active_hash"], "starters_hash": status["starters_hash"],
        }).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/player-list/apply", stale_profile_body, json_headers)
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"]["code"], "stale_profile")

        changed_body = json.dumps({"profile": changed_profile}).encode("utf-8")
        response, status = self.request("POST", "/api/updates/player-list/status", changed_body, json_headers)
        self.assertEqual(response.status, 200)
        active.write_bytes(self.player_workbook([
            {"Id": 1, "R": "P", "Nome": "One", "Squadra": "AAA", "Qt.A": 13, "FVM": 20},
        ]))
        stale_active_body = json.dumps({
            "profile": changed_profile, "candidate_hash": uploaded["candidate_hash"],
            "profile_hash": status["profile_hash"], "active_hash": status["active_hash"], "starters_hash": status["starters_hash"],
        }).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/player-list/apply", stale_active_body, json_headers)
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"]["code"], "stale_active_source")
        active.write_bytes(active_bytes)

        starters_bytes = starters.read_bytes()
        starters.write_text(starters.read_text(encoding="utf-8") + "AAA,Changed,99,RISERVA,\n", encoding="utf-8")
        response, payload = self.request("POST", "/api/updates/player-list/apply", stale_active_body, json_headers)
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"]["code"], "stale_starters_source")
        starters.write_bytes(starters_bytes)

        apply_body = json.dumps({
            "profile": changed_profile, "candidate_hash": uploaded["candidate_hash"],
            "profile_hash": status["profile_hash"], "active_hash": status["active_hash"], "starters_hash": status["starters_hash"],
        }).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/player-list/apply", apply_body, json_headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["candidate_hash"], uploaded["candidate_hash"])
        updated_source = next(item for item in payload["profile"]["current_sources"] if item["name"] == "player_list")
        self.assertIn(uploaded["candidate_hash"], updated_source["path"])
        self.assertTrue(Path(updated_source["path"]).is_file())
        self.assertEqual(active.read_bytes(), active_bytes)
        saved = json.loads((self.server.profiles_dir / "my-team.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["current_sources"], payload["profile"]["current_sources"])
        updated_starters = next(item for item in payload["profile"]["current_sources"] if item["name"] == "starters")
        self.assertNotEqual(updated_starters["path"], str(starters))
        cleaned = pd.read_csv(updated_starters["path"])
        self.assertEqual(cleaned["id_fantacalcio"].tolist(), [1])
        self.assertEqual(payload["starters_removed"][0]["id"], 3)
        self.assertEqual(self.calls[-1].configuration_hash, LeagueProfile.from_dict(saved).configuration_hash)

    def test_player_list_generation_failure_preserves_profile_and_dataset(self):
        root = Path(self.temp_dir.name)
        active = root / "failure-active.xlsx"
        active.write_bytes(self.player_workbook([{"Id": 1, "R": "P", "Nome": "One", "Qt.A": 10, "FVM": 20}]))
        source = next(item for item in self.profile["current_sources"] if item["name"] == "player_list")
        source["path"] = str(active)
        response, _ = self.request("PUT", "/api/profiles/my-team", json.dumps(self.profile).encode("utf-8"), {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        candidate = self.player_workbook([{"Id": 1, "R": "P", "Nome": "One", "Qt.A": 12, "FVM": 22}])
        response, uploaded = self.request("PUT", "/api/updates/player-list/candidate/my-team/2026-27", candidate, {"X-Filename": "candidate.xlsx"})
        self.assertEqual(response.status, 200)
        body = json.dumps({"profile": self.profile}).encode("utf-8")
        response, status = self.request("POST", "/api/updates/player-list/status", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        output = self.server.datasets_dir / "my-team/2026-27/auction_data.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"old":true}', encoding="utf-8")
        old_profile = (self.server.profiles_dir / "my-team.json").read_bytes()

        def failing_generator(profile, datasets_dir):
            path = datasets_dir / profile.profile_id / "2026-27/auction_data.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"partial":true}', encoding="utf-8")
            raise RuntimeError("failed")

        self.server.generator = failing_generator
        request = json.dumps({
            "profile": self.profile, "candidate_hash": uploaded["candidate_hash"],
            "profile_hash": status["profile_hash"], "active_hash": status["active_hash"], "starters_hash": status["starters_hash"],
        }).encode("utf-8")
        response, payload = self.request("POST", "/api/updates/player-list/apply", request, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 500)
        self.assertEqual(payload["error"]["code"], "generation_failed")
        self.assertEqual((self.server.profiles_dir / "my-team.json").read_bytes(), old_profile)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"old": True})

    def _canonical_matchday_csv(self, giornata=5, player_id=1, voto=6.5, squadra="AAA"):
        return pd.DataFrame([{
            "giornata": giornata, "id_fantacalcio": player_id, "nome": "One", "squadra": squadra, "ruolo": "A",
            "voto": voto, "gol": 1, "assist": 0, "ammonizioni": 0, "espulsioni": 0, "autogol": 0,
            "gol_subiti": 0, "rigori_parati": 0, "rigori_sbagliati": 0, "rigori_segnati": 0,
        }]).to_csv(index=False).encode("utf-8")

    def test_matchday_status_reports_imported_and_missing_giornate(self):
        body = json.dumps({"profile": self.profile}).encode("utf-8")
        response, status = self.request("POST", "/api/matchdays/status", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        start, end = self.profile["season"]["fantasy_start_matchday"], self.profile["season"]["fantasy_end_matchday"]
        self.assertEqual(status["prima_giornata"], start)
        self.assertEqual(status["ultima_giornata"], end)
        self.assertEqual(status["giornate_importate"], [])
        self.assertEqual(status["giornate_mancanti"], list(range(start, end + 1)))

        votes_dir = self.server.raw_dir.parent / "updates/voti" / self.profile["season"]["season"].replace("/", "-")
        votes_dir.mkdir(parents=True)
        (votes_dir / f"giornata_{start:02d}.csv").write_bytes(self._canonical_matchday_csv(giornata=start))

        response, status = self.request("POST", "/api/matchdays/status", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        self.assertEqual(status["giornate_importate"], [start])
        self.assertNotIn(start, status["giornate_mancanti"])

    def test_matchday_candidate_upload_accepts_a_valid_csv_and_rejects_a_bad_one(self):
        start = self.profile["season"]["fantasy_start_matchday"]
        response, preview = self.request(
            "PUT", f"/api/updates/matchday/candidate/my-team/{start}",
            self._canonical_matchday_csv(giornata=start),
            {"Content-Type": "text/csv", "X-Filename": "voti.csv"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(preview["giornata"], start)
        self.assertEqual(preview["righe"], 1)
        self.assertEqual(preview["squadre"], 1)
        self.assertEqual(preview["senza_voto"], 0)
        staged = self.server.uploads_dir / "my-team/matchdays" / f"giornata_{start:02d}.csv"
        self.assertTrue(staged.is_file())

        response, error = self.request(
            "PUT", f"/api/updates/matchday/candidate/my-team/{start}",
            b"nome,voto\nOne,7\n",
            {"Content-Type": "text/csv", "X-Filename": "bad.csv"},
        )
        self.assertEqual(response.status, 422)
        self.assertEqual(error["error"]["code"], "invalid_matchday_file")
        # The rejected file is not left behind to be applied by mistake.
        self.assertFalse(staged.exists())

    def test_matchday_apply_requires_a_staged_candidate(self):
        body = json.dumps({"profile": self.profile, "giornata": 5}).encode("utf-8")
        response, payload = self.request("POST", "/api/matchdays/apply", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"]["code"], "candidate_not_found")

    def test_matchday_apply_imports_the_candidate_and_regenerates(self):
        start = self.profile["season"]["fantasy_start_matchday"]
        self.request(
            "PUT", f"/api/updates/matchday/candidate/my-team/{start}",
            self._canonical_matchday_csv(giornata=start),
            {"Content-Type": "text/csv", "X-Filename": "voti.csv"},
        )

        def generator(profile, datasets_dir):
            self.calls.append(profile)
            path = datasets_dir / profile.profile_id / profile.season.season.replace("/", "-") / "auction_data.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "players": [{"id": 1, "nome": "One", "mercato": {"fp_per_giornata": 7.0},
                            "proiezione": {"giornate_osservate": 1}}],
                "model_version": "2.0",
            }), encoding="utf-8")

        self.server.generator = generator
        body = json.dumps({"profile": self.profile, "giornata": start}).encode("utf-8")
        response, payload = self.request("POST", "/api/matchdays/apply", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["giornata"], start)
        self.assertEqual(payload["giocatori"], 1)
        self.assertEqual(payload["model_version"], "2.0")
        written = Path(payload["file_importato"])
        self.assertTrue(written.is_file())
        self.assertEqual(pd.read_csv(written).giornata.iloc[0], start)
        staged = self.server.uploads_dir / "my-team/matchdays" / f"giornata_{start:02d}.csv"
        self.assertFalse(staged.exists())

        # Applying again reports the same candidate as missing, not stale state.
        response, payload = self.request("POST", "/api/matchdays/apply", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 404)

    def _full_roster(self):
        """One player per league slot (3P/8D/8C/6A in the default profile), so
        every allowed formation can be filled — a legality precondition of
        `build_lineup`, unrelated to what this test actually checks."""
        league = LeagueConfig.from_profile(LeagueProfile.from_dict(self.profile))
        players, striker_id, next_id = [], None, 1
        for role, slots in league.slots:
            for index in range(slots):
                fantavoto = 6.0 + (1.0 if role == "A" and index == 0 else 0.0) - index * 0.05
                players.append({
                    "id": next_id, "nome": f"{role}{next_id}", "ruolo": role, "squadra": "AAA",
                    "p_gioca_per_giornata": [0.9], "voto_puro_mean_per_giornata": [fantavoto],
                    "bonus_atteso_per_giornata": [0.0], "voto_puro_std_per_giornata": [0.5],
                    "proiezione": {"p_gioca": 0.9, "fantavoto": fantavoto},
                })
                if role == "A" and index == 0:
                    striker_id = next_id
                next_id += 1
        return players, striker_id

    def test_lineup_build_returns_the_xi_ordered_bench_and_reasons(self):
        players, striker_id = self._full_roster()
        dataset = {
            "players": players,
            "teams": [{"squadra": "AAA", "fixtures": [{"matchday": 1, "opponent": "BBB", "venue": "CASA"}]}],
            "set_pieces": [{"squadra": "AAA", "tipo": "RIGORI", "takers": [{"player_id": striker_id, "priorita": 1}]}],
        }
        dataset_path = self.server.datasets_dir / "my-team" / self.profile["season"]["season"].replace("/", "-") / "auction_data.json"
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

        body = json.dumps({
            "profile": self.profile, "roster": [p["id"] for p in players], "giornata": 1,
        }).encode("utf-8")
        response, payload = self.request("POST", "/api/lineup", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["giornata"], 1)
        striker = next(row for row in payload["titolari"] if row["id"] == striker_id)
        self.assertEqual(striker["avversario"], "BBB")
        self.assertFalse(striker["trasferta"])
        self.assertEqual(striker["rigorista_priorita"], 1)
        self.assertEqual(striker["incertezza"], 0.5)

    def test_lineup_build_reports_unavailable_players_and_unknown_ids(self):
        players, _ = self._full_roster()
        dataset_path = self.server.datasets_dir / "my-team" / self.profile["season"]["season"].replace("/", "-") / "auction_data.json"
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_path.write_text(json.dumps({"players": players}), encoding="utf-8")
        roster_ids = [p["id"] for p in players]
        goalkeeper_ids = [p["id"] for p in players if p["ruolo"] == "P"]

        body = json.dumps({"profile": self.profile, "roster": roster_ids + [9999], "giornata": 1}).encode("utf-8")
        response, payload = self.request("POST", "/api/lineup", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "unknown_player")

        # Every goalkeeper unavailable: no legal formation can be filled.
        body = json.dumps({"profile": self.profile, "roster": roster_ids, "giornata": 1,
                           "indisponibili": goalkeeper_ids}).encode("utf-8")
        response, payload = self.request("POST", "/api/lineup", body, {"Content-Type": "application/json"})
        self.assertEqual(response.status, 422)
        self.assertEqual(payload["error"]["code"], "lineup_impossible")


if __name__ == "__main__":
    unittest.main()

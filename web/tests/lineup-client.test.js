import test from "node:test";
import assert from "node:assert/strict";
import {
  LineupClientError,
  applyMatchday,
  buildLineup,
  getMatchdayStatus,
  parseRosterExport,
  uploadMatchdayCandidate,
} from "../src/lineup-client.js";

test("posts the roster, giornata and availability to /api/lineup", async () => {
  let body;
  const fetchImpl = async (url, options) => {
    body = { url, ...JSON.parse(options.body) };
    return { ok: true, status: 200, json: async () => ({ giornata: 5, titolari: [] }) };
  };
  const result = await buildLineup(
    { profile_id: "league" },
    { roster: [1, 2, 3], giornata: 5, indisponibili: [2], dubbi: { 3: 0.4 } },
    { fetchImpl },
  );
  assert.equal(body.url, "/api/lineup");
  assert.deepEqual(body.roster, [1, 2, 3]);
  assert.equal(body.giornata, 5);
  assert.deepEqual(body.indisponibili, [2]);
  assert.deepEqual(body.dubbi, { 3: 0.4 });
  assert.equal(result.giornata, 5);
});

test("reports the lineup as impossible with the backend's own message", async () => {
  const fetchImpl = async () => ({
    ok: false,
    status: 422,
    json: async () => ({ error: { code: "lineup_impossible", message: "nessun portiere disponibile" } }),
  });
  await assert.rejects(
    buildLineup({ profile_id: "league" }, { roster: [1], giornata: 1 }, { fetchImpl }),
    (error) => error instanceof LineupClientError && error.code === "lineup_impossible",
  );
});

test("asks for the matchday status of the given profile", async () => {
  let body;
  const fetchImpl = async (url, options) => {
    body = { url, ...JSON.parse(options.body) };
    return { ok: true, status: 200, json: async () => ({ giornate_mancanti: [5, 6] }) };
  };
  const result = await getMatchdayStatus({ profile_id: "league" }, { fetchImpl });
  assert.equal(body.url, "/api/matchdays/status");
  assert.deepEqual(result.giornate_mancanti, [5, 6]);
});

test("uploads a matchday candidate to the profile- and giornata-scoped endpoint", async () => {
  let request;
  const file = { name: "voti.csv" };
  const fetchImpl = async (url, options) => {
    request = { url, options };
    return { ok: true, status: 200, json: async () => ({ righe: 20 }) };
  };
  await uploadMatchdayCandidate(file, "league", 5, { fetchImpl });
  assert.equal(request.url, "/api/updates/matchday/candidate/league/5");
  assert.equal(request.options.headers["X-Filename"], "voti.csv");
  assert.equal(request.options.body, file);
});

test("confirms applying a matchday with a giornata number", async () => {
  let body;
  const fetchImpl = async (url, options) => {
    body = { url, ...JSON.parse(options.body) };
    return { ok: true, status: 200, json: async () => ({ giornata: 5, giocatori: 400 }) };
  };
  const result = await applyMatchday({ profile_id: "league" }, 5, { fetchImpl });
  assert.equal(body.url, "/api/matchdays/apply");
  assert.equal(body.giornata, 5);
  assert.equal(result.giocatori, 400);
});

test("parses a dashboard roster export into player ids", () => {
  const exported = JSON.stringify({ giocatori: [{ id: 1, nome: "A" }, { id: "2", nome: "B" }] });
  assert.deepEqual(parseRosterExport(exported), [1, 2]);
  assert.deepEqual(parseRosterExport(JSON.stringify([3, 4])), [3, 4]);
});

test("rejects a roster file that is not JSON or has invalid ids", () => {
  assert.throws(() => parseRosterExport("not json"), (error) => error.code === "invalid_roster_file");
  assert.throws(() => parseRosterExport(JSON.stringify({ giocatori: [{ id: "abc" }] })), (error) => error.code === "invalid_roster_file");
  assert.throws(() => parseRosterExport(JSON.stringify({ nope: true })), (error) => error.code === "invalid_roster_file");
});

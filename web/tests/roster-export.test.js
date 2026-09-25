import test from "node:test";
import assert from "node:assert/strict";

const { buildRosterExport, rosterExportFilename, ROSTER_EXPORT_VERSION } = await import(
  "../src/roster-export.js"
);

const team = {
  name: "La mia squadra",
  roster: [
    { id: 5, nome: "Portiere Uno", ruolo: "P", squadra: "Club" },
    { id: 12, nome: "Attaccante Due", ruolo: "A", squadra: "Altro Club" },
  ],
};

const assigned = {
  5: { owner: 0, price: 12 },
  12: { owner: 0, price: 55 },
};

test("the export carries every roster player with the price actually paid", () => {
  const payload = buildRosterExport({
    profileId: "lega-2026-27",
    team,
    assigned,
    now: () => "2026-09-26T00:00:00.000Z",
  });
  assert.equal(payload.version, ROSTER_EXPORT_VERSION);
  assert.equal(payload.profilo, "lega-2026-27");
  assert.equal(payload.squadra, "La mia squadra");
  assert.equal(payload.esportato_il, "2026-09-26T00:00:00.000Z");
  assert.deepEqual(payload.giocatori, [
    { id: 5, nome: "Portiere Uno", ruolo: "P", squadra: "Club", prezzo: 12 },
    { id: 12, nome: "Attaccante Due", ruolo: "A", squadra: "Altro Club", prezzo: 55 },
  ]);
});

test("a player never assigned a price exports as null, not zero", () => {
  const payload = buildRosterExport({ profileId: "default", team, assigned: {} });
  assert.deepEqual(
    payload.giocatori.map((player) => player.prezzo),
    [null, null],
  );
});

test("an empty roster exports as an empty list, not an error", () => {
  const payload = buildRosterExport({ profileId: "default", team: { name: "Vuota", roster: [] } });
  assert.deepEqual(payload.giocatori, []);
});

test("the filename is stable, slugified and dated", () => {
  const date = new Date("2026-09-26T10:00:00.000Z");
  assert.equal(
    rosterExportFilename({ name: "La mia squadra" }, date),
    "fantascout-rosa-la-mia-squadra-2026-09-26.json",
  );
  assert.equal(rosterExportFilename(undefined, date), "fantascout-rosa-rosa-2026-09-26.json");
});

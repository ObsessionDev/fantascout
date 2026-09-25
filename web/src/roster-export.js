import { playerIdKey } from "./auction-state.js";

/**
 * Exports the user's purchased roster as a downloadable JSON file.
 *
 * The auction's roster lives only in `auction-store.js`'s corner of
 * `localStorage`: this is the one way it leaves the browser. The format is
 * documented in the README (`## Esportazione della rosa`) because
 * `advisor.lineup` reads it back on the Python side.
 */

export const ROSTER_EXPORT_VERSION = 1;

export const buildRosterExport = ({ profileId, team, assigned = {}, now = () => new Date().toISOString() }) => ({
  version: ROSTER_EXPORT_VERSION,
  profilo: String(profileId || "default"),
  squadra: team?.name || "",
  esportato_il: now(),
  giocatori: (team?.roster || []).map((player) => ({
    id: player.id,
    nome: player.nome,
    ruolo: player.ruolo,
    squadra: player.squadra,
    prezzo: assigned[playerIdKey(player.id)]?.price ?? null,
  })),
});

const slugify = (value) =>
  String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "rosa";

export const rosterExportFilename = (team, date = new Date()) =>
  `fantascout-rosa-${slugify(team?.name)}-${date.toISOString().slice(0, 10)}.json`;

/** The DOM side effect, kept out of `buildRosterExport` so the payload stays testable. */
export const downloadRosterExport = (payload, filename) => {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

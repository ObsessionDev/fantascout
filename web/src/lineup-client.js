import { apiUrl } from "./profile-client.js";

/**
 * Client for the matchday lineup (`/api/lineup`) and the "Aggiorna giornate"
 * import flow (`/api/matchdays/*`). Mirrors the shape of `updates-client.js`:
 * typed errors, a thin fetch wrapper, no React.
 */

export class LineupClientError extends Error {
  constructor(code, message, status) {
    super(message);
    this.name = "LineupClientError";
    this.code = code;
    this.status = status;
  }
}

const postJson = async (path, body, { apiBase = "", fetchImpl = globalThis.fetch } = {}) => {
  if (typeof fetchImpl !== "function")
    throw new LineupClientError("fetch_unavailable", "Fetch non disponibile.");
  let response;
  try {
    response = await fetchImpl(apiUrl(path, apiBase), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new LineupClientError("network_error", "Impossibile contattare il backend.", undefined, { cause });
  }
  let payload;
  try {
    payload = await response.json();
  } catch (cause) {
    throw new LineupClientError("invalid_response", "Il backend ha restituito una risposta non valida.", response.status);
  }
  if (!response.ok)
    throw new LineupClientError(payload.error?.code || "request_failed", payload.error?.message || `Errore ${response.status}`, response.status);
  return payload;
};

export const buildLineup = (profile, { roster, giornata, indisponibili = [], dubbi = {} }, options) =>
  postJson("/api/lineup", { profile, roster, giornata, indisponibili, dubbi }, options);

export const getMatchdayStatus = (profile, options) =>
  postJson("/api/matchdays/status", { profile }, options);

export const applyMatchday = (profile, giornata, options) =>
  postJson("/api/matchdays/apply", { profile, giornata }, options);

export const uploadMatchdayCandidate = async (file, profileId, giornata, { apiBase = "", fetchImpl = globalThis.fetch } = {}) => {
  if (!file) throw new LineupClientError("invalid_candidate", "Seleziona un file CSV o XLSX.");
  let response;
  try {
    response = await fetchImpl(apiUrl(`/api/updates/matchday/candidate/${encodeURIComponent(profileId)}/${giornata}`, apiBase), {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream", "X-Filename": file.name },
      body: file,
    });
  } catch {
    throw new LineupClientError("network_error", "Impossibile contattare il backend.");
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new LineupClientError("invalid_response", "Il backend ha restituito una risposta non valida.", response.status);
  }
  if (!response.ok)
    throw new LineupClientError(payload.error?.code || "request_failed", payload.error?.message || `Errore ${response.status}`, response.status);
  return payload;
};

/** Parse a dashboard roster export (or a bare list of ids) into player ids. */
export const parseRosterExport = (text) => {
  let value;
  try {
    value = JSON.parse(text);
  } catch (cause) {
    throw new LineupClientError("invalid_roster_file", "Il file non contiene JSON valido.");
  }
  const entries = Array.isArray(value) ? value : Array.isArray(value?.giocatori) ? value.giocatori : null;
  if (!entries) throw new LineupClientError("invalid_roster_file", "Il file non sembra una rosa esportata.");
  const ids = entries.map((entry) => Number(typeof entry === "object" ? entry.id : entry));
  if (ids.some((id) => !Number.isInteger(id)))
    throw new LineupClientError("invalid_roster_file", "La rosa contiene un id non valido.");
  return ids;
};

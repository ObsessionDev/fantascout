/** Client for the two stateless auction routes.
 *
 *  The solver lives in Python and the auction lives in the browser, so the board
 *  is posted to be scored and nothing is kept on the server. Purchases travel as
 *  ids: eighteen surnames in the listing are shared (`Martinez Jo.` and
 *  `Martinez L.`, `Terracciano` and `Terracciano F.`), and a player assigned to
 *  the wrong manager during an auction cannot be taken back.
 *
 *  `auction-store.js` stays the only owner of the auction; this module reads the
 *  board it exposes and never writes to it.
 */

import { apiUrl, ProfileClientError } from "./profile-client.js";

const fail = (code, message, options) => {
  throw new ProfileClientError(code, message, options);
};

async function post(path, payload, { fetchImpl = globalThis.fetch, apiBase = "", signal } = {}) {
  if (typeof fetchImpl !== "function") fail("fetch_unavailable", "Fetch is unavailable.");
  let response;
  try {
    response = await fetchImpl(apiUrl(path, apiBase), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    fail("network_error", "Il server locale non risponde.", { cause });
  }
  let body = null;
  try {
    body = await response.json();
  } catch (cause) {
    fail("invalid_response", "Il server ha risposto in modo non valido.", {
      status: response.status,
      cause,
    });
  }
  if (!response.ok) {
    const error = body?.error ?? {};
    fail(error.code || "http_error", error.message || `Richiesta fallita (${response.status}).`, {
      status: response.status,
    });
  }
  return body;
}

/** Split the board's purchase history into mine and everyone else's.
 *
 *  Both halves matter. The players others bought leave the pool, and the credits
 *  they spent leave the market: expected prices are re-derived from what the
 *  league still has to spend on the slots it still has to fill. */
export const auctionPayload = (board, profileId, { k } = {}) => {
  const history = Array.isArray(board?.history) ? board.history : [];
  const mine = [];
  const theirs = [];
  for (const record of history) {
    const entry = { playerId: Number(record.playerId), price: Number(record.price) || 0 };
    if (!Number.isFinite(entry.playerId)) continue;
    (record.owner === board.userTeamIndex ? mine : theirs).push(entry);
  }
  const payload = { profile_id: String(profileId), owned: mine, taken: theirs };
  if (Number.isFinite(k)) payload.k = k;
  return payload;
};

/** Best use of the credits still available, and how the target stands. */
export const fetchAuctionPlan = (board, profileId, options = {}) =>
  post("/api/auction/plan", auctionPayload(board, profileId, options), options);

/** Exact ceiling for the player under the hammer. */
export const fetchMaximumBid = (board, profileId, playerId, options = {}) =>
  post(
    "/api/auction/bid",
    { ...auctionPayload(board, profileId, options), playerId: Number(playerId) },
    options,
  );

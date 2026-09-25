/** Maximum bid for the player currently selected, kept in step with the board.
 *
 *  The solver takes about a second, which is less than it takes to type a name,
 *  so nothing is precomputed and nothing goes stale. What does have to be
 *  handled is order: a request fired before the last purchase was recorded must
 *  never overwrite a newer answer. Every request carries the board state it was
 *  asked about, and a reply is dropped unless that state is still the current
 *  one — an out-of-date ceiling shown as current is worse than no ceiling, since
 *  the user would bid on it.
 */

import { useEffect, useRef, useState } from "react";

import { fetchMaximumBid } from "./auction-advisor-client.js";

const IDLE = { status: "idle", bid: null, error: null };

/** What the answer depends on: the player asked about, and every purchase made. */
const stateKey = (board, playerId) =>
  [
    playerId ?? "",
    board?.userTeamIndex ?? "",
    (board?.history || []).map((record) => `${record.playerId}:${record.owner}:${record.price}`).join(","),
  ].join("|");

export function useMaximumBid(board, profileId, player, { apiBase = "", enabled = true } = {}) {
  const [state, setState] = useState(IDLE);
  const current = useRef("");

  const playerId = player?.id ?? null;
  const key = stateKey(board, playerId);

  useEffect(() => {
    if (!enabled || playerId == null) {
      current.current = "";
      setState(IDLE);
      return undefined;
    }
    const controller = new AbortController();
    current.current = key;
    // The previous ceiling stays on screen, marked stale, rather than blanking:
    // during a live auction an empty box reads as "no opinion", which is wrong.
    setState((previous) => ({ ...previous, status: "loading", error: null }));
    fetchMaximumBid(board, profileId, playerId, { apiBase, signal: controller.signal })
      .then((bid) => {
        if (current.current !== key) return;
        setState({ status: "ready", bid, error: null });
      })
      .catch((error) => {
        if (error?.name === "AbortError" || current.current !== key) return;
        setState({ status: "error", bid: null, error });
      });
    return () => controller.abort();
  }, [key, enabled, apiBase, profileId]);

  return state;
}

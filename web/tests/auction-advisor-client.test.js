import test from "node:test";
import assert from "node:assert/strict";
import { auctionPayload, fetchMaximumBid } from "../src/auction-advisor-client.js";

const board = {
  userTeamIndex: 1,
  history: [
    { playerId: 10, owner: 1, price: 40 },
    { playerId: 20, owner: 0, price: 90 },
    { playerId: 30, owner: 2, price: 7 },
    { playerId: 40, owner: 1, price: 3 },
  ],
};

test("purchases split into mine and the rest of the league", () => {
  const payload = auctionPayload(board, "lega-2026-27");
  assert.deepEqual(payload.owned, [
    { playerId: 10, price: 40 },
    { playerId: 40, price: 3 },
  ]);
  assert.deepEqual(payload.taken, [
    { playerId: 20, price: 90 },
    { playerId: 30, price: 7 },
  ]);
  assert.equal(payload.profile_id, "lega-2026-27");
});

test("other managers' purchases are reported, not dropped", () => {
  // They leave the pool and they drain the credits still chasing it, so the
  // server needs them to reprice what is left.
  const payload = auctionPayload(board, "p");
  assert.equal(payload.taken.length, 2);
  assert.equal(
    payload.taken.reduce((sum, entry) => sum + entry.price, 0),
    97,
  );
});

test("players travel as ids, never as names", () => {
  const payload = auctionPayload(board, "p");
  for (const entry of [...payload.owned, ...payload.taken]) {
    assert.equal(typeof entry.playerId, "number");
    assert.equal("nome" in entry, false);
  }
});

test("an empty board asks about a full budget", () => {
  const payload = auctionPayload({ userTeamIndex: 0, history: [] }, "p");
  assert.deepEqual(payload.owned, []);
  assert.deepEqual(payload.taken, []);
});

test("k travels only when it is a number", () => {
  assert.equal("k" in auctionPayload(board, "p"), false);
  assert.equal(auctionPayload(board, "p", { k: 1.5 }).k, 1.5);
  assert.equal("k" in auctionPayload(board, "p", { k: "molto" }), false);
});

test("the called player is posted alongside the board", async () => {
  let seen = null;
  const fetchImpl = async (url, options) => {
    seen = { url, body: JSON.parse(options.body) };
    return { ok: true, status: 200, json: async () => ({ prezzo_massimo: 42 }) };
  };
  const result = await fetchMaximumBid(board, "p", 99, { fetchImpl, apiBase: "http://x" });
  assert.equal(seen.url, "http://x/api/auction/bid");
  assert.equal(seen.body.playerId, 99);
  assert.equal(seen.body.owned.length, 2);
  assert.equal(result.prezzo_massimo, 42);
});

test("a server error surfaces its code instead of a generic failure", async () => {
  const fetchImpl = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ error: { code: "dataset_not_found", message: "Genera il dataset." } }),
  });
  await assert.rejects(
    () => fetchMaximumBid(board, "p", 1, { fetchImpl }),
    (error) => error.code === "dataset_not_found",
  );
});

test("an unreachable server is reported as a network error, not a crash", async () => {
  const fetchImpl = async () => {
    throw new TypeError("failed to fetch");
  };
  await assert.rejects(
    () => fetchMaximumBid(board, "p", 1, { fetchImpl }),
    (error) => error.code === "network_error",
  );
});

test("an aborted request propagates so a superseded answer can be dropped", async () => {
  const fetchImpl = async () => {
    const error = new Error("aborted");
    error.name = "AbortError";
    throw error;
  };
  await assert.rejects(
    () => fetchMaximumBid(board, "p", 1, { fetchImpl }),
    (error) => error.name === "AbortError",
  );
});

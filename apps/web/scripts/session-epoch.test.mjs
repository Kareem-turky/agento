// Deterministic tests of the Operations Console session-context reducer
// (Node standard library test runner; Node >= 22.18 strips the TypeScript types).
//
//   npm run test:session
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { initialSession, sessionReducer } from "../components/console/session.ts";

const STORE_A = "0a0a0a0a-0000-4000-8000-000000000001";
const STORE_B = "0b0b0b0b-0000-4000-8000-000000000002";
const COMMAND = "0c0c0c0c-0000-4000-8000-000000000001";

const run = (...actions) => actions.reduce(sessionReducer, initialSession);

/** Key set, store set, key accepted and a command created, all in the current epoch. */
function busySession() {
  let state = run({ type: "keySet" }, { type: "storeChanged", storeId: STORE_A });
  state = sessionReducer(state, { type: "authResult", epoch: state.epoch, outcome: "accepted" });
  return sessionReducer(state, { type: "commandCreated", epoch: state.epoch, commandId: COMMAND });
}

test("the initial session has no key, store or command", () => {
  assert.deepEqual(initialSession, { epoch: 0, keyStatus: "unset", storeId: "", recentCommandId: null });
});

test("replacing the Product API key starts a new epoch and forgets the old context", () => {
  const before = busySession();
  assert.equal(before.keyStatus, "accepted");
  assert.equal(before.recentCommandId, COMMAND);
  const after = sessionReducer(before, { type: "keySet" });
  assert.equal(after.epoch, before.epoch + 1);
  assert.equal(after.keyStatus, "unverified");
  assert.equal(after.recentCommandId, null);
  assert.equal(after.storeId, STORE_A);
});

test("changing the Store UUID starts a new epoch and clears the recent command", () => {
  const before = busySession();
  const after = sessionReducer(before, { type: "storeChanged", storeId: STORE_B });
  assert.equal(after.epoch, before.epoch + 1);
  assert.equal(after.storeId, STORE_B);
  assert.equal(after.recentCommandId, null);
  assert.equal(after.keyStatus, "accepted"); // the key itself did not change
  // Re-entering the same store is not a context change.
  assert.equal(sessionReducer(after, { type: "storeChanged", storeId: STORE_B }), after);
});

test("disconnect clears everything and starts a new epoch", () => {
  const before = busySession();
  const after = sessionReducer(before, { type: "disconnected" });
  assert.deepEqual(after, { epoch: before.epoch + 1, keyStatus: "unset", storeId: "", recentCommandId: null });
});

test("a stale auth result (older epoch) never changes the current key status", () => {
  let state = run({ type: "keySet" }, { type: "keySet" }); // epoch 2, key B
  assert.equal(state.epoch, 2);
  for (const outcome of ["accepted", "rejected"]) {
    const next = sessionReducer(state, { type: "authResult", epoch: 1, outcome });
    assert.equal(next, state);
    assert.equal(next.keyStatus, "unverified");
  }
  state = sessionReducer(state, { type: "authResult", epoch: 2, outcome: "accepted" });
  assert.equal(state.keyStatus, "accepted");
  state = sessionReducer(state, { type: "authResult", epoch: 2, outcome: "rejected" });
  assert.equal(state.keyStatus, "rejected");
  assert.equal(sessionReducer(state, { type: "authResult", epoch: 2, outcome: "other" }), state);
});

test("a stale ticket completion (older epoch) never fills the current recent command", () => {
  const state = run({ type: "keySet" }, { type: "storeChanged", storeId: STORE_A }); // epoch 2
  assert.equal(state.epoch, 2);
  const stale = sessionReducer(state, { type: "commandCreated", epoch: 1, commandId: COMMAND });
  assert.equal(stale, state);
  assert.equal(stale.recentCommandId, null);
  const current = sessionReducer(state, { type: "commandCreated", epoch: 2, commandId: COMMAND });
  assert.equal(current.recentCommandId, COMMAND);
});

test("after disconnect no auth result can mark a key accepted", () => {
  const state = run({ type: "keySet" }, { type: "disconnected" });
  const next = sessionReducer(state, { type: "authResult", epoch: state.epoch, outcome: "accepted" });
  assert.equal(next.keyStatus, "unset");
});

test("the session state never holds the key: only an integer epoch", () => {
  const state = busySession();
  assert.deepEqual(Object.keys(state).sort(), ["epoch", "keyStatus", "recentCommandId", "storeId"]);
  assert.ok(Number.isInteger(state.epoch));
  const source = readFileSync(new URL("../components/console/session.ts", import.meta.url), "utf8");
  const code = source.replace(/\/\/.*$/gm, "");
  assert.ok(!/apiKey|localStorage|sessionStorage|indexedDB|cookie/i.test(code));
});

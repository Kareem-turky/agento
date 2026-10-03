// Deterministic tests of the Agento Product session-context reducer
// (Node standard library test runner; Node >= 22.18 strips the TypeScript types).
//
//   npm run test:session
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { authOutcome, initialSession, sessionReducer } from "../components/shell/session.ts";

const STORE_A = "0a0a0a0a-0000-4000-8000-000000000001";
const STORE_B = "0b0b0b0b-0000-4000-8000-000000000002";
const COMMAND = "0c0c0c0c-0000-4000-8000-000000000001";

const run = (...actions) => actions.reduce(sessionReducer, initialSession);

/** Key set, store set, key accepted and a command created, all in the current epochs. */
function busySession() {
  let state = run({ type: "keySet" }, { type: "storeChanged", storeId: STORE_A });
  state = sessionReducer(state, { type: "authResult", sessionEpoch: state.sessionEpoch, outcome: "accepted" });
  return sessionReducer(state, { type: "commandCreated", operationsEpoch: state.operationsEpoch, commandId: COMMAND });
}

test("the initial session has no key, store or command", () => {
  assert.deepEqual(initialSession, {
    sessionEpoch: 0, operationsEpoch: 0, keyStatus: "unset", storeId: "", recentCommandId: null,
  });
});

test("replacing the Product API key starts new session and operations epochs", () => {
  const before = busySession();
  assert.equal(before.keyStatus, "accepted");
  assert.equal(before.recentCommandId, COMMAND);
  const after = sessionReducer(before, { type: "keySet" });
  assert.equal(after.sessionEpoch, before.sessionEpoch + 1);
  assert.equal(after.operationsEpoch, before.operationsEpoch + 1);
  assert.equal(after.keyStatus, "unverified");
  assert.equal(after.recentCommandId, null);
  assert.equal(after.storeId, STORE_A); // the store survives a key change
});

test("changing the Store UUID remounts Operations only and clears the recent command", () => {
  const before = busySession();
  const after = sessionReducer(before, { type: "storeChanged", storeId: STORE_B });
  assert.equal(after.operationsEpoch, before.operationsEpoch + 1);
  assert.equal(after.sessionEpoch, before.sessionEpoch); // other pages keep their data
  assert.equal(after.storeId, STORE_B);
  assert.equal(after.recentCommandId, null);
  assert.equal(after.keyStatus, "accepted"); // the store is context, not authorization
  // Re-entering the same store is not a context change.
  assert.equal(sessionReducer(after, { type: "storeChanged", storeId: STORE_B }), after);
});

test("disconnect clears everything and starts new epochs", () => {
  const before = busySession();
  const after = sessionReducer(before, { type: "disconnected" });
  assert.deepEqual(after, {
    sessionEpoch: before.sessionEpoch + 1,
    operationsEpoch: before.operationsEpoch + 1,
    keyStatus: "unset",
    storeId: "",
    recentCommandId: null,
  });
});

test("a stale auth result (older session epoch) never changes the current key status", () => {
  let state = run({ type: "keySet" }, { type: "keySet" }); // session epoch 2, key B
  assert.equal(state.sessionEpoch, 2);
  for (const outcome of ["accepted", "rejected"]) {
    const next = sessionReducer(state, { type: "authResult", sessionEpoch: 1, outcome });
    assert.equal(next, state);
    assert.equal(next.keyStatus, "unverified");
  }
  state = sessionReducer(state, { type: "authResult", sessionEpoch: 2, outcome: "accepted" });
  assert.equal(state.keyStatus, "accepted");
  state = sessionReducer(state, { type: "authResult", sessionEpoch: 2, outcome: "rejected" });
  assert.equal(state.keyStatus, "rejected");
  assert.equal(sessionReducer(state, { type: "authResult", sessionEpoch: 2, outcome: "other" }), state);
});

test("401 rejects, 403 and other failures say nothing, any success accepts", () => {
  assert.equal(authOutcome({ ok: false, status: 401 }), "rejected");
  assert.equal(authOutcome({ ok: false, status: 403 }), "other"); // authenticated, not permitted
  assert.equal(authOutcome({ ok: false, status: 503 }), "other");
  assert.equal(authOutcome({ ok: false, status: null }), "other");
  assert.equal(authOutcome({ ok: true, status: 200 }), "accepted");
  // A 403 after acceptance keeps the key accepted.
  const accepted = busySession();
  const after = sessionReducer(accepted, {
    type: "authResult", sessionEpoch: accepted.sessionEpoch, outcome: authOutcome({ ok: false, status: 403 }),
  });
  assert.equal(after.keyStatus, "accepted");
});

test("a stale ticket completion (older operations epoch) never fills the current recent command", () => {
  const state = run({ type: "keySet" }, { type: "storeChanged", storeId: STORE_A }); // operations epoch 2
  assert.equal(state.operationsEpoch, 2);
  const stale = sessionReducer(state, { type: "commandCreated", operationsEpoch: 1, commandId: COMMAND });
  assert.equal(stale, state);
  assert.equal(stale.recentCommandId, null);
  const current = sessionReducer(state, { type: "commandCreated", operationsEpoch: 2, commandId: COMMAND });
  assert.equal(current.recentCommandId, COMMAND);
});

test("after disconnect no auth result can mark a key accepted", () => {
  const state = run({ type: "keySet" }, { type: "disconnected" });
  const next = sessionReducer(state, { type: "authResult", sessionEpoch: state.sessionEpoch, outcome: "accepted" });
  assert.equal(next.keyStatus, "unset");
});

test("the session state never holds the key: only integer epochs", () => {
  const state = busySession();
  assert.deepEqual(Object.keys(state).sort(),
    ["keyStatus", "operationsEpoch", "recentCommandId", "sessionEpoch", "storeId"]);
  assert.ok(Number.isInteger(state.sessionEpoch) && Number.isInteger(state.operationsEpoch));
  const source = readFileSync(new URL("../components/shell/session.ts", import.meta.url), "utf8");
  const code = source.replace(/\/\/.*$/gm, "");
  assert.ok(!/apiKey|localStorage|sessionStorage|indexedDB|cookie/i.test(code));
});

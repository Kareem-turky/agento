// Session context of the Agento Product UI, as a pure reducer.
//
// There are two integer epochs:
//   - the SESSION epoch changes whenever the Product API key is set, replaced or
//     disconnected. Every authenticated page is keyed by it, so nothing produced under an
//     older key stays visible.
//   - the OPERATIONS epoch additionally changes when the Store UUID changes. Operations
//     results are keyed by it, so no result, form or pending ticket key of an older
//     store context survives.
// Every completion carries the epoch it was started in: a completion of an older epoch
// never updates the current session.
//
// The raw Product API key is deliberately NOT part of this state: the epochs identify a
// context without deriving anything from the key. The store is request context, never
// authorization; the Product API decides what a key may access.

export type KeyStatus = "unset" | "unverified" | "accepted" | "rejected";

export type SessionState = {
  sessionEpoch: number;
  operationsEpoch: number;
  keyStatus: KeyStatus;
  storeId: string;
  recentCommandId: string | null;
};

export type SessionAction =
  | { type: "keySet" }
  | { type: "storeChanged"; storeId: string }
  | { type: "disconnected" }
  | { type: "authResult"; sessionEpoch: number; outcome: "accepted" | "rejected" | "other" }
  | { type: "commandCreated"; operationsEpoch: number; commandId: string };

export const initialSession: SessionState = {
  sessionEpoch: 0,
  operationsEpoch: 0,
  keyStatus: "unset",
  storeId: "",
  recentCommandId: null,
};

export function sessionReducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case "keySet":
      // A new or replaced key: nothing is known about it yet, and nothing produced under
      // the previous key may remain.
      return {
        ...state,
        sessionEpoch: state.sessionEpoch + 1,
        operationsEpoch: state.operationsEpoch + 1,
        keyStatus: "unverified",
        recentCommandId: null,
      };
    case "storeChanged":
      if (action.storeId === state.storeId) return state;
      return { ...state, operationsEpoch: state.operationsEpoch + 1, storeId: action.storeId, recentCommandId: null };
    case "disconnected":
      return {
        sessionEpoch: state.sessionEpoch + 1,
        operationsEpoch: state.operationsEpoch + 1,
        keyStatus: "unset",
        storeId: "",
        recentCommandId: null,
      };
    case "authResult":
      // Only a request of the CURRENT key may say anything about the current key.
      if (action.sessionEpoch !== state.sessionEpoch || state.keyStatus === "unset") return state;
      if (action.outcome === "other" || action.outcome === state.keyStatus) return state;
      return { ...state, keyStatus: action.outcome };
    case "commandCreated":
      if (action.operationsEpoch !== state.operationsEpoch) return state; // an old-context write: ignored
      return { ...state, recentCommandId: action.commandId };
    default:
      return state;
  }
}

/** 401 rejects the key; 403 is authenticated-but-not-permitted; any success accepts it. */
export function authOutcome(result: { ok: boolean; status: number | null }): "accepted" | "rejected" | "other" {
  if (result.ok) return "accepted";
  return result.status === 401 ? "rejected" : "other";
}

// Session context of the Operations Console, as a pure reducer.
//
// The session EPOCH is an integer that changes whenever the Product context changes:
// the Product API key is set or replaced, the Store UUID changes, or the session is
// disconnected. Panels are keyed by it (so no result, form or pending idempotency key
// from an older context survives), and every panel callback carries the epoch it was
// started in: a completion from an older epoch never updates the current session.
//
// The raw Product API key is deliberately NOT part of this state: the epoch identifies a
// context without deriving anything from the key.

export type KeyStatus = "unset" | "unverified" | "accepted" | "rejected";

export type SessionState = {
  epoch: number;
  keyStatus: KeyStatus;
  storeId: string;
  recentCommandId: string | null;
};

export type SessionAction =
  | { type: "keySet" }
  | { type: "storeChanged"; storeId: string }
  | { type: "disconnected" }
  | { type: "authResult"; epoch: number; outcome: "accepted" | "rejected" | "other" }
  | { type: "commandCreated"; epoch: number; commandId: string };

export const initialSession: SessionState = {
  epoch: 0,
  keyStatus: "unset",
  storeId: "",
  recentCommandId: null,
};

export function sessionReducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case "keySet":
      // A new or replaced key: nothing is known about it yet, and nothing produced under
      // the previous key may remain.
      return { ...state, epoch: state.epoch + 1, keyStatus: "unverified", recentCommandId: null };
    case "storeChanged":
      if (action.storeId === state.storeId) return state;
      return { ...state, epoch: state.epoch + 1, storeId: action.storeId, recentCommandId: null };
    case "disconnected":
      return { epoch: state.epoch + 1, keyStatus: "unset", storeId: "", recentCommandId: null };
    case "authResult":
      // Only a request of the CURRENT context may say anything about the current key.
      if (action.epoch !== state.epoch || state.keyStatus === "unset") return state;
      if (action.outcome === "other") return state;
      return { ...state, keyStatus: action.outcome };
    case "commandCreated":
      if (action.epoch !== state.epoch) return state; // an old-context write: ignored here
      return { ...state, recentCommandId: action.commandId };
    default:
      return state;
  }
}

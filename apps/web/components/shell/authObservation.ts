// Two-phase auth observation for the one Product session.
//
// Every authenticated Product request is bound to the session epoch in which it STARTED:
//   begin(usedKey)  runs synchronously BEFORE any network I/O. If the request uses the
//                   session's current key it returns the CURRENT session epoch as an
//                   opaque, non-secret context token; otherwise null (not observed).
//   complete(token) runs after the exchange and reports the outcome for the CAPTURED
//                   epoch, never for whatever epoch is current at completion time.
// The session reducer ignores any completion of an older epoch, so a request started
// before a disconnect, a key replacement or a reconnect with the SAME key can never
// accept or reject the newer session.
//
// The token is only an integer epoch: it never contains or derives from the key, and this
// module keeps no reference to any key. It has no imports, so it can be tested directly.

export type AuthResult = { ok: boolean; status: number | null };

export type AuthObservation = {
  begin(apiKey: string): number | null;
  complete(sessionEpoch: number, result: AuthResult): void;
};

export function createAuthObservation(
  /** Reads the provider's current in-memory key and session epoch. */
  current: () => { apiKey: string | null; sessionEpoch: number },
  /** Reports a completion for the given (captured) session epoch. */
  report: (sessionEpoch: number, result: AuthResult) => void,
): AuthObservation {
  return {
    begin(apiKey) {
      const { apiKey: currentKey, sessionEpoch } = current();
      return currentKey !== null && apiKey === currentKey ? sessionEpoch : null;
    },
    complete(sessionEpoch, result) {
      report(sessionEpoch, result);
    },
  };
}

"use client";

// The ONE Product session of the Agento UI, mounted once in the root layout so it is
// shared across client-side navigation.
//
// The Product API key is MEMORY ONLY: it lives in this provider's React state for the
// lifetime of the browser tab's JavaScript. It is never written to localStorage,
// sessionStorage, IndexedDB, cookies, the URL, server props, HTML attributes, logs or
// error messages. A hard reload forgets it.
//
// API reachability (health) is tracked separately from authentication: an online API
// says nothing about whether a key is accepted.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { getHealth, observeAuthOutcomes } from "../../lib/product-api/client";
import type { ProductResult } from "../../lib/product-api/types";
import { createAuthObservation } from "./authObservation";
import { authOutcome, initialSession, sessionReducer, type KeyStatus } from "./session";

export type HealthState = "checking" | "online" | "unavailable";

export type ProductSession = {
  apiKey: string | null;
  keyStatus: KeyStatus;
  sessionEpoch: number;
  operationsEpoch: number;
  storeId: string;
  recentCommandId: string | null;
  health: HealthState;
  connect: (key: string) => void;
  disconnect: () => void;
  changeStore: (storeId: string) => void;
  checkHealth: () => void;
  /** Reports a completion started in `sessionEpoch`; older epochs are ignored. */
  reportResult: (sessionEpoch: number, result: ProductResult<unknown>) => void;
  /** Records a ticket command created in `operationsEpoch`; older epochs are ignored. */
  commandCreated: (operationsEpoch: number, commandId: string) => void;
};

const SessionContext = createContext<ProductSession | null>(null);

export function ProductSessionProvider({ children }: { children: ReactNode }) {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [session, dispatch] = useReducer(sessionReducer, initialSession);
  const [health, setHealth] = useState<HealthState>("checking");
  // Mirrors read by the auth observer's begin(), which runs outside React rendering. A
  // request only begins after the render that follows connect/disconnect, so these match.
  const keyRef = useRef<string | null>(null);
  const epochRef = useRef(session.sessionEpoch);
  epochRef.current = session.sessionEpoch;

  const checkHealth = useCallback(async () => {
    setHealth("checking");
    const result = await getHealth();
    // Reachability only: it says nothing about whether a key is accepted.
    setHealth(result.ok && result.data.status === "ok" ? "online" : "unavailable");
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  // Any page's authenticated request feeds the one session: 401 marks the key rejected,
  // 403 changes nothing (authenticated, not permitted), a success marks it accepted.
  // Each request is bound to the session epoch captured when it BEGAN (and only when it
  // used the then-current key); its completion is dispatched for that captured epoch, so
  // the reducer drops it once the session has moved on, even after a same-key reconnect.
  useEffect(
    () =>
      observeAuthOutcomes(
        createAuthObservation(
          () => ({ apiKey: keyRef.current, sessionEpoch: epochRef.current }),
          (sessionEpoch, result) => dispatch({ type: "authResult", sessionEpoch, outcome: authOutcome(result) }),
        ),
      ),
    [],
  );

  const connect = useCallback((key: string) => {
    keyRef.current = key;
    setApiKey(key);
    dispatch({ type: "keySet" });
  }, []);

  const disconnect = useCallback(() => {
    keyRef.current = null;
    setApiKey(null);
    dispatch({ type: "disconnected" });
  }, []);

  const changeStore = useCallback((storeId: string) => {
    dispatch({ type: "storeChanged", storeId });
  }, []);

  const reportResult = useCallback((sessionEpoch: number, result: ProductResult<unknown>) => {
    dispatch({ type: "authResult", sessionEpoch, outcome: authOutcome(result) });
  }, []);

  const commandCreated = useCallback((operationsEpoch: number, commandId: string) => {
    dispatch({ type: "commandCreated", operationsEpoch, commandId });
  }, []);

  const value = useMemo<ProductSession>(
    () => ({
      apiKey,
      ...session,
      health,
      connect,
      disconnect,
      changeStore,
      checkHealth: () => void checkHealth(),
      reportResult,
      commandCreated,
    }),
    [apiKey, session, health, connect, disconnect, changeStore, checkHealth, reportResult, commandCreated],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useProductSession(): ProductSession {
  const value = useContext(SessionContext);
  if (value === null) throw new Error("useProductSession must be used inside ProductSessionProvider");
  return value;
}

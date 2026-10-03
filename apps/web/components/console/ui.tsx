// Operations-specific helpers. The generic primitives live in components/ui and are
// re-exported here so existing imports keep working.
import type { ProductErrorKind } from "../../lib/product-api/types";

export { Badge, Card, ErrorNotice, KeyValue, Metric, Mono, Timestamp, type Tone } from "../ui/primitives";

const SERVICE_UNAVAILABLE: Record<string, string> = {
  analysis: "Operations service unavailable",
  report: "Daily operations report unavailable",
  ticket: "Operations ticket service unavailable",
  command: "Ticket command status unavailable",
};

/** Fixed, concise messages per failure class; never raw bodies, stacks or origins. */
export function errorMessage(kind: ProductErrorKind, context: keyof typeof SERVICE_UNAVAILABLE): string {
  switch (kind) {
    case "unauthenticated":
      return "Product API key not accepted";
    case "forbidden":
      return "Store access denied";
    case "invalid":
      return "Invalid request";
    case "not_found":
      return context === "command" ? "Ticket command not found" : "Not found";
    case "conflict":
      if (context === "analysis") return "Operations Agent is disabled (Settings → Agents)";
      return "Idempotency conflict: this request key was already used for a different ticket. Reset the ticket form.";
    case "too_large":
      return "Request too large";
    case "service_unavailable":
      return SERVICE_UNAVAILABLE[context];
    default:
      return "Product API unavailable";
  }
}

export function NeedsSession() {
  return (
    <div className="notice notice--neutral">
      Connect to the Product API and set a Store UUID (under Store) first.
    </div>
  );
}

// Small presentational helpers. They only label backend values; they never decide
// business outcomes.
import type { ReactNode } from "react";
import type { ProductErrorKind } from "../../lib/product-api/types";

export type Tone = "success" | "pending" | "attention" | "danger" | "neutral";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  // The text always carries the meaning; colour is only reinforcement.
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function Card({ title, subtitle, actions, children }: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card" aria-label={title}>
      <header className="card__header">
        <div>
          <h2 className="card__title">{title}</h2>
          {subtitle ? <p className="card__subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="card__actions">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="metric">
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
      {hint ? <span className="metric__hint">{hint}</span> : null}
    </div>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([key, value]) => (
        <div className="kv__row" key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <code className="mono">{children}</code>;
}

export function Timestamp({ value }: { value: string }) {
  const parsed = new Date(value);
  const local = Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  return <time dateTime={value} title={value}>{local}</time>;
}

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

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice notice--danger" role="alert">
      <strong>Request failed.</strong> {message}
    </div>
  );
}

export function NeedsSession() {
  return (
    <div className="notice notice--neutral">
      Set a Product API key and a Store UUID in <strong>Session</strong> first.
    </div>
  );
}

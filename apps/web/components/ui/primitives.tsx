// Shared presentational primitives of the Agento Product UI. They only label backend
// values; they never decide business outcomes, and every status carries text (colour is
// only reinforcement).
import type { ReactNode } from "react";

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

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice notice--danger" role="alert">
      <strong>Request failed.</strong> {message}
    </div>
  );
}

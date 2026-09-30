"use client";

import { useRef, useState, type FormEvent } from "react";
import { getDailyReport, looksLikeUuid } from "../../lib/product-api/client";
import type { DailyOperationsReportResponse, ProductResult } from "../../lib/product-api/types";
import { Badge, Card, ErrorNotice, KeyValue, Metric, Mono, NeedsSession, Timestamp, errorMessage } from "./ui";

// Presentation labels for the Product's canonical codes. The report itself (metrics,
// findings, their order, severity and recommended actions) is shown as returned.
const label = (code: string) => code.replaceAll("_", " ");

export function ReportPanel({ epoch, apiKey, storeId, onAuthResult }: {
  apiKey: string | null;
  storeId: string;
  /** The session epoch this panel instance belongs to. */
  epoch: number;
  onAuthResult: (epoch: number, result: ProductResult<unknown>) => void;
}) {
  const [businessDate, setBusinessDate] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [data, setData] = useState<DailyOperationsReportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ready = apiKey !== null && looksLikeUuid(storeId);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (apiKey === null || !looksLikeUuid(storeId) || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    // A blank date is omitted: the Product decides the store-local "today".
    const response = await getDailyReport(apiKey, storeId, businessDate || undefined);
    busyRef.current = false;
    setBusy(false);
    onAuthResult(epoch, response);
    if (response.ok) {
      setData(response.data);
    } else {
      setData(null);
      setError(errorMessage(response.error, "report"));
    }
  }

  const report = data?.report;
  return (
    <Card
      title="Daily operations report"
      subtitle="Deterministic, computed by the Product backend. No model is involved."
      actions={<Badge tone="neutral">Read-only</Badge>}
    >
      {!ready ? <NeedsSession /> : null}
      <form className="form form--inline" onSubmit={submit}>
        <p className="form__context">Store <Mono>{storeId || "not set"}</Mono></p>
        <label className="field">
          <span className="field__label">Business date (optional)</span>
          <input type="date" name="business_date" value={businessDate}
                 onChange={(event) => setBusinessDate(event.target.value)} />
          <span className="field__hint">Leave blank for the store’s own current business day.</span>
        </label>
        <div className="form__actions">
          <button type="submit" className="button" disabled={!ready || busy}>
            {busy ? "Loading…" : "Load daily report"}
          </button>
        </div>
      </form>

      <div aria-live="polite" aria-busy={busy}>
        {busy ? <p className="loading">Loading the daily report…</p> : null}
        {error ? <ErrorNotice message={error} /> : null}
      </div>

      {data && report ? (
        <div className="report">
          <KeyValue items={[
            ["Business date", <Mono key="d">{report.business_date}</Mono>],
            ["Timezone", <Mono key="t">{report.timezone}</Mono>],
            ["Generated at", <Timestamp key="g" value={report.generated_at} />],
            ["Request ID", <Mono key="r">{data.request_id}</Mono>],
          ]} />

          <div className="metrics">
            <Metric label="Orders created" value={report.metrics.orders_created} />
            <Metric label="Shipments shipped" value={report.metrics.shipments_shipped} />
            <Metric label="Affected orders" value={report.metrics.affected_orders}
                    hint="Distinct orders with at least one finding" />
          </div>

          <div className="split">
            <StatusCounts caption="Orders by status" counts={report.metrics.order_status_counts} />
            <StatusCounts caption="Shipments by status" counts={report.metrics.shipment_status_counts} />
          </div>

          <h3 className="section-title">Coverage</h3>
          <ul className="coverage">
            <li><Badge tone="success">Included</Badge> Orders: <Mono>{report.coverage.orders}</Mono></li>
            <li><Badge tone="success">Included</Badge> Shipments: <Mono>{report.coverage.shipments}</Mono></li>
            <li>
              {report.coverage.inventory === "not_included"
                ? <Badge tone="attention">Not analyzed</Badge> : <Badge tone="neutral">See value</Badge>}{" "}
              Inventory: <Mono>{report.coverage.inventory}</Mono>{" "}
              (reason: <Mono>{report.coverage.inventory_reason}</Mono>)
              {report.coverage.inventory === "not_included"
                ? <p className="coverage__note">Inventory was not part of this report.</p> : null}
            </li>
          </ul>

          <h3 className="section-title">
            Findings <span className="section-title__count">{report.findings_total} total</span>
          </h3>
          {report.findings_truncated ? (
            <div className="notice notice--attention" role="status">
              Showing the first {report.findings.length} of {report.findings_total} findings. The
              returned list is capped by the Product; it is not complete.
            </div>
          ) : null}
          {report.findings.length === 0 ? (
            <p className="empty">No findings were returned for this business day.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="visually-hidden">Findings, in the order returned by the Product</caption>
                <thead>
                  <tr>
                    <th scope="col">Severity</th>
                    <th scope="col">Code</th>
                    <th scope="col">Entity</th>
                    <th scope="col">Entity ID</th>
                    <th scope="col">Order ID</th>
                    <th scope="col">Canonical status</th>
                    <th scope="col">Recommended action</th>
                  </tr>
                </thead>
                <tbody>
                  {report.findings.map((finding, index) => (
                    <tr key={`${index}-${finding.entity_id}`}>
                      <td>
                        <Badge tone={finding.severity === "critical" ? "danger" : "attention"}>
                          {finding.severity}
                        </Badge>
                      </td>
                      <td className="nowrap"><Mono>{finding.code}</Mono></td>
                      <td>{finding.entity_type}</td>
                      <td><Mono>{finding.entity_id}</Mono></td>
                      <td><Mono>{finding.order_id}</Mono></td>
                      <td><Mono>{finding.canonical_status}</Mono></td>
                      <td>{label(finding.recommended_action)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}
    </Card>
  );
}

function StatusCounts({ caption, counts }: { caption: string; counts: { status: string; count: number }[] }) {
  return (
    <table className="table table--compact">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Status</th>
          <th scope="col" className="num">Count</th>
        </tr>
      </thead>
      <tbody>
        {counts.map((row) => (
          <tr key={row.status}>
            <td><Mono>{row.status}</Mono></td>
            <td className="num">{row.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

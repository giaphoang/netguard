import React, { useEffect, useState } from "react";
import { Activity, IncidentReview, incidentSteps } from "./ChatDetails";
import type { Run } from "./Incidents";
import { RecordTime } from "./ImpactEvidence";
import { duration, outcomeLabel } from "./impactData";

export type IncidentReference = { source: "incident" | "investigation"; id: string };

type RecordedIncident = IncidentReference & {
  title: string;
  status: string;
  outcome: string | null;
  observed: number | null;
  devices: string[];
  summary: string;
};

export type IncidentHistory = {
  records: RecordedIncident[];
  total: number;
  inspection: boolean;
};

type JournalDetail =
  | { source: "incident"; data: Run }
  | { source: "investigation"; data: {
    incident_id?: string;
    events?: { time: number; stage: string; tool: string }[];
  } };

const statusLabels: Record<string, string> = {
  resolved: "Resolved",
  handoff: "Needs human review",
  proposed: "Awaiting approval",
};

const timingLabels: Record<string, string> = {
  detection_s: "Detection",
  verified_failover_s: "Verified failover",
  traffic_recovery_s: "Traffic recovery",
  closure_s: "Verified closure",
};

function recordPath({ id, source }: IncidentReference) {
  return `/api/${source === "incident" ? "incidents" : "investigations"}/${encodeURIComponent(id)}`;
}

function RecordedDetails({ id, source }: IncidentReference) {
  const [record, setRecord] = useState<JournalDetail>();
  const [error, setError] = useState(false);
  const [attempt, retry] = useState(0);
  const [showLog, setShowLog] = useState(false);
  const url = recordPath({ id, source });
  useEffect(() => {
    const controller = new AbortController();
    fetch(url, { cache: "no-store", signal: controller.signal })
      .then(response => {
        if (!response.ok) throw Error("Record unavailable");
        return response.json();
      })
      .then(data => { if (!controller.signal.aborted) setRecord({ source, data }); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [url, source, attempt]);

  const readings = record?.source === "incident" ? Object.entries(timingLabels)
    .filter(([metric]) => typeof record.data.metrics?.[metric] === "number")
    .map(([metric, label]) => [label, duration(record.data.metrics[metric], 2)]) : [];
  const steps = record?.source === "incident" ? incidentSteps(record.data.events ?? []) : (
    record?.data.events?.map(event => ({ label: event.stage, detail: event.tool, time: event.time })) ?? []
  );
  return (
    <div className="chat-record-details">
      {error && (
        <div className="chat-actions">
          <p role="status">The saved log could not be loaded.</p>
          <button type="button" onClick={() => { setError(false); retry(attempt + 1); }}>Retry</button>
        </div>
      )}
      {!error && !record && <p role="status">Loading journal evidence…</p>}
      {!!readings.length && (
        <dl className="chat-record-times">
          {readings.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>
      )}
      <Activity label={source === "incident" ? "Incident timeline" : "Investigation timeline"} steps={steps} />
      {record?.source === "incident" && record.data.review && <IncidentReview review={record.data.review} />}
      {record?.source === "investigation" && record.data.incident_id && (
        <a href={recordPath({ id: record.data.incident_id, source: "incident" })} target="_blank" rel="noreferrer">
          Open linked controller incident
        </a>
      )}
      {record && (
        <details className="chat-evidence" onToggle={event => setShowLog(event.currentTarget.open)}>
          <summary>Full journal log</summary>
          {showLog && <pre tabIndex={0}>{JSON.stringify(record.data, null, 2)}</pre>}
        </details>
      )}
      <a href={url} target="_blank" rel="noreferrer">Open original journal record</a>
    </div>
  );
}

export function ChatIncidents({ history, busy, inspect }: {
  history: IncidentHistory;
  busy: boolean;
  inspect: (record: IncidentReference) => void;
}) {
  if (!history.records.length) return null;
  return (
    <section className="chat-incident-results" aria-label={history.inspection ? "Incident inspection" : "Incident search results"}>
      {history.records.map(record => (
        <div className="chat-record" key={`${record.source}/${record.id}`}>
          <div className="chat-record-heading">
            <div>
              <small>{record.source === "incident" ? "Controller incident" : "Investigation"} · {record.id.slice(0, 8)}</small>
              <strong>{record.title}</strong>
            </div>
            <span className="chat-record-status">{statusLabels[record.status] ?? outcomeLabel(record.status)}</span>
          </div>
          <small className="chat-record-meta">
            {record.devices.length > 0 && <span>{record.devices.join(" / ")}</span>}
            <RecordTime at={record.observed ?? undefined} />
          </small>
          {record.outcome && record.outcome !== record.status && <p>{outcomeLabel(record.outcome)}</p>}
          {record.summary && <p>{record.summary}</p>}
          {history.inspection
            ? <RecordedDetails id={record.id} source={record.source} />
            : <button type="button" disabled={busy} aria-label={`Inspect ${record.title} ${record.id.slice(0, 8)}`}
                onClick={() => inspect({ id: record.id, source: record.source })}>
                Inspect
              </button>}
        </div>
      ))}
    </section>
  );
}

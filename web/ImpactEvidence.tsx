import React, { useEffect, useRef, useState } from "react";
import {
  duration, modeLabel, outcomeLabel, recordDate, sampleLabel, scenarioLabels, shortRecordId,
  type EvaluationCase, type EvidenceRef, type EvidenceSelection,
} from "./impactData";

type LoggedCase = EvaluationCase & {
  escalation_correct?: boolean;
  tool_calls?: { started?: number; completed?: number }[];
};

type LoggedRecord = {
  observed?: number;
  started?: number;
  events?: { time: number; phase: string; reason?: string }[];
  text?: string;
  facts?: { results?: LoggedCase[] };
};

export function RecordTime({ at }: { at?: number }) {
  const date = recordDate(at);
  if (!date) return null;
  return <time dateTime={date.toISOString()}>{date.toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "numeric",
    minute: "2-digit", second: "2-digit", timeZoneName: "short",
  })}</time>;
}

function Log({ label, value }: { label: React.ReactNode; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="impact-log" onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>{label}</summary>
      {open && <pre tabIndex={0}>{JSON.stringify(value, null, 2)}</pre>}
    </details>
  );
}

function EvaluationCases({
  cases, selection,
}: { cases: LoggedCase[]; selection: NonNullable<EvidenceSelection["evaluation"]> }) {
  const visible = cases.filter(entry =>
    (!selection.version || entry.version === selection.version) &&
    (!selection.kinds || selection.kinds.includes(entry.kind))
  );
  return (
    <div className="impact-log-list">
      <h3>Case results · {sampleLabel(visible.length, "case")}</h3>
      {visible.map((entry, i) => {
        const passed = selection.score ? entry[selection.score] : entry.passed ?? entry.diagnosis_correct;
        const firstCall = entry.tool_calls?.find(call => call.started || call.completed);
        return <Log key={`${entry.version}:${entry.id}:${i}`} value={entry} label={
          <>
            <span className="impact-case-label">{entry.id}</span>
            <span className="impact-log-meta">
              {entry.version} · {passed === undefined ? "Recorded" : passed ? "Passed" : "Review failure"}
              {entry.latency_s !== undefined && ` · ${duration(entry.latency_s, 2)}`}
            </span>
            <RecordTime at={firstCall?.started ?? firstCall?.completed} />
            {entry.error && <span className="impact-log-meta">{entry.error}</span>}
          </>
        } />;
      })}
    </div>
  );
}

function EvidenceDetail({
  reference, evaluation,
}: { reference: EvidenceRef; evaluation?: EvidenceSelection["evaluation"] }) {
  const [record, setRecord] = useState<LoggedRecord>();
  const [error, setError] = useState(false);
  const [attempt, retry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch(reference.url, { cache: "no-store", signal: controller.signal })
      .then(response => {
        if (!response.ok) throw Error("Evidence unavailable");
        return response.json() as Promise<LoggedRecord>;
      })
      .then(value => { if (!controller.signal.aborted) setRecord(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [reference.url, attempt]);

  return (
    <div className="impact-evidence-detail">
      <div className="impact-evidence-heading">
        <h3>{evaluation ? "Evaluation record" : `Record ${shortRecordId(reference.url)}`}</h3>
        <a href={reference.url} target="_blank" rel="noreferrer">Open original record ↗</a>
      </div>
      {error && <div className="impact-evidence-error">
        <p role="status">The recorded log could not be loaded.</p>
        <button type="button" onClick={() => { setError(false); retry(attempt + 1); }}>Retry</button>
      </div>}
      {!error && !record && <p role="status">Loading recorded log…</p>}
      {record && (
        <>
          <RecordTime at={record.observed ?? record.started ?? reference.at} />
          {record.text && <p>{record.text}</p>}
          {!!record.events?.length && <ol className="impact-event-list">
            {record.events.map((event, i) => <li key={i}>
              <strong>{outcomeLabel(event.phase)}</strong>
              <RecordTime at={event.time} />
              {event.reason && <p>{event.reason}</p>}
            </li>)}
          </ol>}
          {evaluation && record.facts?.results && <EvaluationCases cases={record.facts.results} selection={evaluation} />}
          <Log label="Full recorded log" value={record} />
        </>
      )}
    </div>
  );
}

export function ImpactEvidence({ selection, close }: { selection: EvidenceSelection; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState(0);
  const active = selection.records[selected];
  useEffect(() => { dialog.current?.showModal(); }, []);
  return (
    <dialog
      className="impact-evidence"
      aria-labelledby="impact-evidence-title"
      ref={dialog}
      onClose={close}
      onClick={event => { if (event.target === event.currentTarget) dialog.current?.close(); }}
    >
      <div className="impact-evidence-head">
        <div>
          <h2 id="impact-evidence-title">{selection.title}</h2>
          {selection.description && <p>{selection.description}</p>}
        </div>
        <button type="button" autoFocus aria-label="Close evidence" onClick={() => dialog.current?.close()}>×</button>
      </div>
      <div className="impact-evidence-body">
        {!active && <p>No qualifying records are available.</p>}
        {!!active && !selection.evaluation && (
          <div className="impact-run-picker" aria-label="Evidence records">
            <h3>{sampleLabel(selection.records.length, "record")}</h3>
            {selection.records.map((entry, i) => (
              <button type="button" key={entry.url} aria-pressed={i === selected} onClick={() => setSelected(i)}>
                <span>
                  <strong>{entry.label || (entry.task && scenarioLabels[entry.task]) || entry.task || "Record"}</strong>
                  <small>{[shortRecordId(entry.url), entry.mode && modeLabel[entry.mode], entry.outcome && outcomeLabel(entry.outcome)].filter(Boolean).join(" · ")}</small>
                  <RecordTime at={entry.at} />
                </span>
                {entry.seconds != null && <b>{duration(entry.seconds, 2)}</b>}
              </button>
            ))}
          </div>
        )}
        {active && <EvidenceDetail key={active.url} reference={active} evaluation={selection.evaluation} />}
      </div>
    </dialog>
  );
}

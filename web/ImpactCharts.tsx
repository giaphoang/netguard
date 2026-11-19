import React, { useState } from "react";
import {
  duration, recordDate, sampleLabel, shortRecordId,
  type Evaluation, type EvaluationScore, type EvidenceSelection,
} from "./impactData";

export function ImpactBar({
  label, detail, value, maximum, display, tone, select,
}: {
  label: string;
  detail?: string;
  value: number;
  maximum: number;
  display: string;
  tone?: "manual" | "timer";
  select: () => void;
}) {
  const width = maximum > 0 ? Math.max(0, Math.min(100, 100 * value / maximum)) : 0;
  return (
    <button type="button" className="impact-bar" aria-haspopup="dialog" onClick={select}>
      <span className="impact-bar-label">
        <span>{label} {detail && <small>{detail}</small>}</span>
        <strong>{display}</strong>
      </span>
      <span className="impact-bar-track" aria-hidden="true">
        <span className="impact-bar-fill" data-tone={tone} style={{ width: width + "%" }} />
      </span>
    </button>
  );
}

const scores: { key: EvaluationScore; label: string }[] = [
  { key: "diagnosis_correct", label: "Correct diagnosis" },
  { key: "escalation_correct", label: "Correct disposition" },
];

export function ImpactEvaluations({
  evaluations, busy, evaluating, status, evaluate, onEvidence,
}: {
  evaluations: Evaluation[];
  busy: boolean;
  evaluating: boolean;
  status?: string;
  evaluate: () => void;
  onEvidence: (selection: EvidenceSelection) => void;
}) {
  const [selectedId, selectId] = useState("");
  const report = evaluations.find(entry => entry.id === selectedId) ?? evaluations[0];
  const versions = Object.entries(report?.summary ?? {});
  const unsupported = versions.some(([, result]) => result.unsupported_citations !== undefined);

  function openReport(title: string, description?: string, evaluation: EvidenceSelection["evaluation"] = {}) {
    if (report) onEvidence({
      title, description, evaluation,
      records: [{ url: report.url, at: report.observed }],
    });
  }

  return (
    <section className="impact-panel impact-evaluations" aria-labelledby="impact-evaluations-title">
      <div className="impact-panel-head">
        <div>
          <h2 id="impact-evaluations-title">AI quality by workflow version</h2>
          <p>Compare versions on the labeled cases from a single evaluation run.</p>
        </div>
        <button type="button" className="impact-action" disabled={busy} onClick={evaluate}>
          {evaluating ? "Evaluating recorded cases…" : "Run read-only evaluation"}
        </button>
      </div>
      {status && <p className="impact-status" role="status">{status}</p>}
      {!report && <p className="impact-empty">No versioned evaluations recorded yet.</p>}
      {report && (
        <>
          <div className="impact-evaluation-controls">
            <label>Evaluation run
              <select value={report.id} onChange={event => selectId(event.target.value)}>
                {evaluations.map(entry => (
                  <option key={entry.id} value={entry.id}>
                    {recordDate(entry.observed)?.toLocaleString()} · {shortRecordId(entry.url)}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="impact-action" aria-haspopup="dialog"
              onClick={() => openReport("Evaluation cases", "Recorded decisions, failures and tool results")}>
              View all cases <span aria-hidden="true">→</span>
            </button>
          </div>
          {versions.length > 0 ? (
            <>
              <div className="impact-score-charts">
                {scores.map(score => (
                  <section key={score.key} aria-label={score.label}>
                    <h3>{score.label}</h3>
                    {versions.map(([version, result]) => (
                      <ImpactBar key={version} label={version}
                        value={result[score.key]} maximum={result.n} display={`${result[score.key]} / ${result.n}`}
                        select={() => openReport(
                          `${score.label} · ${version}`,
                          `${result[score.key]} of ${sampleLabel(result.n, "case")}`,
                          { kinds: ["diagnosis"], version, score: score.key },
                        )}
                      />
                    ))}
                  </section>
                ))}
              </div>
              <div className="impact-table-scroll" tabIndex={0} aria-label="Additional evaluation metrics" role="region">
                <table className="impact-table">
                  <thead><tr>
                    <th scope="col">Workflow</th>
                    <th scope="col">Unsafe proposals</th>
                    {unsupported && <th scope="col">Unsupported citations</th>}
                    <th scope="col">Mean workflow latency</th>
                    <th scope="col">Model calls</th>
                  </tr></thead>
                  <tbody>
                    {versions.map(([version, result]) => (
                      <tr key={version}>
                        <th scope="row">{version}</th>
                        <td>{result.unsafe_proposals}</td>
                        {unsupported && <td>{result.unsupported_citations ?? "—"}</td>}
                        <td>{duration(result.mean_latency_s, 2)}</td>
                        <td>{result.model_calls}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : <p className="impact-empty">This evaluation has no version-level diagnosis scores.</p>}
          <div className="impact-checks">
            {[
              { title: "Intent context", kinds: ["intent"] },
              { title: "Policy and detector checks", kinds: ["policy", "detector"] },
            ].map(check => {
              const cases = report.results.filter(entry => check.kinds.includes(entry.kind));
              if (!cases.length) return null;
              const passed = cases.filter(entry => entry.passed).length;
              return <button key={check.title} type="button" aria-haspopup="dialog"
                onClick={() => openReport(check.title, `${passed} / ${cases.length} passed`, { kinds: check.kinds })}>
                <span><strong>{check.title}</strong>{check.kinds[0] === "intent" && <small>{cases[0].version}</small>}</span>
                <b>{passed} / {cases.length}<small>passed</small></b>
              </button>;
            })}
          </div>
          <p className="impact-note">{report.limits}</p>
        </>
      )}
    </section>
  );
}

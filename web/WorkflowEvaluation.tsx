import React from "react";

type Summary = {
  n: number;
  diagnosis_correct: number;
  handoff_required: number;
  handoff_correct: number;
  unsafe_proposals: number;
  unsafe_accepted: number;
  guard_blocks: number;
  median_latency_s: number;
  model_calls: number;
  mean_context_bytes: number;
  errors: number;
};
type Decision = { diagnosis: string; component: string; action: string };
type Case = {
  id: string;
  origin: string;
  version: string;
  expected: Decision;
  decision?: Decision;
  rejected_decision?: Decision;
  error?: string;
  diagnosis_correct: boolean;
  escalation_correct: boolean;
  latency_s: number;
};
export type EvaluationReport = {
  summary: Record<string, Summary>;
  recommendation: string;
  record_id?: string;
  limits: string;
  results: Case[];
};

function decisionText(decision: Decision) {
  return [decision.diagnosis, decision.component, decision.action.replace("_", " ")].join(" · ");
}

export function WorkflowEvaluation({ report }: { report: EvaluationReport }) {
  const profiles = Object.entries(report.summary);
  const cases = Array.from(new Set(report.results.map(row => row.id)));
  return (
    <>
      <section className="architecture-card">
        <h3>Paired workflow comparison</h3>
        <p>{cases.length} labeled cases per workflow · Identical source observations</p>
        <div className="architecture-table-scroll" tabIndex={0} aria-label="Workflow evaluation metrics">
          <table className="architecture-table">
            <thead><tr><th scope="col">Metric</th>{profiles.map(([name]) => <th key={name} scope="col">{name}</th>)}</tr></thead>
            <tbody>
              {([
                ["Correct class and component", (s: Summary) => s.diagnosis_correct + " / " + s.n],
                ["Correct human handoffs", (s: Summary) => s.handoff_correct + " / " + s.handoff_required],
                ["Incorrect repair suggestions", (s: Summary) => s.unsafe_proposals],
                ["Incorrect repairs passing assessment", (s: Summary) => s.unsafe_accepted],
                ["Policy blocks", (s: Summary) => s.guard_blocks],
                ["Workflow errors", (s: Summary) => s.errors],
                ["Median case latency", (s: Summary) => s.median_latency_s.toFixed(2) + " s"],
                ["Model calls", (s: Summary) => s.model_calls],
                ["Mean context size", (s: Summary) => Math.round(s.mean_context_bytes) + " bytes"],
              ] as const).map(([label, value]) => (
                <tr key={label}><th scope="row">{label}</th>
                  {profiles.map(([name, summary]) => <td key={name}>{value(summary)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>{report.recommendation}</p>
        <p>{report.limits}</p>
        {report.record_id && <a href={"/api/investigations/" + report.record_id} target="_blank" rel="noreferrer">AI Impact evidence ↗</a>}
      </section>
      <section className="architecture-card">
        <h3>Case outcomes</h3>
        <div className="architecture-tools">
          {cases.map(id => {
            const pair = report.results.filter(row => row.id === id);
            const matching = pair.filter(row => row.diagnosis_correct && row.escalation_correct).length;
            return <details key={id}>
              <summary>{id} <small>· {matching}/{pair.length} matched</small></summary>
              <p>{pair[0].origin}</p>
              <p>Expected {decisionText(pair[0].expected)}</p>
              <ul>{pair.map(row => (
                <li key={row.version}>
                  <strong>{row.version}</strong>
                  <p>{row.decision ? decisionText(row.decision) : row.error || "Decision unavailable"}
                    {row.rejected_decision && " · Rejected " + decisionText(row.rejected_decision)}
                    {" · " + row.latency_s.toFixed(2) + " s"}
                  </p>
                </li>
              ))}</ul>
            </details>;
          })}
        </div>
      </section>
    </>
  );
}

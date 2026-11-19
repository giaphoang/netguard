import React from "react";

type Row = {
  metric: string;
  scenario: string;
  manual: number | null;
  assisted: number | null;
  n: number[];
};
export type Impact = {
  evaluations?: {id:string;observed:number;summary:Record<string,{n:number;diagnosis_correct:number;escalation_correct:number;unsafe_proposals:number;mean_latency_s:number;model_calls:number}>;results:{id:string;kind:string;version:string;passed?:boolean;diagnosis_correct?:boolean;error?:string;latency_s?:number}[];limits:string;url:string}[];
  outcomes?: Record<string,number>;
  kpis?: {label:string;value:string;evidence:{url:string;label:string}[]}[];
  investigations?:{kind:string;text:string;url:string}[];
  saved: number | null;
  correct: number;
  evaluated: number;
  verified: number;
  attempted: number;
  rejected: number;
  escalated: number;
  required_handoffs: number;
  correct_handoffs: number;
  rows: Row[];
  recent: {
    task: string;
    mode: string;
    outcome: string;
    seconds: number | null;
  }[];
};
export const duration = (n: number | null) => (n === null ? "—" : n.toFixed(1) + " s");

export function WorkflowImpact({ impact, busy, live, evaluating, status, evaluate }: {
  impact?: Impact;
  busy: boolean;
  live: boolean;
  evaluating: boolean;
  status?: string;
  evaluate: () => void;
}) {
  return (
    <details className="drawer impact" open>
      <summary>Workflow impact</summary>
      {impact ? (
        <>
          <div className="impact-grid">
            {[
              [
                impact.correct + "/" + impact.evaluated,
                "Correct AI diagnoses",
              ],
              [
                impact.verified + "/" + impact.attempted,
                "Verified executions",
              ],
              [impact.rejected, "Repair proposals withheld"],
              [impact.escalated, "AI cases escalated"],
              [duration(impact.saved), "Time saved · matched closure"],
              [
                impact.correct_handoffs + "/" + impact.required_handoffs,
                "Required handoffs completed",
              ],
            ].map(([v, l]) => (
              <div key={l}>
                <b>{v}</b>
                <small>{l}</small>
              </div>
            ))}
          </div>
          <h3>Manual and AI-assisted durations</h3>
          <p>
            Matched by fault, topology, duration and approval policy. Medians
            include inference and operator wait. Missing observations appear
            as a dash.
          </p>
          {impact.rows.length === 0 && <p>No measured timing pairs yet.</p>}
          {impact.rows.map((r, i) => (
            <div key={i}>
              <strong>{r.metric}</strong> · {r.scenario}
              {(["manual", "assisted"] as const).map((key, j) => (
                <div key={key}>
                  <small>
                    {key === "manual" ? "Manual" : "AI assisted"} ·{" "}
                    {duration(r[key])} · n={r.n[j]}
                  </small>
                  {r[key] !== null && (
                    <div
                      className={"bar " + (j ? "ai" : "")}
                      style={{
                        width:
                          (100 * r[key]!) /
                            Math.max(
                              ...impact.rows.flatMap((row) => [
                                row.manual || 0,
                                row.assisted || 0,
                              ]),
                              1,
                            ) +
                          "%",
                      }}
                    />
                  )}
                </div>
              ))}
            </div>
          ))}
          {impact.rejected > 0 && (
            <p>
              Suggested evaluation · Replay the withheld repair proposals
              against the current prompt. Review the results before approving
              a prompt or policy change.
            </p>
          )}
          <h3>Versioned reliability evaluations</h3>
          <button disabled={busy || !live} onClick={evaluate}>
            {evaluating ? "Evaluating recorded cases…" : "Run read-only evaluation"}
          </button>
          {status && <p role="status">{status}</p>}
          {!impact.evaluations?.length && <p>No versioned evaluation recorded yet.</p>}
          {impact.evaluations?.map(e=><section className="evaluation-result" key={e.id}>
            <p><a href={e.url} target="_blank" rel="noreferrer">Experiment · {new Date(e.observed*1000).toLocaleString()}</a></p>
            {Object.keys(e.summary).length>0&&<div className="table-scroll"><table><thead><tr><th>Workflow</th><th>Correct diagnosis</th><th>Correct disposition</th><th>Unsafe proposals</th><th>Mean inference workflow</th><th>Model calls</th></tr></thead><tbody>{Object.entries(e.summary).map(([version,m])=><tr key={version}><td>{version}</td><td>{m.diagnosis_correct}/{m.n}</td><td>{m.escalation_correct}/{m.n}</td><td>{m.unsafe_proposals}</td><td>{m.mean_latency_s.toFixed(2)} s</td><td>{m.model_calls}</td></tr>)}</tbody></table></div>}
            <p>Intent context {e.results.find(r=>r.kind==="intent")?.version || "historical"} · {e.results.filter(r=>r.kind==="intent"&&r.passed).length}/{e.results.filter(r=>r.kind==="intent").length} passed. Policy and detector checks · {e.results.filter(r=>["policy","detector"].includes(r.kind)&&r.passed).length}/{e.results.filter(r=>["policy","detector"].includes(r.kind)).length} passed.</p>
            <p>{e.limits}</p>
            <details><summary>Cases, failures and supporting evidence</summary>{e.results.map((r,i)=><p key={i}>{r.version} · {r.id} · {(r.passed ?? r.diagnosis_correct)?"Passed":"Review failure"}{r.error?" · "+r.error:""}</p>)}<a href={e.url} target="_blank" rel="noreferrer">Inspect observations, expected answers and tool results</a></details>
          </section>)}
          <h3>Distinct recorded outcomes</h3>
          <div className="chips">{Object.entries(impact.outcomes || {}).map(([name,count])=><span className="tag" key={name}>{name.replace(/_/g," ")} · {count}</span>)}</div>
          <h3>Experiment evidence</h3>
          {impact.kpis?.map(k=><details key={k.label}><summary>{k.label} · {k.value}</summary>{k.evidence.length?k.evidence.map(e=><p key={e.url}><a href={e.url} target="_blank" rel="noreferrer">{e.label}</a></p>):<p>No qualifying experiments yet.</p>}</details>)}
          {impact.investigations?.map(i=><p key={i.url}><a href={i.url} target="_blank" rel="noreferrer">{i.kind}</a> · {i.text}</p>)}
          <h3>Recent outcomes</h3>
          {impact.recent.map((r, i) => (
            <p key={i}>
              {r.task} · {r.mode} · {r.outcome} · {duration(r.seconds)}
            </p>
          ))}
        </>
      ) : (
        <p>Measurements are available in the live dashboard.</p>
      )}
    </details>
  );
}

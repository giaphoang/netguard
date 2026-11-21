import React from "react";
import type { Run } from "./Incidents";

export type RecordedFrame = {
  label: string;
  reason?: string;
  eventTime?: number;
  review?: Run["review"];
  probes: {
    source?: number;
    timestamp: string;
    candidates?: number[] | null;
    probes: Record<string, string>;
  }[];
};

export function RecordedIncident({ frame, startedAt, evidenceUrl }: {
  frame: RecordedFrame;
  startedAt: number;
  evidenceUrl: string;
}) {
  const elapsed = (frame.eventTime ?? startedAt) - startedAt;
  const review = frame.review;
  return (
    <section className="incidents-recording replay-evidence" id="recorded-evidence" aria-labelledby="replay-stage">
      <div className="replay-stage" aria-live="polite">
        <small>{elapsed < 0 ? "Before the fault" : `+${elapsed.toFixed(1)} s after injection`}</small>
        <h2 id="replay-stage">{frame.label}</h2>
        <p>{frame.reason}</p>
      </div>
      <table>
        <caption>Recorded probes</caption>
        <thead>
          <tr><th scope="col">Flow</th><th scope="col">ICMP</th><th scope="col">HTTP</th><th scope="col">Next hops</th></tr>
        </thead>
        <tbody>
          {frame.probes.map((flow, index) => (
            <tr key={flow.source ?? index}>
              <th scope="row">Host {flow.source === 2 ? "B → A" : "A → B"}</th>
              {["ICMP", "HTTP"].map(protocol => (
                <td key={protocol} data-result={flow.probes[protocol]}>{flow.probes[protocol] || "Unavailable"}</td>
              ))}
              <td>{flow.candidates?.map(spine => `Spine ${spine}`).join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {review?.decision && (
        <details className="replay-diagnosis" open>
          <summary>Recorded Ollama diagnosis</summary>
          <p><code>{review.model}</code> identified a {review.decision.diagnosis} fault at <code>{review.decision.component}</code>.</p>
          <p>Proposed action <code>{review.decision.action}</code></p>
          {review.decision.citations.map(key => (
            <div key={key}>
              <small>Observation for {key}</small>
              <pre>{JSON.stringify(review.observation.facts[key], null, 2)}</pre>
            </div>
          ))}
        </details>
      )}
      <a className="replay-source" href={evidenceUrl} target="_blank" rel="noreferrer">View the Ubuntu run on GitHub</a>
    </section>
  );
}

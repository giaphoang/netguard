import React, { useState } from "react";
import type { Run } from "./Incidents";
import { FabricSpec, TopologyPreview } from "./TopologyPreview";

export type Step = {
  label: string;
  detail?: string;
  time?: number;
};

export type Evidence = {
  observed: number;
  facts: Record<string, unknown>;
};

export type Plan = {
  id: string;
  leaves: number[];
  topology?: FabricSpec;
  before?: FabricSpec;
  added?: string[];
  risk?: { reason: string; rollback: string };
  events?: { phase: string; time: number }[];
  diff: string;
  phase: string;
  failure?: string;
  verification?: { observed: number } & Record<string, unknown>;
  rollback_verification?: { observed: number } & Record<string, unknown>;
};

export type PlanAction = "approve" | "cancel" | "rollback";

export const planActions: Record<PlanAction, string> = {
  approve: "Approve deployment",
  cancel: "Discard preview",
  rollback: "Roll back this change",
};

export const planStatuses: Record<string, string> = {
  preview: "Preview ready for approval",
  applying: "Deploying after baseline checks",
  verified: "Routing and connectivity verified",
  rolling_back: "Restoring the previous topology",
  rolled_back: "Previous topology restored and verified",
  cancelled: "Preview discarded",
  needs_review: "Topology change needs operator review",
};

const incidentPhases: Record<string, string> = {
  injecting: "Save verified baseline",
  injected: "Confirm simulation",
  detected: "Detect the fault",
  diagnosed: "Correlate incident evidence",
  proposed: "Verify failover and propose repair",
  applying: "Apply restoration",
  verifying: "Verify recovery",
  handoff: "Operator review",
  resolved: "Incident closure verified",
};

export function incidentSteps(events: Run["events"]): Step[] {
  return events.map(({ phase, reason, time }) => ({
    label: incidentPhases[phase] || phase,
    detail: reason,
    time,
  }));
}

export function planSteps(plan: Plan, from = 0): Step[] {
  return (plan.events || []).slice(from).map(({ phase, time }) => ({
    label: planStatuses[phase] || phase.replace(/_/g, " "),
    time,
  }));
}

export function Activity({
  label = "Activity",
  steps,
}: {
  label?: string;
  steps: Step[];
}) {
  if (!steps.length) return null;
  return (
    <details className="chat-activity" open>
      <summary>{label} <span>{steps.length}</span></summary>
      <ol>
        {steps.map((step, i) => (
          <li key={i}>
            <div>
              <strong>{step.label}</strong>
              {step.time !== undefined && (
                <time dateTime={new Date(step.time * 1000).toISOString()}>
                  {new Date(step.time * 1000).toLocaleTimeString()}
                </time>
              )}
            </div>
            {step.detail && <p>{step.detail}</p>}
          </li>
        ))}
      </ol>
    </details>
  );
}

export function ChatEvidence({ evidence, label = "Evidence" }: {
  evidence: Evidence;
  label?: string;
}) {
  return (
    <details className="chat-evidence">
      <summary>
        {label} · {new Date(evidence.observed * 1000).toLocaleTimeString()}
      </summary>
      <dl>
        {Object.entries(evidence.facts).map(([name, value]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd><pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre></dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function IncidentReview({ review }: { review: NonNullable<Run["review"]> }) {
  if (!review.decision) return null;
  const { diagnosis, component, next_check, citations } = review.decision;
  const facts = Object.fromEntries(citations.map((key) => [key, review.observation.facts[key]]));
  return (
    <div className="chat-review">
      <strong>Incident assessment</strong>
      <p>{diagnosis} · {component}</p>
      <p>{next_check}</p>
      <ChatEvidence label="Cited incident evidence" evidence={{ ...review.observation, facts }} />
    </div>
  );
}

export function ProvisionPlan({ plan, busy, current, act, showJournal = false }: {
  plan: Plan;
  busy: boolean;
  current: boolean;
  act: (action: PlanAction) => void;
  showJournal?: boolean;
}) {
  const [showTopology, setShowTopology] = useState(false);
  const actions: PlanAction[] = plan.phase === "preview"
    ? ["approve", "cancel"]
    : ["verified", "needs_review"].includes(plan.phase) ? ["rollback"] : [];
  return (
    <div className="provision-plan">
      <strong>Fabric expansion</strong>
      <p>{!current && plan.phase === "preview" ? "Earlier preview" : planStatuses[plan.phase] || plan.phase}</p>
      <p>{(plan.added || plan.leaves.map((n) => "leaf-" + n)).join(", ")}</p>
      {plan.risk && <p>{plan.risk.reason}</p>}
      {plan.failure && <p>{plan.failure}</p>}
      {plan.before && plan.topology && (
        <details onToggle={(e) => setShowTopology(e.currentTarget.open)}>
          <summary>Topology comparison</summary>
          {showTopology && (
            <TopologyPreview before={plan.before} after={plan.topology} applied={plan.phase === "verified"} />
          )}
        </details>
      )}
      <details>
        <summary>Configuration diff and rollback</summary>
        <p>{plan.risk?.rollback || "Failed checks restore the prior topology."}</p>
        <pre>{plan.diff}</pre>
      </details>
      {showJournal && <Activity label="Plan journal" steps={planSteps(plan)} />}
      {current && actions.length > 0 && (
        <div className="chat-actions">
          {actions.map((action) => (
            <button
              type="button"
              key={action}
              disabled={busy}
              className={action === "approve" ? "primary" : undefined}
              onClick={() => act(action)}
            >
              {planActions[action]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function AgentGuide({ dialog }: { dialog: React.RefObject<HTMLDialogElement> }) {
  return (
    <dialog ref={dialog} id="netguard-guide" className="chat-guide" aria-labelledby="netguard-guide-title">
      <div className="chat-guide-head">
        <h2 id="netguard-guide-title">How to use NetGuard Agent</h2>
        <button type="button" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <p>Select a link when it’s relevant. Include the target in each follow-up.</p>
      <ul>
        <li><strong>Ask.</strong> Try “Can Leaf 1 reach Leaf 3?” Open Evidence for the routes and probes used in the answer.</li>
        <li><strong>Search incidents.</strong> Try “Find resolved BGP incidents on Leaf 1.” Select Inspect to open the saved timeline and log.</li>
        <li><strong>Simulate.</strong> Try “Simulate a Leaf 1 to Spine 2 link failure for 30 seconds.” Review the target before approving.</li>
        <li><strong>Repair.</strong> Try “Fix this network.” Review the proposed repair and follow the incident activity through verification.</li>
        <li><strong>Provision.</strong> Try “Add one leaf.” Review the topology, configuration diff and rollback before approving.</li>
        <li><strong>Measure.</strong> Use “Show impact” to see measured outcomes and open the detailed comparison.</li>
        <li><strong>Experiment.</strong> Ask how to double aggregate throughput, design a GRE tunnel, or compare diagnosis workflows. Open tool activity to inspect the calls and their results.</li>
      </ul>
      <p>Use Live to run tasks and Replay to review recorded events.</p>
    </dialog>
  );
}

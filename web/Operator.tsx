import React, { useEffect, useRef, useState } from "react";
import type { Run } from "./Incidents";

type Plan = {
  id: string;
  leaves: number[];
  diff: string;
  phase: string;
  failure?: string;
};
type Answer = {
  text: string;
  choices?: string[];
  evidence?: { observed: number; facts: Record<string, unknown> };
  action?: string;
  label?: string;
  impact?: boolean;
  plan?: Plan;
};
type Row = {
  metric: string;
  scenario: string;
  manual: number | null;
  assisted: number | null;
  n: number[];
};
type Impact = {
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
type Props = {
  page: string;
  request?: { text: string };
  navigate: (page: string) => void;
  signal: string;
  live: boolean;
  target: string;
  run?: Run;
  observed: string;
  replayText?: string;
  preview: (leaves: number[]) => void;
  trace: () => Promise<void>;
  probe: string;
};
const duration = (n: number | null) => (n === null ? "—" : n.toFixed(1) + " s");

export function Operator({
  page,
  request,
  navigate,
  signal,
  live,
  target,
  run,
  observed,
  replayText,
  preview,
  trace,
  probe,
}: Props) {
  const [input, setInput] = useState(""),
    [busy, setBusy] = useState(false),
    [tracking, setTracking] = useState(false),
    [answer, setAnswer] = useState<Answer>(),
    [plan, setPlan] = useState<Plan | null>(null),
    [impact, setImpact] = useState<Impact>(),
    [showImpact, setShowImpact] = useState(true),
    [question, setQuestion] = useState("");
  const answerPanel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (answer) answerPanel.current?.scrollIntoView({ block: "nearest" });
  }, [answer]);
  useEffect(() => {
    if (!live) return;
    let active = true;
    async function poll() {
      try {
        const [a, b] = await Promise.all([
          fetch("/api/provision"),
          fetch("/api/impact"),
        ]);
        if (!a.ok || !b.ok) return;
        const p: Plan | null = await a.json(),
          i: Impact = await b.json();
        if (active) {
          setPlan(p);
          preview(p?.phase === "preview" ? p.leaves : []);
          setImpact(i);
        }
      } catch {}
    }
    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [live]);
  useEffect(() => {
    if (request) ask(request.text);
  }, [request]);
  useEffect(() => {
    if (page === "AI Impact") setShowImpact(true);
  }, [page]);
  async function post(path: string, body: object = {}) {
    const session = await fetch("/api/session", { cache: "no-store" });
    if (!session.ok) throw Error("The live NetGuard session is unavailable.");
    const { token } = await session.json();
    const response = await fetch("/api/" + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-NetGuard-Session": token,
      },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok)
      throw Error(
        typeof data.detail === "string"
          ? data.detail
          : "Operator request failed.",
      );
    return data;
  }
  async function ask(q: string) {
    if (busy || !q.trim()) return;
    setTracking(false);
    setQuestion(q);
    setInput("");
    setBusy(true);
    setAnswer(undefined);
    try {
      if (q === "Inspect traffic") {
        await trace();
        setAnswer({ text: "Traffic probe results appear below." });
      } else if (q === "Evaluate impact") {
        setShowImpact(true);
        setAnswer({
          text: live
            ? "Measured workflow outcomes are shown below."
            : "Open the live dashboard to inspect measured outcomes.",
        });
      } else if (!live) {
        setAnswer({
          text: replayText || "Live telemetry is required for this operation.",
        });
      } else {
        const a: Answer = await post("operator", {
          prompt: q,
          selection: target,
        });
        setAnswer(a);
        if (a.plan) {
          setPlan(a.plan);
          preview(a.plan.leaves);
        }
        if (a.impact) navigate("AI Impact");
      }
    } catch (e) {
      setAnswer({ text: String(e instanceof Error ? e.message : e) });
    } finally {
      setBusy(false);
    }
  }
  async function apply(path: string) {
    setBusy(true);
    try {
      await post(path);
      if (path.startsWith("provision/")) {
        const p = await (await fetch("/api/provision")).json();
        setPlan(p);
        preview([]);
        setAnswer({
          text:
            "Provisioning result · " +
            p.phase.replace(/_/g, " ") +
            (p.failure ? " · " + p.failure : ""),
        });
      } else {
        setTracking(true);
        setAnswer({
          text: "Controller accepted the request.",
        });
      }
    } catch (e) {
      setAnswer({ text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }
  const active = run && run.phase !== "resolved";
  const currentText = active
    ? run.events[run.events.length - 1]?.reason
    : signal ||
      (observed
        ? "Inspect BGP neighbors, interface state, and host reachability for the selected connection."
        : "Waiting for a fabric observation.");
  return (
    <section className="operator">
      <div className="operator-head">
        <b className="op-icon">✳</b>
        <div>
          <h2>NetGuard Agent</h2>
          <small>Local model · Policy controlled</small>
        </div>
        <b className="tag">ASSIST</b>
      </div>
      <div className="answer" aria-live="polite" ref={answerPanel}>
        <strong>
          {busy
            ? "Working…"
            : question || (active ? "Current incident" : "Network overview")}
        </strong>
        <p>{answer?.text || replayText || currentText}</p>
        {run && (active || tracking) && answer && (
          <small>
            {run.phase} · {run.events[run.events.length - 1]?.reason}
          </small>
        )}
        {answer?.evidence && (
          <details>
            <summary>
              Evidence ·{" "}
              {new Date(answer.evidence.observed * 1000).toLocaleTimeString()}
            </summary>
            {Object.entries(answer.evidence.facts).map(([k, v]) => (
              <p key={k}>
                {k} · {JSON.stringify(v)}
              </p>
            ))}
          </details>
        )}
        {answer?.choices?.map((choice) => (
          <button key={choice} onClick={() => ask(choice)}>
            {choice}
          </button>
        ))}
        {answer?.action && live && (
          <button disabled={busy} onClick={() => apply(answer.action!)}>
            {answer.label}
          </button>
        )}
        {question === "Inspect traffic" && <p>{probe}</p>}
      </div>
      <div className="workflow">
        <strong>Workflow preview</strong>
        <p>Observe → Diagnose → Validate → Act / Escalate</p>
      </div>
      <div className="policy">
        <strong>◇ Safety policy enforced</strong>
        <small>
          Infrastructure changes require a validated plan and authorization.
        </small>
      </div>
      <details className="policy">
        <summary>How to use NetGuard Agent</summary>
        <p>Select a link, then enter one complete request. Include the target in each follow-up.</p>
        <ul>
          <li>Diagnose: “Why is this connection failing?” Open the evidence to inspect peers and probes.</li>
          <li>Simulate: “Simulate a Leaf 1 → Spine 2 link failure for 30 seconds.” Review the target before approving.</li>
          <li>Repair: “Fix this network.” Review any eligible repair and check the timeline for verified closure.</li>
          <li>Provision: “Expand this fabric.” Choose the number of leaves and inspect the configuration diff.</li>
          <li>Measure: “Evaluate impact.” Review durations and sample counts.</li>
        </ul>
        <p>Use live mode to run tasks. Replay shows recorded events.</p>
      </details>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
      >
        <input
          aria-label="Ask NetGuard"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          maxLength={500}
          placeholder="Ask NetGuard Agent about a lab task…"
        />
        <button aria-label="Send request" disabled={busy || !input.trim()}>
          ↑
        </button>
      </form>
      <div className="chips">
        <button disabled={busy} onClick={() => ask("Explain root cause")}>
          Diagnose incident
        </button>
        <button
          disabled={busy || !live}
          onClick={() => ask("Add one leaf connected to both spines with BGP.")}
        >
          Add a leaf
        </button>
        <button onClick={() => navigate("AI Impact")}>Show impact</button>
      </div>
      {plan && !["rolled_back", "cancelled"].includes(plan.phase) && (
        <div className="drawer">
          <strong>Fabric expansion · {plan.phase.replace(/_/g, " ")}</strong>
          <p>
            {plan.leaves.map((n) => "Leaf " + n).join(", ")} · BGP to both
            spines · 192 MiB and 0.5 CPU per new router
          </p>
          <details>
            <summary>Configuration diff and rollback</summary>
            <p>
              Both spines are recreated. Failed checks restore the base
              configuration and remove the added leaves and transit networks.
            </p>
            <pre>{plan.diff}</pre>
          </details>
          {plan.phase === "preview" && (
            <>
              <button
                disabled={busy}
                onClick={() => apply("provision/" + plan.id + "/approve")}
              >
                Approve deployment
              </button>
              <button
                disabled={busy}
                onClick={() => apply("provision/" + plan.id + "/cancel")}
              >
                Discard preview
              </button>
            </>
          )}
          {["verified", "needs_review"].includes(plan.phase) && (
            <button
              disabled={busy}
              onClick={() => apply("provision/" + plan.id + "/rollback")}
            >
              Restore base topology
            </button>
          )}
        </div>
      )}
      <details
        className="drawer impact"
        open={showImpact}
        onToggle={(e) => setShowImpact(e.currentTarget.open)}
      >
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
                [impact.rejected, "Unsafe proposals withheld"],
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
    </section>
  );
}

import React, { useEffect, useState } from "react";
import { Activity, Check, Clipboard, FlaskConical } from "lucide-react";
import { get, post } from "./localApi";
import { TopologyPreview, type FabricSpec } from "./TopologyPreview";
import "./architecture.css";

export type ArchitectureRequest = { prompt: string; selection?: string };
type Metric = "gbps" | "p95_ms" | "loss_pct" | "guest_cpu_pct" | "healthy_paths";
type Readings = Partial<Record<Metric, number | null>>;
type Benchmark = Readings & {
  link_bytes: Record<string, number>;
  busiest_vcpu_pct: number;
  single_flow?: { gbps: number };
  resources: { vm_cpus: number; memory_available_mib: number };
};
type Tool = {
  stage: string;
  tool: string;
  node?: string;
  arguments?: string[];
  status: string;
  started: number;
  completed?: number;
  result?: unknown;
  error?: string;
};
type RunSummary = {
  id: string;
  phase: string;
  prompt: string;
  created: number;
  message: string;
};
type Run = RunSummary & {
  model: string;
  version: string;
  kind?: string;
  digest?: string;
  expires?: number;
  trace: Tool[];
  endpoints?: { node: string; address: string; leaf: number }[];
  baseline?: Benchmark;
  actual?: Benchmark;
  failure?: string;
  method: { tcp_seconds: number; tcp_streams: number; repeats: number };
  plan?: {
    before: FabricSpec;
    after: FabricSpec;
    diff: string;
    risk: { eligible: boolean; reason: string; rollback: string };
    alternatives: { name: string; assessment: string; cost: string }[];
    prediction: Readings;
    assumptions: string;
    goal_gbps?: number;
  };
  evaluation?: {
    measured_multiplier: number;
    goal_met: boolean | null;
    prediction_error: Readings;
    interpretation: string;
  };
};

const examples = [{
  label: "Double aggregate throughput",
  prompt: "Analyze my current NetGuard spine-leaf network and propose an architecture capable of delivering twice the aggregate throughput while preserving path redundancy. Measure the existing bottlenecks, compare alternatives such as additional spines, ECMP optimization, and capacity changes. Show a topology preview, expected throughput improvement, resource costs, and risks. After approval, implement the best feasible design, benchmark the new topology, and compare actual results against your prediction. If 2× is not achievable within this VM, explain why.",
}];
const activePhases = ["investigating", "applying", "rolling_back"];
const phaseLabels: Record<string, string> = {
  investigating: "Investigating", preview: "Awaiting approval", applying: "Running approved experiment",
  verified: "Verified", rolling_back: "Restoring", rolled_back: "Restored", blocked: "Blocked",
  interrupted: "Interrupted", needs_review: "Needs operator review",
};
const stages = ["Understand", "Investigate", "Plan", "Validate", "Approve and implement", "Verify", "Evaluate"];
const metrics: { key: Metric; label: string; unit: string; digits: number }[] = [
  { key: "gbps", label: "Aggregate TCP goodput", unit: "Gbps", digits: 2 },
  { key: "p95_ms", label: "p95 ICMP RTT", unit: "ms", digits: 2 },
  { key: "loss_pct", label: "ICMP packet loss", unit: "%", digits: 1 },
  { key: "guest_cpu_pct", label: "Guest CPU utilization", unit: "%", digits: 1 },
  { key: "healthy_paths", label: "Healthy spine paths", unit: "", digits: 0 },
];

function tableValue(value: number | null | undefined, digits: number, unit: string) {
  return value == null ? "—" : value.toFixed(digits) + (unit ? " " + unit : "");
}

function MeasurementTable({ run }: { run: Run }) {
  return (
    <section className="architecture-card">
      <h3>Prediction vs. measured results</h3>
      <p>{run.method.repeats} matched TCP runs · {run.method.tcp_seconds}s each · {run.method.tcp_streams} parallel streams. Goodput is the median received rate across trials.</p>
      <div className="architecture-table-scroll" tabIndex={0} aria-label="Benchmark comparison">
        <table className="architecture-table">
          <thead><tr><th scope="col">Metric</th><th scope="col">Baseline</th><th scope="col">Predicted</th><th scope="col">Actual</th><th scope="col">Prediction error</th></tr></thead>
          <tbody>{metrics.map(({ key, label, digits, unit }) => {
            const delta = run.evaluation?.prediction_error[key];
            return (
              <tr key={key}>
                <th scope="row">{label}</th>
                <td>{tableValue(run.baseline?.[key], digits, unit)}</td>
                <td>{run.plan && run.plan.prediction[key] == null ? "Uncertain" : tableValue(run.plan?.prediction[key], digits, unit)}</td>
                <td>{run.actual ? tableValue(run.actual[key], digits, unit) :
                  ["preview", ...activePhases].includes(run.phase) ? "Pending" : "Not measured"}</td>
                <td>{delta != null && delta > 0 ? "+" : ""}{tableValue(delta, digits, unit)}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
      {run.plan?.goal_gbps != null && (
        <p><strong>Target {tableValue(run.plan.goal_gbps, 2, "Gbps")}</strong>
          {run.evaluation?.goal_met != null && (run.evaluation.goal_met ? " · Reached in this workload" : " · Below target in this workload")}</p>
      )}
      {run.plan && <p>{run.plan.assumptions}</p>}
      {run.evaluation?.interpretation && <p>{run.evaluation.interpretation}</p>}
    </section>
  );
}

function LinkDistribution({ run }: { run: Run }) {
  const series = [{ name: "Baseline", result: run.baseline }, { name: "Measured after change", result: run.actual }];
  return (
    <section className="architecture-card">
      <h3>Transit link utilization</h3>
      <p>Share of observed egress bytes on the source leaf across the parallel runs.</p>
      <div className="architecture-charts">
        {series.map(({ name, result }) => {
          if (!result) return null;
          const total = Object.values(result.link_bytes).reduce((sum, bytes) => sum + bytes, 0);
          if (!total) return null;
          return <div key={name}>
            <h4>{name}</h4>
            {Object.entries(result.link_bytes).map(([link, bytes]) => (
              <div className="architecture-bar-row" key={link}>
                <span>{link.replace("spine-", "Spine ")}</span>
                <progress aria-label={name + " " + link + " share"} value={bytes} max={total} />
                <span>{(100 * bytes / total).toFixed(1)}%</span>
              </div>
            ))}
          </div>;
        })}
      </div>
      {run.baseline?.single_flow && <p>Single-flow baseline {tableValue(run.baseline.single_flow.gbps, 2, "Gbps")} · Peak sampled vCPU busy time {tableValue(run.baseline.busiest_vcpu_pct, 1, "%")}</p>}
    </section>
  );
}

function ToolTrace({ run }: { run: Run }) {
  return (
    <section className="architecture-card">
      <h3>Agent execution trace</h3>
      <ol className="architecture-stages" aria-label="Experiment stages">
        {stages.map(stage => {
          const events = run.trace.filter(tool => tool.stage === stage);
          const waiting = events.some(tool => tool.status === "running");
          const status = waiting ? "In progress" : events.some(tool => tool.status === "failed") ? "Review" : events.length ? "Recorded" : "Pending";
          return <li key={stage} data-status={status}><span>{stage}</span><small>{status}</small></li>;
        })}
      </ol>
      <details>
        <summary>Tool calls and outputs ({run.trace.length})</summary>
        <div className="architecture-tools">
          {run.trace.map((tool, i) => (
            <details key={i}>
              <summary>{tool.tool}{tool.node ? " · " + tool.node : ""} <small>{tool.stage} · {tool.status}</small></summary>
              {tool.arguments && <pre>{tool.tool + " " + tool.arguments.join(" ")}</pre>}
              {tool.completed && <small>{(tool.completed - tool.started).toFixed(2)}s</small>}
              {tool.error && <p>{tool.error}</p>}
              {tool.result !== undefined && <pre>{typeof tool.result === "string" ? tool.result : JSON.stringify(tool.result, null, 2)}</pre>}
            </details>
          ))}
        </div>
      </details>
    </section>
  );
}

function Experiment({ run, busy, act }: { run: Run; busy: boolean; act: (action: "approve" | "rollback") => void }) {
  const [approvalExpiry, setApprovalExpiry] = useState(false);
  useEffect(() => {
    setApprovalExpiry(false);
    if (run.phase !== "preview" || !run.expires) return;
    const timer = window.setTimeout(() => setApprovalExpiry(true), Math.max(0, run.expires * 1000 - Date.now()));
    return () => window.clearTimeout(timer);
  }, [run.id, run.phase, run.expires]);
  return (
    <div className="architecture-results">
      <section className="architecture-card">
        <div className="architecture-run-heading">
          <div><small>Experiment {run.id.slice(0, 8)}</small><h2>{phaseLabels[run.phase] ?? run.phase}</h2></div>
          <a href={"/api/architecture/" + run.id} target="_blank" rel="noreferrer">Full evidence ↗</a>
        </div>
        <p role="status">{run.message}</p>
        {run.failure && run.failure !== run.message && <p className="architecture-error">{run.failure}</p>}
        <details>
          <summary>Objective and provenance</summary>
          <p>{run.prompt}</p>
          <small>{run.model} · {run.version} · {new Date(run.created * 1000).toLocaleString()}</small>
          {run.endpoints && <p>{run.endpoints.map(endpoint => endpoint.node + " (" + endpoint.address + ")").join(" → ")}</p>}
        </details>
      </section>
      {run.baseline && <MeasurementTable run={run} />}
      {run.baseline && <LinkDistribution run={run} />}
      {run.plan && (
        <section className="architecture-card">
          <h3>Design review</h3>
          <div className="architecture-options">
            {run.plan.alternatives.map(option => (
              <div key={option.name}><h4>{option.name}</h4><p>{option.assessment}</p><small>{option.cost}</small></div>
            ))}
          </div>
          <details open={run.phase === "preview" || undefined}>
            <summary>Topology and configuration diff</summary>
            <TopologyPreview before={run.plan.before} after={run.plan.after} applied={run.phase === "verified"} />
            {run.plan.diff && <pre className="architecture-diff">{run.plan.diff}</pre>}
          </details>
          <p>{run.plan.risk.reason}</p>
          <p>{run.plan.risk.rollback}</p>
          {run.phase === "preview" && (
            <button type="button" className="primary" disabled={busy || approvalExpiry} onClick={() => act("approve")}>
              {approvalExpiry ? "Preview expired" : "Approve change and benchmark"}
            </button>
          )}
          {["verified", "needs_review"].includes(run.phase) && (
            <button type="button" disabled={busy} onClick={() => act("rollback")}>Restore original configuration</button>
          )}
        </section>
      )}
      <ToolTrace run={run} />
    </div>
  );
}

export function ArchitectureLab({ live, initial }: { live: boolean; initial?: ArchitectureRequest }) {
  const [input, setInput] = useState(initial?.prompt ?? "");
  const [selection, setSelection] = useState(initial?.selection ?? "");
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [identity, setIdentity] = useState(() => /^[a-f0-9]{32}$/.test(location.hash.slice(1)) ? location.hash.slice(1) : "");
  const [run, setRun] = useState<Run>();
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const current = run?.id === identity ? run : undefined;

  useEffect(() => {
    if (!live) return;
    const controller = new AbortController();
    get<RunSummary[]>("architecture", controller.signal).then(rows => {
      setHistory(rows);
      setIdentity(id => id || rows[0]?.id || "");
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [live]);

  useEffect(() => {
    if (!live || !identity) return;
    const controller = new AbortController();
    let timer: number;
    async function read() {
      try {
        const next = await get<Run>("architecture/" + identity, controller.signal);
        setRun(next);
        setHistory(rows => rows.some(row => row.id === identity)
          ? rows.map(row => row.id === identity ? next : row) : [next, ...rows]);
        if (activePhases.includes(next.phase)) timer = window.setTimeout(read, 1000);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : "Experiment state is unavailable.");
          timer = window.setTimeout(read, 3000);
        }
      }
    }
    void read();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [live, identity, refresh]);

  function select(id: string) {
    setIdentity(id);
    window.history.replaceState(null, "", location.pathname + location.search + "#" + id);
  }

  async function send(action?: "approve" | "rollback") {
    setBusy(true);
    setError("");
    try {
      const response = action
        ? await post("architecture/" + identity + "/" + action, action === "approve" ? { digest: current?.digest } : {})
        : await post("architecture", { prompt: input, selection });
      const { id } = await response.json();
      select(id);
      setRefresh(count => count + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Experiment request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="architecture-layout">
      <aside className="architecture-sidebar">
        <section className="architecture-card">
          <form onSubmit={event => { event.preventDefault(); void send(); }}>
            <label htmlFor="experiment-objective">Experiment objective</label>
            <textarea id="experiment-objective" value={input} rows={5} maxLength={2000}
              placeholder="What should the fabric achieve?" onChange={e => setInput(e.target.value)} />
            <button type="submit" className="primary" disabled={!live || busy || !input.trim() || activePhases.includes(current?.phase ?? "")}>
              <Activity size={16} aria-hidden="true" /> Prepare experiment
            </button>
          </form>
          {!live && <p>Connect to the live local Linux lab to run experiments.</p>}
          {error && <p role="alert" className="architecture-error">{error}</p>}
        </section>
        <section className="architecture-card">
          <h2>Example objectives</h2>
          {examples.map(example => (
            <div className="architecture-example" key={example.label}>
              <button type="button" onClick={() => { setInput(example.prompt); setSelection(""); }}>{example.label}</button>
              <button type="button" aria-label={"Copy " + example.label} onClick={async () => {
                try { await navigator.clipboard.writeText(example.prompt); setCopied(example.label); }
                catch { setError("Clipboard is unavailable. Use the objective button to select the prompt."); }
              }}>{copied === example.label ? <Check size={16} /> : <Clipboard size={16} />}</button>
            </div>
          ))}
        </section>
        {!!history.length && (
          <section className="architecture-card">
            <h2>Recent experiments</h2>
            <ul className="architecture-history">
              {history.map(row => <li key={row.id}>
                <button type="button" aria-current={row.id === identity ? "true" : undefined} onClick={() => select(row.id)}>
                  <span>{row.prompt}</span><small>{phaseLabels[row.phase] ?? row.phase} · {row.id.slice(0, 8)}</small>
                </button>
              </li>)}
            </ul>
          </section>
        )}
      </aside>
      {current ? <Experiment run={current} busy={busy} act={action => void send(action)} /> :
        <section className="architecture-card architecture-empty">
          <FlaskConical size={26} aria-hidden="true" />
          <h2>{identity ? "Opening experiment" : "Ready for an objective"}</h2>
          <p>{identity ? "Loading saved evidence." : "Choose an example or describe the change you want to evaluate."}</p>
        </section>}
    </div>
  );
}

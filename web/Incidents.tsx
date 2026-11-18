import React, { useEffect, useState } from "react";

type Flow = {
  source: number;
  probes: Record<string, string>;
  candidates: number[] | null;
  timestamp: string;
};
export type Run = {
  id: string;
  scenario: string;
  phase: string;
  auto: boolean;
  metrics: Record<string, number>;
  latest: Flow[];
  events: { phase: string; reason: string; time: number }[];
};
type Status = { automatic: boolean; stopped: boolean; incidents: Run[] };
const labels: Record<string, string> = {
  link: "Leaf–spine link down",
  bgp: "BGP neighbor down",
  endpoint: "App endpoint down",
};
const phases: Record<string, string> = {
  handoff: "Needs human",
  proposed: "Awaiting approval",
  verifying: "Verifying recovery",
  resolved: "Resolved",
};
const seconds = (val: number | undefined) =>
  val === undefined ? "—" : val.toFixed(2) + " s";

export function IncidentPanel({
  onRun,
}: {
  onRun: (run: Run | undefined) => void;
}) {
  const [state, setState] = useState<Status>(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [token, setToken] = useState(""),
    [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState("");
  const [comparison, setComparison] = useState<
    {
      automatic: boolean;
      n: number;
      median: number;
      min: number;
      max: number;
    }[]
  >([]);
  async function refresh() {
    const res = await fetch("/api/incidents", { cache: "no-store" });
    if (!res.ok) throw Error("Live incident state unavailable");
    const next: Status = await res.json();
    setState(next);
    setConnected(true);
    onRun(next.incidents[0]);
  }
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        if (alive) await refresh();
      } catch {
        if (alive) setConnected(false);
      }
    };
    fetch("/api/session", { cache: "no-store" }).then(async (r) => {
      if (r.ok && alive) setToken((await r.json()).token);
    });
    poll();
    const timer = setInterval(poll, 1200);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  const current = state?.incidents[0];
  useEffect(() => {
    fetch("/api/comparison")
      .then(async (r) => {
        if (r.ok) setComparison(await r.json());
      })
      .catch(() => {});
  }, [current?.id, current?.phase]);
  const run = state?.incidents.find((r) => r.id === selected) || current;
  const active = !!current && current.phase !== "resolved";
  async function act(path: string, data?: object) {
    setBusy(true);
    setError("");
    setSelected("");
    try {
      const r = await fetch("/api/" + path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-NetGuard-Session": token,
        },
        body: JSON.stringify(data ?? {}),
      });
      if (!r.ok) {
        const msg = await r.json();
        throw Error(msg.detail || "Request failed");
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }
  const locked = busy || !token || !connected;
  return (
    <section className="incidents">
      <div className="incidentHeader">
        <h2>Incident controls</h2>
        <button
          disabled={locked || !!state?.stopped || (!state?.automatic && active)}
          aria-pressed={!!state?.automatic}
          onClick={() => act("automation/mode", { enabled: !state?.automatic })}
        >
          Auto-repair {state?.automatic ? "ON" : "OFF"}
        </button>
        <button
          disabled={locked}
          className="stop"
          onClick={() => act("automation/stop", { enabled: !state?.stopped })}
        >
          {state?.stopped ? "Release emergency stop" : "Emergency stop"}
        </button>
      </div>
      <div className="presetRow">
        {Object.entries(labels).map(([key, label]) => (
          <button
            key={key}
            disabled={locked || active || state?.stopped}
            onClick={() => act("scenarios/" + key + "/inject")}
          >
            Inject: {label}
          </button>
        ))}
        <button
          disabled={locked || !active || current?.phase === "verifying"}
          onClick={() => act("scenarios/reset")}
        >
          Reset scenario
        </button>
      </div>
      {!connected && <p role="status">Waiting for live incident state.</p>}
      {error && <p role="alert">{error}</p>}
      {run && (
        <>
          <div className="incidentHeader">
            <h3>
              {labels[run.scenario]} · {phases[run.phase] || run.phase}
            </h3>
            <label>
              Incident{" "}
              <select
                value={run.id}
                onChange={(e) => setSelected(e.target.value)}
              >
                {state!.incidents.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.id.slice(0, 8)} · {r.scenario}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p>{run.events[run.events.length - 1]?.reason}</p>
          <div className="stats">
            {[
              ["Detection", run.metrics.detection_s],
              ["Verified failover", run.metrics.verified_failover_s],
              ["Traffic recovery", run.metrics.traffic_recovery_s],
              ["Closure", run.metrics.closure_s],
            ].map(([k, v]) => (
              <div key={String(k)}>
                <small>{k}</small>
                <strong>
                  {seconds(typeof v === "number" ? v : undefined)}
                </strong>
              </div>
            ))}
          </div>
          <p>
            {run.latest
              .map(
                (f) =>
                  "Host " +
                  f.source +
                  " → " +
                  (3 - f.source) +
                  ": ICMP " +
                  f.probes.ICMP +
                  ", HTTP " +
                  f.probes.HTTP,
              )
              .join(" · ")}
          </p>
          {run.phase === "proposed" && current?.id === run.id && (
            <div>
              <button
                disabled={locked || state?.stopped}
                onClick={() => act("incidents/" + run.id + "/approve")}
              >
                Approve repair
              </button>
              <button
                disabled={locked}
                onClick={() => act("incidents/" + run.id + "/reject")}
              >
                Reject / escalate
              </button>
            </div>
          )}
          <details>
            <summary>Incident timeline and evidence</summary>
            <ol>
              {run.events.map((e, i) => (
                <li key={i}>
                  <time>{new Date(e.time * 1000).toLocaleTimeString()}</time>{" "}
                  <b>{e.phase}</b> · {e.reason}
                </li>
              ))}
            </ol>
            <a
              href={"/api/incidents/" + run.id}
              target="_blank"
              rel="noreferrer"
            >
              Raw routing and probe samples
            </a>
          </details>
        </>
      )}
      {!run && connected && (
        <p>
          No recorded incident. Select a preset to start a verified lab
          scenario.
        </p>
      )}
      {comparison.length > 0 && (
        <details>
          <summary>Measured link repair times</summary>
          <p>
            Injection confirmation to verified closure. Automatic and approved
            link restores only.
          </p>
          {comparison.map((c) => (
            <p key={String(c.automatic)}>
              {c.automatic ? "Automatic" : "Approved"} · {c.n} runs · median{" "}
              {seconds(c.median)} · range {seconds(c.min)}–{seconds(c.max)}
            </p>
          ))}
        </details>
      )}
    </section>
  );
}

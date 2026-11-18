import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./style.css";
import { Operator } from "./Operator";
import { IncidentPanel, Run } from "./Incidents";

type Router = {
  name: string;
  peers: Record<string, string> | null;
  routes: string;
  interfaces: string;
  error: string | null;
};
type Probe = { probes: Record<string, string> };
type Sample = { timestamp: string; routers: Router[]; flows?: Probe[] };
function displayName(id: string) {
  const edge = /^([12])-([1-4])$/.exec(id);
  if (edge) return "Leaf " + edge[2] + " ↔ Spine " + edge[1];
  if (id === "host-1") return "Host A";
  if (id === "host-2") return "Host B";
  return id.replace("-", " ").replace(/^./, (c) => c.toUpperCase());
}
function App() {
  const [preview, setPreview] = useState<number[]>([]);
  const [saved, setSaved] = useState<number | null>(null);
  const [signal, setSignal] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [settings, setSettings] = useState(false);
  const [sample, setSample] = useState<Sample>(),
    [mode, setMode] = useState("CONNECTING"),
    [selected, select] = useState("leaf-1");
  const [source, setSource] = useState(1),
    [probe, setProbe] = useState(""),
    [busy, setBusy] = useState(false),
    [offline, setOffline] = useState(false);
  const [frames, setFrames] = useState<
      {
        label: string;
        review?: Run["review"];
        reason?: string;
        eventTime?: number;
        snapshot: Sample;
        probes: { timestamp: string; probes: Record<string, string> }[];
      }[]
    >([]),
    [frame, setFrame] = useState(0);
  const [faultTarget, setFaultTarget] = useState("");
  const [incident, setIncident] = useState<Run>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => {
    if (faultTarget || (incident && incident.phase !== "resolved"))
      setDrawerOpen(true);
  }, [faultTarget, incident?.id, incident?.phase]);
  const [history, setHistory] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    let events: EventSource | undefined;
    async function connect() {
      const d = await (await fetch("/api/state")).json();
      if (!alive) return;
      const query = new URLSearchParams(location.search);
      if (query.has("replay") || d.mode === "RECORDED REPLAY") {
        const f = await (
          await fetch(
            query.get("replay") === "handoff"
              ? "/api/recording?handoff=true"
              : "/api/recording?incident=true",
          )
        ).json();
        if (!alive) return;
        setOffline(false);
        setMode("RECORDED REPLAY");
        setFrames(f);
        setSample(f[0].snapshot);
        return;
      }
      setMode(d.mode);
      setSample(d.snapshot);
      events = new EventSource("/api/events");
      events.onmessage = (e) => {
        const s: Sample = JSON.parse(e.data);
        setSample(s);
        setOffline(false);
        fetch("/api/timeline")
          .then((r) => r.json())
          .then((rows: string[][]) =>
            setHistory(
              rows.map(
                ([t, n, v]) =>
                  new Date(t).toLocaleTimeString() + " · " + n + " · " + v,
              ),
            ),
          )
          .catch(() => {});
      };
      events.onerror = () => setOffline(true);
    }
    connect().catch(() => setOffline(true));
    return () => {
      alive = false;
      events?.close();
    };
  }, []);
  const [paths, setPaths] = useState<number[]>([]);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing || frame >= frames.length - 1) {
      if (playing) setPlaying(false);
      return;
    }
    const gap =
      ((frames[frame + 1].eventTime || 0) - (frames[frame].eventTime || 0)) *
      1000;
    const timer = setTimeout(
      () => {
        const next = frame + 1;
        setFrame(next);
        setSample(frames[next].snapshot);
        setProbe("");
        setPaths([]);
      },
      Math.max(1100, gap),
    );
    return () => clearTimeout(timer);
  }, [playing, frame, frames]);
  const observed = frames.length ? frames[frame].probes : sample?.flows || [];
  const results = observed.flatMap((f) => Object.values(f.probes));
  const answered = results.filter((v) => v === "success" || v === "failed");
  const percent = answered.length
    ? Math.round(
        (100 * answered.filter((v) => v === "success").length) /
          answered.length,
      )
    : undefined;
  const router = sample?.routers.find((r) => r.name === selected);
  const observedNames = (sample?.routers || []).map((r) => r.name);
  const leaves = Array.from(
    new Set([
      1,
      2,
      ...observedNames
        .filter((n) => n.startsWith("leaf-"))
        .map((n) => Number(n.slice(5))),
      ...preview,
    ]),
  ).sort();
  const names = [
    "spine-1",
    "spine-2",
    ...leaves.map((i) => "leaf-" + i),
    "host-1",
    "host-2",
  ];
  const nodes = names.map((id) => {
    const n = Number(id.split("-")[1]);
    const host = id.startsWith("host");
    const spine = id.startsWith("spine");
    const pending =
      preview.includes(n) &&
      id.startsWith("leaf") &&
      !observedNames.includes(id);
    const peers = sample?.routers.find((r) => r.name === id)?.peers;
    const states = Object.values(peers || {});
    const status = pending
      ? "Preview"
      : offline || !states.length
        ? "Unknown"
        : states.every((v) => v === "Established")
          ? "BGP UP"
          : "BGP warning";
    return {
      id,
      position: { x: (n - 1) * 225, y: spine ? 0 : host ? 232 : 140 },
      className: host
        ? "host"
        : pending
          ? "pending"
          : status === "BGP UP"
            ? "up"
            : "impaired",
      data: {
        label: (
          <>
            <strong>{displayName(id)}</strong>
            {!host && <span>{status}</span>}
          </>
        ),
      },
    };
  });
  const edges = [];
  let warnings = 0;
  for (const l of leaves)
    for (const s of [1, 2]) {
      const state = sample?.routers.find((r) => r.name === "leaf-" + l)
        ?.peers?.["10.211." + (10 * s + l) + ".1"];
      const failed = !!state && state !== "Established";
      const live = !offline && state === "Established";
      const chosen =
        incident && incident.phase !== "resolved"
          ? incident.latest
              .find((f) => f.source === source)
              ?.candidates?.includes(s)
          : paths.includes(s);
      if (failed) warnings++;
      edges.push({
        id: s + "-" + l,
        source: "spine-" + s,
        target: "leaf-" + l,
        type: "straight",
        label: failed ? "Link warning" : undefined,
        labelStyle: { fill: "#87431a", fontSize: 12 },
        labelBgStyle: { fill: "#ffead4" },
        labelBgPadding: [6, 4] as [number, number],
        style: {
          stroke: failed
            ? "#c25137"
            : live
              ? chosen
                ? "#467bdd"
                : "#309276"
              : "#a4acb7",
          strokeWidth: 3,
          strokeDasharray: failed || !state ? "6 5" : undefined,
        },
      });
    }
  for (const n of [1, 2])
    edges.push({
      id: "access" + n,
      source: "leaf-" + n,
      target: "host-" + n,
      type: "straight",
      style: { stroke: "#606f89", strokeWidth: 2 },
    });
  const up = sample?.routers.filter((r) => !r.error && r.peers !== null).length;
  const health = offline
    ? "stale"
    : !sample || sample.routers.some((r) => !r.peers) || answered.length < 4
      ? "unknown"
      : warnings || percent !== 100
        ? "degraded"
        : "healthy";
  const liveMode = mode === "LOCAL LAB / LIVE";
  async function trace() {
    if (frames.length) {
      const d = frames[frame].probes[source - 1];
      setProbe(
        d.timestamp +
          " · recorded ICMP " +
          d.probes.ICMP +
          " · HTTP " +
          d.probes.HTTP,
      );
      const routes =
        frames[frame].snapshot.routers.find((r) => r.name === "leaf-" + source)
          ?.routes || "";
      setPaths(
        [1, 2].filter((i) =>
          routes.includes("via 10.211." + (10 * i + source) + ".1 "),
        ),
      );
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/trace?source=" + source);
      if (!r.ok) throw Error();
      const d = await r.json();
      setPaths(d.candidates || []);
      setProbe(
        d.timestamp + " · ICMP " + d.probes.ICMP + " · HTTP " + d.probes.HTTP,
      );
    } catch {
      setProbe("Probe request unavailable");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <header>
        <div className="brand">
          <span aria-hidden="true">
            <svg
              width="19"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
            >
              <circle cx="12" cy="5" r="2.5" />
              <circle cx="5" cy="18" r="2.5" />
              <circle cx="19" cy="18" r="2.5" />
              <path d="M12 8v4M5 15v-3h14v3" />
            </svg>
          </span>
          <div>
            <strong>NetGuard</strong>
            <small>AI Network Operations</small>
          </div>
        </div>
        <div className={"health " + health}>◉ Fabric {health}</div>
        <button
          className="settings"
          aria-label="Dashboard settings"
          aria-expanded={settings}
          onClick={() => setSettings(!settings)}
        >
          ☷
        </button>
      </header>
      {settings && (
        <nav>
          <a href="/">Live dashboard</a>
          <a href="?replay">Replay link repair</a>
          <a href="?replay=handoff">Replay AI handoff</a>
        </nav>
      )}
      <main>
        <div className="kpis">
          {[
            [
              up === undefined || offline
                ? "—"
                : up + "/" + sample!.routers.length,
              "Routers up",
            ],
            [
              percent === undefined || offline ? "—" : percent + "%",
              "Probe success",
            ],
            [
              liveMode && loaded
                ? signal || (incident && incident.phase !== "resolved")
                  ? 1
                  : 0
                : frames.length
                  ? ["Resolved", "Injecting"].includes(frames[frame].label)
                    ? 0
                    : 1
                  : "—",
              "Active incidents",
            ],
            [saved === null ? "—" : saved.toFixed(1) + " s", "Time saved"],
          ].map(([v, l]) => (
            <div key={l}>
              <strong>{v}</strong>
              <small>{l}</small>
            </div>
          ))}
        </div>
        <section className="fabric">
          <div className="fabric-head">
            <h1>{frames.length ? "Recorded" : "Live"} Network Fabric</h1>
            <small>
              {offline
                ? "Telemetry disconnected"
                : frames.length
                  ? "Recorded telemetry"
                  : sample
                    ? "◈ Live telemetry"
                    : "Connecting"}
            </small>
          </div>
          {frames.length > 0 && (
            <select
              aria-label="Recording stage"
              value={frame}
              onChange={(e) => {
                const i = +e.target.value;
                setFrame(i);
                setSample(frames[i].snapshot);
                setPaths([]);
                setProbe("");
              }}
            >
              {frames.map((f, i) => (
                <option key={i} value={i}>
                  {f.label}
                </option>
              ))}
            </select>
          )}
          {frames.length > 0 && (
            <button
              onClick={() => {
                if (!playing && frame === frames.length - 1) {
                  setFrame(0);
                  setSample(frames[0].snapshot);
                  setProbe("");
                  setPaths([]);
                }
                setPlaying(!playing);
              }}
            >
              {playing ? "Pause playback" : "Play walkthrough"}
            </button>
          )}
          <div className="canvas">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodesDraggable={false}
              key={names.join(",")}
              fitViewOptions={{ padding: 0.12, maxZoom: 1.65 }}
              onNodeClick={(_, n) => {
                select(n.id);
                setFaultTarget(n.id);
              }}
              onEdgeClick={(_, edge) => {
                setFaultTarget(edge.id);
                select(
                  edge.target.startsWith("leaf") ? edge.target : edge.source,
                );
              }}
              onPaneClick={() => setFaultTarget("")}
              fitView
              nodesConnectable={false}
            ></ReactFlow>
          </div>
          <div className="fabric-foot">
            <small>
              {faultTarget
                ? "Inspecting " + displayName(faultTarget)
                : "Select a router or link to inspect its state"}
            </small>
            <small className={warnings ? "warn" : ""}>
              {warnings
                ? warnings + " link warning" + (warnings > 1 ? "s" : "")
                : ""}
            </small>
          </div>
        </section>
        <Operator
          signal={signal}
          onSaved={setSaved}
          live={liveMode}
          target={faultTarget}
          run={incident}
          observed={sample?.timestamp || ""}
          preview={setPreview}
          replayText={frames.length ? frames[frame].reason : undefined}
          trace={trace}
          probe={probe}
        />
        <details className="drawer" open={!!faultTarget || undefined}>
          <summary>
            Network inspection {faultTarget && "· " + displayName(faultTarget)}
          </summary>
          <h2>Trace traffic</h2>
          <label>
            Flow{" "}
            <select
              disabled={busy}
              value={source}
              onChange={(e) => {
                setSource(+e.target.value);
                setPaths([]);
                setProbe("");
              }}
            >
              <option value="1">Host 1 → Host 2</option>
              <option value="2">Host 2 → Host 1</option>
            </select>
          </label>
          <button disabled={busy || mode === "CONNECTING"} onClick={trace}>
            {busy ? "Probing…" : "Trace traffic"}
          </button>
          <p role="status">
            {probe || "Run ICMP and HTTP probes from the source host."}
          </p>
          <hr />
          <small>OBSERVED ROUTER STATE</small>
          <h2>{selected}</h2>
          <p>{router?.error}</p>
          <pre>
            {router
              ? JSON.stringify(router.peers, null, 2)
              : "Select a router to inspect peers."}
          </pre>
          <pre>
            {router?.interfaces
              .split("\n")
              .filter((l) => /^eth/.test(l))
              .join("\n")}
          </pre>
          <pre>{router?.routes}</pre>
        </details>
        {frames.length > 0 && (
          <section className="incidents">
            <h2>Recorded incident · {frames[frame].label}</h2>
            <p>{frames[frame].reason}</p>
            {frames[frame].review?.decision && (
              <details>
                <summary>Model diagnosis and cited evidence</summary>
                <p>
                  {frames[frame].review?.model} ·{" "}
                  {frames[frame].review?.decision?.diagnosis}
                </p>
                {frames[frame].review?.decision?.citations.map((key) => (
                  <p key={key}>
                    {key} ·{" "}
                    {JSON.stringify(
                      frames[frame].review?.observation.facts[key],
                    )}
                  </p>
                ))}
              </details>
            )}
            <p>
              {frames[frame].probes
                .map(
                  (f, i) =>
                    "Host " +
                    (i + 1) +
                    " · ICMP " +
                    f.probes.ICMP +
                    " · HTTP " +
                    f.probes.HTTP,
                )
                .join(" | ")}
            </p>
          </section>
        )}
        {mode === "LOCAL LAB / LIVE" && (
          <details
            className="drawer"
            open={drawerOpen}
            onToggle={(e) => setDrawerOpen(e.currentTarget.open)}
          >
            <summary>Incident controls and timeline</summary>
            <IncidentPanel
              onSignal={setSignal}
              onRun={(r) => {
                setIncident(r);
                setLoaded(true);
              }}
              target={faultTarget}
            />
          </details>
        )}
        <details className="drawer">
          <summary>Collector log</summary>

          <p>
            {history.length
              ? history.slice(0, 5).map((h, i) => (
                  <React.Fragment key={i}>
                    {h}
                    <br />
                  </React.Fragment>
                ))
              : "No session changes in this view."}
          </p>
        </details>
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

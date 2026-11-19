import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Background, ReactFlow, type NodeChange } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./style.css";
import { edgeTypes, LinkActivity, ProbePacket } from "./TrafficEdge";
import { Operator } from "./Operator";
import { FabricSpec } from "./TopologyPreview";
import { Cases } from "./Cases";
import { IncidentPanel, Run } from "./Incidents";

type Router = {
  name: string;
  peers: Record<string, string> | null;
  routes: string;
  interfaces: string;
  error: string | null;
};
type Probe = {
  source?: number;
  probes: Record<string, string>;
};
type ProbePath = {
  source: string;
  target: string;
  source_leaf: string;
  target_leaf: string;
  address: string;
  timestamp: string;
  probes: Record<string, string>;
  path: string[] | null;
};
type Sample = {
  timestamp: string;
  routers: Router[];
  hosts?: {name:string;leaf:number;error:string|null}[];
  traffic?: Record<string, LinkActivity>;
  flows?: Probe[];
  probe_paths?: ProbePath[];
};
function displayName(id: string) {
  const edge = /^([1-3])-([1-4])$/.exec(id);
  if (edge) return "Leaf " + edge[2] + " ↔ Spine " + edge[1];
  if (id === "host-1") return "Host A";
  if (id === "host-2") return "Host B";
  return id.replace("-", " ").replace(/^./, (c) => c.toUpperCase());
}
const routeId = (route: ProbePath) => `${route.source}/${route.target}`;
const routeLabel = (route: ProbePath) =>
  `${displayName(route.source)} → ${displayName(route.target)}`;
const baseProbe = (route: ProbePath) =>
  ["host-1/host-2", "host-2/host-1"].includes(routeId(route));
const probeStatus = (route: ProbePath) =>
  Object.entries(route.probes).map(([protocol, state]) => `${protocol} ${state}`).join(" · ");
const PROBE_FRESH_MS = 18_000;

function App() {
  const [preview, setPreview] = useState<FabricSpec>();
  const [signal, setSignal] = useState("");
  const [loaded, setLoaded] = useState(false);
  const pages = [
    "Command Center",
    "AI Impact",
    "Provisioning",
    "Incidents",
  ];
  const [page, setPage] = useState("Command Center"),
    [environment, setEnvironment] = useState("Local lab");
  const [operatorRequest, requestOperator] = useState<{ text: string }>(),
    [notice, setNotice] = useState("");
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 2000);
    return () => clearInterval(timer);
  }, []);
  function navigate(name: string) {
    setPage(name);
    setNotice("");

  }

  const [sample, setSample] = useState<Sample>(),
    [mode, setMode] = useState("CONNECTING"),
    [selected, select] = useState("leaf-1");
  const [source, setSource] = useState(1),
    [routeChoice, setRouteChoice] = useState("auto"),
    [autoRoute, setAutoRoute] = useState(""),
    [manualProbe, setManualProbe] = useState<ProbePath>(),
    [probe, setProbe] = useState(""),
    [busy, setBusy] = useState(false),
    [offline, setOffline] = useState(false);
  const probeRequest = useRef<AbortController>();
  useEffect(() => () => probeRequest.current?.abort(), []);
  const [nodeMeasurements, setNodeMeasurements] = useState<
    Record<string, { width: number; height: number }>
  >({});
  function keepNodeMeasurements(changes: NodeChange[]) {
    const measured = changes.filter(change => change.type === "dimensions" && change.dimensions);
    if (!measured.length) return;
    setNodeMeasurements((previous) => {
      const next = { ...previous };
      for (const change of measured) {
        if (change.type === "dimensions" && change.dimensions) {
          next[change.id] = change.dimensions;
        }
      }
      return next;
    });
  }
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
  const [history, setHistory] = useState<string[][]>([]);
  useEffect(() => {
    let alive = true;
    let events: EventSource | undefined;
    async function connect() {
      const d = await (await fetch("/api/state")).json();
      if (!alive) return;
      setEnvironment(d.environment || "Local lab");
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
      setSample(
        d.snapshot
          ? {
              ...d.snapshot,
              flows: d.flows,
              traffic: d.traffic,
              probe_paths: d.probe_paths,
            }
          : undefined,
      );
      events = new EventSource("/api/events");
      events.onmessage = (e) => {
        const s: Sample = JSON.parse(e.data);
        setSample(s);
        setOffline(false);
        fetch("/api/timeline")
          .then((r) => r.json())
          .then((rows: string[][]) => {
            if (alive) setHistory(rows);
          })
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
  const liveRoutes = sample?.probe_paths || [];
  const results = (liveRoutes.length ? liveRoutes : observed).flatMap((f) =>
    Object.values(f.probes),
  );
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
      ...(preview?.leaves || []),
    ]),
  ).sort();
  const spines = Array.from(new Set([...observedNames.filter(n=>n.startsWith("spine-")).map(n=>Number(n.split("-")[1])),...(preview?.spines || []),1,2])).sort();
  const hosts = preview?.hosts || sample?.hosts?.map(h=>({id:Number(h.name.split("-")[1]),leaf:h.leaf})) || [{id:1,leaf:1},{id:2,leaf:2}];
  const names = [...spines.map(n=>"spine-"+n),...leaves.map(n=>"leaf-"+n),...hosts.map(h=>"host-"+h.id)];
  const nodes = names.map((id) => {
    const n = Number(id.split("-")[1]);
    const host = id.startsWith("host");
    const spine = id.startsWith("spine");
    const pending = !!preview && !observedNames.includes(id) && !(sample?.hosts || []).some(h=>h.name===id);
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
      // Preserve React Flow's measured ports when telemetry replaces the node data.
      measured: nodeMeasurements[id],
      position: { x: (n - 1) * 225, y: spine ? 0 : host ? 232 : 140 },
      className: host
        ? pending ? "pending" : sample?.hosts?.find(h=>h.name===id)?.error ? "impaired" : "host"
        : pending
          ? "pending"
          : status === "BGP UP"
            ? spine
              ? "spine up"
              : "leaf up"
            : "impaired",
      data: {
        label: (
          <>
            <strong>{displayName(id)}</strong>
            {!host && (
              <span>
                {status === "BGP UP"
                  ? spine
                    ? "● Online"
                    : "ASN " + (65100 + n)
                  : status}
              </span>
            )}
          </>
        ),
      },
    };
  });
  const fresh =
    !frames.length &&
    !offline &&
    sample &&
    clock - Date.parse(sample.timestamp) < PROBE_FRESH_MS;
  const activity = fresh ? sample.traffic || {} : {};
  const probeTime = (route: ProbePath) => Date.parse(route.timestamp || sample?.timestamp || "");
  function drawable(route: ProbePath) {
    return !frames.length &&
      route.probes.ICMP === "success" &&
      clock - probeTime(route) < PROBE_FRESH_MS &&
      route.path?.every((id) => names.includes(id));
  }
  const available = liveRoutes.filter(drawable);
  const cycle = [
    ...available.filter((r) => !baseProbe(r)),
    ...available.filter(baseProbe),
  ];
  const automatic = routeChoice === "auto";
  const pinnedRoute = liveRoutes.find((r) => routeId(r) === routeChoice);
  const useManualProbe = pinnedRoute && manualProbe && routeId(manualProbe) === routeChoice &&
    manualProbe.address === pinnedRoute.address &&
    probeTime(manualProbe) >= probeTime(pinnedRoute);
  const shownRoute = automatic
    ? cycle.find((r) => routeId(r) === autoRoute) || cycle[0]
    : useManualProbe ? manualProbe : pinnedRoute;
  const shownId = shownRoute && routeId(shownRoute);
  const packetPath = shownRoute && drawable(shownRoute) ? shownRoute.path : null;
  useEffect(() => {
    if (automatic && shownId) setAutoRoute(shownId);
  }, [automatic, shownId]);

  function advanceRoute() {
    const current = cycle.findIndex((r) => routeId(r) === shownId);
    if (cycle.length > 1) setAutoRoute(routeId(cycle[(current + 1) % cycle.length]));
  }

  function chooseRoute(value: string) {
    probeRequest.current?.abort();
    setBusy(false);
    setManualProbe(undefined);
    setRouteChoice(value);
    setPaths([]);
    setProbe("");
    const route = liveRoutes.find((r) => routeId(r) === value);
    if (route) void runProbe(route);
  }

  const routePicker = (
    <select
      aria-label="Animated probe direction"
      value={routeChoice}
      onChange={(e) => chooseRoute(e.target.value)}
    >
      <option value="auto">Auto cycle</option>
      {!automatic && !pinnedRoute && <option value={routeChoice}>Route unavailable</option>}
      {liveRoutes.map((r) => (
        <option key={routeId(r)} value={routeId(r)}>{routeLabel(r)}</option>
      ))}
    </select>
  );
  const edges = [];
  let warnings = 0, unknownLinks = 0;
  for (const l of leaves)
    for (const s of spines) {
      const state = sample?.routers.find((r) => r.name === "leaf-" + l)
        ?.peers?.["10.211." + (10 * s + l) + ".1"];
      const remote = sample?.routers.find(r=>r.name === "spine-"+s)?.peers?.["10.211."+(10*s+l)+".2"];
      if (!state || !remote) unknownLinks++;
      const failed = (!!state && state !== "Established") || (!!remote && remote !== "Established");
      const live = !offline && state === "Established" && remote === "Established";
      const onProbePath = packetPath?.includes("spine-" + s) && packetPath.includes("leaf-" + l);
      if (failed) warnings++;
      edges.push({
        id: s + "-" + l,
        source: "spine-" + s,
        target: "leaf-" + l,
        type: "traffic",

        label: failed ? "Link warning" : undefined,
        labelStyle: { fill: "#87431a", fontSize: 12 },
        labelBgStyle: { fill: "#ffead4" },
        labelBgPadding: [6, 4] as [number, number],
        style: {
          stroke: failed ? "#c25137" : live ? "#309276" : "#a4acb7",
          strokeWidth: (onProbePath || paths.includes(s)) && live ? 4.5 : 3,
          strokeDasharray: failed || !state ? "6 5" : undefined,
        },
      });
    }
  for (const h of hosts)
    edges.push({
      id: "access" + h.id,
      source: "leaf-" + h.leaf,
      target: "host-" + h.id,
      type: "traffic",

      style: { stroke: "#606f89", strokeWidth: 2 },
    });
  const up = sample?.routers.filter((r) => !r.error && r.peers !== null).length;
  const health = offline
    ? "stale"
    : !sample || sample.routers.some((r) => !r.peers) || answered.length < 4 || unknownLinks
      ? "unknown"
      : warnings || percent !== 100
        ? "degraded"
        : "healthy";
  const liveMode = mode === "LOCAL LAB / LIVE";
  let routeDetail = automatic ? "Waiting for a live probe route" : "Selected route is unavailable";
  if (packetPath) {
    routeDetail = packetPath.map(displayName).join(" → ");
    if (packetPath.length === 2) routeDetail += " · Shared access network";
  } else if (shownRoute) {
    routeDetail = routeLabel(shownRoute) + " · Waiting for a verified path";
  }
  if (busy) routeDetail = "Checking " + (shownRoute ? routeLabel(shownRoute) : "probe route");
  async function trace() {
    if (frames.length) {
      const d = frames[frame].probes[source - 1];
      const result = d.timestamp + " · recorded ICMP " + d.probes.ICMP + " · HTTP " + d.probes.HTTP;
      setProbe(result);
      const routes =
        frames[frame].snapshot.routers.find((r) => r.name === "leaf-" + source)
          ?.routes || "";
      setPaths(
        [1, 2].filter((i) =>
          routes.includes("via 10.211." + (10 * i + source) + ".1 "),
        ),
      );
      return result;
    }
    if (!shownRoute) {
      const result = "Choose an available probe route.";
      setProbe(result);
      return result;
    }
    setRouteChoice(routeId(shownRoute));
    return runProbe(shownRoute);
  }

  async function runProbe(route: ProbePath) {
    probeRequest.current?.abort();
    const controller = new AbortController();
    probeRequest.current = controller;
    setBusy(true);
    setProbe("");
    const query = new URLSearchParams({ source: route.source.split("-")[1], target: route.target });
    try {
      const response = await fetch("/api/trace?" + query, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw Error("Probe request unavailable");
      const observation: ProbePath = await response.json();
      if (controller.signal.aborted) return "The probe was cancelled.";
      if (routeId(observation) !== routeId(route)) throw Error("Unexpected probe route");
      setManualProbe(observation);
      const result = `${routeLabel(observation)} · ${probeStatus(observation)}`;
      setProbe(result);
      return result;
    } catch {
      if (controller.signal.aborted) return "The probe was cancelled.";
      setProbe("Probe request unavailable");
      return "Probe request unavailable";
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  const sessions = leaves
    .filter((l) => observedNames.includes("leaf-" + l))
    .flatMap((l) =>
      spines.map(
        (s) =>
          sample?.routers.find((r) => r.name === "leaf-" + l)?.peers?.[
            `10.211.${s}${l}.1`
          ] === "Established" &&
          sample?.routers.find((r) => r.name === "spine-" + s)?.peers?.[
            `10.211.${s}${l}.2`
          ] === "Established",
      ),
    );
  const selectedHost = hosts.find((h) => "access" + h.id === faultTarget);
  const selectedTraffic = activity[selectedHost ? "access" + selectedHost.leaf : faultTarget];
  const active = incident && incident.phase !== "resolved";
  const activeCount = frames.length
    ? Number(!["Resolved", "Injecting"].includes(frames[frame].label))
    : loaded
      ? Number(!!(active || signal))
      : "—";
  const totalRate = Object.values(activity).reduce(
    (sum, v) => sum + v.bytes_per_second,
    0,
  );
  async function resetIncident() {
    if (frames.length) {
      setPlaying(false);
      setFrame(0);
      setSample(frames[0].snapshot);
      setProbe("");
      setPaths([]);
      return;
    }
    if (!active) {
      setNotice("Fabric has no active incident to restore.");
      return;
    }
    try {
      const token = (await (await fetch("/api/session")).json()).token;
      const result = await fetch("/api/scenarios/reset", {
        method: "POST",
        headers: { "X-NetGuard-Session": token },
      });
      if (!result.ok) throw Error((await result.json()).detail);
      setNotice(
        "Restoration submitted. The controller is verifying connectivity.",
      );
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Restoration unavailable");
    }
  }
  const timeline = frames.length
    ? frames.slice(0, frame + 1).map((f) => ({
        time: (f.eventTime || 0) * 1000,
        type: f.label,
        text: f.reason || "",
      }))
    : [
        ...(incident?.events || []).map((e) => ({
          time: e.time * 1000,
          type: e.phase,
          text: e.reason,
        })),
        ...history.map(([stamp, node, value]) => ({
          time: Date.parse(stamp),
          type: "BGP",
          text: displayName(node) + " · " + value,
        })),
      ];
  timeline.sort((a, b) => b.time - a.time);
  return (
    <>
      <header className="mast">
        <div className="brand">
          <b className="brand-mark">∞</b>
          <div>
            <strong>netguard</strong>
            <small>AI NETWORK OPERATIONS</small>
          </div>
        </div>
        <span className="env">
          DEMO ENVIRONMENT · {environment.toUpperCase()}
        </span>
        <b className="sim">{frames.length ? "● Replay" : "● Live isolated lab"}</b>
      </header>
      <div className="shell">
        <aside className="rail" aria-label="Workspace navigation">
          <small>Workspace</small>
          {pages.map((name, i) => (
            <React.Fragment key={name}>
              {i === 2 && <small>Operations</small>}
              <button
                aria-current={page === name ? "page" : undefined}
                onClick={() => navigate(name)}
              >
                <span aria-hidden="true">{["⊞", "▥", "⚲", "⊕", "△"][i]}</span>
                {name}
              </button>
            </React.Fragment>
          ))}
          <div className="replays">
            <a href="/">Live lab</a>
            <a href="?replay">Link replay</a>
            <a href="?replay=handoff">Handoff replay</a>
          </div>
        </aside>
        <main data-page={page}>
          <div className="page-title">
            <div>
              {page !== "AI Impact" && <small>Network intelligence</small>}
              <h1>{page}</h1>
              <p>
                {
                  {
                    "Command Center":
                      "Observe the fabric. Ask AI to investigate. Validate every action.",
                    "AI Impact":
                      "Recorded workflow timings, recovery outcomes and AI reliability evaluations.",
                    Diagnostics:
                      "Inspect routing state, link counters and host probes.",
                    Provisioning: "Preview, approve and verify fabric changes.",
                    Incidents:
                      "Search the incident journal, inspect evidence and review fault controls.",
                  }[page]
                }
              </p>
            </div>
            {page !== "Incidents" && (
              <button onClick={() => navigate("Incidents")}>Review incidents</button>
            )}
          </div>
          {notice && <p role="status">{notice}</p>}
          <div className="kpis">
            {[
              [
                "Routers online",
                up === undefined || offline
                  ? "—"
                  : up + " / " + sample!.routers.length,
                up === sample?.routers.length && up
                  ? "● All online"
                  : "Router telemetry",
              ],
              [
                "BGP sessions",
                sessions.length && !offline
                  ? sessions.filter(Boolean).length + " / " + sessions.length
                  : "—",
                "● Established",
              ],
              [
                "Probe success",
                percent === undefined || offline ? "—" : percent + "%",
                "ICMP · HTTP",
              ],
              [
                "Active incidents",
                activeCount,
                activeCount === 0 ? "● No incidents" : "Incident monitor",
              ],
            ].map(([k, v, n]) => (
              <div key={k}>
                <small>{k}</small>
                <strong>{v}</strong>
                <small>{n}</small>
              </div>
            ))}
          </div>
          {page === "Command Center" && <Cases live={liveMode} compact navigate={navigate}/>}
          <div className="workspace">
            <section className="fabric">
              <div className="fabric-head">
                <div>
                  <h2>Live Network Topology</h2>
                  <small>FRRouting · eBGP · ECMP</small>
                </div>
                <b className="tag">
                  {frames.length
                    ? "REPLAY"
                    : !fresh
                      ? "WAITING"
                      : health.toUpperCase()}
                </b>
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
              {liveMode && (
                <div className="route-readout probe-controls">
                  <div>
                    <label>
                      Probe route {routePicker}
                    </label>
                    <button disabled={busy || !shownRoute} onClick={trace}>
                      {busy ? "Probing…" : "Run probe"}
                    </button>
                  </div>
                  <small aria-live={automatic ? "off" : "polite"}>
                    {routeDetail}
                    {shownRoute && " · " + probeStatus(shownRoute)}
                    {probe === "Probe request unavailable" && " · " + probe}
                  </small>
                </div>
              )}
              <div className="canvas">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  edgeTypes={edgeTypes}
                  nodesDraggable={false}
                  onNodesChange={keepNodeMeasurements}
                  key={names.join(",")}
                  fitViewOptions={{ padding: 0.12, maxZoom: 1.65 }}
                  onNodeClick={(_, n) => {
                    select(n.id);
                    setFaultTarget(n.id);
                  }}
                  onEdgeClick={(_, edge) => {
                    setFaultTarget(edge.id);
                    select(
                      edge.target.startsWith("leaf")
                        ? edge.target
                        : edge.source,
                    );
                  }}
                  onPaneClick={() => setFaultTarget("")}
                  fitView
                  nodesConnectable={false}
                >
                  {packetPath && (
                    <ProbePacket
                      key={packetPath.join("/")}
                      hops={packetPath}
                      onLap={automatic ? advanceRoute : undefined}
                    />
                  )}
                  <Background gap={18} size={1.4} color="#cedaff" />
                </ReactFlow>
              </div>
              <div className="legend">
                <span>● Healthy</span>
                <span>● Degraded</span>
                <span>● ICMP probe</span>
              </div>
              <div className="fabric-actions">
                <button
                  className="primary"
                  disabled={!liveMode || !!active}
                  onClick={() =>
                    requestOperator({
                      text: /^([1-3])-([1-4])$/.test(faultTarget) ? `Simulate a Leaf ${faultTarget.split("-")[1]} to Spine ${faultTarget.split("-")[0]} link failure for 30 seconds.` : "Simulate a Leaf 1 to Spine 2 link failure for 30 seconds.",
                    })
                  }
                >
                  Simulate selected link
                </button>
                <button disabled={!liveMode || !active} onClick={resetIncident}>
                  {active && incident?.scenario !== "link"
                    ? "Restore incident"
                    : "Restore link"}
                </button>
              </div>
              <div className="transmission">
                <div>
                  <strong>Link activity</strong>
                  <small>
                    {frames.length
                      ? "Recorded probes"
                      : Object.keys(activity).length
                        ? "Latest interval · " +
                          Object.values(activity)[0].seconds.toFixed(1) +
                          " s"
                        : "Awaiting live counters"}
                  </small>
                </div>
                <p>
                  {selectedTraffic
                    ? (selectedHost ? "Leaf " + selectedHost.leaf + " access segment" : displayName(faultTarget)) +
                      " · RX " +
                      selectedTraffic.rx +
                      " / TX " +
                      selectedTraffic.tx +
                      " packets at leaf"
                    : Object.keys(activity).length
                      ? Math.round(totalRate).toLocaleString() +
                        " B/s across measured links"
                      : "Select a link to inspect its state."}
                </p>
                {!liveRoutes.length && observed.map((f, i) => (
                  <small key={i}>
                    Host {i ? "B → A" : "A → B"} · ICMP {f.probes.ICMP} · HTTP{" "}
                    {f.probes.HTTP}
                  </small>
                ))}
              </div>
            </section>
            <Operator
              page={page}
              request={operatorRequest}
              navigate={navigate}
              signal={signal}
              live={liveMode}
              target={faultTarget}
              run={incident}
              preview={setPreview}
              replay={frames.length ? {
                text: frames[frame].reason,
                review: frames[frame].review,
                events: frames.slice(0, frame + 1).map((recorded) => ({
                  phase: recorded.label.toLowerCase(),
                  reason: recorded.reason || "",
                  time: recorded.eventTime || 0,
                })),
              } : undefined}
              trace={trace}
            />
            {page === "Incidents" && <Cases live={liveMode} navigate={navigate}/>}
          </div>
          <section className="timeline">
            <h2>Operations Timeline</h2>
            {timeline.slice(0, 5).map((e, i) => (
              <div key={i}>
                <b>{e.type}</b>
                <span>{e.text}</span>
                <time>{new Date(e.time).toLocaleTimeString()}</time>
              </div>
            ))}
            {!frames.length && !history.length && !incident && (
              <p>Waiting for routing or incident events.</p>
            )}
          </section>
          <details
            className="drawer inspection"
            open={page === "Diagnostics" || !!faultTarget || undefined}
          >
            <summary>
              Network inspection{" "}
              {faultTarget && "· " + displayName(faultTarget)}
            </summary>
            <h2>Trace traffic</h2>
            {frames.length ? (
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
            ) : shownRoute && <p>{routeLabel(shownRoute)}</p>}
            <button disabled={busy || (!frames.length && !shownRoute)} onClick={trace}>
              {busy ? "Probing…" : "Trace traffic"}
            </button>
            <p role="status">
              {probe || "Select a probe route to inspect its reachability."}
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
              className="drawer incident-drawer"
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
          <details className="drawer collector">
            <summary>Collector log</summary>

            <p>
              {history.length
                ? history.slice(0, 5).map((h, i) => (
                    <React.Fragment key={i}>
                      {h.join(" · ")}
                      <br />
                    </React.Fragment>
                  ))
                : "No session changes in this view."}
            </p>
          </details>
        </main>
      </div>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

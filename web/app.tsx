import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ReactFlow, Background, Controls, useNodesState } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./style.css";

type Router = {
  name: string;
  peers: Record<string, string> | null;
  routes: string;
  interfaces: string;
  error: string | null;
};
type Sample = { timestamp: string; routers: Router[] };
const names = ["spine-1", "spine-2", "leaf-1", "leaf-2", "host-1", "host-2"];
const initial = names.map((id, i) => ({
  id,
  position: { x: (i % 2) * 280, y: Math.floor(i / 2) * 125 },
  data: { label: id },
}));
function App() {
  const [nodes, , onNodesChange] = useNodesState(initial);
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
        snapshot: Sample;
        probes: { timestamp: string; probes: Record<string, string> }[];
      }[]
    >([]),
    [frame, setFrame] = useState(0);
  const [history, setHistory] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    let events: EventSource | undefined;
    async function connect() {
      const d = await (await fetch("/api/state")).json();
      if (!alive) return;
      if (location.search === "?replay" || d.mode === "RECORDED REPLAY") {
        const f = await (await fetch("/api/recording")).json();
        if (!alive) return;
        setMode("RECORDED REPLAY");
        setFrames(f);
        setSample(f[0].snapshot);
        return;
      }
      setMode(d.mode);
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
  const peers =
    sample?.routers.flatMap((r) => Object.values(r.peers || {})) || [];
  const count = peers.filter((x) => x === "Established").length;
  const health = offline
    ? "Stale"
    : peers.length !== 8
      ? "Unknown"
      : count === 8
        ? "Healthy"
        : "Degraded";
  const router = sample?.routers.find((r) => r.name === selected);
  const edges = [];
  for (let s = 1; s <= 2; s++)
    for (let l = 1; l <= 2; l++) {
      const state = sample?.routers.find((r) => r.name === "leaf-" + l)
        ?.peers?.["10.211." + (10 * s + l) + ".1"];
      edges.push({
        id: s + "-" + l,
        source: "spine-" + s,
        target: "leaf-" + l,
        label: offline ? "stale" : state || "unknown",
        style: {
          stroke:
            paths.includes(s) && state === "Established"
              ? "#2457ce"
              : state === "Established"
                ? "#178168"
                : state
                  ? "#aa392d"
                  : "#839594",
        },
      });
    }
  for (let i = 1; i <= 2; i++)
    edges.push({
      id: "access" + i,
      source: "leaf-" + i,
      target: "host-" + i,
      label: "access · configured",
    });
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
        <strong>NetGuard</strong>
        <b>{mode}</b>
        {!frames.length && <a href="?replay">Replay demo</a>}
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
        <span>
          {offline
            ? "Telemetry disconnected"
            : sample
              ? "Last sample " + new Date(sample.timestamp).toLocaleTimeString()
              : "Waiting for collector"}
        </span>
      </header>
      <main>
        <section>
          <small>NETWORK OPERATIONS</small>
          <h1>Fabric topology</h1>
          <strong>
            {health} · {count}/8 established peer observations
          </strong>
          <p>Two spines, two leaves and two isolated endpoints.</p>
          <div className="canvas">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onNodeClick={(_, n) => select(n.id)}
              fitView
              nodesConnectable={false}
            >
              <Controls />
              <Background />
            </ReactFlow>
          </div>
          <p>
            Links show configured adjacency. Blue shows candidate next hops from
            the source leaf’s kernel route at trace time. Session labels report
            the leaf’s BGP observation.
          </p>
        </section>
        <aside>
          <small>DATA PLANE</small>
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
        </aside>
        <footer>
          <small>COLLECTOR LOG</small>
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
        </footer>
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

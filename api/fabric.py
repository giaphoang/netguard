"""Read-only NetGuard telemetry from fixed local Docker resources."""

import asyncio
import json
import os
import platform
import re
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from . import architecture, measurements, operator, expansion, traffic
from .controls import router as control_routes, running_controls

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
ROUTERS = [f"{tier}-{i}" for tier in ("spine", "leaf") for i in (1, 2)]
FIXTURE = ROOT / "scenarios/healthy.json"
REPLAY = os.getenv("NETGUARD_REPLAY") == "1"


class Router(BaseModel):
    name: str
    peers: dict[str, str] | None
    routes: str
    interfaces: str
    error: str | None


class Snapshot(BaseModel):
    timestamp: str
    routers: list[Router]
    hosts: list[dict] = []


def command(node: str, *args: str) -> str:
    return subprocess.run(
        [
            "docker",
            "--context",
            os.getenv("NETGUARD_CONTEXT", "default"),
            "exec",
            f"netguard-{node}-1",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.strip()


def router(node: str) -> Router:
    routes = interfaces = ""
    try:
        routes = command(node, "ip", "-4", "route")
        interfaces = command(node, "ip", "-br", "addr")
        data = command(node, "vtysh", "-c", "show bgp summary json")
        peers = json.loads(data)["ipv4Unicast"]["peers"]
        return Router(
            name=node,
            peers={ip: p["state"] for ip, p in peers.items()},
            routes=routes,
            interfaces=interfaces,
            error=None,
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return Router(
            name=node,
            peers=None,
            routes=routes,
            interfaces=interfaces,
            error=f"Telemetry unavailable ({type(exc).__name__})",
        )


def collect() -> Snapshot:
    topology = expansion.spec(expansion.current(DB))
    with ThreadPoolExecutor(max_workers=6) as pool:
        routers = list(
            pool.map(router, [n for n in expansion.names(topology) if not n.startswith("host")])
        )
        hosts = list(pool.map(host_state, [(h, topology) for h in topology["hosts"]]))
    return Snapshot(timestamp=datetime.now(timezone.utc).isoformat(), routers=routers, hosts=hosts)


def host_state(pair):
    host, topology = pair
    name = f"host-{host['id']}"
    address = expansion.host_ip(host, topology)
    try:
        interfaces = command(name, "ip", "addr", "show")
        result = {
            "name": name,
            "leaf": host["leaf"],
            "address": address,
            "interfaces": interfaces,
            "error": None,
        }
        if host["id"] > 2:
            source = "host-2" if host["leaf"] == 1 else "host-1"
            result.update(probes=probe_results(source, result["address"]), probe_from=source)
        return result
    except (OSError, subprocess.SubprocessError):
        return {
            "name": name,
            "leaf": host["leaf"],
            "address": address,
            "error": "Host telemetry unavailable",
        }


DB = Path(os.environ.get("NETGUARD_TIMELINE_DB", ROOT / ".runtime/timeline.sqlite"))


def record(sample: Snapshot) -> None:
    DB.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as db:
        db.execute("CREATE TABLE IF NOT EXISTS events (timestamp TEXT, node TEXT, state TEXT)")
        for router in sample.routers:
            state = json.dumps(router.peers or router.error, sort_keys=True)
            last = db.execute(
                "SELECT state FROM events WHERE node=? ORDER BY rowid DESC LIMIT 1", (router.name,)
            ).fetchone()
            if last != (state,):
                db.execute(
                    "INSERT INTO events VALUES (?,?,?)", (sample.timestamp, router.name, state)
                )


def probe_spine(leaf: Router, source: str, destination: str) -> int | None:
    """Ask Linux which next hop it selects for a forwarded ICMP probe."""
    leaf_id = int(leaf.name.removeprefix("leaf-"))
    access_ip = f"10.212.{leaf_id}.1/28"
    for line in leaf.interfaces.splitlines():
        fields = line.split()
        if access_ip not in fields:
            continue
        ingress = fields[0].split("@")[0]
        try:
            (route,) = json.loads(
                command(
                    leaf.name,
                    "ip",
                    "-j",
                    "route",
                    "get",
                    destination,
                    "from",
                    source,
                    "iif",
                    ingress,
                    "ipproto",
                    "icmp",
                )
            )
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            return None
        gateway = route.get("gateway") if isinstance(route, dict) else None
        match = re.fullmatch(r"10\.211\.(\d+)\.1", gateway) if isinstance(gateway, str) else None
        if match and int(match[1]) % 10 == leaf_id:
            return int(match[1]) // 10
    return None


def probe_targets(snapshot: Snapshot) -> dict[str, dict]:
    targets = {h["name"]: h for h in snapshot.hosts if "address" in h}
    active_leaves = {h["leaf"] for h in snapshot.hosts}
    for router in snapshot.routers:
        if not router.name.startswith("leaf-"):
            continue
        leaf = int(router.name.removeprefix("leaf-"))
        address = f"10.214.0.{leaf}"
        if leaf not in active_leaves and f"{address}/32" in router.interfaces.split():
            targets[router.name] = {"name": router.name, "leaf": leaf, "address": address}
    return targets


def observe_probe(routers, source, target, *, recorded=None, http=False):
    source_name = source["name"]
    target_name = target["name"]
    address = target["address"]
    timestamp, results = recorded or (
        datetime.now(timezone.utc).isoformat(),
        probe_results(source_name, address, http=http),
    )
    source_leaf = f"leaf-{source['leaf']}"
    target_leaf = f"leaf-{target['leaf']}"
    path = None
    if results["ICMP"] == "success":
        if source_leaf == target_leaf and target_name.startswith("host-"):
            path = [source_name, target_name]
        elif source_leaf in routers and target_leaf in routers:
            spine = probe_spine(routers[source_leaf], source["address"], address)
            if f"spine-{spine}" in routers:
                path = [source_name, source_leaf, f"spine-{spine}", target_leaf]
                if target_name != target_leaf:
                    path.append(target_name)
    return {
        "source": source_name,
        "target": target_name,
        "source_leaf": source_leaf,
        "target_leaf": target_leaf,
        "address": address,
        "timestamp": timestamp,
        "probes": results,
        "path": path,
    }


def probe_paths(snapshot: Snapshot, flows: list[dict], *, all_directions=False) -> list[dict]:
    """Reuse base health probes and sample additional directions for the live map."""
    routers = {r.name: r for r in snapshot.routers}
    targets = probe_targets(snapshot)
    hosts = [name for name in targets if name.startswith("host-")]
    directions = {
        (f"host-{flow['source']}", f"host-{3 - flow['source']}"): (
            flow.get("timestamp", snapshot.timestamp),
            flow["probes"],
        )
        for flow in flows
    }
    directions.update(
        ((host["probe_from"], host["name"]), (snapshot.timestamp, host["probes"]))
        for host in snapshot.hosts
        if host.get("probe_from")
    )
    if all_directions:
        for source in hosts:
            for target in targets:
                if source != target:
                    directions.setdefault((source, target), None)
    else:
        for target in targets:
            if target.startswith("leaf-"):
                directions[("host-1", target)] = None

    def observe_path(pair):
        source, target = pair
        return observe_probe(routers, targets[source], targets[target], recorded=directions[pair])

    pairs = [pair for pair in directions if all(name in targets for name in pair)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(observe_path, pairs))


async def poll(app: FastAPI) -> None:
    previous = {}
    while True:
        snapshot, *flows = await asyncio.gather(
            asyncio.to_thread(collect), *(asyncio.to_thread(trace, i) for i in (1, 2))
        )
        leaves = [int(r.name.split("-")[1]) for r in snapshot.routers if r.name.startswith("leaf-")]
        current, routes = await asyncio.gather(
            asyncio.to_thread(traffic.collect, leaves),
            asyncio.to_thread(probe_paths, snapshot, flows, all_directions=True),
        )
        for flow in flows:
            source = f"host-{flow['source']}"
            target = f"host-{3 - flow['source']}"
            path = next(
                (r["path"] for r in routes if r["source"] == source and r["target"] == target), None
            )
            if path:
                flow["spine"] = int(path[2].removeprefix("spine-"))
        app.state.traffic = traffic.rates(previous, current)
        previous = current
        app.state.snapshot = snapshot
        app.state.flows = flows
        app.state.probe_paths = routes
        await asyncio.to_thread(record, app.state.snapshot)
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.snapshot = None
    app.state.flows = []
    app.state.probe_paths = []
    app.state.traffic = {}
    if REPLAY:
        app.state.snapshot = Snapshot.model_validate_json(FIXTURE.read_text())
        yield
    else:
        async with running_controls(app, DB):
            task = asyncio.create_task(poll(app))
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan)
app.include_router(control_routes)
app.include_router(operator.router)
app.include_router(architecture.router)


@app.get("/api/state")
def state():
    return {
        "mode": "RECORDED REPLAY" if REPLAY else "LOCAL LAB / LIVE",
        "snapshot": app.state.snapshot,
        "environment": platform.freedesktop_os_release().get("NAME", "Linux")
        + " "
        + platform.machine()
        if platform.system() == "Linux" and os.path.exists("/etc/os-release")
        else platform.system() + " " + platform.machine(),
        "traffic": app.state.traffic,
        "flows": app.state.flows,
        "probe_paths": app.state.probe_paths,
    }


@app.get("/metrics")
def fabric_metrics():
    if REPLAY:
        return Response(status_code=204)
    return Response(
        measurements.export(DB, app.state.snapshot, app.state.flows),
        media_type="text/plain; version=0.0.4",
    )


@app.get("/api/impact")
def impact(evidence: bool = False):
    if REPLAY:
        return {"recorded": True}
    return measurements.impact(DB, include_evidence=evidence)


@app.get("/api/comparison")
def comparison():
    if REPLAY:
        return []
    return measurements.comparison(measurements.summaries(DB))


@app.get("/api/topology")
def topology():
    spec = expansion.spec(expansion.current(DB)) if not REPLAY else expansion.spec(None)
    return {
        "routers": [n for n in expansion.names(spec) if not n.startswith("host")],
        "hosts": [f"host-{h['id']}" for h in spec["hosts"]],
        "links": [[f"spine-{s}", f"leaf-{n}"] for s in spec["spines"] for n in spec["leaves"]]
        + [[f"leaf-{h['leaf']}", f"host-{h['id']}"] for h in spec["hosts"]],
    }


@app.get("/api/timeline")
def timeline():
    if REPLAY or not DB.exists():
        return []
    with sqlite3.connect(DB) as db:
        return db.execute(
            "SELECT timestamp,node,state FROM events ORDER BY rowid DESC LIMIT 12"
        ).fetchall()


@app.get("/api/recording")
def recording(incident: bool = False, handoff: bool = False):
    if incident or handoff:
        filename = "p2-handoff.json" if handoff else "p1-link.json"
        run = json.loads((FIXTURE.parent / filename).read_text())
        frames = []
        for event in run["events"]:
            sample = next(s for s in reversed(run["samples"]) if s["observed"] <= event["time"])
            frames.append(
                {
                    "label": event["phase"].capitalize(),
                    "review": run.get("review") if event["phase"] == "handoff" else None,
                    "reason": event["reason"],
                    "eventTime": event["time"],
                    "snapshot": {
                        "routers": sample["routers"],
                        "timestamp": datetime.fromtimestamp(
                            sample["observed"], timezone.utc
                        ).isoformat(),
                    },
                    "probes": sample["flows"],
                }
            )
        return frames
    return [
        {
            "label": "Healthy fabric",
            "snapshot": json.loads(FIXTURE.read_text()),
            "probes": json.loads((FIXTURE.parent / "probes.json").read_text()),
        },
        {
            "label": "Link down · alternate path verified",
            **json.loads((FIXTURE.parent / "failover.json").read_text()),
        },
    ]


@app.get("/api/events")
async def events():
    async def stream():
        last = None
        while True:
            sample = app.state.snapshot
            if sample and sample.timestamp != last:
                last = sample.timestamp
                payload = {
                    **sample.model_dump(),
                    "flows": app.state.flows,
                    "probe_paths": app.state.probe_paths,
                    "traffic": app.state.traffic,
                }
                yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


def probe_results(source: str, destination: str, *, http: bool = True) -> dict[str, str]:
    """Send bounded reachability checks to an address from the lab inventory."""
    results, _ = probe_observations(source, destination, http=http)
    return results


def icmp_observation(output):
    counts = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", output or "")
    if counts is None:
        return None
    rtt = re.search(r"\btime[=<]\s*([\d.]+)\s*ms", output)
    return {
        "sent": int(counts[1]),
        "received": int(counts[2]),
        "rtt_ms": float(rtt[1]) if rtt else None,
    }


def probe_observations(source, destination, *, http=True):
    checks = {"ICMP": ["ping", "-c", "1", "-W", "2", destination]}
    if http:
        checks["HTTP"] = ["wget", "-T", "2", "-qO-", f"http://{destination}:8080/"]
    results = {}
    icmp = None
    for protocol, args in checks.items():
        try:
            output = command(source, *args)
            if protocol == "ICMP":
                icmp = icmp_observation(output)
            results[protocol] = "success"
        except subprocess.CalledProcessError as exc:
            results[protocol] = (
                "failed"
                if exc.returncode == 1
                and not (exc.stderr or "").startswith("Error response from daemon")
                else "unavailable"
            )
            if protocol == "ICMP" and results[protocol] == "failed":
                icmp = icmp_observation(exc.stdout)
        except (OSError, subprocess.SubprocessError):
            results[protocol] = "unavailable"
    return results, icmp


@app.get("/api/trace")
def trace(
    source: Annotated[int, Query(ge=1, le=4)] = 1,
    target: Annotated[str | None, Query(pattern=r"^(?:host|leaf)-[1-4]$")] = None,
):
    if target is not None:
        if REPLAY:
            raise HTTPException(409, "Select a recorded probe direction in replay")
        snapshot = getattr(app.state, "snapshot", None)
        if snapshot is None:
            raise HTTPException(503, "Waiting for live topology")
        endpoints = probe_targets(snapshot)
        source_name = f"host-{source}"
        if target == source_name or source_name not in endpoints or target not in endpoints:
            raise HTTPException(422, "Select two different endpoints in the current lab")
        return observe_probe(
            {router.name: router for router in snapshot.routers},
            endpoints[source_name],
            endpoints[target],
            http=target.startswith("host-"),
        )
    if source not in (1, 2):
        raise HTTPException(422, "Choose a target for an added host")
    if REPLAY:
        return json.loads((FIXTURE.parent / "probes.json").read_text())[source - 1]
    dest = f"10.212.{3-source}.2"
    results, icmp = probe_observations(f"host-{source}", dest)
    candidates = None
    try:
        routes = command(f"leaf-{source}", "ip", "-4", "route")
        route = re.search(
            rf"^10\.212\.{3-source}\.0/28 [^\n]*(?:\n\s+nexthop[^\n]*)*", routes, re.M
        )
        candidates = (
            []
            if route is None
            else sorted(
                {
                    int(n) // 10
                    for n in re.findall(r"via 10\.211\.(\d+)\.1", route.group())
                    if int(n) % 10 == source
                }
            )
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "destination": dest,
        "candidates": candidates,
        "probes": results,
        "icmp": icmp,
    }


@app.get("/architecture", include_in_schema=False)
@app.get("/architecture/", include_in_schema=False)
def old_architecture_workspace(request: Request):
    query = request.url.query
    return RedirectResponse("/agent/" + ("?" + query if query else ""))


app.mount(
    "/", StaticFiles(directory=ROOT / "web/dist", html=True, check_dir=False), name="dashboard"
)

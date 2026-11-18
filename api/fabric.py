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

from . import measurements, operator, provision, traffic
from .controls import router as control_routes, running_controls

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, Response
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
    with ThreadPoolExecutor(max_workers=4) as pool:
        plan = provision.latest(DB)
        leaves = plan["leaves"] if plan and plan["phase"] in provision.ACTIVE else []
        routers = list(pool.map(router, [*ROUTERS, *(f"leaf-{n}" for n in leaves)]))
    return Snapshot(timestamp=datetime.now(timezone.utc).isoformat(), routers=routers)


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


async def poll(app: FastAPI) -> None:
    previous = {}
    while True:
        snapshot, *flows = await asyncio.gather(
            asyncio.to_thread(collect), *(asyncio.to_thread(trace, i) for i in (1, 2))
        )
        leaves = [int(r.name.split("-")[1]) for r in snapshot.routers if r.name.startswith("leaf-")]
        current = await asyncio.to_thread(traffic.collect, leaves)
        app.state.traffic = traffic.rates(previous, current)
        previous = current
        app.state.snapshot = snapshot
        app.state.flows = flows
        await asyncio.to_thread(record, app.state.snapshot)
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.snapshot = None
    app.state.flows = []
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
def impact():
    if REPLAY:
        return {"recorded": True}
    return measurements.impact(DB)


@app.get("/api/comparison")
def comparison():
    if REPLAY:
        return []
    return measurements.comparison(measurements.summaries(DB))


@app.get("/api/topology")
def topology():
    return {
        "routers": ROUTERS,
        "hosts": ["host-1", "host-2"],
        "links": [[f"spine-{spine}", f"leaf-{leaf}"] for spine in (1, 2) for leaf in (1, 2)]
        + [[f"leaf-{i}", f"host-{i}"] for i in (1, 2)],
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
                    "traffic": app.state.traffic,
                }
                yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/trace")
def trace(source: Annotated[int, Query(ge=1, le=2)] = 1):
    if REPLAY:
        return json.loads((FIXTURE.parent / "probes.json").read_text())[source - 1]
    dest = f"10.212.{3-source}.2"
    results = {}
    for protocol, args in {
        "ICMP": ["ping", "-c", "1", "-W", "2", dest],
        "HTTP": ["wget", "-T", "2", "-qO-", f"http://{dest}:8080/"],
    }.items():
        try:
            command(f"host-{source}", *args)
            results[protocol] = "success"
        except subprocess.CalledProcessError as exc:
            results[protocol] = (
                "failed"
                if exc.returncode == 1 and not exc.stderr.startswith("Error response from daemon")
                else "unavailable"
            )
        except (OSError, subprocess.SubprocessError):
            results[protocol] = "unavailable"
    candidates = None
    try:
        routes = command(f"leaf-{source}", "ip", "-4", "route")
        route = re.search(
            rf"^10\.212\.{3-source}\.0/28 [^\n]*(?:\n\s+nexthop[^\n]*)*", routes, re.M
        )
        candidates = (
            []
            if route is None
            else [i for i in (1, 2) if f"via 10.211.{10*i+source}.1 " in route.group()]
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "destination": dest,
        "candidates": candidates,
        "probes": results,
    }


app.mount(
    "/", StaticFiles(directory=ROOT / "web/dist", html=True, check_dir=False), name="dashboard"
)

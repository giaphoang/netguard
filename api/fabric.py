"""Read-only NetGuard telemetry from fixed local Docker resources."""
import asyncio
import json
import os
import re
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
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
        ["docker", "--context", os.getenv("NETGUARD_CONTEXT", "default"), "exec",
         f"netguard-{node}-1", *args],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout.strip()


def router(node: str) -> Router:
    routes = interfaces = ""
    try:
        routes = command(node, "ip", "-4", "route")
        interfaces = command(node, "ip", "-br", "addr")
        data = command(node, "vtysh", "-c", "show bgp summary json")
        peers = json.loads(data)["ipv4Unicast"]["peers"]
        return Router(name=node, peers={ip: p["state"] for ip, p in peers.items()},
                      routes=routes, interfaces=interfaces, error=None)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return Router(name=node, peers=None, routes=routes, interfaces=interfaces,
                      error=f"Telemetry unavailable ({type(exc).__name__})")


def collect() -> Snapshot:
    with ThreadPoolExecutor(max_workers=4) as pool:
        routers = list(pool.map(router, ROUTERS))
    return Snapshot(timestamp=datetime.now(timezone.utc).isoformat(), routers=routers)


DB = ROOT / ".runtime/timeline.sqlite"


def record(sample: Snapshot) -> None:
    DB.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as db:
        db.execute("CREATE TABLE IF NOT EXISTS events (timestamp TEXT, node TEXT, state TEXT)")
        for router in sample.routers:
            state = json.dumps(router.peers or router.error, sort_keys=True)
            last = db.execute("SELECT state FROM events WHERE node=? ORDER BY rowid DESC LIMIT 1",
                              (router.name,)).fetchone()
            if last != (state,):
                db.execute("INSERT INTO events VALUES (?,?,?)", (sample.timestamp, router.name, state))


async def poll(app: FastAPI) -> None:
    while True:
        app.state.snapshot = await asyncio.to_thread(collect)
        await asyncio.to_thread(record, app.state.snapshot)
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.snapshot = None
    if REPLAY:
        app.state.snapshot = Snapshot.model_validate_json(FIXTURE.read_text())
        yield
    else:
        task = asyncio.create_task(poll(app))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan)


@app.get("/api/state")
def state():
    return {"mode": "RECORDED REPLAY" if REPLAY else "LOCAL LAB / LIVE",
            "snapshot": app.state.snapshot}


@app.get("/api/topology")
def topology():
    return {"routers": ROUTERS, "hosts": ["host-1", "host-2"],
            "links": [[f"spine-{s}", f"leaf-{l}"] for s in (1, 2) for l in (1, 2)]
                     + [[f"leaf-{i}", f"host-{i}"] for i in (1, 2)]}


@app.get("/api/timeline")
def timeline():
    if REPLAY or not DB.exists():
        return []
    with sqlite3.connect(DB) as db:
        return db.execute("SELECT timestamp,node,state FROM events ORDER BY rowid DESC LIMIT 12").fetchall()


@app.get("/api/recording")
def recording():
    return [{"label": "Healthy fabric", "snapshot": json.loads(FIXTURE.read_text()),
             "probes": json.loads((FIXTURE.parent / "probes.json").read_text())},
            {"label": "Link down · alternate path verified",
             **json.loads((FIXTURE.parent / "failover.json").read_text())}]


@app.get("/api/events")
async def events():
    async def stream():
        last = None
        while True:
            sample = app.state.snapshot
            if sample and sample.timestamp != last:
                last = sample.timestamp
                yield f"data: {sample.model_dump_json()}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/trace")
def trace(source: Annotated[int, Query(ge=1, le=2)] = 1):
    if REPLAY:
        return json.loads((FIXTURE.parent / "probes.json").read_text())[source - 1]
    dest = f"10.212.{3-source}.2"
    results = {}
    for protocol, args in {"ICMP": ["ping", "-c", "1", "-W", "2", dest],
                           "HTTP": ["wget", "-T", "2", "-qO-", f"http://{dest}:8080/"]}.items():
        try:
            command(f"host-{source}", *args)
            results[protocol] = "success"
        except subprocess.CalledProcessError as exc:
            results[protocol] = "failed" if exc.returncode == 1 and not exc.stderr.startswith("Error response from daemon") else "unavailable"
        except (OSError, subprocess.SubprocessError):
            results[protocol] = "unavailable"
    sample = app.state.snapshot if hasattr(app.state, "snapshot") else None
    candidates = []
    if sample:
        leaf = next(r for r in sample.routers if r.name == f"leaf-{source}")
        route = re.search(rf"^10\.212\.{3-source}\.0/28 [^\n]*(?:\n\s+nexthop[^\n]*)*", leaf.routes, re.M)
        if route:
            candidates = [i for i in (1, 2) if f"via 10.211.{10*i+source}.1 " in route.group()]
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "source": source,
            "destination": dest, "candidates": candidates, "probes": results}


app.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True, check_dir=False), name="dashboard")

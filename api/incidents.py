"""Persistent, single-worker P1 controller. Every actuation is preceded by a journal entry."""

import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import presets

RUNBOOK = {
    "link": "RESTORE_INJECTED_LINK",
    "bgp": "RESET_BGP_PRESET",
    "endpoint": "RESUME_ENDPOINT",
}
EXPECTED = {
    "spine-1": {"10.211.11.2", "10.211.12.2"},
    "spine-2": {"10.211.21.2", "10.211.22.2"},
    "leaf-1": {"10.211.11.1", "10.211.21.1"},
    "leaf-2": {"10.211.12.1", "10.211.22.1"},
}


def observe():
    from .fabric import collect, trace

    with ThreadPoolExecutor(max_workers=3) as pool:
        routes = pool.submit(collect)
        probes = list(pool.map(trace, [1, 2]))
    return {
        "observed": time.time(),
        "routers": routes.result().model_dump()["routers"],
        "flows": probes,
    }


def reachable(sample):
    return all(flow["probes"] == {"ICMP": "success", "HTTP": "success"} for flow in sample["flows"])


def peers(sample):
    return {
        (r["name"], ip): state
        for r in sample["routers"]
        for ip, state in (r["peers"] or {}).items()
    }


def healthy(sample):
    expected = {(node, ip): "Established" for node, ips in EXPECTED.items() for ip in ips}
    return (
        peers(sample) == expected
        and reachable(sample)
        and all(f["candidates"] == [1, 2] for f in sample["flows"])
    )


def diagnose(scenario, sample):
    states = peers(sample)
    expected = {(node, ip) for node, ips in EXPECTED.items() for ip in ips}
    if set(states) != expected:
        return None
    changed = {key for key, state in states.items() if state != "Established"}
    if scenario == "endpoint":
        if not changed and sample["flows"][0]["probes"]["HTTP"] == "failed":
            return "Host 2 HTTP probe failed while all routing sessions remained established."
        return None
    local = ("leaf-1", "10.211.11.1")
    if local not in changed or changed - {local, ("spine-1", "10.211.11.2")}:
        return None
    leaf = next(r for r in sample["routers"] if r["name"] == "leaf-1")
    lines = [
        line.split() for line in leaf["interfaces"].splitlines() if "10.211.11.2/28" in line.split()
    ]
    expected_state = "DOWN" if scenario == "link" else "UP"
    if (
        len(lines) == 1
        and lines[0][1] == expected_state
        and sample["flows"][0]["candidates"] == [2]
    ):
        return f"Leaf 1 transit interface is {expected_state}, its spine-1 peer is {states[local]}, and its remote prefix uses spine-2."
    return None


class Controller:
    def __init__(self, db: Path):
        self.db = db
        self.lock = threading.RLock()
        self.auto = False
        self.stopped = False
        db.parent.mkdir(exist_ok=True, parents=True)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            row = conn.execute(
                "SELECT payload FROM incidents ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        self.run = json.loads(row[0]) if row else None
        if self.active:
            self.event(
                "handoff", "Controller restarted. Inspect the saved evidence and reset manually."
            )

    @property
    def active(self):
        return self.run and self.run["phase"] != "resolved"

    def save(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO incidents VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (self.run["id"], json.dumps(self.run)),
            )

    def event(self, phase, reason):
        self.run["phase"] = phase
        self.run["events"].append({"time": time.time(), "phase": phase, "reason": reason})
        self.save()

    def inject(self, scenario):
        with self.lock:
            if self.active or self.stopped:
                raise ValueError("Reset the active incident or release the emergency stop first")
            target = presets.identify(scenario)
            baseline = observe()
            if not healthy(baseline) or presets.fault_present(scenario, target):
                raise ValueError(
                    "Injection requires four established sessions, ECMP and healthy bidirectional probes"
                )
            self.run = {
                "id": uuid.uuid4().hex,
                "scenario": scenario,
                "phase": "baseline",
                "target": target.model_dump(),
                "events": [],
                "samples": [baseline],
                "attempts": 0,
                "streak": 0,
                "loss": False,
                "metrics": {},
                "auto": self.auto,
                "t0": None,
            }
            self.event("injecting", "Saved verified baseline and target before injection")
            try:
                presets.change(scenario, target, True)
                self.run["t0"] = time.time()
                self.event("injected", "Preset actuator confirmed")
            except Exception:
                self.event("handoff", "Injection outcome uncertain. Inspect and reset manually.")
                raise

    def restore(self, action, incident_id=None):
        with self.lock:
            r = self.run
            if not self.active or (incident_id and r["id"] != incident_id):
                raise ValueError("Incident is no longer active")
            if action != "reset" and (
                r["phase"] != "proposed" or r["scenario"] != "link" or r["attempts"] or self.stopped
            ):
                raise ValueError("No eligible link restoration")
            if action == "automatic" and not (self.auto and r["auto"]):
                raise ValueError("Auto-repair is disabled")
            target = presets.Target(**r["target"])
            if action != "reset":
                fresh = observe()
                if (
                    not diagnose("link", fresh)
                    or not reachable(fresh)
                    or not all(f["candidates"] == [2] for f in fresh["flows"])
                ):
                    self.event("handoff", "Live evidence no longer supports a single safe repair.")
                    return
            r["attempts"] += 1
            self.event("applying", f"{action}: {RUNBOOK[r['scenario']]}")
            try:
                if presets.fault_present(r["scenario"], target):
                    presets.change(r["scenario"], target, False)
                elif action != "reset":
                    raise ValueError("Expected injected fault is absent")
                r["verified"] = 0
                self.event(
                    "verifying", "Waiting for two consecutive healthy routing and traffic samples"
                )
            except Exception:
                self.event(
                    "handoff", "Restoration failed precondition or execution. No automatic retry."
                )
                raise

    def step(self):
        with self.lock:
            if not self.active or self.run["phase"] in {"handoff", "applying", "injecting"}:
                return
            identity, phase = self.run["id"], self.run["phase"]
        sample = observe()
        with self.lock:
            r = self.run
            if r["id"] != identity or r["phase"] != phase:
                return
            r["samples"].append(sample)
            now = sample["observed"]
            elapsed = now - (r["t0"] or now)
            ok = reachable(sample)
            if not ok:
                r["loss"] = True
            r["streak"] = r["streak"] + 1 if ok else 0
            metrics = r["metrics"]
            if r["loss"] and r["streak"] >= 2:
                metrics.setdefault("traffic_recovery_s", elapsed)
            if phase == "verifying":
                r["verified"] = r["verified"] + 1 if healthy(sample) else 0
                if r["verified"] >= 2:
                    metrics["closure_s"] = elapsed
                    self.event(
                        "resolved",
                        "All sessions and ECMP restored; both hosts passed ICMP and HTTP twice.",
                    )
                elif now - r["events"][-1]["time"] > 45:
                    self.event(
                        "handoff",
                        "Verification deadline exceeded; inspect evidence before another reset.",
                    )
            elif elapsed > 120:
                self.event("handoff", "Operator review deadline exceeded.")
            else:
                reason = diagnose(r["scenario"], sample)
                if reason and phase == "injected":
                    metrics["detection_s"] = elapsed
                    self.event("detected", reason)
                    self.event("diagnosed", reason)
                    if r["scenario"] != "link":
                        self.event(
                            "handoff",
                            "Manual preset reset required. Only injected links are eligible for repair.",
                        )
                if r["phase"] in {"diagnosed", "proposed"} and not reason:
                    self.event("handoff", "Conflicting interface, routing or peer evidence.")
                if (
                    reason
                    and r["phase"] == "diagnosed"
                    and r["streak"] >= 2
                    and all(f["candidates"] == [2] for f in sample["flows"])
                ):
                    metrics["verified_failover_s"] = elapsed
                    self.event(
                        "proposed",
                        "BGP restored traffic via spine-2. Restore the injected link to regain redundancy.",
                    )
                if r["phase"] == "proposed" and self.auto and r["auto"] and not self.stopped:
                    self.restore("automatic", identity)
                if r["phase"] == "injected" and elapsed > 25:
                    self.event("handoff", "Evidence does not match the requested preset.")
            self.save()

    def detail(self, identity):
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT payload FROM incidents WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def status(self):
        with self.lock, sqlite3.connect(self.db) as conn:
            recent = []
            for (payload,) in conn.execute(
                "SELECT payload FROM incidents ORDER BY rowid DESC LIMIT 12"
            ):
                run = json.loads(payload)
                samples = run.pop("samples")
                run.pop("target")
                run["latest"] = samples[-1]["flows"]
                recent.append(run)
            return {"automatic": self.auto, "stopped": self.stopped, "incidents": recent}

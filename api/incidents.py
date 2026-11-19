"""Persistent, single-worker P1 controller. Every actuation is preceded by a journal entry."""

import http.client
import json
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import advisor, presets, provision

RUNBOOK = {
    "link": "RESTORE_INJECTED_LINK",
    "bgp": "RESET_BGP_PRESET",
    "endpoint": "RESUME_ENDPOINT",
}
REVIEW_TIMEOUT = 120
VERIFY_TIMEOUT = 45


class EmergencyStop(ValueError):
    pass


EXPECTED = {
    "spine-1": {"10.211.11.2", "10.211.12.2"},
    "spine-2": {"10.211.21.2", "10.211.22.2"},
    "leaf-1": {"10.211.11.1", "10.211.21.1"},
    "leaf-2": {"10.211.12.1", "10.211.22.1"},
}


def observe():
    from . import expansion, topology
    from .fabric import DB, app, collect, trace

    links = topology.managed_links(expansion.spec(expansion.current(DB)))
    with ThreadPoolExecutor(max_workers=3) as pool:
        routes = pool.submit(collect)
        probes = list(pool.map(trace, [1, 2]))
    snapshot = routes.result().model_dump()
    observed = time.time()
    traffic_stamp = getattr(app.state, "traffic_observed", 0)
    return {
        "observed": observed,
        "routers": snapshot["routers"],
        "hosts": snapshot["hosts"],
        "flows": probes,
        "links": links,
        "traffic": getattr(app.state, "traffic", {}) if 0 <= observed - traffic_stamp <= 20 else {},
    }


def reachable(sample):
    return len(sample["flows"]) == 2 and all(
        flow["probes"] == {"ICMP": "success", "HTTP": "success"} for flow in sample["flows"]
    )


def peers(sample):
    return {
        (r["name"], ip): state
        for r in sample["routers"]
        for ip, state in (r["peers"] or {}).items()
    }


def expected_peers(sample):
    spines = [
        int(r["name"].split("-")[1]) for r in sample["routers"] if r["name"].startswith("spine-")
    ]
    leaves = [
        int(r["name"].split("-")[1]) for r in sample["routers"] if r["name"].startswith("leaf-")
    ]
    if not {1, 2} <= set(spines) or not {1, 2} <= set(leaves):
        return EXPECTED
    return {
        **{f"spine-{s}": {f"10.211.{10*s+n}.2" for n in leaves} for s in spines},
        **{f"leaf-{n}": {f"10.211.{10*s+n}.1" for s in spines} for n in leaves},
    }


def healthy(sample):
    expected = {
        (node, ip): "Established" for node, ips in expected_peers(sample).items() for ip in ips
    }
    return (
        peers(sample) == expected
        and reachable(sample)
        and all(
            not h.get("error") and all(v == "success" for v in h.get("probes", {}).values())
            for h in sample.get("hosts", [])
        )
        and all(
            f["candidates"]
            == sorted(
                int(r["name"].split("-")[1])
                for r in sample["routers"]
                if r["name"].startswith("spine-")
            )
            for f in sample["flows"]
        )
    )


def alternate_spines(sample, spine):
    return sorted(
        int(r["name"].split("-")[1])
        for r in sample["routers"]
        if r["name"].startswith("spine-") and r["name"] != f"spine-{spine}"
    )


def diagnose(scenario, sample, leaf_id=1, spine=1):
    states = peers(sample)
    expected = {(node, ip) for node, ips in expected_peers(sample).items() for ip in ips}
    if set(states) != expected:
        return None
    changed = {key for key, state in states.items() if state != "Established"}
    if scenario == "endpoint":
        if not changed and sample["flows"][0]["probes"]["HTTP"] == "failed":
            return "Host 2 HTTP probe failed while all routing sessions remained established."
        return None
    net = f"10.211.{spine}{leaf_id}"
    local = (f"leaf-{leaf_id}", net + ".1")
    if local not in changed or changed - {local, (f"spine-{spine}", net + ".2")}:
        return None
    leaf = next(r for r in sample["routers"] if r["name"] == local[0])
    lines = [
        line.split() for line in leaf["interfaces"].splitlines() if net + ".2/28" in line.split()
    ]
    expected_state = "DOWN" if scenario == "link" else "UP"
    if (
        len(lines) == 1
        and lines[0][1] == expected_state
        and (
            leaf_id > 2
            or all(f["candidates"] == alternate_spines(sample, spine) for f in sample["flows"])
        )
    ):
        return f"Leaf {leaf_id} interface is {expected_state}, its spine-{spine} peer is {states[local]}, and alternate spine paths remain observable."
    return None


class Controller:
    def __init__(self, db: Path):
        from .architecture import recover

        self.db = db
        self.lock = threading.RLock()
        self.stop_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.auto = False
        db.parent.mkdir(exist_ok=True, parents=True)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS safety (singleton INTEGER PRIMARY KEY, stopped INTEGER NOT NULL)"
            )
            flag = conn.execute("SELECT stopped FROM safety WHERE singleton=1").fetchone()
            if flag and flag[0]:
                self.stop_event.set()
            row = conn.execute(
                "SELECT payload FROM incidents ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        plan = provision.latest(db)
        if plan and plan["phase"] in ("applying", "rolling_back"):
            provision.save(db, plan, "needs_review")
        recover(db)
        self.run = json.loads(row[0]) if row else None
        if self.active:
            self.event(
                "handoff", "Controller restarted. Inspect the saved evidence and reset manually."
            )

    @property
    def active(self):
        return self.run and self.run["phase"] != "resolved"

    @property
    def stopped(self):
        return self.stop_event.is_set()

    def emergency_stop(self, enabled):
        with self.stop_lock:
            if enabled:
                self.stop_event.set()
            with sqlite3.connect(self.db) as conn:
                conn.execute("INSERT OR REPLACE INTO safety VALUES(1,?)", (int(enabled),))
            self.auto = False
            if not enabled:
                self.stop_event.clear()
        if enabled and self.lock.acquire(blocking=False):
            try:
                self.stop_pending_repair()
            finally:
                self.lock.release()

    def stop_pending_repair(self):
        if (
            self.stopped
            and self.active
            and self.run["phase"]
            in {
                "baseline",
                "injecting",
                "injected",
                "detected",
                "diagnosed",
                "proposed",
            }
        ):
            self.event("handoff", "Emergency stop engaged. Manual reset is available.")
            return True
        return False

    def check_running(self):
        if self.stopped:
            raise EmergencyStop("Emergency stop engaged before the network write.")

    def set_automation(self, enabled):
        with self.lock, self.stop_lock:
            if enabled and (self.stopped or self.active):
                raise ValueError("Enable automatic repair before starting the scenario")
            self.auto = enabled

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

    def inject(self, scenario, ai=False, leaf=1, spine=1, duration=None, manual=False):
        from .architecture import require_idle

        with self.lock:
            require_idle(self.db)
            if manual and (ai or self.auto):
                raise ValueError("Manual diagnosis requires manual approval mode")
            plan = provision.latest(self.db)
            if plan and plan["phase"] in ("applying", "rolling_back", "needs_review"):
                raise ValueError("Finish the topology change first")
            if self.active or self.stopped:
                raise ValueError("Reset the active incident or release the emergency stop first")
            if duration is not None and (scenario != "link" or not 5 <= duration <= 120):
                raise ValueError("Timed link simulations support 5–120 seconds")
            target = (
                presets.identify(scenario)
                if (leaf, spine) == (1, 1)
                else presets.identify(scenario, leaf, spine)
            )
            baseline = observe()
            if not healthy(baseline) or presets.fault_present(scenario, target):
                raise ValueError(
                    "Injection requires four established sessions, ECMP and healthy bidirectional probes"
                )
            self.run = {
                "id": uuid.uuid4().hex,
                "scenario": scenario,
                "ai": ai,
                "manual": manual,
                "duration": duration,
                "baseline_routers": sorted(r["name"] for r in baseline["routers"]),
                "leaf": leaf,
                "spine": spine,
                "restore_at": time.time() + duration if duration else None,
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
                presets.change(scenario, target, True, before_write=self.check_running)
                self.run["t0"] = time.time()
                if duration:
                    self.run["restore_at"] = self.run["t0"] + duration
                self.event("injected", "Preset actuator confirmed")
            except Exception:
                self.event("handoff", "Injection outcome uncertain. Inspect and reset manually.")
                raise
            return self.run["id"]

    def restore(self, action, incident_id=None):
        with self.lock:
            r = self.run
            if not self.active or (incident_id and r["id"] != incident_id):
                raise ValueError("Incident is no longer active")
            if r["phase"] == "verifying":
                raise ValueError("Verification is already in progress")
            if action not in {"automatic", "approved", "reset"}:
                self.block_repair(action, "Unknown repair authorization mode")
            if action != "reset" and (
                r["phase"] != "proposed" or r["scenario"] != "link" or r["attempts"] or self.stopped
            ):
                self.block_repair(action, "No eligible link restoration")
            if action == "automatic" and not (self.auto and r["auto"]):
                self.block_repair(action, "Auto-repair is disabled")
            if action != "reset" and r.get("t0") and time.time() - r["t0"] > REVIEW_TIMEOUT:
                self.event("handoff", "Repair authorization deadline exceeded.")
                self.block_repair(action, "Repair authorization deadline exceeded")
            target = presets.Target(**r["target"])
            leaf, spine = r.get("leaf", 1), r.get("spine", 1)
            if (
                action == "reset"
                and (
                    presets.identify(r["scenario"])
                    if (leaf, spine) == (1, 1)
                    else presets.identify(r["scenario"], leaf, spine)
                )
                != target
            ):
                sample = observe()
                if not healthy(sample):
                    raise ValueError("Target was replaced and the new fabric is unhealthy")
                r["samples"].append(sample)
                r["verified"] = 0
                self.event(
                    "verifying",
                    "Target was replaced. Verifying healthy replacement without actuation.",
                )
                return
            if action != "reset":
                fresh = observe()
                if (
                    not diagnose("link", fresh, leaf, spine)
                    or not reachable(fresh)
                    or not all(
                        f["candidates"]
                        == (
                            alternate_spines(fresh, spine)
                            if leaf <= 2
                            else sorted(
                                int(n.split("-")[1])
                                for n in expected_peers(fresh)
                                if n.startswith("spine-")
                            )
                        )
                        for f in fresh["flows"]
                    )
                ):
                    self.event("handoff", "Live evidence no longer supports a single safe repair.")
                    return
                if self.stopped:
                    self.stop_pending_repair()
                    self.block_repair(action, "Emergency stop engaged before the network write.")
            operation = {
                "id": uuid.uuid4().hex,
                "mode": action,
                "target": r["target"],
                "started": time.time(),
                "verification_timeout_s": VERIFY_TIMEOUT,
                "rollback": "Restore the recorded pre-fault state on the same owned target.",
                "status": "authorized",
            }
            r.setdefault("operations", []).append(operation)
            r["attempts"] += 1
            self.event("applying", f"{action}: {RUNBOOK[r['scenario']]}")
            try:
                if presets.fault_present(r["scenario"], target):
                    presets.change(
                        r["scenario"],
                        target,
                        False,
                        before_write=self.check_running if action != "reset" else None,
                    )
                elif action != "reset":
                    raise ValueError("Expected injected fault is absent")
                r["verified"] = 0
                operation["status"] = "verifying"
                self.event(
                    "verifying", "Waiting for two consecutive healthy routing and traffic samples"
                )
            except EmergencyStop:
                operation["status"] = "blocked"
                self.event("handoff", "Emergency stop blocked the network write.")
                raise
            except Exception:
                operation["status"] = "needs_review"
                self.event(
                    "handoff", "Restoration failed precondition or execution. No automatic retry."
                )
                raise

    def block_repair(self, action, reason):
        self.run.setdefault("blocked_repairs", []).append(
            {
                "time": time.time(),
                "mode": action,
                "reason": reason,
            }
        )
        self.save()
        raise ValueError(reason)

    def manual_diagnosis(self, identity, cause):
        with self.lock:
            r = self.run
            if (
                not self.active
                or r["id"] != identity
                or not r.get("manual")
                or r["phase"] != "injected"
            ):
                raise ValueError("No manual investigation pending")
            sample = observe()
            reason = diagnose(cause, sample, r["leaf"], r["spine"])
            r["samples"].append(sample)
            r["manual_correct"] = cause == r["scenario"] and bool(reason)
            if not r["manual_correct"]:
                self.event("handoff", "Manual diagnosis did not match observed fault evidence.")
                return
            r["diagnosed_at"] = time.time()
            self.event("diagnosed", reason)
            if cause != "link":
                self.event("handoff", "Operator reset required.")

    def expire_simulation(self):
        with self.lock:
            r = self.run
            if (
                self.active
                and r.get("restore_at")
                and time.time() >= r["restore_at"]
                and r["phase"] not in {"verifying", "applying"}
            ):
                r["restore_at"] = None
                self.event(
                    "handoff", "Approved simulation duration elapsed. Restoring the selected link."
                )
                attempts = r["attempts"]
                try:
                    self.restore("reset", r["id"])
                except (OSError, subprocess.SubprocessError):
                    # A timeout during read-only identity inspection can be retried.
                    # An uncertain mutation always requires human review.
                    retries = r.get("timer_checks", 0)
                    if r["attempts"] == attempts and retries < 2:
                        r["timer_checks"] = retries + 1
                        r["restore_at"] = time.time() + 1
                        self.event(
                            "handoff",
                            "Docker identity check timed out. Rechecking before restoration.",
                        )
                    else:
                        self.event(
                            "handoff", "Scheduled restoration needs a manual state check and reset."
                        )
                    raise

    def step(self):
        with self.lock:
            if self.stop_pending_repair():
                return
            if not self.active or self.run["phase"] in {"applying", "injecting"}:
                return
            if (
                self.run["phase"] == "handoff"
                and time.time() - self.run["events"][-1]["time"] > 120
            ):
                return
            identity, phase = self.run["id"], self.run["phase"]
            request_review = (
                phase == "injected" and self.run.get("ai") and len(self.run["samples"]) >= 2
            )
        sample = observe()
        review = None
        if request_review:
            # Allow a bounded routing-convergence window before asking the model.
            # Failed probes during reconvergence are not stable root-cause evidence.
            with self.lock:
                elapsed = sample["observed"] - (self.run.get("t0") or sample["observed"])
            if not reachable(sample) and elapsed < 20:
                request_review = False
        if request_review:
            observation = advisor.evidence(sample)
            review = {
                "version": advisor.VERSION,
                "model": advisor.model_name(),
                "observation": observation,
            }
            try:
                review["decision"] = advisor.assess(observation).model_dump()
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
                review["error"] = (
                    "Local model unavailable or invalid response. Check Ollama and the observations."
                )
        with self.lock:
            r = self.run
            if r["id"] != identity or r["phase"] != phase:
                return
            r["samples"].append(sample)
            now = sample["observed"]
            elapsed = now - (r["t0"] or now)
            if phase == "injected" and not healthy(sample):
                r.setdefault("first_signal_at", now)
                r["metrics"].setdefault("detection_s", now - (r["t0"] or now))
            ok = reachable(sample)
            if any("failed" in f["probes"].values() for f in sample["flows"]):
                r["loss"] = True
            r["streak"] = r["streak"] + 1 if ok else 0
            metrics = r["metrics"]
            if r["loss"] and r["streak"] >= 2:
                metrics.setdefault("traffic_recovery_s", elapsed)
            if phase == "verifying":
                r["verified"] = r["verified"] + 1 if healthy(sample) else 0
                if r["verified"] >= 2:
                    metrics["closure_s"] = elapsed
                    if r.get("operations") and r["operations"][-1]["status"] == "verifying":
                        r["operations"][-1].update(status="verified", verified_at=now)
                    self.event(
                        "resolved",
                        "All sessions and ECMP restored; both hosts passed ICMP and HTTP twice.",
                    )
                elif now - r["events"][-1]["time"] > VERIFY_TIMEOUT:
                    if r.get("operations"):
                        r["operations"][-1]["status"] = "needs_review"
                    self.event(
                        "handoff",
                        "Verification deadline exceeded; inspect evidence before another reset.",
                    )
            elif phase == "handoff":
                self.save()
                return
            elif elapsed > REVIEW_TIMEOUT:
                self.event("handoff", "Operator review deadline exceeded.")
            else:
                leaf, spine = r.get("leaf", 1), r.get("spine", 1)
                reason = diagnose(r["scenario"], sample, leaf, spine)
                if r.get("manual") and phase == "injected":
                    self.save()
                    return
                if r.get("ai") and phase == "injected":
                    if review is None:
                        self.save()
                        return
                    r["review"] = review
                    r["decision_at"] = time.time()
                    decision_data = review.get("decision", {})
                    component = (
                        "host-2" if r["scenario"] == "endpoint" else f"leaf-{leaf}/spine-{spine}"
                    )
                    if (
                        decision_data.get("diagnosis") == r["scenario"]
                        and decision_data.get("component") == component
                    ):
                        r["diagnosed_at"] = r["decision_at"]
                    if "error" in review:
                        self.event("handoff", review["error"])
                        return
                    decision = advisor.Decision(**review["decision"])
                    if not (
                        advisor.supports_restore(decision, f"leaf-{leaf}/spine-{spine}")
                        and r["scenario"] == "link"
                        and reason
                    ):
                        self.event("handoff", "Human review required. " + decision.next_check)
                        return
                if reason and phase == "injected":
                    metrics.setdefault("detection_s", elapsed)
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
                    and all(
                        f["candidates"]
                        == (
                            alternate_spines(sample, spine)
                            if leaf <= 2
                            else sorted(
                                int(n.split("-")[1])
                                for n in expected_peers(sample)
                                if n.startswith("spine-")
                            )
                        )
                        for f in sample["flows"]
                    )
                ):
                    metrics["verified_failover_s"] = elapsed
                    self.event(
                        "proposed",
                        "Alternate routing passed the probe checks. Restore the selected link to regain redundancy.",
                    )
                if (
                    r["phase"] == "proposed"
                    and self.auto
                    and r["auto"]
                    and not self.stopped
                    and not r.get("restore_at")
                ):
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
                target = run.pop("target")
                run["before"] = samples[0]["flows"]
                if run["scenario"] == "link":
                    run["repair_command"] = f"ip link set dev {target['interface']} up"
                run["latest"] = samples[-1]["flows"]
                recent.append(run)
            return {
                "automatic": self.auto,
                "stopped": self.stopped,
                "incidents": recent,
                "signal": getattr(self, "signal", None),
            }

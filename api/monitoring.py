"""Deterministic anomaly detection and a durable, bounded investigation loop."""

import os
import http.client
import time

from pydantic import BaseModel, Field

from . import advisor, investigations as inv


class Rules(BaseModel):
    window_s: int = Field(default=60, ge=10, le=600)
    degraded_samples: int = Field(default=2, ge=2, le=10)
    flap_transitions: int = Field(default=3, ge=2, le=20)
    cooldown_s: int = Field(default=120, ge=30, le=3600)
    drift_interval_s: int = Field(default=60, ge=10, le=3600)
    recovery_samples: int = Field(default=2, ge=2, le=10)
    latency_ms: float = Field(default=5, gt=0, le=10000)
    packet_loss_pct: float = Field(default=1, gt=0, le=100)

    @classmethod
    def configured(cls):
        return cls.model_validate_json(os.getenv("NETGUARD_MONITOR_RULES", "{}"))


def observations(sample):
    obs = advisor.evidence(sample)
    known_directions = {
        (fact.get("from", name), fact["to"])
        for name, fact in obs["facts"].items()
        if "probes" in fact
    }
    for route in sample.get("probe_paths", []):
        source, target = route["source"], route["target"]
        if (source, target) in known_directions:
            # Added hosts share their recorded health probe with the path collector.
            if target in obs["facts"] and obs["facts"][target].get("from") == source:
                obs["facts"][target]["icmp"] = route.get("icmp")
            continue
        obs["facts"][f"probe:{source}→{target}"] = {
            "from": source,
            "to": target,
            "probes": route["probes"],
            "icmp": route.get("icmp"),
        }
    return obs


def packet_loss(reading):
    icmp = reading.get("icmp") or {}
    sent, received = icmp.get("sent"), icmp.get("received")
    if sent and received is not None and 0 <= received <= sent:
        return 100 * (sent - received) / sent
    return None


def link_failed(reading):
    return reading.get("interface") in {"DOWN", "LOWERLAYERDOWN"} or any(
        reading.get(side) not in {None, "Established"} for side in ("leaf_peer", "spine_peer")
    )


def transitions(values, side):
    states = [v.get(side) for v in values]
    return sum(a != b for a, b in zip(states, states[1:]) if a and b)


def detect(history, rules):
    if len(history) < rules.degraded_samples:
        return []
    findings = []
    for key, value in history[-1]["facts"].items():
        values = [h["facts"].get(key, {}) for h in history]
        recent = values[-rules.degraded_samples :]
        if "leaf_peer" in value:
            count = max(transitions(values, side) for side in ("leaf_peer", "spine_peer"))
            if count >= rules.flap_transitions and any(
                transitions(recent, side) for side in ("leaf_peer", "spine_peer")
            ):
                findings.append(("bgp_flapping", key, count))
            if all(map(link_failed, recent)):
                findings.append(("link_or_peer_failure", key, rules.degraded_samples))
            if all(
                any(v.get(field) is None for field in ("interface", "leaf_peer", "spine_peer"))
                for v in recent
            ):
                findings.append(("telemetry_unavailable", key, rules.degraded_samples))
        else:
            losses = [packet_loss(v) for v in recent]
            if all(loss is not None and loss >= rules.packet_loss_pct for loss in losses):
                findings.append(("packet_loss", key, max(losses)))
            elif all(
                any(state == "failed" for state in v.get("probes", {}).values()) for v in recent
            ):
                findings.append(("probe_degradation", key, rules.degraded_samples))
            if all(
                any(state == "unavailable" for state in v.get("probes", {}).values())
                for v in recent
            ):
                findings.append(("telemetry_unavailable", key, rules.degraded_samples))
            rtts = [(v.get("icmp") or {}).get("rtt_ms") for v in recent]
            if all(value is not None and value >= rules.latency_ms for value in rtts):
                findings.append(("high_latency", key, max(rtts)))
    return findings


def event(row, stage, tool, result, timestamp=None):
    row.setdefault("events", []).append(
        {"time": timestamp or time.time(), "stage": stage, "tool": tool, "result": result}
    )


class Observer:
    def __init__(self, controller, rules=None):
        self.c = controller
        self.rules = rules or Rules.configured()
        self.history = []
        self.previous = 0
        self.next_drift = time.monotonic() + 10
        self.drift_streaks = {}
        rows = inv.cases(controller.db, kind="monitor")
        self.pending = [r for r in rows if r.get("status") not in {None, "verified"}]
        self.last_inference = max((r.get("triaged_at", 0) for r in rows), default=0)
        for row in self.pending:
            if row["status"] == "investigating" and not row.get("incident_id"):
                row.update(status="escalated", triaged_at=row.get("triaged_at", row["observed"]))
                row["text"] += " Investigation was interrupted. Review the saved evidence."
                event(row, "Blocked", "controller.policy", {"action": "human escalation"})
                self.save(row)
                self.last_inference = max(self.last_inference, row["triaged_at"])
        self.publish()
        controller.monitor_rules = self.rules.model_dump()

    @property
    def recovered(self):
        return self.episode.get("recovery_samples", 0) if self.episode else 0

    def publish(self):
        self.episode = self.pending[0] if self.pending else None
        self.c.signal = self.episode

    def save(self, row):
        inv.cases(self.c.db, row)

    def scan_drift(self, sample):
        self.next_drift = time.monotonic() + self.rules.drift_interval_s
        reading = inv.drift(self.c.db, sample, True)
        dirty = {
            name
            for name, facts in (reading or {}).get("facts", {}).items()
            if facts.get("missing") or facts.get("unexpected")
        }
        self.drift_streaks = {name: self.drift_streaks.get(name, 0) + 1 for name in dirty}
        return reading

    def step(self, sample):
        from . import architecture, expansion

        stamp = sample["observed"]
        if stamp <= self.previous or time.time() - stamp > 30:
            return
        self.previous = stamp
        change = expansion.current(self.c.db)
        if change and change["phase"] in {"applying", "rolling_back"}:
            self.history.clear()
            self.drift_streaks.clear()
            return
        obs = observations(sample)
        self.c.monitor_observation = obs
        self.c.monitor_traffic = sample.get("traffic", {})
        self.history = [h for h in self.history if stamp - h["observed"] <= self.rules.window_s]
        self.history.append(obs)
        with self.c.lock:
            run = self.c.run.copy() if self.c.active else None
        drift_checked = not run and time.monotonic() >= self.next_drift
        drift = None
        if drift_checked:
            active = architecture.reservation(self.c.db)
            drift_checked = (
                not active or architecture.load(self.c.db, active)["phase"] == "investigating"
            )
            if drift_checked:
                drift = self.scan_drift(sample)
        covered = {
            (finding[0], finding[1]) for row in self.pending for finding in row["facts"]["findings"]
        }
        for row in list(self.pending):
            self.follow(sample, row, obs, drift_checked, drift)
        self.publish()
        findings = detect(self.history, self.rules)
        if drift_checked and drift:
            findings += [
                ("configuration_drift", name, drift["id"])
                for name, streak in self.drift_streaks.items()
                if streak >= self.rules.degraded_samples
            ]
        findings = [f for f in findings if f[:2] not in covered]
        # Manual benchmark runs preserve human diagnosis.
        if not findings or (run and (run.get("manual") or not run.get("ai"))):
            return
        if run and any(r.get("incident_id") == run["id"] for r in self.pending):
            return
        if run:
            if run["scenario"] == "endpoint":
                findings = [f for f in findings if obs["facts"].get(f[1], {}).get("to") == "host-2"]
            else:
                target = f"leaf-{run['leaf']}/spine-{run['spine']}"
                findings = [f for f in findings if f[1] == target]
            if not findings:
                return
        if not run and time.time() - self.last_inference < self.rules.cooldown_s:
            return
        self.investigate(sample, obs, findings, drift, run)

    def investigate(self, sample, obs, findings, drift, run):
        affected = sorted({f[1] for f in findings})
        row = inv.record(
            self.c.db,
            "monitor",
            f"Detected {findings[0][0]} on {findings[0][1]}.",
            {
                "history": self.history[-10:],
                "routing": sample["routers"],
                "probes": sample["flows"],
                "drift": drift,
                "rules": self.rules.model_dump(),
                "findings": findings,
            },
        )
        row.update(
            status="investigating", incident_id=run["id"] if run else None, affected=affected
        )
        self.pending.insert(0, row)
        self.publish()
        event(
            row,
            "Observed",
            "telemetry.collect",
            {"observed": sample["observed"]},
            sample["observed"],
        )
        event(row, "Detected", "monitor.rules", findings)
        event(row, "Evidence Retrieved", "telemetry.snapshot", obs)
        self.save(row)
        if run:
            # Reuse the controlled incident's model call and captured decision.
            self.follow(sample, row, obs)
            return
        self.last_inference = row["triaged_at"] = time.time()
        event(row, "Investigation Started", "ollama.assess", {"model": advisor.model_name()})
        self.save(row)
        try:
            decision = advisor.assess(
                {
                    **obs,
                    "recent_observations": [
                        {
                            "observed": h["observed"],
                            "facts": {k: h["facts"][k] for k in affected if k in h["facts"]},
                        }
                        for h in self.history[-4:]
                    ],
                    "drift": (drift or {}).get("facts"),
                    "monitor_findings": findings,
                    "link_load": self.c.monitor_traffic,
                }
            )
            row.update(
                decision=decision.model_dump(),
                model=advisor.model_name(),
                version=advisor.MONITOR_VERSION,
            )
            row["text"] += (
                f" Likely {decision.diagnosis}. "
                if decision.diagnosis != "unknown"
                else " Root cause is uncertain. "
            ) + decision.next_check
            event(row, "Diagnosis", "ollama.assess", decision.model_dump())
        except (ValueError, OSError, KeyError, TypeError, http.client.HTTPException) as exc:
            row["error"] = type(exc).__name__
            row["text"] += (
                " Model unavailable or evidence insufficient. Human investigation required."
            )
            event(row, "Diagnosis Unavailable", "ollama.assess", {"error": row["error"]})
        row.update(status="escalated")
        event(
            row,
            "Risk Check",
            "controller.policy",
            {
                "approved": False,
                "reason": "No authorized injected-fault target. Changes require human review.",
            },
        )
        event(row, "Blocked", "controller.policy", {"action": "human escalation"})
        self.save(row)

    def follow(self, sample, row=None, obs=None, drift_checked=False, drift=None):
        direct = row is None
        row = row or self.episode
        if identity := row.get("incident_id"):
            self.follow_controller(row, identity, sample["observed"])
        else:
            obs = obs or observations(sample)
            if direct and time.monotonic() >= self.next_drift:
                drift_checked, drift = True, self.scan_drift(sample)
            self.verify_recovery(row, obs, drift_checked, drift)
        self.save(row)
        if row["status"] == "verified":
            self.pending = [r for r in self.pending if r["id"] != row["id"]]
        if direct:
            if row["status"] != "verified" and row not in self.pending:
                self.pending.insert(0, row)
            self.publish()

    def follow_controller(self, row, identity, observed):
        run = self.c.detail(identity)
        if not run:
            return
        review = run.get("review")
        if review and "review" not in row:
            row.update(review=review, triaged_at=run.get("decision_at", time.time()))
            row["decision"] = review.get("decision")
            self.last_inference = row["triaged_at"]
            event(row, "Diagnosis", "ollama.assess", review, row["triaged_at"])
        phases = {
            "proposed": "Risk Check",
            "handoff": "Blocked",
            "applying": "Executed",
            "verifying": "Verification Started",
            "resolved": "Verified",
        }
        for e in run["events"][row.get("controller_events", 0) :]:
            if e["phase"] in phases:
                if e["phase"] == "applying":
                    event(row, "Approved", "controller.authorization", e["reason"], e["time"])
                event(row, phases[e["phase"]], "controller." + e["phase"], e["reason"], e["time"])
        row["controller_events"] = len(run["events"])
        row["status"] = "verified" if run["phase"] == "resolved" else run["phase"]
        row["text"] = run["events"][-1]["reason"]
        if row["status"] == "verified":
            from .measurements import outcome

            row.update(verified_healthy_at=observed, outcome=outcome(run))

    def verify_recovery(self, row, obs, drift_checked, drift):
        row["latest_observation"] = obs
        clean = []
        for kind, target, _ in row["facts"]["findings"]:
            value = obs["facts"].get(target, {})
            if kind == "configuration_drift":
                if not drift_checked:
                    continue
                facts = (drift or {}).get("facts", {})
                devices = list(facts.values()) if target == "fabric" else [facts.get(target)]
                clean.append(
                    bool(devices)
                    and all(
                        d is not None
                        and not any(d.get(k) for k in ("missing", "unexpected", "status"))
                        for d in devices
                    )
                )
            elif "/" in target:
                clean.append(
                    not link_failed(value)
                    and value.get("interface") == "UP"
                    and all(value.get(k) == "Established" for k in ("leaf_peer", "spine_peer"))
                )
            elif kind == "high_latency":
                latency = (value.get("icmp") or {}).get("rtt_ms")
                clean.append(latency is not None and latency < self.rules.latency_ms)
            elif kind == "packet_loss":
                loss = packet_loss(value)
                clean.append(loss is not None and loss < self.rules.packet_loss_pct)
            else:
                probes = value.get("probes", {})
                clean.append(bool(probes) and all(s == "success" for s in probes.values()))
        if drift_checked and any(f[0] == "configuration_drift" for f in row["facts"]["findings"]):
            event(row, "Evidence Retrieved", "configuration.drift", drift)
        if clean and not all(clean):
            row["recovery_samples"] = 0
            return
        drift_required = any(f[0] == "configuration_drift" for f in row["facts"]["findings"])
        if drift_required and not drift_checked:
            return
        if not clean:
            return
        streak = row.get("recovery_samples", 0) + 1
        row["recovery_samples"] = streak
        if streak >= self.rules.recovery_samples:
            row.update(
                status="verified", verified_healthy_at=obs["observed"], outcome="observed_recovery"
            )
            event(
                row,
                "Verified",
                "telemetry.collect",
                {"healthy_samples": streak, "observed": obs["observed"]},
            )

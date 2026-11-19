"""Deterministic anomaly detection and a durable, bounded investigation loop."""

import os
import http.client
import time

from pydantic import BaseModel, Field

from . import advisor, investigations as inv
from .incidents import healthy


class Rules(BaseModel):
    window_s: int = Field(default=60, ge=10, le=600)
    degraded_samples: int = Field(default=2, ge=2, le=10)
    flap_transitions: int = Field(default=3, ge=2, le=20)
    cooldown_s: int = Field(default=120, ge=30, le=3600)
    drift_interval_s: int = Field(default=60, ge=10, le=3600)
    recovery_samples: int = Field(default=2, ge=2, le=10)

    @classmethod
    def configured(cls):
        return cls.model_validate_json(os.getenv("NETGUARD_MONITOR_RULES", "{}"))


def detect(history, rules):
    if len(history) < rules.degraded_samples:
        return []
    findings = []
    for key, value in history[-1]["facts"].items():
        values = [h["facts"].get(key, {}) for h in history]
        if "/" in key:
            states = [v.get("leaf_peer") for v in values]
            transitions = sum(a != b for a, b in zip(states, states[1:]) if a and b)
            if transitions >= rules.flap_transitions:
                findings.append(("bgp_flapping", key, transitions))
            if all(
                v.get("interface") != "UP"
                or v.get("leaf_peer") != "Established"
                or v.get("spine_peer") != "Established"
                for v in values[-rules.degraded_samples :]
            ):
                findings.append(("link_or_peer_failure", key, rules.degraded_samples))
        elif all(
            not v.get("probes") or any(s != "success" for s in v["probes"].values())
            for v in values[-rules.degraded_samples :]
        ):
            findings.append(("probe_degradation", key, rules.degraded_samples))
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
        rows = [r for r in inv.cases(controller.db) if r["kind"] == "monitor"]
        self.episode = next((r for r in rows if r.get("status") not in {None, "verified"}), None)
        self.last_inference = max((r.get("triaged_at", r["observed"]) for r in rows), default=0)
        self.recovered = 0
        controller.signal = self.episode
        controller.monitor_rules = self.rules.model_dump()

    def save(self):
        inv.cases(self.c.db, self.episode)
        self.c.signal = self.episode

    def step(self, sample):
        stamp = sample["observed"]
        if stamp <= self.previous or time.time() - stamp > 30:
            return
        self.previous = stamp
        obs = advisor.evidence(sample)
        self.history = [h for h in self.history if stamp - h["observed"] <= self.rules.window_s]
        self.history.append(obs)
        findings = detect(self.history, self.rules)
        drift = None
        if time.monotonic() >= self.next_drift and not self.c.active and not self.episode:
            self.next_drift = time.monotonic() + self.rules.drift_interval_s
            drift = inv.drift(self.c.db, sample, True)
            if drift and any(
                v.get("missing") or v.get("unexpected") for v in drift["facts"].values()
            ):
                findings.append(("configuration_drift", "fabric", drift["id"]))
        if self.episode:
            self.follow(sample)
            return
        controlled = self.c.active and self.c.run.get("ai")
        if not findings or (
            not controlled and time.time() - self.last_inference < self.rules.cooldown_s
        ):
            return
        # Manual benchmark runs retain human diagnosis and do not invoke the model.
        with self.c.lock:
            run = self.c.run if self.c.active else None
            if run and (run.get("manual") or not run.get("ai")):
                return
            run_id = run["id"] if run else None
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
        row.update(status="investigating", incident_id=run_id, affected=[f[1] for f in findings])
        event(row, "Observed", "telemetry.collect", {"observed": stamp}, stamp)
        event(row, "Detected", "monitor.rules", findings)
        event(row, "Evidence Retrieved", "telemetry.snapshot", obs)
        self.episode = row
        self.save()
        # Controlled incidents already use the same orchestrator. Reuse that model call.
        if run_id:
            self.follow(sample)
            return
        self.last_inference = time.time()
        event(row, "Investigation Started", "ollama.assess", {"model": advisor.model_name()})
        self.save()
        try:
            decision = advisor.assess(
                {**obs, "recent_observations": self.history[-4:], "drift": drift}
            )
            row.update(
                decision=decision.model_dump(), model=advisor.model_name(), version=advisor.VERSION
            )
            row["text"] += f" Likely {decision.diagnosis}: {decision.next_check}"
            event(row, "Diagnosis", "ollama.assess", decision.model_dump())
        except (
            ValueError,
            OSError,
            KeyError,
            TypeError,
            http.client.HTTPException,
        ) as exc:
            row["error"] = type(exc).__name__
            row["text"] += (
                " Model unavailable or evidence insufficient; human investigation required."
            )
            event(row, "Diagnosis Unavailable", "ollama.assess", {"error": row["error"]})
        row.update(triaged_at=time.time(), status="escalated")
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
        self.save()

    def follow(self, sample):
        row = self.episode
        identity = row.get("incident_id")
        if identity:
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
            seen = row.get("controller_events", 0)
            for e in run["events"][seen:]:
                if e["phase"] in phases:
                    if e["phase"] == "applying":
                        event(row, "Approved", "controller.authorization", e["reason"], e["time"])
                    event(
                        row, phases[e["phase"]], "controller." + e["phase"], e["reason"], e["time"]
                    )
            row["controller_events"] = len(run["events"])
            row["status"] = "verified" if run["phase"] == "resolved" else run["phase"]
            row["text"] = run["events"][-1]["reason"]
            if run["phase"] == "resolved":
                row["verified_healthy_at"] = sample["observed"]
                row["outcome"] = (
                    "ai_repair"
                    if any(
                        e["phase"] == "applying"
                        and e["reason"].startswith(("approved:", "automatic:"))
                        for e in run["events"]
                    )
                    else "controller_reset"
                )
        else:
            # Drift needs a fresh clean configuration scan, not only healthy forwarding.
            drifting = any(f[0] == "configuration_drift" for f in row["facts"]["findings"])
            drift_clean = not drifting
            if drifting and time.monotonic() >= self.next_drift:
                self.next_drift = time.monotonic() + self.rules.drift_interval_s
                check = inv.drift(self.c.db, sample, True)
                drift_clean = bool(check) and all(
                    not (v.get("missing") or v.get("unexpected") or v.get("status"))
                    for v in check["facts"].values()
                )
                event(row, "Evidence Retrieved", "configuration.drift", check)
            self.recovered = (
                self.recovered + 1
                if healthy(sample) and drift_clean
                else (self.recovered if drifting else 0)
            )
            if self.recovered >= self.rules.recovery_samples:
                row.update(
                    status="verified",
                    verified_healthy_at=sample["observed"],
                    outcome="observed_recovery",
                )
                event(
                    row,
                    "Verified",
                    "telemetry.collect",
                    {"healthy_samples": self.recovered, "observed": sample["observed"]},
                )
        self.save()
        if row["status"] == "verified":
            self.episode = None
            self.c.signal = None
            self.recovered = 0

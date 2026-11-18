"""Aggregate completed, comparable link repairs and export bounded Prometheus labels."""

import json
import sqlite3
from collections import Counter
from statistics import median

from prometheus_client import CollectorRegistry, Gauge, generate_latest
from prometheus_client.core import CounterMetricFamily

from .incidents import RUNBOOK


def summaries(db):
    with sqlite3.connect(db) as conn:
        return [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT json_remove(payload,'$.samples','$.target') FROM incidents ORDER BY rowid"
            )
        ]


def comparison(runs):
    groups = {False: [], True: []}
    for r in runs:
        if r.get("ai"):
            continue
        if r["scenario"] == "link" and r["phase"] == "resolved" and r["attempts"] == 1:
            expected = "automatic" if r["auto"] else "approved"
            if any(e["reason"] == expected + ": RESTORE_INJECTED_LINK" for e in r["events"]):
                groups[r["auto"]].append(r["metrics"]["closure_s"])
    return [
        {
            "automatic": mode,
            "n": len(times),
            "median": median(times),
            "min": min(times),
            "max": max(times),
        }
        for mode, times in groups.items()
        if times
    ]


def export(db, snapshot, flows):
    registry = CollectorRegistry()
    sessions = Gauge(
        "netguard_bgp_established_sessions", "Adjacencies confirmed at both ends", registry=registry
    )
    if snapshot and all(r.peers is not None for r in snapshot.routers):
        states = {r.name: r.peers for r in snapshot.routers}
        sessions.set(
            sum(
                states[f"leaf-{leaf}"].get(f"10.211.{10*spine+leaf}.1") == "Established"
                and states[f"spine-{spine}"].get(f"10.211.{10*spine+leaf}.2") == "Established"
                for spine in (1, 2)
                for leaf in (1, 2)
            )
        )
    else:
        sessions.set(float("nan"))
    success = Gauge(
        "netguard_probe_success",
        "Most recent host probe",
        ["source", "protocol"],
        registry=registry,
    )
    for flow in flows:
        for protocol, result in flow["probes"].items():
            success.labels(str(flow["source"]), protocol).set(
                {"success": 1, "failed": 0}.get(result, float("nan"))
            )
    detections = Gauge(
        "netguard_fault_detection_seconds",
        "Latest confirmed-injection to detector sample",
        ["scenario"],
        registry=registry,
    )
    recovery = Gauge(
        "netguard_recovery_seconds",
        "Latest observed loss to recovered sequence, relative to injection",
        ["scenario"],
        registry=registry,
    )
    counts, actions = Counter(), Counter()
    for r in summaries(db):
        scenario = r["scenario"]
        for event in r["events"]:
            if event["phase"] in {"injected", "detected", "resolved", "handoff"}:
                counts[(event["phase"], scenario)] += 1
            if event["phase"] == "applying":
                actions[(RUNBOOK[scenario], "attempted")] += 1
            if event["phase"] == "resolved":
                actions[(RUNBOOK[scenario], "verified")] += 1
        if "detection_s" in r["metrics"]:
            detections.labels(scenario).set(r["metrics"]["detection_s"])
        if "traffic_recovery_s" in r["metrics"]:
            recovery.labels(scenario).set(r["metrics"]["traffic_recovery_s"])

    class Totals:
        def collect(self):
            for name, labels, values in [
                ("netguard_incidents", ["status", "scenario"], counts),
                ("netguard_actions", ["action", "outcome"], actions),
            ]:
                metric = CounterMetricFamily(
                    name, "Recorded incident journal transitions", labels=labels
                )
                for key, count in values.items():
                    metric.add_metric(key, count)
                yield metric

    registry.register(Totals())
    return generate_latest(registry)


def impact(db):
    """Matched medians require the same fabric, fault, timing and approval policy."""
    groups = {}
    diagnosed = evaluated = rejected = escalated = verified = attempted = 0
    recent = []
    required_handoffs = correct_handoffs = 0
    provisions = {}
    for r in summaries(db):
        kind = "assisted" if r.get("ai") else "manual" if r.get("manual") else None
        if not kind:
            continue
        decision = (r.get("review") or {}).get("decision") or {}
        leaf, spine = r.get("leaf", 1), r.get("spine", 1)
        expected = "host-2" if r["scenario"] == "endpoint" else f"leaf-{leaf}/spine-{spine}"
        correct = (
            decision.get("diagnosis") == r["scenario"] and decision.get("component") == expected
        )
        if kind == "assisted" and r.get("review"):
            evaluated += 1
            diagnosed += correct
            human_review = any(
                e["phase"] == "handoff" and e["reason"].startswith(("Human review", "Local model"))
                for e in r["events"]
            )
            escalated += human_review
            if r["scenario"] in {"bgp", "endpoint"}:
                required_handoffs += 1
                correct_handoffs += human_review
            rejected += (
                decision.get("action") == "restore_link"
                and not any(e["phase"] == "proposed" for e in r["events"])
                and human_review
            )
        if r.get("attempts"):
            attempted += r["attempts"]
            verified += r["phase"] == "resolved"
        recent.append(
            {
                "at": r["events"][-1]["time"] if r["events"] else 0,
                "task": r["scenario"],
                "mode": kind,
                "outcome": r["phase"],
                "seconds": r["metrics"].get("closure_s"),
            }
        )
        if "baseline_routers" not in r:
            continue
        key = (
            r["scenario"],
            leaf,
            spine,
            tuple(r["baseline_routers"]),
            r["auto"],
            r.get("duration"),
        )
        group = groups.setdefault(key, {"manual": {}, "assisted": {}})[kind]
        if r.get("first_signal_at") and r.get("diagnosed_at"):
            group.setdefault("Diagnosis", []).append(
                max(0, r["diagnosed_at"] - r["first_signal_at"])
            )
        if "traffic_recovery_s" in r["metrics"]:
            group.setdefault("Recovery", []).append(r["metrics"]["traffic_recovery_s"])
        if r["phase"] == "resolved" and "closure_s" in r["metrics"]:
            group.setdefault("Verified closure", []).append(r["metrics"]["closure_s"])
    rows = []
    for key, modes in groups.items():
        for metric in ("Diagnosis", "Recovery", "Verified closure"):
            a, b = (modes[k].get(metric, []) for k in ("manual", "assisted"))
            if a or b:
                rows.append(
                    {
                        "metric": metric,
                        "scenario": f"{key[0]} · L{key[1]} / S{key[2]} · {len(key[3])} routers · {'automatic' if key[4] else 'approval'}"
                        + (f" · {key[5]}s" if key[5] else ""),
                        "manual": median(a) if a else None,
                        "assisted": median(b) if b else None,
                        "n": [len(a), len(b)],
                    }
                )
    with sqlite3.connect(db) as conn:
        for (raw,) in conn.execute("SELECT data FROM expansions ORDER BY rowid"):
            p = json.loads(raw)
            end = next((e["time"] for e in p["events"] if e["phase"] == "verified"), None)
            if any(e["phase"] == "applying" for e in p["events"]):
                attempted += 1
                verified += end is not None
                recent.append(
                    {
                        "at": p["events"][-1]["time"],
                        "task": "Provisioning",
                        "mode": "assisted",
                        "outcome": p["phase"],
                        "seconds": end - p["started"] if end else None,
                    }
                )
                if end:
                    provisions.setdefault(len(p["leaves"]), []).append(end - p["started"])
    for size, times in provisions.items():
        rows.append(
            {
                "metric": "Provisioning",
                "scenario": f"{size} new leaves",
                "manual": None,
                "assisted": median(times),
                "n": [0, len(times)],
            }
        )
    matched = [
        r["manual"] - r["assisted"]
        for r in rows
        if r["metric"] == "Verified closure" and all(r["n"])
    ]
    return {
        "rows": rows,
        "saved": median(matched) if matched else None,
        "correct": diagnosed,
        "evaluated": evaluated,
        "verified": verified,
        "attempted": attempted,
        "rejected": rejected,
        "escalated": escalated,
        "required_handoffs": required_handoffs,
        "correct_handoffs": correct_handoffs,
        "recent": sorted(recent, key=lambda row: row["at"], reverse=True)[:8],
    }

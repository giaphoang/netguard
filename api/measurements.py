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

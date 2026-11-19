"""Project controller evidence into reusable chat content blocks."""

import time


def section(title, blocks, *, collapsed=False):
    return {"type": "section", "title": title, "blocks": blocks, "collapsed": collapsed}


def text(value):
    return {"type": "text", "text": value}


def table(title, columns, rows):
    return {"type": "table", "title": title, "columns": columns, "rows": rows}


def evidence(title, value):
    return {"type": "data", "title": title, "value": value}


def link(label, href):
    return {"type": "link", "label": label, "href": href}


def topology(before, after, **options):
    return {
        "type": "topology",
        "title": "Topology comparison",
        "before": before,
        "after": after,
        **options,
    }


def code(title, value):
    return {"type": "code", "title": title, "text": value}


def experiment_trace(trace):
    return [
        {
            "label": event["tool"],
            "stage": event.get("stage"),
            "target": " · ".join(
                str(event[key]) for key in ("node", "case", "version") if key in event
            ),
            "status": event["status"],
            "started": event["started"],
            "completed": event.get("completed"),
            "arguments": event.get("arguments"),
            "result": event.get("result"),
            "error": event.get("error"),
        }
        for event in trace
    ]


METRICS = {
    "gbps": ("Aggregate TCP goodput", "Gbps", 2),
    "p95_ms": ("p95 ICMP RTT", "ms", 3),
    "loss_pct": ("ICMP packet loss", "%", 1),
    "guest_cpu_pct": ("Guest CPU utilization", "%", 1),
    "healthy_paths": ("Healthy spine paths", "", 0),
    "mtu_bytes": ("Usable IPv4 MTU", "bytes", 0),
}


def readings(run):
    baseline = run["baseline"]
    actual = run.get("actual", {})
    plan = run.get("plan", {})
    prediction = plan.get("prediction", {})
    evaluation = run.get("evaluation", {})
    delta = evaluation.get("prediction_error", {})
    method = run["method"]

    def value(values, key, missing="—", *, difference=False):
        measurement = values.get(key)
        if measurement is None:
            return missing
        _, unit, digits = METRICS[key]
        prefix = "+" if difference and measurement > 0 else ""
        return (f"{prefix}{measurement:.{digits}f} {unit}").strip()

    rows = [
        [
            label,
            value(baseline, key),
            value(prediction, key, "Uncertain" if plan else "—"),
            value(actual, key, "Pending" if run["phase"] in {"preview", "applying"} else "—"),
            value(delta, key, difference=True),
        ]
        for key, (label, _, _) in METRICS.items()
        if key in baseline or key in prediction or key in actual
    ]
    caption = (
        f"{method['repeats']} matched TCP trials with {method['tcp_streams']} parallel "
        f"streams over {method['tcp_seconds']} seconds each. Goodput is the median received rate."
    )
    blocks = [
        text(caption),
        table(
            "Prediction vs. measured results",
            [
                "Metric",
                "Direct path" if plan.get("overlay") else "Baseline",
                "Predicted",
                "Measured",
                "Prediction error",
            ],
            rows,
        ),
    ]
    if plan.get("goal_gbps") is not None:
        goal = f"Target {plan['goal_gbps']:.2f} Gbps"
        if evaluation.get("goal_met") is not None:
            goal += (
                " · Reached in this workload"
                if evaluation["goal_met"]
                else " · Below target in this workload"
            )
        blocks.append(text(goal))
    for detail in (plan.get("assumptions"), evaluation.get("interpretation")):
        if detail:
            blocks.append(text(detail))
    if run.get("encapsulation"):
        blocks.append(evidence("GRE packet capture", run["encapsulation"]["capture"]))
    before_links, after_links = baseline.get("link_bytes", {}), actual.get("link_bytes", {})
    if before_links:
        before_total, after_total = sum(before_links.values()), sum(after_links.values())
        blocks.append(
            table(
                "Transit egress byte share",
                ["Path", "Baseline", "Measured"],
                [
                    [
                        name,
                        f"{100 * before_links.get(name, 0) / before_total:.1f}%"
                        if before_total
                        else "—",
                        f"{100 * after_links.get(name, 0) / after_total:.1f}%"
                        if after_total
                        else "—",
                    ]
                    for name in sorted(before_links.keys() | after_links.keys())
                ],
            )
        )
    return blocks


def design(plan, phase):
    blocks = []
    if plan.get("alternatives"):
        blocks.append(
            table(
                "Design alternatives",
                ["Design", "Assessment", "Cost"],
                [[row["name"], row["assessment"], row["cost"]] for row in plan["alternatives"]],
            )
        )
    if plan.get("before") and plan.get("after"):
        blocks.append(
            topology(
                plan["before"],
                plan["after"],
                overlay=plan.get("overlay"),
                after_label="Deployed" if phase == "verified" else "Proposed",
            )
        )
    if plan.get("diff"):
        blocks.append(code("Configuration diff", plan["diff"]))
    for name in ("reason", "rollback"):
        if plan["risk"].get(name):
            blocks.append(text(plan["risk"][name]))
    return section("Design and risk review", blocks)


def quality(report):
    versions = report["summary"]
    metrics = [
        ("Correct class and component", lambda x: f"{x['diagnosis_correct']} / {x['n']}"),
        ("Correct human handoffs", lambda x: f"{x['handoff_correct']} / {x['handoff_required']}"),
        ("Incorrect repair suggestions", lambda x: x["unsafe_proposals"]),
        ("Incorrect repairs passing assessment", lambda x: x["unsafe_accepted"]),
        ("Policy blocks", lambda x: x["guard_blocks"]),
        ("Workflow errors", lambda x: x["errors"]),
        ("Median case latency", lambda x: f"{x['median_latency_s']:.2f} s"),
        ("Model calls", lambda x: x["model_calls"]),
        ("Mean context size", lambda x: f"{x['mean_context_bytes']:.0f} bytes"),
    ]
    blocks = [
        table(
            "Paired workflow comparison",
            ["Metric", *versions],
            [
                [name, *(get_value(version) for version in versions.values())]
                for name, get_value in metrics
            ],
        ),
        text(report["recommendation"]),
        text(report["limits"]),
    ]
    if report.get("record_id"):
        blocks.append(link("AI Impact evidence", "/api/investigations/" + report["record_id"]))
    cases = {}
    for row in report["results"]:
        cases.setdefault(row["id"], []).append(
            {
                key: row[key]
                for key in (
                    "version",
                    "expected",
                    "decision",
                    "rejected_decision",
                    "error",
                    "latency_s",
                    "diagnosis_correct",
                    "tool_calls",
                )
                if key in row
            }
        )
    blocks.append(
        section(
            "Case outcomes",
            [evidence(identity, results) for identity, results in cases.items()],
            collapsed=True,
        )
    )
    return blocks


PHASES = {
    "investigating": "Investigating",
    "preview": "Awaiting approval",
    "applying": "Implementing approved experiment",
    "verified": "Verified",
    "rolling_back": "Restoring",
    "rolled_back": "Restored",
    "blocked": "Blocked",
    "interrupted": "Interrupted",
    "needs_review": "Needs operator review",
    "evaluated": "Evaluation complete",
}


def experiment(run):
    identity, phase = run["id"], run["phase"]
    blocks = []
    if run.get("failure") and run["failure"] != run["message"]:
        blocks.append(text(run["failure"]))
    if run.get("baseline"):
        blocks.extend(readings(run))
    if run.get("plan"):
        blocks.append(design(run["plan"], phase))
    if run.get("quality"):
        blocks.extend(quality(run["quality"]))
    blocks.append(
        section(
            "Objective and provenance",
            [
                text(run["prompt"]),
                evidence(
                    "Experiment metadata",
                    {
                        key: run[key]
                        for key in (
                            "id",
                            "created",
                            "model",
                            "model_revision",
                            "version",
                            "prompt_sha256",
                            "endpoints",
                            "scope",
                            "approval",
                        )
                        if key in run
                    },
                ),
                link("Full experiment evidence", "/api/architecture/" + identity),
            ],
            collapsed=True,
        )
    )
    actions = []
    if phase == "preview" and time.time() < run["expires"]:
        actions.append(
            {
                "key": "approve",
                "label": "Approve change and benchmark",
                "primary": True,
                "revision": run["digest"],
                "expires": run["expires"],
            }
        )
    if phase in {"verified", "needs_review"}:
        actions.append({"key": "rollback", "label": "Restore original configuration"})
    return {
        "title": PHASES.get(phase, phase),
        "text": run["message"],
        "blocks": blocks,
        "trace": experiment_trace(run["trace"]),
        "actions": actions,
        "refresh_seconds": 1 if phase in {"investigating", "applying", "rolling_back"} else 0,
        "href": "/agent/#experiment=" + identity,
    }


def provisioning(plan, *, current, latest):
    from . import capacity

    phase = plan["phase"]
    verification = plan.get("verification", {})
    blocks = []
    if plan.get("capacity"):
        blocks.extend(capacity.blocks(plan["capacity"], verification.get("resources")))
    blocks.append(
        design(
            {
                "before": plan.get("before"),
                "after": plan.get("topology"),
                "diff": plan["diff"],
                "risk": plan.get("risk", {}),
            },
            phase,
        )
    )
    if plan.get("failure"):
        blocks.append(text(plan["failure"]))
    stages = {
        "preview": ("Render Compose and FRR configuration", "Plan"),
        "applying": ("Deploy validated Compose configuration", "Implement"),
        "verified": ("Verify BGP, host probes and any requested resources", "Verify"),
        "rolling_back": ("Restore earlier Compose configuration", "Rollback"),
        "rolled_back": ("Verify restored topology", "Verify"),
    }
    trace = []
    for event in plan["events"]:
        name, stage = stages.get(event["phase"], (event["phase"].replace("_", " "), "Controller"))
        trace.append(
            {
                "label": name,
                "stage": stage,
                "started": event["time"],
                "status": "running"
                if event["phase"] == phase and phase in {"applying", "rolling_back"}
                else "recorded",
                "result": verification
                if event["phase"] == "verified"
                else plan.get("rollback_verification")
                if event["phase"] == "rolled_back"
                else None,
            }
        )
    blocks.append(link("Full provisioning evidence", "/api/provision/" + plan["id"]))
    actions = []
    if phase == "preview" and latest:
        actions.append(
            {
                "key": "approve",
                "label": "Approve deployment",
                "primary": True,
                "revision": plan["digest"],
            }
        )
    if phase in {"verified", "needs_review"} and current:
        actions.append({"key": "rollback", "label": "Restore original configuration"})
    return {
        "title": PHASES.get(phase, phase),
        "text": f"Fabric change adding {', '.join(plan['added'])}.",
        "blocks": blocks,
        "trace": trace,
        "actions": actions,
        "refresh_seconds": 1 if phase in {"applying", "rolling_back"} else 0,
    }

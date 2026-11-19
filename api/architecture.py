"""Durable, approval-gated architecture experiments on the isolated NetGuard fabric."""

import hashlib
import http.client
import json
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from . import (
    advisor,
    architecture_plans as plans,
    expansion,
    gre,
    intents,
    investigations,
    protocol_study,
    provision,
    quality,
)
from .benchmarks import DURATION, STREAMS, TRIALS, Lab
from .controls import authorized, local
from .incidents import healthy, observe
from .routing_lab import RoutingLab

router = APIRouter(prefix="/api/architecture")
RESERVED = ("investigating", "applying", "rolling_back", "verified", "needs_review")
ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    subprocess.SubprocessError,
    http.client.HTTPException,
)
PREVIEW_TTL = 900


class Request(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    selection: str = Field(default="", max_length=60)


class Objective(BaseModel):
    objective: Literal["throughput", "gre", "evaluation", "unsupported"]


def objective_schema(prompt):
    schema = Objective.model_json_schema()
    # Narrow the tools when the request names a tunnel protocol and contains no AI evaluation target.
    tunnel = re.search(r"\b(?:gre|generic routing encapsulation|mpls|ip.in.ip)\b", prompt, re.I)
    workflow = re.search(r"\b(?:prompt|retrieval|diagnosis workflow|model)\b", prompt, re.I)
    if tunnel and not workflow:
        options = ["unsupported"]
        if re.search(r"\b(?:gre|generic routing encapsulation)\b", prompt, re.I):
            options.insert(0, "gre")
        schema["properties"]["objective"]["enum"] = options
    return schema


class Approval(BaseModel):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@contextmanager
def journal(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS architecture_runs "
            "(id TEXT PRIMARY KEY, phase TEXT NOT NULL, data TEXT NOT NULL)"
        )
        yield db


def load(path, identity):
    with journal(path) as db:
        row = db.execute("SELECT data FROM architecture_runs WHERE id=?", (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def save(path, run):
    with journal(path) as db:
        db.execute(
            "INSERT INTO architecture_runs VALUES(?,?,?) ON CONFLICT(id) "
            "DO UPDATE SET phase=excluded.phase, data=excluded.data",
            (run["id"], run["phase"], json.dumps(run, allow_nan=False)),
        )


def reservation(path):
    with journal(path) as db:
        row = db.execute(
            "SELECT id FROM architecture_runs WHERE phase IN (?, ?, ?, ?, ?) LIMIT 1", RESERVED
        ).fetchone()
    return row[0] if row else None


def require_idle(path):
    if reservation(path):
        raise ValueError("Finish or roll back the active Architecture Lab experiment first.")


def recover(path):
    with journal(path) as db:
        rows = db.execute(
            "SELECT data FROM architecture_runs WHERE phase IN "
            "('investigating','applying','rolling_back')"
        ).fetchall()
    for (data,) in rows:
        run = json.loads(data)
        Trace(path, run).mark(
            "needs_review" if run.get("change_started") else "interrupted",
            "Controller restarted during the experiment. Review the saved evidence.",
        )


class Trace:
    def __init__(self, path, run):
        self.path = path
        self.run = run
        self.stage = "Understand"
        self.lock = threading.Lock()

    def mark(self, phase, detail):
        self.run.update(phase=phase, message=detail, updated=time.time())
        save(self.path, self.run)

    @contextmanager
    def tool(self, name, **inputs):
        event = {
            "stage": self.stage,
            "tool": name,
            "started": time.time(),
            "status": "running",
            **inputs,
        }
        with self.lock:
            index = len(self.run["trace"])
            self.run["trace"].append(dict(event))
            save(self.path, self.run)
        try:
            yield event
            event["status"] = "complete" if event.get("exit_code", 0) == 0 else "nonzero exit"
        except Exception as exc:
            event.update(status="failed", error=error_text(exc))
            raise
        finally:
            event["completed"] = time.time()
            with self.lock:
                self.run["trace"][index] = event
                save(self.path, self.run)


def error_text(exc):
    if isinstance(exc, ValidationError):
        return "The model returned an invalid structured response."
    if isinstance(exc, subprocess.CalledProcessError):
        return (
            f"Tool exited with status {exc.returncode}: "
            + (exc.stderr or exc.stdout or "no output").strip()[:500]
        )
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"Tool timed out after {exc.timeout} seconds."
    return str(exc)[:500]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def digest(run):
    return fingerprint(
        {
            key: run[key]
            for key in ("id", "scope", "version", "kind", "endpoints", "plan", "method", "baseline")
        }
    )


def check_controller(controller, *, rollback=False):
    latest = provision.latest(controller.db)
    if (
        controller.active
        or latest
        and latest["phase"] in {"applying", "rolling_back", "needs_review"}
    ):
        raise ValueError("Finish the active incident or topology operation first.")
    if controller.stopped and not rollback:
        raise ValueError("Emergency stop is enabled.")


def current_fabric(controller):
    active = expansion.current(controller.db)
    if active and active["phase"] != "verified":
        raise ValueError("The architecture workflow requires a verified topology.")
    return active, expansion.spec(active)


def inspect_fabric(trace, spec):
    with trace.tool("Inspect FRR peers, kernel routes and host probes") as event:
        sample = observe()
        event["result"] = sample
        if {r["name"] for r in sample["routers"]} | {h["name"] for h in sample["hosts"]} != set(
            expansion.names(spec)
        ) or not healthy(sample):
            raise ValueError(
                "The current fabric must have established BGP, all paths and healthy probes."
            )
    return sample


def open_scope(controller, lab, active, spec, pair, *, benchmark=True):
    nodes = list(
        dict.fromkeys(node for endpoint in pair for node in (endpoint.node, endpoint.router))
    )
    lab.inspect(spec, expansion.approved_origins(controller.db, active), nodes, benchmark=benchmark)
    return {
        "topology": spec,
        "provision_id": active["id"] if active else None,
        "identities": lab.identities,
        "containers": lab.containers,
    }


def checkpoint(controller):
    if controller.stopped:
        raise ValueError("Emergency stop requested. End the experiment.")


def measure(controller, lab, pair, spec, *, single=False, sockets=None):
    paths = plans.route_paths(lab, pair, spec)
    source, target = sockets or [endpoint.socket for endpoint in pair]
    result = lab.benchmark(
        source,
        target,
        counters=lambda: plans.link_counters(lab, pair[0], spec["spines"]),
        checkpoint=lambda: checkpoint(controller),
        single=single,
    )
    result.update(paths=paths, healthy_paths=min(map(len, paths.values())))
    return result


def prepare(controller, run):
    trace = Trace(controller.db, run)
    try:
        active, spec = current_fabric(controller)
        run["topology"] = spec
        with trace.tool("Read the installed model revision", model=run["model"]) as event:
            run["model_revision"] = event["result"] = advisor.model_revision()
        with trace.tool("Interpret objective with the local model", model=run["model"]) as event:
            schema = objective_schema(run["prompt"])
            objective = Objective.model_validate_json(
                advisor.infer(
                    plans.INTENT_PROMPT,
                    {"request": run["prompt"], "available_nodes": expansion.names(spec)},
                    schema,
                )
            ).objective
            if objective not in schema["properties"]["objective"]["enum"]:
                raise ValueError("The selected experiment does not match the requested protocol.")
            event["result"] = objective
            run["kind"] = objective
        if objective == "unsupported":
            trace.mark("blocked", "Choose a throughput, GRE or workflow evaluation objective.")
            return
        if objective == "evaluation":
            run["method"] = {"role": "Alternating paired replay on labeled observations"}
            report = quality.run_suite(trace, lambda: checkpoint(controller), run["model_revision"])
            record = investigations.record(
                controller.db,
                "reliability evaluation",
                report["recommendation"],
                report,
            )
            run["quality"] = {**report, "record_id": record["id"]}
            trace.mark("evaluated", "Paired diagnosis workflow evaluation complete.")
            return
        is_gre = objective == "gre"
        multiplier = None if is_gre else plans.requested_multiplier(run["prompt"])
        run["requested_multiplier"] = multiplier
        trace.stage = "Investigate"
        run["snapshot"] = inspect_fabric(trace, spec)
        select_endpoints = gre.endpoints if is_gre else plans.endpoints
        pair = select_endpoints(run["prompt"], run["selection"], spec, run["snapshot"])
        run["endpoints"] = [asdict(endpoint) for endpoint in pair]
        with Lab(trace) as lab:
            run["scope"] = open_scope(controller, lab, active, spec, pair)
            underlay = gre.underlay(lab, pair, spec["spines"]) if is_gre else {}
            run["baseline"] = {
                **underlay,
                **measure(controller, lab, pair, spec, single=not is_gre),
            }
            trace.stage = "Plan"
            run["plan"] = (
                gre.plan(lab, pair, spec, run["baseline"], run["id"])
                if is_gre
                else plans.throughput(lab, pair, spec, run["baseline"], multiplier)
            )
        trace.stage = "Validate"
        with trace.tool("Review policy, capacity and rollback") as event:
            checkpoint(controller)
            if run["baseline"]["loss_pct"]:
                run["plan"]["risk"].update(
                    eligible=False, reason="The baseline ICMP workload observed packet loss."
                )
            event["result"] = run["plan"]["risk"]
        run["digest"] = digest(run)
        run["expires"] = time.time() + PREVIEW_TTL
        eligible = run["plan"]["risk"]["eligible"]
        trace.mark(
            "preview" if eligible else "blocked",
            "Review the prediction and configuration diff."
            if eligible
            else run["plan"]["risk"]["reason"],
        )
    except ERRORS as exc:
        trace.mark("blocked", error_text(exc))


def prepare_routing(controller, run):
    trace = Trace(controller.db, run)
    try:
        protocols = protocol_study.requested_protocols(run["prompt"])
        if len(protocols) < 2:
            raise ValueError("Choose at least two routing protocols for comparison.")
        with trace.tool("Resolve the requested protocols", model=run["model"]) as event:
            event["result"] = protocols
        with trace.tool("Read the installed model revision", model=run["model"]) as event:
            run["model_revision"] = event["result"] = advisor.model_revision()
        active, spec = current_fabric(controller)
        trace.stage = "Investigate"
        run["snapshot"] = inspect_fabric(trace, spec)
        lab = RoutingLab(controller.db, run["id"], trace)
        trace.stage = "Plan"
        run["plan"] = protocol_study.draft(protocols, lab.project)
        trace.stage = "Validate"
        runtime = lab.runtime(spec, run["plan"])
        with trace.tool("Select an owned FRR image for syntax checks") as event:
            owners = expansion.ownership(spec, expansion.approved_origins(controller.db, active))
            source = next(
                (
                    owner["Id"]
                    for owner in owners.values()
                    if owner["Image"] == runtime["images"][protocol_study.ROUTER_IMAGE]
                ),
                None,
            )
            if not source:
                raise ValueError(
                    "An owned router with the local trial FRR image is required for syntax checks."
                )
            event["result"] = source
        lab.validate(run["plan"], source, lambda: checkpoint(controller))
        checkpoint(controller)
        run["scope"] = {
            "topology": spec,
            "provision_id": (active or {}).get("id"),
            "runtime": runtime,
        }
        run["digest"] = digest(run)
        run["expires"] = time.time() + PREVIEW_TTL
        trace.mark(
            "preview",
            "Review the predicted convergence, fault scope, and configuration before approving the comparison.",
        )
    except ERRORS as exc:
        trace.mark("blocked", error_text(exc))


def apply_routing(controller, run):
    trace = Trace(controller.db, run)
    trace.stage = "Approve and implement"
    try:
        lab = RoutingLab(controller.db, run["id"], trace)
        active, spec = current_fabric(controller)
        plan = run["plan"]
        runtime = lab.runtime(spec, plan)
        if run["scope"] != {
            "topology": spec,
            "provision_id": (active or {}).get("id"),
            "runtime": runtime,
        }:
            raise ValueError(
                "The active fabric or trial runtime changed. Generate a fresh preview."
            )
        inspect_fabric(trace, spec)
        run["actual"], run["evaluation"] = {}, {}
        for protocol in plan["arms"]:
            trace.stage = "Approve and implement"
            lab.preflight(plan)
            with controller.lock:
                checkpoint(controller)
                run.update(active_arm=protocol, change_started=True)
                trace.mark(
                    "applying", f"Starting the approved {protocol} trial in the isolated project."
                )
            lab.start(plan, protocol)
            trace.stage = "Verify"
            trial = lab.trial(
                plan, protocol, runtime, run["method"], lambda: checkpoint(controller)
            )
            run["actual"][protocol] = trial
            trace.stage = "Evaluate"
            with trace.tool(
                "Compare measured results with the saved prediction", node=protocol
            ) as event:
                run["evaluation"][protocol] = event["result"] = protocol_study.assess(
                    trial, plan["prediction"]
                )
            trace.stage = "Clean up"
            lab.remove(plan, protocol, runtime)
            run["active_arm"] = None
            save(controller.db, run)
        trace.stage = "Verify"
        run["verification"] = inspect_fabric(trace, spec)
        run["change_started"] = False
        trace.mark(
            "completed",
            "Routing comparison complete. The trial containers and networks were removed and the active fabric passed verification.",
        )
    except ERRORS as exc:
        run["failure"] = error_text(exc)
        if run.get("change_started"):
            restore_routing(controller, run, trace)
        else:
            trace.mark("blocked", run["failure"])


def restore_routing(controller, run, trace):
    trace.stage = "Clean up"
    trace.mark("rolling_back", "Removing the isolated trial and rechecking the active fabric.")
    try:
        if run.get("active_arm"):
            lab = RoutingLab(controller.db, run["id"], trace)
            lab.remove(run["plan"], run["active_arm"], run["scope"]["runtime"])
            run["active_arm"] = None
        _, spec = current_fabric(controller)
        run["rollback_verification"] = inspect_fabric(trace, spec)
        run["change_started"] = False
        trace.mark(
            "rolled_back",
            "The isolated trial was removed and the active fabric passed verification.",
        )
    except ERRORS as exc:
        trace.mark("needs_review", error_text(exc))


def verdict(run):
    baseline, actual = run["baseline"], run["actual"]
    prediction = run["plan"]["prediction"]
    measured = actual["gbps"] / baseline["gbps"]
    goal = run["requested_multiplier"]
    before_trials = [trial["gbps"] for trial in baseline["trials"]]
    after_trials = [trial["gbps"] for trial in actual["trials"]]
    overlaps = min(max(before_trials), max(after_trials)) >= max(
        min(before_trials), min(after_trials)
    )
    total_bytes = sum(actual["link_bytes"].values())
    busiest_share = (
        max(actual["link_bytes"].values(), default=0) / total_bytes if total_bytes else None
    )
    interpretation = []
    if overlaps:
        interpretation.append(
            "The trial ranges overlap, so the median change needs more repetitions."
        )
    if goal is not None and measured < goal and busiest_share is not None and busiest_share < 0.95:
        interpretation.append(
            "Traffic spread across more spine paths, and the aggregate target was missed. "
            "This is consistent with shared forwarding or endpoint limits."
        )
    if min(actual["guest_cpu_pct"], baseline["guest_cpu_pct"]) >= 90:
        interpretation.append("Median guest CPU utilization was at least 90% in both arms.")
    if run["kind"] == "gre":
        interpretation.append(
            f"DF pings measured an inner MTU of {actual['mtu_bytes']} bytes. "
            "The wire capture confirmed bidirectional GRE."
        )
    return {
        "measured_multiplier": measured,
        "goal_met": None if goal is None else measured >= goal,
        "prediction_error": {
            key: actual[key] - value
            for key, value in prediction.items()
            if value is not None and actual.get(key) is not None
        },
        "finding": (
            f"Measured {measured:.2f}× the baseline with {actual['healthy_paths']} installed paths "
            f"and {actual['loss_pct']:.1f}% ICMP loss during the sampled runs."
        ),
        "interpretation": " ".join(interpretation),
    }


def apply(controller, run):
    trace = Trace(controller.db, run)
    try:
        active, spec = current_fabric(controller)
        pair = tuple(plans.Endpoint(**row) for row in run["endpoints"])
        trace.stage = "Approve and implement"
        with Lab(trace) as lab:
            scope = open_scope(controller, lab, active, spec, pair)
            if scope != run["scope"]:
                raise ValueError(
                    "The topology or endpoint namespaces changed. Review the saved plan."
                )
            inspect_fabric(trace, spec)
            is_gre = run["kind"] == "gre"
            if is_gre:
                live = gre.underlay(lab, pair, spec["spines"])
                if live["route_mtu"] != run["baseline"]["route_mtu"]:
                    raise ValueError("The underlay MTU changed. Generate a fresh preview.")
            configure = gre.configure if is_gre else plans.set_policies
            configure(
                lab,
                run["plan"],
                before_write=lambda node: begin_change(controller, run, trace, node),
            )
            trace.stage = "Verify"
            run["verification"] = inspect_fabric(trace, spec)
            sockets = None
            if is_gre:
                run["encapsulation"] = gre.verify(lab, run["plan"])
                sockets = gre.sockets(run["plan"]["change"])
            run["actual"] = measure(controller, lab, pair, spec, sockets=sockets)
            if is_gre:
                run["actual"]["mtu_bytes"] = run["encapsulation"]["mtu_bytes"]
            inspect_fabric(trace, spec)
            trace.stage = "Evaluate"
            with trace.tool("Compare prediction with measurements") as event:
                run["evaluation"] = event["result"] = verdict(run)
                if (
                    run["actual"]["loss_pct"]
                    or run["actual"]["healthy_paths"] < run["baseline"]["healthy_paths"]
                ):
                    raise ValueError("Packet loss or path loss breached the experiment guard.")
                if run["actual"]["iperf3"] != run["baseline"]["iperf3"]:
                    raise ValueError("The benchmark tool changed between measurements.")
                checkpoint(controller)
        trace.mark("verified", run["evaluation"]["finding"])
    except ERRORS as exc:
        run["failure"] = error_text(exc)
        if not run.get("change_started"):
            trace.mark("blocked", run["failure"])
            return
        restore(controller, run, trace)


def begin_change(controller, run, trace, node):
    checkpoint(controller)
    if node not in run.setdefault("touched", []):
        run["touched"].append(node)
    run["change_started"] = True
    trace.mark("applying", "Applying the approved configuration.")


def restore(controller, run, trace):
    trace.stage = "Rollback"
    trace.mark("rolling_back", "Restoring the recorded configuration.")
    try:
        active, spec = current_fabric(controller)
        pair = tuple(plans.Endpoint(**row) for row in run["endpoints"])
        with Lab(trace) as lab:
            scope = open_scope(controller, lab, active, spec, pair, benchmark=False)
            if (
                scope["topology"] != run["scope"]["topology"]
                or scope["provision_id"] != run["scope"]["provision_id"]
            ):
                raise ValueError("The approved topology changed. Review rollback manually.")
            restore_change = gre.restore if run["kind"] == "gre" else plans.restore_policies
            restore_change(lab, run["plan"], run["touched"], run["scope"]["identities"])
            run["rollback_verification"] = inspect_fabric(trace, spec)
        trace.mark("rolled_back", "Original configuration restored and connectivity verified.")
    except ERRORS as exc:
        trace.mark("needs_review", error_text(exc))


def get_run(controller, identity):
    run = load(controller.db, identity)
    if run is None:
        raise HTTPException(404, "Unknown architecture experiment.")
    return run


@router.get("")
def history(controller=Depends(local)):
    with journal(controller.db) as db:
        rows = db.execute(
            "SELECT data FROM architecture_runs ORDER BY rowid DESC LIMIT 30"
        ).fetchall()
    return [
        {key: run[key] for key in ("id", "phase", "prompt", "created", "message")}
        for (data,) in rows
        for run in [json.loads(data)]
    ]


@router.get("/{identity}")
def evidence(identity: str, controller=Depends(local)):
    return get_run(controller, identity)


@router.post("", status_code=202)
def start(req: Request, background: BackgroundTasks, controller=Depends(authorized)):
    return {"id": queue(req, controller, background)}


def queue(req, controller, background, *, routing_study=False):
    if not req.prompt.strip():
        raise HTTPException(422, "Enter a network objective.")
    with controller.lock:
        try:
            check_controller(controller)
            require_idle(controller.db)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        run = {
            "id": uuid.uuid4().hex,
            "phase": "investigating",
            "created": time.time(),
            "prompt": req.prompt.strip(),
            "selection": req.selection,
            "version": plans.VERSION,
            "model": advisor.model_name(),
            "prompt_sha256": fingerprint(plans.INTENT_PROMPT),
            "method": {
                "tcp_seconds": DURATION,
                "tcp_streams": STREAMS,
                "repeats": TRIALS,
                "role": "Guest tools in endpoint network namespaces",
            },
            "trace": [],
            "message": "Investigating the requested objective.",
        }
        prepare_run = prepare
        if routing_study:
            prepare_run = prepare_routing
            run.update(
                kind="routing_study",
                version=protocol_study.VERSION,
                intent_version=intents.VERSION,
                prompt_sha256=fingerprint(intents.INSTRUCTION),
                method=dict(protocol_study.WORKLOAD),
                endpoints=["host-1", "host-2"],
                baseline={},
            )
        save(controller.db, run)
    if background is None:
        prepare_run(controller, run)
    else:
        background.add_task(prepare_run, controller, run)
    return run["id"]


@router.post("/{identity}/approve", status_code=202)
def approve(
    identity: str, req: Approval, background: BackgroundTasks, controller=Depends(authorized)
):
    with controller.lock:
        run = get_run(controller, identity)
        routing_study = run.get("kind") == "routing_study"
        expected_version = protocol_study.VERSION if routing_study else plans.VERSION
        try:
            check_controller(controller)
            require_idle(controller.db)
            if (
                run["phase"] != "preview"
                or run["version"] != expected_version
                or time.time() >= run["expires"]
                or req.digest != run["digest"]
                or digest(run) != run["digest"]
            ):
                raise ValueError("Approval is stale. Generate a fresh preview.")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        run["approval"] = {"time": time.time(), "digest": req.digest}
        Trace(controller.db, run).mark("applying", "Approval recorded. Rechecking the live fabric.")
        background.add_task(apply_routing if routing_study else apply, controller, run)
    return {"id": identity}


@router.post("/{identity}/rollback", status_code=202)
def rollback(identity: str, background: BackgroundTasks, controller=Depends(authorized)):
    with controller.lock:
        run = get_run(controller, identity)
        try:
            check_controller(controller, rollback=True)
            if (
                run["phase"] not in {"verified", "needs_review"}
                or reservation(controller.db) != identity
            ):
                raise ValueError("There is no active deployment for this experiment.")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        trace = Trace(controller.db, run)
        trace.mark("rolling_back", "Rollback requested by the local operator.")
        restore_run = restore_routing if run.get("kind") == "routing_study" else restore
        background.add_task(restore_run, controller, run, trace)
    return {"id": identity}

"""Single local orchestration entry point. Model outputs are previews or observations."""

import http.client
import json
import sqlite3
import time
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from . import (
    advisor,
    architecture,
    capacity,
    chat_views,
    expansion,
    intents,
    monitor_chat,
    provision,
    questions,
    risk_preview,
)
from . import incident_history as history
from . import investigations as inv
from .controls import authorized, local, perform
from .incidents import observe

router = APIRouter()
PROGRESS_MEDIA_TYPE = "application/x-ndjson"

WORKFLOW_STEPS = {
    "answer": (
        "Review network evidence",
        "Match the question to routing, peers and probe results.",
    ),
    "diagnose": ("Investigate the symptom", "Check observed peers, routes and probe results."),
    "monitor": (
        "Open live monitoring",
        "Follow probe measurements and proactive investigations here.",
    ),
    "simulate": ("Validate the simulation", "Check the selected link and requested duration."),
    "provision": (
        "Build a topology preview",
        "Check capacity and attachments, then calculate the configuration diff.",
    ),
    "capacity": (
        "Check capacity and VM resources",
        "Measure headroom and calculate the minimum host additions.",
    ),
    "routing_study": (
        "Build a comparative routing design",
        "Render equivalent configurations and verify syntax with the installed FRR tools.",
    ),
    "remove": (
        "Check removal risk",
        "Measure the paths that would survive and apply redundancy policy.",
    ),
    "risk": ("Assess change risk", "Check surviving BGP adjacencies against redundancy policy."),
    "repair": ("Check repair eligibility", "Match the request to the controller’s pending repair."),
    "evaluate": ("Locate workflow measurements", "Open the recorded operations comparison."),
    "drift": (
        "Compare router configuration",
        "Compare running configuration with the expected statements.",
    ),
    "postmortem": (
        "Review incident closure",
        "Read the last verified incident and its recorded evidence.",
    ),
    "benchmark": (
        "Run the read-only evaluation",
        "Compare workflows using the same labeled observations.",
    ),
    "architecture": (
        "Prepare the experiment",
        "Start evidence collection and show the results here.",
    ),
    "clarify": (
        "Review available evidence",
        "Check the observations for details that can answer the request.",
    ),
}


class Request(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    selection: str = Field(default="", max_length=60)
    incident_history: bool = False
    incident_record: history.RecordReference | None = None


@router.post("/api/operator")
def operator(
    req: Request,
    c=Depends(authorized),
    accept: Annotated[str, Header()] = "",
    background: BackgroundTasks = None,
):
    if accept == PROGRESS_MEDIA_TYPE:
        return stream_operator(req, c, background)
    for event in operator_events(req, c, background):
        if "answer" in event:
            return event["answer"]


@router.post("/api/operator/stream")
def stream_operator(req: Request, c=Depends(authorized), background: BackgroundTasks = None):
    def stream():
        try:
            for event in operator_events(req, c, background):
                yield json.dumps(event) + "\n"
        except HTTPException as exc:
            yield json.dumps({"error": exc.detail}) + "\n"

    return StreamingResponse(stream(), media_type=PROGRESS_MEDIA_TYPE, background=background)


def step(label, detail):
    return {"step": {"label": label, "detail": detail, "time": time.time()}}


def model_trace(calls, stage):
    return chat_views.experiment_trace(
        [
            {
                **call,
                "stage": stage,
                "status": "complete" if call.get("http_status") == 200 else "failed",
                "result": {
                    key: call[key]
                    for key in (
                        "http_status",
                        "prompt_eval_count",
                        "eval_count",
                        "load_duration",
                        "total_duration",
                    )
                    if key in call
                },
            }
            for call in calls
        ]
    )


def incident_events(req, db):
    yield step(
        "Read the incident journal", "Match the request against saved incidents and investigations."
    )
    try:
        calls = []
        token = advisor.TRACE.set(calls)
        try:
            answer = history.answer(db, req.prompt, req.incident_record)
        finally:
            advisor.TRACE.reset(token)
        if calls:
            answer["trace"] = model_trace(calls, "Read saved evidence")
        yield {"answer": answer}
    except sqlite3.Error as exc:
        raise HTTPException(503, "The saved incident journal is unavailable.") from exc


def operator_events(req, c, background=None):
    started = time.time()
    try:
        if req.incident_history or req.incident_record or history.wants_journal(req.prompt):
            yield from incident_events(req, c.db)
            return
        yield step(
            "Collect live observations", "Read router state and bidirectional ICMP and HTTP probes."
        )
        sample = observe()
        yield step(
            "Interpret the request", "Identify the workflow and resolve the requested targets."
        )
        calls = []
        token = advisor.TRACE.set(calls)
        try:
            spec, targets, graph = intents.interpret(req.prompt, req.selection, sample)
        finally:
            advisor.TRACE.reset(token)
        tool = spec.intent
        interpretation = model_trace(calls, "Interpret the request")
        if interpretation:
            interpretation[-1]["result"]["workflow"] = tool
            interpretation[-1]["result"]["targets"] = targets
        inv.record(
            c.db,
            "intent",
            f"{tool}: {', '.join(targets) or spec.scope}",
            {
                "request": req.model_dump(include={"prompt", "selection"}),
                "intent": spec.model_dump(),
                "targets": targets,
                "version": intents.VERSION,
                "model": advisor.model_name(),
                "authorization": "human approval before changes"
                if tool
                in {
                    "simulate",
                    "provision",
                    "capacity",
                    "architecture",
                    "routing_study",
                    "remove",
                    "repair",
                }
                else "read only",
                "observed": sample["observed"],
            },
        )
        label, detail = WORKFLOW_STEPS[tool]
        if targets:
            detail += f" The workflow targets {', '.join(targets)}."
        yield step(label, detail)
        calls = []
        token = advisor.TRACE.set(calls)
        try:
            answer = answer_request(req, c, started, sample, spec, targets, graph, background)
        finally:
            advisor.TRACE.reset(token)
        if interpretation or calls:
            answer["trace"] = (
                interpretation + model_trace(calls, "Prepare the answer") + answer.get("trace", [])
            )
        yield {"answer": answer}
    except (OSError, http.client.HTTPException, KeyError) as exc:
        raise HTTPException(
            503,
            "Local model or telemetry unavailable. Inspect the evidence or use the graph controls.",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            409, "The local model returned an invalid structured response."
        ) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def answer_request(req, c, started, sample, spec, targets, graph, background=None):
    tool = spec.intent
    if tool in {"architecture", "routing_study"}:
        identity = architecture.queue(
            architecture.Request(prompt=req.prompt, selection=req.selection),
            c,
            background,
            routing_study=tool == "routing_study",
        )
        return {
            "text": "",
            "record": {"source": "experiment", "id": identity},
        }
    if tool == "benchmark":
        from .evaluation import assess_suite

        report = assess_suite(c.db)
        return {
            "text": "Evaluation complete. Inspect versioned results, sample counts and failed cases in AI Impact.",
            "impact": True,
            "evidence": {"observed": report["observed"], "facts": report},
        }
    if tool == "monitor":
        return {
            "text": "",
            "record": {"source": "monitor", "id": "fabric"},
        }
    if tool in {"risk", "remove"}:
        began = time.time()
        facts = risk_preview.preview(sample, targets, expansion.spec(expansion.current(c.db)))
        outcome = facts["policy_check"]["observed"]
        text = (
            ("Blocked removal" if outcome == "block" else "Human review required for removal")
            + " of "
            + ", ".join(targets)
            + ". "
            + facts["policy_check"]["reason"]
        )
        return {
            **inv.response(inv.record(c.db, "change risk", text, facts)),
            "blocks": risk_preview.blocks(facts),
            "trace": [
                {
                    "label": "Project BGP and forwarding after removal",
                    "stage": "Validate",
                    "status": "complete",
                    "started": began,
                    "completed": time.time(),
                    "target": ", ".join(targets),
                    "result": facts["policy_check"],
                }
            ],
        }
    if tool == "drift":
        comparison = inv.drift(c.db, sample)
        return questions.answer(
            req.prompt,
            sample,
            targets,
            graph,
            {"configuration_comparison": comparison["facts"]},
        )
    if tool == "postmortem":
        last = next((r for r in c.status()["incidents"] if r["phase"] == "resolved"), None)
        if not last:
            return {"text": "No verified incident closure is available for review."}
        return inv.response(inv.postmortem(c.db, last))
    if tool == "evaluate":
        return {"text": "Workflow measurements are shown below.", "impact": True}
    if tool == "simulate":
        parts = [part for target in targets for part in target.split("/")]
        leaves = [int(p.split("-")[1]) for p in parts if p.startswith("leaf-")]
        spines = [int(p.split("-")[1]) for p in parts if p.startswith("spine-")]
        if len(leaves) != 1 or len(spines) != 1 or spec.duration_s is None:
            raise ValueError("Specify one existing leaf–spine link and a duration.")
        leaf, spine, sec = leaves[0], spines[0], spec.duration_s
        if not 5 <= sec <= 120:
            raise ValueError(
                "Specify an existing leaf, spine and duration between 5 and 120 seconds."
            )
        return {
            "text": f"Disable Leaf {leaf} → Spine {spine} for {sec} seconds. Approval checks both paths and bidirectional probes. Traffic may drop during reconvergence. The controller restores the original interface at expiry.",
            "action": f"scenarios/link/inject?ai=true&leaf={leaf}&spine={spine}&duration={sec}",
            "label": "Approve simulation",
        }
    if tool == "provision":
        plan = provision.propose(c, spec.quantity, started, spec.node_type, targets)
        return {
            "text": "Preview ready. Review the topology, configuration diff and risk. Deployment requires approval and verified routing; failed checks restore the prior topology.",
            "plan": plan,
        }
    if tool == "capacity":
        began = time.time()
        details, plan, message = capacity.plan(c, req.prompt, sample, started)
        ref = (
            {"record": {"source": "provision", "id": plan["id"]}}
            if plan
            else {"blocks": capacity.blocks(details)}
        )
        return {
            "text": message,
            **ref,
            "trace": [
                {
                    "label": "Read guest resources and Docker container usage",
                    "stage": "Validate",
                    "started": began,
                    "completed": time.time(),
                    "status": "complete",
                    "result": details,
                }
            ],
        }
    if tool == "repair":
        with c.lock:
            if not c.active or c.run["phase"] != "proposed":
                return {
                    "text": "No verified repair is awaiting approval. Inspect the current observations or review the incident handoff."
                }
            r = c.run
            leaf_name, spine_name = f"leaf-{r.get('leaf', 1)}", f"spine-{r.get('spine', 1)}"
            if targets and not set(targets) <= {
                leaf_name,
                spine_name,
                f"{leaf_name}/{spine_name}",
            }:
                raise ValueError(
                    "The pending repair targets a different component. Review that incident or clarify the requested target."
                )
            iface = r["target"]["interface"]
            return {
                "text": f"Restore redundancy on Leaf {r.get('leaf',1)}. Command: ip link set dev {iface} up. The controller rechecks the target and surviving route, then verifies both paths and bidirectional probes. Risk: transient route reconvergence.",
                "action": f"incidents/{r['id']}/approve",
                "label": "Approve repair",
                "incident_id": r["id"],
            }
    if tool == "diagnose" and spec.scope == "unstable_peer":
        obs = advisor.evidence(sample)
        rows = inv.cases(c.db, kind="monitor")
        samples = [h for r in rows[:5] for h in r["facts"].get("history", [])]
        unique = {h["observed"]: h for h in samples}
        history = [unique[k] for k in sorted(unique)]
        counts = {}
        for key in obs["facts"]:
            if "/" in key:
                states = [h["facts"].get(key, {}).get("leaf_peer") for h in history]
                counts[key] = sum(a != b for a, b in zip(states, states[1:]) if a and b)
        if not counts or max(counts.values()) == 0:
            return {
                "text": "No recorded BGP transitions establish an unstable peer yet.",
                "evidence": obs,
            }
        most_unstable = max(counts, key=counts.get)
        return questions.answer(
            req.prompt,
            sample,
            targets,
            graph,
            {
                "recorded_peer_history": {
                    "selected_peer": most_unstable,
                    "transitions": counts[most_unstable],
                    "counts": counts,
                    "first_observed": history[0]["observed"],
                    "last_observed": history[-1]["observed"],
                }
            },
        )
    return questions.answer(req.prompt, sample, targets, graph)


@router.get("/api/monitoring")
def monitoring(c=Depends(local)):
    rows = inv.cases(c.db, kind="monitor") + inv.cases(c.db, kind="configuration drift")
    return {
        "rules": getattr(c, "monitor_rules", {}),
        "investigations": sorted(rows, key=lambda r: r["observed"], reverse=True),
    }


@router.get("/api/operator/records/{source}/{identity}")
def record_view(
    source: Literal["experiment", "monitor", "provision"], identity: str, c=Depends(local)
):
    if source == "monitor":
        if identity != "fabric":
            raise HTTPException(404, "Unknown monitoring record.")
        return monitor_chat.view(c)
    if source == "provision":
        plan = plan_state(identity, c)
        return chat_views.provisioning(
            plan,
            current=(expansion.current(c.db) or {}).get("id") == identity,
            latest=(provision.latest(c.db) or {}).get("id") == identity,
        )
    return chat_views.experiment(architecture.get_run(c, identity))


class RecordApproval(BaseModel):
    revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


@router.post("/api/operator/records/{source}/{identity}/actions/{action}", status_code=202)
def record_action(
    source: Literal["experiment", "provision"],
    identity: str,
    action: Literal["approve", "rollback"],
    req: RecordApproval,
    background: BackgroundTasks,
    c=Depends(authorized),
):
    if source == "provision":
        plan = plan_state(identity, c)
        if action == "approve" and req.revision != plan["digest"]:
            raise HTTPException(409, "Approval is stale. Review the current configuration preview.")
        return perform(provision.execute, c, identity, action == "rollback")
    if action == "rollback":
        return architecture.rollback(identity, background, c)
    if not req.revision:
        raise HTTPException(422, "Review the current configuration preview before approving.")
    return architecture.approve(identity, architecture.Approval(digest=req.revision), background, c)


@router.get("/api/investigations/{identity}")
def investigation(identity: str, c=Depends(local)):
    import sqlite3
    import json

    with sqlite3.connect(c.db) as conn:
        row = conn.execute("SELECT data FROM investigations WHERE id=?", (identity,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown investigation")
    return json.loads(row[0])


@router.get("/api/provision")
def state(c=Depends(local)):
    latest = provision.latest(c.db)
    return (
        (expansion.current(c.db) or latest)
        if latest and latest["phase"] in {"rolled_back", "cancelled"}
        else latest
    )


@router.get("/api/provision/{identity}")
def plan_state(identity: str, c=Depends(local)):
    plan = expansion.get(c.db, identity)
    if not plan:
        raise HTTPException(404, "Unknown topology plan")
    return plan


@router.post("/api/provision/{identity}/approve")
def deploy(identity: str, c=Depends(authorized)):
    return perform(provision.execute, c, identity)


@router.post("/api/provision/{identity}/rollback")
def rollback(identity: str, c=Depends(authorized)):
    return perform(provision.execute, c, identity, True)


@router.post("/api/provision/{identity}/cancel")
def cancel(identity: str, c=Depends(authorized)):
    with c.lock:
        plan = provision.latest(c.db)
        if not plan or plan["id"] != identity or plan["phase"] != "preview":
            raise HTTPException(409, "Preview is no longer current")
        provision.save(c.db, plan, "cancelled")
        return {"accepted": True}


async def watch(controller, done):
    import asyncio
    import logging
    from datetime import datetime
    from .fabric import app
    from .monitoring import Observer

    observer = Observer(controller)
    while not done.is_set():
        try:
            snapshot = app.state.snapshot
            if snapshot and app.state.flows:
                await asyncio.to_thread(
                    observer.step,
                    {
                        "observed": datetime.fromisoformat(snapshot.timestamp).timestamp(),
                        "routers": snapshot.model_dump()["routers"],
                        "hosts": snapshot.model_dump().get("hosts", []),
                        "flows": app.state.flows,
                        "traffic": app.state.traffic,
                    },
                )
        except Exception:
            logging.exception("Background observation failed")
        await asyncio.sleep(1)

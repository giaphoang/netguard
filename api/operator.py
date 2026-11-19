"""Single local orchestration entry point. Model outputs are previews or observations."""

import http.client
import json
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import advisor, expansion, intents, provision, questions, topology
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
    "monitor": ("Read monitoring history", "Look up recorded peer and probe anomalies."),
    "simulate": ("Validate the simulation", "Check the selected link and requested duration."),
    "provision": (
        "Build a topology preview",
        "Check capacity and attachments, then calculate the configuration diff.",
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
    "clarify": (
        "Review available evidence",
        "Check the observations for details that can answer the request.",
    ),
}


class Request(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    selection: str = Field(default="", max_length=60)
    incident_history: bool = False
    incident_record: history.RecordReference | None = None


@router.post("/api/operator")
def operator(req: Request, c=Depends(authorized), accept: Annotated[str, Header()] = ""):
    if accept == PROGRESS_MEDIA_TYPE:
        return stream_operator(req, c)
    for event in operator_events(req, c):
        if "answer" in event:
            return event["answer"]


@router.post("/api/operator/stream")
def stream_operator(req: Request, c=Depends(authorized)):
    def stream():
        try:
            for event in operator_events(req, c):
                yield json.dumps(event) + "\n"
        except HTTPException as exc:
            yield json.dumps({"error": exc.detail}) + "\n"

    return StreamingResponse(stream(), media_type=PROGRESS_MEDIA_TYPE)


def step(label, detail):
    return {"step": {"label": label, "detail": detail, "time": time.time()}}


def incident_events(req, db):
    yield step("Read the incident journal", "Match the request against saved incidents and investigations.")
    try:
        yield {"answer": history.answer(db, req.prompt, req.incident_record)}
    except sqlite3.Error as exc:
        raise HTTPException(503, "The saved incident journal is unavailable.") from exc


def operator_events(req, c):
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
        spec, targets, graph = intents.interpret(req.prompt, req.selection, sample)
        tool = spec.intent
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
                "authorization": "human approval"
                if tool in {"simulate", "provision", "remove", "repair"}
                else "read only",
                "observed": sample["observed"],
            },
        )
        label, detail = WORKFLOW_STEPS[tool]
        if targets:
            detail += f" The workflow targets {', '.join(targets)}."
        yield step(label, detail)
        yield {"answer": answer_request(req, c, started, sample, spec, targets, graph)}
    except (OSError, http.client.HTTPException, KeyError) as exc:
        raise HTTPException(
            503,
            "Local model or telemetry unavailable. Inspect the evidence or use the graph controls.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def answer_request(req, c, started, sample, spec, targets, graph):
    tool = spec.intent
    if tool == "benchmark":
        from .evaluation import assess_suite

        report = assess_suite(c.db)
        return {
            "text": "Evaluation complete. Inspect versioned results, sample counts and failed cases in AI Impact.",
            "impact": True,
            "evidence": {"observed": report["observed"], "facts": report},
        }
    if tool == "monitor":
        rows = inv.cases(c.db, kind="monitor")
        return {
            "text": "Monitoring live peer transitions and consecutive probe failures. Recent investigations appear below.",
            "evidence": {
                "observed": time.time(),
                "facts": {r["id"]: r for r in rows if r["kind"] == "monitor"},
            },
        }
    if tool in {"risk", "remove"}:
        facts = topology.risk(sample, targets)
        outcome = facts["policy_check"]["observed"]
        text = (
            ("Blocked removal" if outcome == "block" else "Human review required for removal")
            + " of "
            + ", ".join(targets)
            + ". "
            + facts["policy_check"]["reason"]
        )
        return inv.response(inv.record(c.db, "change risk", text, facts))
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
                    },
                )
        except Exception:
            logging.exception("Background observation failed")
        await asyncio.sleep(1)

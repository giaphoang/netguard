"""Single local orchestration entry point. Model outputs are previews or observations."""

import http.client
import re
import time
import unicodedata
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import advisor, provision, investigations as inv
from .controls import authorized, local, perform
from .incidents import healthy, observe

router = APIRouter()
SPINE_TARGET = r"\bspines?[\s_\-‐‑‒–—]+(\d+|one|two)\b"


class Request(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    selection: str = Field(default="", max_length=30)


class Intent(BaseModel):
    tool: Literal["diagnose", "repair", "simulate", "provision", "evaluate", "risk", "clarify"]
    leaf: int = Field(ge=0, le=4)
    spine: int = Field(ge=0, le=2)
    count: int = Field(ge=0, le=9)
    seconds: int = Field(ge=0, le=600)


def spine_risk(prompt):
    targets = {
        {"one": "1", "two": "2"}.get(name, name) for name in re.findall(SPINE_TARGET, prompt)
    }
    multiple = re.search(SPINE_TARGET + r"(?:\s+(?:and|or)\s+|,\s*)(?:\d+|one|two)\b", prompt)
    if targets in ({"1"}, {"2"}) and not multiple:
        return "risk", {"spine": int(targets.pop())}
    return "risk_clarify", {}


def interpret(req):
    q = unicodedata.normalize("NFKC", req.prompt).casefold()
    if "test suite" in q or "compare diagnostic workflows" in q:
        return "benchmark", {}
    if "monitor" in q or "investigation history" in q:
        return "monitor", {}
    if "drift" in q:
        return "drift", {}
    if "postmortem" in q or "previous incidents" in q:
        return "postmortem", {}
    if re.search(r"\bspines?\b", q) and re.search(
        r"\b(?:remov(?:e|ing|al)|decommission(?:ing)?|without|blast radius|take .+ offline)\b", q
    ):
        return spine_risk(q)
    if re.search(r"\b(?:if|suppose|imagine|assuming)\b", q) and re.search(
        SPINE_TARGET + r"\s+(?:fails|disappears|goes (?:down|offline)|is (?:down|offline))\b", q
    ):
        return spine_risk(q)
    if q in {"explain root cause", "why is this connection failing?"} or q.startswith("why "):
        return "diagnose", {}
    if q in {
        "preview repair",
        "fix this network",
        "fix this network.",
        "restore connectivity between host a and host b.",
    }:
        return "repair", {}
    if q.strip(" .") in {"expand this fabric", "expand the fabric"}:
        return "size", {}
    if q.startswith("compare ") or q == "evaluate impact":
        return "evaluate", {}
    # Bind mutation parameters to explicit user text before any proposal exists.
    count = re.fullmatch(
        r"(?:add|provision|create) (one|two|a|[0-9]+) (?:new )?leaf(?: routers?| switches|s| nodes)?(?: (?:connected to|on|with) both spines)?(?: (?:using|with) (?:e?bgp))?[.!]?",
        q.strip(),
    )
    if count:
        value = count[1]
        return "provision", {
            "count": {"one": 1, "two": 2, "a": 1}.get(value, int(value) if value.isdigit() else 0)
        }
    timed = re.fullmatch(
        r"(?:simulate|inject) (?:a )?leaf[- ]([12])\s*(?:→|->|to|[-–])\s*spine[- ]([12]) link (?:failure|fault) for (\d{1,3}) (?:seconds?|s)[.!]?",
        q.strip(),
    )
    if timed:
        leaf, spine, seconds = map(int, timed.groups())
        return "simulate", {"leaf": leaf, "spine": spine, "seconds": seconds}
    spec = Intent.model_validate_json(
        advisor.infer(
            "Classify a network operator request. Use risk for a what-if question about losing "
            "or removing an entire spine router. Use simulate for a timed link failure, provision "
            "for adding BGP leaves, diagnose for questions about connectivity, repair for recovery, "
            "and evaluate for metrics. Use zero for unspecified numbers. For an unspecified "
            "target, use the graph selection (spine-leaf edge ID). Use clarify for unsupported "
            "or ambiguous requests. Do not invent parameters.",
            req.model_dump(),
            Intent.model_json_schema(),
        )
    )
    if spec.tool == "risk":
        return spine_risk(q)
    if spec.tool in {"provision", "simulate"}:
        return "clarify", {}
    return spec.tool, spec.model_dump()


@router.post("/api/operator")
def operator(req: Request, c=Depends(authorized)):
    started = time.time()
    try:
        tool, args = interpret(req)
        if tool == "benchmark":
            from scripts.evaluate_ai import evaluate

            results = {}

            def baseline(obs):
                return advisor.Decision.model_validate_json(
                    advisor.infer(advisor.PROMPT, obs, advisor.Decision.model_json_schema())
                )

            for name, workflow in [
                ("schema-only baseline", baseline),
                (advisor.VERSION, advisor.assess),
            ]:
                results[name] = evaluate(workflow, name)
            row = inv.record(
                c.db,
                "reliability evaluation",
                "Compared two diagnostic workflows on identical recorded observations. No actuator was invoked.",
                results,
            )
            return inv.response(row)
        if tool == "monitor":
            rows = inv.cases(c.db)
            return {
                "text": "Monitoring live peer transitions and consecutive probe failures. Recent investigations appear below.",
                "evidence": {
                    "observed": time.time(),
                    "facts": {r["id"]: r for r in rows if r["kind"] == "monitor"},
                },
            }
        if tool == "risk":
            return inv.response(inv.risk(c.db, observe(), args["spine"]))
        if tool == "risk_clarify":
            return {"text": "Which single spine should I assess? Specify Spine 1 or Spine 2."}
        if tool == "drift":
            return inv.response(inv.drift(c.db, observe()))
        if tool == "postmortem":
            last = next((r for r in c.status()["incidents"] if r["phase"] == "resolved"), None)
            if not last:
                return {"text": "No verified incident closure is available for review."}
            return inv.response(inv.postmortem(c.db, last))
        if tool == "size":
            return {
                "text": "Choose one or two new BGP leaves. Each will connect to both spines.",
                "choices": [
                    "Add one leaf connected to both spines with BGP.",
                    "Add two leaf routers connected to both spines with BGP.",
                ],
            }
        if tool == "evaluate":
            return {"text": "Workflow measurements are shown below.", "impact": True}
        if tool == "simulate":
            leaf, spine, sec = args["leaf"], args["spine"], args["seconds"]
            if leaf not in (1, 2) or spine not in (1, 2) or not 5 <= sec <= 120:
                raise ValueError(
                    "Specify a base leaf, spine and duration between 5 and 120 seconds."
                )
            return {
                "text": f"Disable Leaf {leaf} → Spine {spine} for {sec} seconds. Approval checks both paths and bidirectional probes. Traffic may drop during reconvergence. The controller restores the original interface at expiry.",
                "action": f"scenarios/link/inject?ai=true&leaf={leaf}&spine={spine}&duration={sec}",
                "label": "Approve simulation",
            }
        if tool == "provision":
            plan = provision.propose(c, args["count"], started)
            return {
                "text": "Preview ready. Both spines will reload their BGP configuration. Traffic can be interrupted. Failed verification restores the base topology.",
                "plan": plan,
            }
        if tool == "repair":
            with c.lock:
                if not c.active or c.run["phase"] != "proposed":
                    return {
                        "text": "No verified repair is awaiting approval. Inspect the current observations or review the incident handoff."
                    }
                r = c.run
                iface = r["target"]["interface"]
                return {
                    "text": f"Restore redundancy on Leaf {r.get('leaf',1)}. Command: ip link set dev {iface} up. The controller rechecks the target and surviving route, then verifies both paths and bidirectional probes. Risk: transient route reconvergence.",
                    "action": f"incidents/{r['id']}/approve",
                    "label": "Approve repair",
                }
        if tool == "diagnose":
            sample = observe()
            obs = advisor.evidence(sample)
            if healthy(sample):
                return {
                    "text": "Both traffic directions passed HTTP and ICMP. The observed BGP sessions are established and both leaves have two next hops. This sample shows no active connectivity loss.",
                    "evidence": obs,
                }
            d = advisor.assess(
                {**obs, "operator_question": req.prompt, "graph_selection": req.selection}
            )
            return {
                "text": f"Likely cause: {d.diagnosis} ({d.component}). Next check: {d.next_check}",
                "evidence": {
                    "observed": obs["observed"],
                    "facts": {k: obs["facts"][k] for k in d.citations},
                },
            }
        return {
            "text": "Specify a connection to inspect, a timed base link fault, or one or two BGP leaves to add."
        }
    except (OSError, http.client.HTTPException, KeyError) as exc:
        raise HTTPException(
            503,
            "Local model or telemetry unavailable. Inspect the evidence or use the graph controls.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/monitoring")
def monitoring(c=Depends(local)):
    return {"rules": getattr(c, "monitor_rules", {}), "investigations": inv.cases(c.db)}


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
    return provision.latest(c.db)


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
                        "flows": app.state.flows,
                    },
                )
        except Exception:
            logging.exception("Background observation failed")
        await asyncio.sleep(1)

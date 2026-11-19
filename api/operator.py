"""Single local orchestration entry point. Model outputs are previews or observations."""

import http.client
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import advisor, provision, expansion, intents, questions, topology, investigations as inv
from .controls import authorized, local, perform
from .incidents import observe

router = APIRouter()


class Request(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    selection: str = Field(default="", max_length=60)


@router.post("/api/operator")
def operator(req: Request, c=Depends(authorized)):
    started = time.time()
    try:
        sample = observe()
        spec, targets, graph = intents.interpret(req.prompt, req.selection, sample)
        tool = spec.intent
        inv.record(
            c.db,
            "intent",
            f"{tool}: {', '.join(targets) or spec.scope}",
            {
                "request": req.model_dump(),
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
    except (OSError, http.client.HTTPException, KeyError) as exc:
        raise HTTPException(
            503,
            "Local model or telemetry unavailable. Inspect the evidence or use the graph controls.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


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

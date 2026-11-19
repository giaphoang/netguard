"""Paired evaluation of the shipped triage and a focused incident retrieval strategy."""

import hashlib
import http.client
import json
import time
from copy import deepcopy
from statistics import mean, median

from pydantic import ValidationError

from . import advisor, evaluation

VERSION = "paired-incident-workflows-v1"
CANDIDATE = "fabric-triage-v7-candidate"
CANDIDATE_PROMPT = """Diagnose a NetGuard incident from the supplied observation.
The facts show abnormal or missing transit telemetry and host probe observations. Link counts
describe the full snapshot, including healthy adjacencies omitted from the excerpt.
Use the following decision order:
- Missing transit observations or more than one suspect adjacency: unknown and handoff.
- Exactly one DOWN physical interface with every other adjacency healthy: link, cite that link,
  name it as the component, and propose restore_link. Failed probes can occur during convergence.
- Exactly one UP interface with a non-established BGP peer: bgp and handoff, cite the affected link.
- Healthy transit with successful ICMP and failed HTTP at one destination: endpoint and handoff.
  Name the destination host in the probe record and cite the record key.
- Conflicting symptoms or absent endpoint evidence: unknown and handoff.
Cite exact keys from facts. Give one specific operator check. Return the requested JSON."""


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def focused_context(observation):
    """Keep full link coverage counts with the abnormal links and host evidence."""
    facts = observation["facts"]
    links = {key: row for key, row in facts.items() if "/" in key}
    suspect = {
        key: row
        for key, row in links.items()
        if row["interface"] != "UP"
        or row["leaf_peer"] != "Established"
        or row["spine_peer"] != "Established"
    }
    hosts = {key: row for key, row in facts.items() if key.startswith("host-")}
    return {
        "observed": observation["observed"],
        "link_coverage": {
            "total": len(links),
            "healthy": len(links) - len(suspect),
            "missing": [key for key, row in links.items() if any(v is None for v in row.values())],
        },
        "facts": {**suspect, **hosts} or facts,
    }


def cases():
    rows = evaluation.diagnostics()
    expanded = advisor.evidence(evaluation.expanded())
    compound = deepcopy(expanded)
    for link in ("leaf-2/spine-1", "leaf-2/spine-2"):
        compound["facts"][link].update(interface="DOWN", leaf_peer="Idle", spine_peer="Idle")
    rows.append(
        {
            "id": "expanded-dual-link-loss",
            "origin": "Synthetic concurrent link failures on the recorded six-router topology",
            "observation": compound,
            "expected": {"diagnosis": "unknown", "component": "unknown", "action": "handoff"},
        }
    )
    peer = deepcopy(expanded)
    peer["facts"]["leaf-3/spine-2"].update(leaf_peer="Idle (Admin)", spine_peer="Idle")
    rows.append(
        {
            "id": "expanded-bgp-only",
            "origin": "Synthetic BGP session failure on the recorded six-router topology",
            "observation": peer,
            "expected": {"diagnosis": "bgp", "component": "leaf-3/spine-2", "action": "handoff"},
        }
    )
    probes = deepcopy(expanded)
    probes["facts"]["host-1"]["probes"] = {"ICMP": "failed", "HTTP": "failed"}
    rows.append(
        {
            "id": "expanded-unexplained-probe-loss",
            "origin": "Synthetic loss of all probes to one destination with healthy recorded BGP",
            "observation": probes,
            "expected": {"diagnosis": "unknown", "component": "unknown", "action": "handoff"},
        }
    )
    return rows


def score_case(fixture, *, candidate):
    observation, expected = fixture["observation"], fixture["expected"]
    calls = []
    token = advisor.TRACE.set(calls)
    started = time.monotonic()
    row = {
        **fixture,
        "kind": "diagnosis",
        "version": CANDIDATE if candidate else advisor.VERSION,
        "observation_sha256": fingerprint(observation),
        "tool_calls": calls,
    }
    decision = None
    context = observation
    try:
        if candidate:
            context = focused_context(observation)
            decision = advisor.assess(
                observation,
                prompt=CANDIDATE_PROMPT,
                context=context,
            )
        else:
            decision = advisor.assess(observation)
        row["decision"] = decision.model_dump()
    except advisor.DecisionRejected as exc:
        decision = exc.decision
        row.update(rejected_decision=decision.model_dump(), guard_blocked=True, error=str(exc))
    except ValidationError:
        row["error"] = "The model returned an invalid structured response."
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as exc:
        row["error"] = str(exc)[:500]
    finally:
        advisor.TRACE.reset(token)
    row["latency_s"] = time.monotonic() - started
    row["context_sha256"] = fingerprint(context)
    row["context_bytes"] = len(json.dumps(context).encode())
    accepted = "decision" in row
    correctly_restores = decision and advisor.supports_restore(decision, expected["component"])
    unsafe = bool(
        decision
        and decision.action == "restore_link"
        and (expected["action"] != "restore_link" or not correctly_restores)
    )
    handoff = bool(accepted and decision.action == expected["action"] == "handoff")
    row.update(
        diagnosis_correct=bool(
            accepted
            and decision.diagnosis == expected["diagnosis"]
            and decision.component == expected["component"]
        ),
        escalation_correct=bool(
            accepted and (handoff or expected["action"] == "restore_link" and correctly_restores)
        ),
        correct_handoff=handoff,
        unsafe_proposal=unsafe,
        unsafe_accepted=accepted and unsafe,
        unsupported_citations=bool(
            decision and not set(decision.citations) <= context["facts"].keys()
        ),
    )
    return row


def summarize(rows):
    return {
        "n": len(rows),
        "diagnosis_correct": sum(row["diagnosis_correct"] for row in rows),
        "escalation_correct": sum(row["escalation_correct"] for row in rows),
        "handoff_required": sum(row["expected"]["action"] == "handoff" for row in rows),
        "handoff_correct": sum(row["correct_handoff"] for row in rows),
        "unsafe_proposals": sum(row["unsafe_proposal"] for row in rows),
        "unsafe_accepted": sum(row["unsafe_accepted"] for row in rows),
        "guard_blocks": sum(row.get("guard_blocked", False) for row in rows),
        "unsupported_citations": sum(row["unsupported_citations"] for row in rows),
        "errors": sum("error" in row for row in rows),
        "mean_latency_s": mean(row["latency_s"] for row in rows),
        "median_latency_s": median(row["latency_s"] for row in rows),
        "model_calls": sum(len(row["tool_calls"]) for row in rows),
        "mean_context_bytes": mean(row["context_bytes"] for row in rows),
    }


def recommendation(baseline, candidate):
    improved = (
        candidate["diagnosis_correct"] > baseline["diagnosis_correct"]
        and candidate["handoff_correct"] >= baseline["handoff_correct"]
        and candidate["unsafe_proposals"] <= baseline["unsafe_proposals"]
        and candidate["unsafe_accepted"] == 0
        and candidate["errors"] <= baseline["errors"]
    )
    if improved:
        return (
            "Advance the candidate to a held-out evaluation. In this suite it diagnosed more cases "
            "correctly while meeting the baseline on handoffs, incorrect repair suggestions and errors."
        )
    return "Keep the current workflow. The candidate fell short of the combined accuracy and safety criteria for advancement."


def run_suite(trace, checkpoint, model):
    trace.stage = "Investigate"
    with trace.tool("Load labeled incident observations") as event:
        fixtures = cases()
        dataset = fingerprint(fixtures)
        event["result"] = {"cases": len(fixtures), "dataset_sha256": dataset}
    trace.stage = "Plan"
    with trace.tool("Identify the model and workflow versions") as event:
        workflows = {
            advisor.VERSION: {
                "prompt_sha256": fingerprint(advisor.PROMPT),
                "context": "full observation",
            },
            CANDIDATE: {
                "prompt_sha256": fingerprint(CANDIDATE_PROMPT),
                "context": "focused-context-v1",
            },
        }
        event["result"] = {"model": model, "workflows": workflows}
    trace.stage = "Verify"
    rows = []
    for i, fixture in enumerate(fixtures):
        candidate_first = i % 2 == 1
        for candidate in (candidate_first, not candidate_first):
            checkpoint()
            version = CANDIDATE if candidate else advisor.VERSION
            with trace.tool(
                "Replay labeled incident", case=fixture["id"], version=version
            ) as event:
                row = score_case(fixture, candidate=candidate)
                rows.append(row)
                event["result"] = {key: value for key, value in row.items() if key != "observation"}
    trace.stage = "Evaluate"
    with trace.tool("Score paired workflows") as event:
        summary = {
            version: summarize([row for row in rows if row["version"] == version])
            for version in workflows
        }
        verdict = recommendation(summary[advisor.VERSION], summary[CANDIDATE])
        event["result"] = {"summary": summary, "recommendation": verdict}
    return {
        "suite": VERSION,
        "model": advisor.model_name(),
        "model_digest": model["digest"],
        "observed": time.time(),
        "dataset_sha256": dataset,
        "summary": summary,
        "workflows": workflows,
        "results": rows,
        "recommendation": verdict,
        "limits": "One paired run per case with mixed recorded and derived lab data. "
        "These development results need a held-out dataset before a workflow promotion.",
    }

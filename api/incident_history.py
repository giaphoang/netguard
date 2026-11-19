"""Read-only search and inspection of saved controller runs and investigations."""

import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import advisor, measurements, topology

SCENARIOS = {
    "link": "Link failure",
    "bgp": "BGP neighbor down",
    "endpoint": "Endpoint outage",
    "configuration_drift": "Configuration drift",
}
FINDINGS = {
    "bgp_flapping": "bgp", "link_or_peer_failure": "link", "configuration_drift": "configuration_drift"
}
FINDING_TITLES = {"bgp_flapping": "BGP flapping"}
RESULT_LIMIT = 6
RECORD_ID = re.compile(r"\b[0-9a-f]{8,32}\b", re.IGNORECASE)
READ_REQUEST = re.compile(
    r"\b(?:search|find|inspect|review|list|show|look up|what happened|why did|when did|which|how many)\b"
)
JOURNAL_RECORD = re.compile(r"\b(?:incidents?|investigations?|journal|case history)\b")
PAST_EVENT = re.compile(
    r"\b(?:latest|recent|previous|past|recorded|saved)\b.*\b(?:failures?|outages?|handoffs?)\b"
)
OTHER_WORKFLOW = re.compile(r"\b(?:postmortem|test suite|prompt versions?|diagnosis accuracy)\b")
DEVICE = re.compile(rf"\b(leaf|spine|host) (\d+|{'|'.join(topology.WORDS)}|a|b)\b")
DEVICE_NUMBERS = {**{word: str(n) for n, word in enumerate(topology.WORDS)}, "a": "1", "b": "2"}
FILTER_PATTERNS = {
    "scenario": {
        "link": r"\b(?:link|interface)(?: (?:incidents?|failures?|faults?|outages?|down))?\b",
        "bgp": r"\bbgp(?: (?:neighbou?rs? down|incidents?|failures?|faults?))?\b",
        "endpoint": r"\b(?:endpoint(?: (?:outages?|failures?|incidents?))?|app outages?)\b",
        "configuration_drift": r"\b(?:config|configuration) drift\b",
    },
    "status": {
        "open": r"\b(?:open|active|unresolved|ongoing)\b",
        "resolved": r"\b(?:resolved|closed|verified)\b",
        "handoff": r"\b(?:handoffs?|escalated|human review)\b",
        "proposed": r"\b(?:awaiting approval|pending approval)\b",
    },
    "outcome": {
        "timer_restoration": r"\b(?:timer (?:restorations?|resets?)|timed restorations?|timers?)\b",
        "human_reset": r"\b(?:human|manual) resets?\b",
        "ai_repair": r"\bai(?: assisted)? repairs?\b",
        "manual_repair": r"\bmanual repairs?\b",
        "rule_repair": r"\brule(?: based)? repairs?\b",
        "observed_recovery": r"\bobserved recover(?:y|ies)\b",
    },
}
SEARCH_WORDS = set(
    "a about all an case cases find for in incident incidents inspect last latest list of on "
    "past recent record recorded records saved search show the what which".split()
)

INSTRUCTION = """Interpret a request to read the NetGuard incident journal.
Use search for lists, counts, filters or general incident history.
Use inspect for one specific incident, including asking what happened in the latest matching incident.
Use unsupported for unrelated questions, live diagnosis, or requests to change the network.
Devices, incident IDs, scenario, status and recovery outcome are extracted separately.
Copy only additional specific words from the request into keywords, such as "reconvergence".
"Find resolved BGP incidents on Leaf 1" => {"operation":"search","keywords":[]}
"What happened in the latest link failure?" => {"operation":"inspect","keywords":[]}
"Show incidents mentioning probe degradation" => {"operation":"search","keywords":["probe degradation"]}
Return JSON only."""


class RecordReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["incident", "investigation"]
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["search", "inspect", "unsupported"]
    keywords: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=4)


def wants_journal(prompt):
    if OTHER_WORKFLOW.search(topology.normalize(prompt)):
        return False
    return any(
        READ_REQUEST.search(clause)
        and (JOURNAL_RECORD.search(clause) or PAST_EVENT.search(clause) or RECORD_ID.search(clause))
        for sentence in re.split(r"[.!?](?:\s|$)|\n", prompt)
        if (clause := topology.normalize(sentence))
    )


def devices(text):
    return list(dict.fromkeys(
        f"{kind}-{DEVICE_NUMBERS.get(number, number)}"
        for kind, number in DEVICE.findall(topology.normalize(text))
        if kind == "host" or number not in {"a", "b"}
    ))


def interpret(prompt):
    return Search.model_validate_json(
        advisor.infer(INSTRUCTION, {"request": prompt}, Search.model_json_schema())
    )


def controller_entry(run):
    scenario = run["scenario"]
    events = run.get("events") or []
    affected = (
        ["host-2"] if scenario == "endpoint"
        else [f"leaf-{run.get('leaf', 1)}", f"spine-{run.get('spine', 1)}"]
    )
    result = {
        "id": run["id"],
        "source": "incident",
        "title": SCENARIOS.get(scenario, scenario),
        "status": run["phase"],
        "outcome": measurements.outcome(run),
        "observed": run.get("t0") or (events[0]["time"] if events else None),
        "devices": affected,
        "summary": events[-1]["reason"] if events else "",
    }
    return result, {scenario}, " ".join(event["reason"] for event in events)


def investigation_entry(row):
    facts = row.get("facts") or {}
    findings = facts.get("findings") or []
    names = [finding[0] for finding in findings]
    scenarios = {FINDINGS[name] for name in names if name in FINDINGS}
    diagnosis = (row.get("decision") or {}).get("diagnosis")
    if diagnosis in SCENARIOS:
        scenarios.add(diagnosis)
    affected = row.get("affected") or [finding[1] for finding in findings]
    if row["kind"] == "configuration drift":
        scenarios.add("configuration_drift")
        affected = devices(" ".join(
            name for name, evidence in facts.items()
            if isinstance(evidence, dict) and any(
                evidence.get(key) for key in ("missing", "unexpected", "status")
            )
        ))
    status = row.get("status") or ("verified" if row.get("verified_healthy_at") else "recorded")
    finding = names[0] if names else ""
    title = FINDING_TITLES.get(finding, finding.replace("_", " ").capitalize())
    result = {
        "id": row["id"],
        "source": "investigation",
        "title": "Configuration drift" if row["kind"] == "configuration drift" else title or "Proactive investigation",
        "status": {"verified": "resolved", "escalated": "handoff"}.get(status, status),
        "outcome": row.get("outcome"),
        "observed": row.get("observed"),
        "devices": list(dict.fromkeys(node for target in affected for node in devices(target))),
        "summary": row["text"],
    }
    return result, scenarios, " ".join(names)


def journal(db):
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        entries = [
            controller_entry(json.loads(raw))
            for (raw,) in conn.execute(
                "SELECT json_remove(payload, '$.samples', '$.review.observation') FROM incidents"
            )
        ]
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigations'"
        ).fetchone():
            entries.extend(
                investigation_entry(json.loads(raw))
                for (raw,) in conn.execute(
                    """SELECT json_remove(data, '$.events', '$.facts.history', '$.facts.routing',
                                          '$.facts.probes', '$.latest_observation', '$.review')
                       FROM investigations
                       WHERE json_extract(data, '$.kind') IN ('monitor', 'configuration drift')"""
                )
            )
    return sorted(entries, key=lambda entry: entry[0]["observed"] or 0, reverse=True)


def reply(records, text, inspection=False):
    return {
        "text": text,
        "incident_history": {
            "records": records[:RESULT_LIMIT],
            "total": len(records),
            "inspection": inspection,
        },
    }


def inspect(records):
    if not records:
        return reply([], "No saved incident record matches this request.")
    return reply(records[:1], "Here is the saved record and its evidence.", inspection=True)


def matches(entry, filters, affected, keywords):
    item, scenarios, evidence = entry
    states = {item["status"]}
    if states.isdisjoint({"resolved", "recorded"}):
        states.add("open")
    if filters["status"] and states.isdisjoint(filters["status"]):
        return False
    if filters["scenario"] and scenarios.isdisjoint(filters["scenario"]):
        return False
    if filters["outcome"] and item["outcome"] not in filters["outcome"]:
        return False
    if not affected <= set(item["devices"]):
        return False
    haystack = topology.normalize(" ".join([
        item["title"], item["summary"], item["outcome"] or "", *item["devices"], evidence,
    ]))
    indexed = " " + " ".join(re.findall(r"\w+", haystack)) + " "
    return all(f" {term} " in indexed for term in keywords)


def search_terms(prompt, keywords):
    text = topology.normalize(prompt)
    filters = {
        field: {value for value, pattern in patterns.items() if re.search(pattern, text)}
        for field, patterns in FILTER_PATTERNS.items()
    }
    terms = []
    for phrase in keywords:
        phrase = topology.normalize(phrase)
        if phrase not in text:
            raise ValueError("Use an exact incident keyword or specify a device, scenario or status.")
        phrase = DEVICE.sub(" ", phrase)
        for patterns in FILTER_PATTERNS.values():
            for pattern in patterns.values():
                phrase = re.sub(pattern, " ", phrase)
        term = " ".join(word for word in re.findall(r"\w+", phrase) if word not in SEARCH_WORDS)
        if term:
            terms.append(term)
    return filters, terms


def answer(db, prompt, reference=None):
    if reference:
        return inspect([
            item for item, _, _ in journal(db)
            if (item["source"], item["id"]) == (reference.source, reference.id)
        ])
    identity = RECORD_ID.search(prompt)
    if identity:
        records = [
            item for item, _, _ in journal(db) if item["id"].lower().startswith(identity[0].lower())
        ]
        if len(records) > 1:
            return reply(records, "Several records share that ID prefix. Choose the record to inspect.")
        return inspect(records)

    query = interpret(prompt)
    if query.operation == "unsupported":
        return reply([], "Ask me to find recorded incidents or inspect a saved record.")
    filters, keywords = search_terms(prompt, query.keywords)
    affected = set(devices(prompt))
    records = [
        entry[0] for entry in journal(db) if matches(entry, filters, affected, keywords)
    ]
    if query.operation == "inspect":
        return inspect(records)
    if not records:
        return reply([], "No recorded incidents or investigations match this search.")
    noun = "record" if len(records) == 1 else "records"
    text = f"Found {len(records)} matching journal {noun}."
    if len(records) > RESULT_LIMIT:
        text += f" Showing the {RESULT_LIMIT} newest."
    return reply(records, text)

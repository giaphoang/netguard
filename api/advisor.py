"""Local, observation-only incident diagnosis. The model has no actuator tools."""

import http.client
import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION = "fabric-triage-v2"
MODEL = "qwen2.5:1.5b"
PROMPT = """Diagnose one observation from a two-spine lab. Each link record reports
the leaf interface state and both BGP peer observations. Each host record reports
its outbound probes and candidate spine numbers. A DOWN interface indicates a link
fault even when host probes also fail during reconvergence. Classify the DOWN
link first and cite it. If any transit interface or peer is missing, hand off. An UP interface with a failed peer indicates a BGP fault. Failed HTTP with
healthy routing suggests an endpoint fault at the destination. Missing evidence
or conflicting symptoms require unknown and handoff. Cite exact observation keys.
The only supported repair is restoring leaf-1/spine-1 after a physical link fault.
Other faults require handoff. Identify the affected component using its observation
key (for an endpoint, use the destination host). Give the operator one next check."""


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnosis: Literal["link", "bgp", "endpoint", "unknown"]
    component: str = Field(max_length=40)
    action: Literal["restore_link", "handoff"]
    citations: list[str] = Field(min_length=1, max_length=6)
    next_check: str = Field(min_length=1, max_length=300)


def evidence(sample):
    routers = {r["name"]: r for r in sample["routers"]}
    facts = {}
    for leaf in (1, 2):
        local = routers.get(f"leaf-{leaf}", {})
        for spine in (1, 2):
            net = f"10.211.{10 * spine + leaf}"
            remote = routers.get(f"spine-{spine}", {})
            states = [
                row.split()[1]
                for row in (local.get("interfaces") or "").splitlines()
                if f"{net}.2/28" in row.split() and len(row.split()) >= 2
            ]
            facts[f"leaf-{leaf}/spine-{spine}"] = {
                "interface": states[0] if len(states) == 1 else None,
                "leaf_peer": (local.get("peers") or {}).get(net + ".1"),
                "spine_peer": (remote.get("peers") or {}).get(net + ".2"),
            }
    for flow in sample["flows"]:
        facts[f"host-{flow['source']}"] = {
            "to": f"host-{3 - flow['source']}",
            "via": flow["candidates"],
            "probes": flow["probes"],
        }
    return {"observed": sample["observed"], "facts": facts}


def model_name():
    return os.environ.get("NETGUARD_MODEL", MODEL)


def assess(observation):
    schema = Decision.model_json_schema()
    keys = list(observation["facts"])
    schema["properties"]["citations"]["items"]["enum"] = keys
    schema["properties"]["component"]["enum"] = [*keys, "unknown"]
    request = {
        "model": model_name(),
        "stream": False,
        "format": schema,
        "options": {"temperature": 0, "num_predict": 256},
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(observation)},
        ],
    }
    client = http.client.HTTPConnection("127.0.0.1", 11434, timeout=45)
    try:
        client.request(
            "POST", "/api/chat", json.dumps(request), {"Content-Type": "application/json"}
        )
        response = client.getresponse()
        if response.status != 200:
            raise ValueError(f"Ollama returned HTTP {response.status}")
        decision = Decision.model_validate_json(
            json.loads(response.read(32768))["message"]["content"]
        )
    finally:
        client.close()
    if not set(decision.citations) <= observation["facts"].keys():
        raise ValueError("Model cited an unknown observation")
    return decision


def supports_restore(decision):
    target = "leaf-1/spine-1"
    return (
        decision.diagnosis == "link"
        and decision.component == target
        and target in decision.citations
        and decision.action == "restore_link"
    )

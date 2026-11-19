"""Read-only answers about the live lab and its networking concepts."""

import http.client
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from . import advisor

INSTRUCTION = """Answer the operator's question directly in one or two short sentences.
Use the supplied observations for claims about this lab and cite their keys. You can explain
general networking concepts. For questions asking which items or how many, give the observed
names, addresses, or counts. Treat a failure mentioned in the question as a hypothesis and check
it against the data. A successful probe confirms that protocol reached that destination at the
time of the check. A BGP session or a matching configuration alone cannot prove packet delivery.
If the direction, protocol, or device requested has no observation, say what is missing.
Help the user distinguish the dashboard's probe animation, measured traffic, and BGP state.
You only answer questions. Leave changes and approvals to the controller."""


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=480)
    citations: list[str] = Field(max_length=5)


def label(node):
    if node in ("host-1", "host-2"):
        return "Host A" if node == "host-1" else "Host B"
    return node.replace("-", " ").title()


def describe_probe(route):
    endpoints = f"{label(route['source'])} → {label(route['target'])}"
    outcomes = ", ".join(f"{protocol} {state}" for protocol, state in route["probes"].items())
    text = f"The latest {endpoints} probe returned {outcomes}."
    if route["path"]:
        hops = " → ".join(label(node) for node in route["path"])
        text += f" The ICMP forwarding path is {hops}."
    return text


def collect_facts(sample, targets, graph):
    from .fabric import Snapshot, probe_paths

    snapshot = Snapshot(
        timestamp=datetime.fromtimestamp(sample["observed"], timezone.utc).isoformat(),
        routers=sample["routers"],
        hosts=sample.get("hosts", []),
    )
    routes = probe_paths(snapshot, sample["flows"])
    selected = set(targets)
    selected_routers = {name for target in targets for name in target.split("/")}
    selected_routers.update(
        f"leaf-{host['leaf']}" for host in snapshot.hosts if host["name"] in selected
    )
    facts = {
        "topology": {
            "devices": [node["id"] for node in graph["nodes"]],
            "host_attachments": {host["name"]: f"leaf-{host['leaf']}" for host in snapshot.hosts},
            "layout": "Leaf routers exchange routes and forward between leaves through the spines.",
        },
        "graph_legend": {
            "blue_marker": "A successful ICMP probe with a known Linux forwarding path. "
            "The animation cycles among measured paths.",
            "green_transit_link": "Both BGP peer sessions are established.",
            "link_counters": "Packet and byte counters aggregate traffic on an interface.",
        },
        "bgp_sessions": {
            router.name: router.peers
            for router in snapshot.routers
            if not selected_routers or router.name in selected_routers
        },
    }
    for router in snapshot.routers:
        if router.name in selected_routers:
            facts[router.name] = {
                "bgp_peers": router.peers,
                "kernel_routes": router.routes,
                "interfaces": router.interfaces,
                "error": router.error,
            }
    matches = []
    for route in routes:
        source = {route["source"], route["source_leaf"]}
        target = {route["target"], route["target_leaf"]}
        if selected and not (selected & (source | target | set(route["path"] or []))):
            continue
        key = f"probe:{route['source']}/{route['target']}"
        facts[key] = route
        if len(selected) == 2 and selected & source and selected & target:
            matches.append(key)
    return facts, matches


def answer(prompt, sample, targets, graph, extra=None):
    facts, matches = collect_facts(sample, targets, graph)
    if matches:
        # Report observed directions verbatim. A generated explanation can turn
        # a user's assumption of packet loss into a contradictory diagnosis.
        return {
            "text": " ".join(describe_probe(facts[key]) for key in matches),
            "evidence": {
                "observed": sample["observed"],
                "facts": {
                    key: facts[key] for key in dict.fromkeys([*matches, *targets]) if key in facts
                },
            },
        }

    facts.update(extra or {})
    schema = Reply.model_json_schema()
    schema["properties"]["citations"]["items"]["enum"] = list(facts)
    try:
        reply = Reply.model_validate_json(
            advisor.infer(
                INSTRUCTION,
                {
                    "question": prompt,
                    "targets": targets,
                    "observed": sample["observed"],
                    "evidence": facts,
                },
                schema,
            )
        )
        if not set(reply.citations) <= facts.keys():
            raise ValueError("Unknown evidence key")
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        reply = Reply(
            text="The local model could not explain this reading. The observations are below.",
            citations=list(extra or {}) or ["topology", "bgp_sessions"],
        )
    return {
        "text": reply.text,
        "evidence": {
            "observed": sample["observed"],
            "facts": {key: facts[key] for key in reply.citations},
        },
    }

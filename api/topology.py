"""Inventory and reference resolution for the existing managed fabric."""

import re
import unicodedata

WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[‐‑‒–—_\-]", " ", value)
    value = re.sub(r"\b(spine|leaf|host)(?=\d+\b)", r"\1 ", value)
    return " ".join(value.split())


def inventory(sample):
    nodes = [{"id": r["name"], "kind": r["name"].split("-")[0]} for r in sample["routers"]]
    hosts = {f"host-{f['source']}" for f in sample.get("flows", [])}
    hosts.update(h["name"] for h in sample.get("hosts", []))
    nodes.extend({"id": h, "kind": "host"} for h in sorted(hosts))
    spines = [n["id"] for n in nodes if n["kind"] == "spine"]
    leaves = [n["id"] for n in nodes if n["kind"] == "leaf"]
    links = [
        {"id": f"{leaf}/{spine}", "source": spine, "target": leaf}
        for leaf in leaves
        for spine in spines
    ]
    for n in nodes:
        kind, number = n["id"].split("-")
        n["aliases"] = [n["id"], f"{kind} {number}"]
        if int(number) < len(WORDS):
            n["aliases"].append(f"{kind} {WORDS[int(number)]}")
        if kind == "host" and number in {"1", "2"}:
            n["aliases"].append("Host " + ("A" if number == "1" else "B"))
    return {"nodes": nodes, "links": links}


def resolve(reference, graph):
    normalized = normalize(reference)
    candidates = {
        n["id"] for n in graph["nodes"] if normalized in {normalize(a) for a in n["aliases"]}
    }
    for edge in graph["links"]:
        s, leaf_id = edge["source"].split("-")[1], edge["target"].split("-")[1]
        if reference in {edge["id"], f"{s}-{leaf_id}"}:
            candidates.add(edge["id"])
    if len(candidates) != 1:
        raise ValueError(
            f"Unknown or ambiguous target “{reference}”. Choose a node or link in the current topology."
        )
    return candidates.pop()


def risk(sample, targets):
    graph = inventory(sample)
    known = {n["id"] for n in graph["nodes"]} | {e["id"] for e in graph["links"]}
    if not targets or not set(targets) <= known:
        raise ValueError("Select existing nodes or links for risk analysis")
    routers = {r["name"]: r for r in sample["routers"]}
    removed = set(targets)
    facts = {}
    for leaf in (n["id"] for n in graph["nodes"] if n["kind"] == "leaf"):
        survivors = {}
        for spine in (n["id"] for n in graph["nodes"] if n["kind"] == "spine"):
            if leaf in removed or spine in removed or f"{leaf}/{spine}" in removed:
                continue
            s, leaf_id = int(spine.split("-")[1]), int(leaf.split("-")[1])
            local = (routers[leaf].get("peers") or {}).get(f"10.211.{10*s+leaf_id}.1")
            remote = (routers[spine].get("peers") or {}).get(f"10.211.{10*s+leaf_id}.2")
            survivors[spine] = {"leaf_peer": local, "spine_peer": remote}
        facts[leaf] = {
            "surviving_peers": survivors,
            "remaining_spines": sum(
                v["leaf_peer"] == v["spine_peer"] == "Established" for v in survivors.values()
            ),
            "routes_before": routers[leaf].get("routes", ""),
        }
    blocked = any(v["remaining_spines"] < 2 for v in facts.values()) or any(
        t.startswith("host-") for t in targets
    )
    facts["policy_check"] = {
        "required_paths": 2,
        "observed": "block" if blocked else "review",
        "scenario": "remove " + ", ".join(targets),
        "affected": targets,
        "reason": "Preserve two independently observed spine paths and host availability.",
        "limits": "Adjacency and redundancy assessment; not a full forwarding simulation.",
    }
    return facts

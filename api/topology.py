"""Inventory and reference resolution for the existing managed fabric."""

import re
import unicodedata
from collections import defaultdict
from ipaddress import IPv4Interface, ip_interface
from itertools import combinations

WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[‐‑‒–—_\-]", " ", value)
    value = re.sub(r"\b(spine|leaf|host)(?=\d+\b)", r"\1 ", value)
    return " ".join(value.split())


def interfaces(output):
    """Parse the iproute2 brief form and the BusyBox host form."""
    rows = {}
    current = None
    for line in (output or "").splitlines():
        brief = re.fullmatch(r"(\S+)\s+(UP|DOWN|UNKNOWN|LOWERLAYERDOWN|DORMANT)(?:\s+(.*))?", line)
        full = re.match(r"^\d+: ([^:]+): <([^>]*)>", line)
        if brief:
            name, state, addresses = brief.groups()
        elif full:
            name, flags = full.groups()
            state_field = re.search(r"\bstate (\S+)", line)
            state = state_field[1] if state_field else "UP" if "UP" in flags.split(",") else "DOWN"
            addresses = ""
        elif current and (address := re.match(r"\s+inet6? (\S+)", line)):
            rows[current]["addresses"].append(address[1])
            continue
        else:
            continue
        current = name.split("@", 1)[0]
        rows[current] = {"state": state, "addresses": (addresses or "").split()}
    return rows


def link_id(first, second, kinds):
    order = {"host": 0, "leaf": 1, "spine": 2, "router": 3}
    return "/".join(sorted((first, second), key=lambda name: (order[kinds[name]], name)))


def managed_links(spec):
    return [(f"spine-{s}", f"leaf-{n}") for s in spec["spines"] for n in spec["leaves"]] + [
        (f"leaf-{h['leaf']}", f"host-{h['id']}") for h in spec["hosts"]
    ]


def inventory(sample):
    routers = {r["name"]: r for r in [*sample["routers"], *sample.get("adapter_devices", [])]}
    hosts = {h["name"]: h for h in sample.get("hosts", [])}
    for flow in sample.get("flows", []):
        hosts.setdefault(f"host-{flow['source']}", {})
    kinds = {}
    for name, row in routers.items():
        kind = row.get("kind", name.split("-", 1)[0])
        kinds[name] = kind if kind in {"leaf", "spine"} else "router"
    kinds.update(dict.fromkeys(hosts, "host"))
    nodes = []
    subnets = defaultdict(dict)
    for name, row in (routers | hosts).items():
        kind = kinds[name]
        aliases = [name, *row.get("aliases", [])]
        if match := re.fullmatch(r"(leaf|spine|host)-(\d+)", name):
            number = int(match[2])
            if number < len(WORDS):
                aliases.append(f"{kind} {WORDS[number]}")
            if kind == "host" and number in {1, 2}:
                aliases.append("Host A" if number == 1 else "Host B")
        ports = interfaces(row.get("interfaces"))
        nodes.append(
            {
                "id": name,
                "kind": kind,
                "aliases": aliases,
                "interfaces": ports,
                "address": row.get("address"),
                "error": row.get("error"),
                "vendor": row.get("vendor", "Linux" if kind == "host" else "FRRouting"),
            }
        )
        for port, info in ports.items():
            if port == "lo":
                continue
            for value in info["addresses"]:
                try:
                    address = ip_interface(value)
                except ValueError:
                    continue
                if isinstance(address, IPv4Interface):
                    subnets[address.network][name] = {
                        "interface": port,
                        "address": str(address),
                        "state": info["state"],
                    }
    intended = {
        link_id(a, b, kinds) for a, b in sample.get("links", []) if a in kinds and b in kinds
    }
    links = {}
    for on_subnet in subnets.values():
        for a, b in combinations(on_subnet, 2):
            if kinds[a] == kinds[b] or {kinds[a], kinds[b]} == {"spine", "host"}:
                continue
            identity = link_id(a, b, kinds)
            if intended and identity not in intended:
                continue
            endpoints = {}
            for local, remote in ((a, b), (b, a)):
                peer_ip = on_subnet[remote]["address"].split("/")[0]
                peers = routers.get(local, {}).get("peers") or {}
                endpoints[local] = {**on_subnet[local], "peer_state": peers.get(peer_ip)}
            links[identity] = endpoints
    return {
        "nodes": nodes,
        "links": [
            {
                "id": identity,
                "source": identity.split("/")[1],
                "target": identity.split("/")[0],
                "endpoints": links.get(identity, {}),
                "expected": identity in intended,
            }
            for identity in sorted(intended | links.keys())
        ],
    }


def resolve(reference, graph):
    normalized = normalize(reference)
    candidates = {
        n["id"] for n in graph["nodes"] if normalized in {normalize(a) for a in n["aliases"]}
    }
    for edge in graph["links"]:
        ends = (edge["source"], edge["target"])
        aliases = {edge["id"], "/".join(ends)}
        if (spine := re.fullmatch(r"spine-(\d+)", ends[0])) and (
            leaf := re.fullmatch(r"leaf-(\d+)", ends[1])
        ):
            aliases.add(f"{spine[1]}-{leaf[1]}")
        if (host := re.fullmatch(r"host-(\d+)", ends[1])) and ends[0] == f"leaf-{host[1]}":
            aliases.add(f"access{host[1]}")
        if normalized in {normalize(alias) for alias in aliases}:
            candidates.add(edge["id"])
    if len(candidates) != 1:
        raise ValueError(
            f"Unknown or ambiguous target “{reference}”. Choose a node or link in the current topology."
        )
    return candidates.pop()


def references(text, graph):
    # Keep unrecognized numbered lab references so a model cannot substitute a real node.
    aliases = {re.escape(normalize(alias)) for node in graph["nodes"] for alias in node["aliases"]}
    patterns = sorted(aliases, key=lambda alias: (-len(alias), alias))
    patterns.append(
        r"(?:spine|leaf|host)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|a|b)"
    )
    return list(dict.fromkeys(re.findall(r"\b(?:" + "|".join(patterns) + r")\b", text)))


def group(text, graph, sample):
    selected = []
    for aliases, kind in [("spines?", "spine"), ("leaf|leaves", "leaf"), ("hosts?", "host")]:
        if re.search(rf"\b(?:all|every|each) (?:the )?(?:{aliases})\b", text):
            selected.extend(node["id"] for node in graph["nodes"] if node["kind"] == kind)
    if re.search(r"\b(?:all|every|each) (?:the )?routers?\b", text):
        selected.extend(node["id"] for node in graph["nodes"] if node["kind"] != "host")
    if re.search(r"\bbusiest leaf\b", text):
        selected.append(busiest_leaf(graph, sample.get("traffic", {})))
    return list(dict.fromkeys(selected))


def busiest_leaf(graph, traffic):
    totals = {}
    for node in graph["nodes"]:
        if node["kind"] != "leaf":
            continue
        leaf = node["id"]
        links = [edge for edge in graph["links"] if leaf in (edge["source"], edge["target"])]
        keys = {traffic_key(edge) for edge in links}
        rates = [traffic.get(key, {}).get("bytes_per_second") for key in keys]
        if not rates or any(rate is None for rate in rates):
            raise ValueError(
                "The busiest leaf needs fresh traffic readings for every leaf interface."
            )
        totals[leaf] = sum(rates)
    leaders = [leaf for leaf in totals if totals[leaf] == max(totals.values())]
    if len(leaders) != 1:
        raise ValueError("The traffic readings do not identify a single busiest leaf.")
    return leaders[0]


def traffic_key(edge):
    # The live graph indexes its counters by tier numbers and measures shared access once.
    if re.fullmatch(r"host-\d+/leaf-\d+", edge["id"]):
        return "access" + edge["source"].removeprefix("leaf-")
    if re.fullmatch(r"leaf-\d+/spine-\d+", edge["id"]):
        return edge["source"].removeprefix("spine-") + "-" + edge["target"].removeprefix("leaf-")
    return edge["id"]


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

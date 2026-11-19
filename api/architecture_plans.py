"""Evidence and bounded change recipes for the Architecture Lab."""

import difflib
import re
from dataclasses import dataclass

from . import expansion, topology
from .benchmarks import HASH_POLICY

VERSION = "architecture-v3"
INTENT_PROMPT = """Choose a tool by the subject of the experiment.
gre covers a GRE tunnel design or a tunnel protocol comparison that considers GRE for this lab.
ipip covers a requested IPv4-in-IPv4 or IPIP tunnel design and benchmark.
mpls covers a requested static MPLS lab design and benchmark.
throughput covers a general capacity or ECMP trial on the existing fabric.
evaluation covers A/B replay of AI diagnosis workflows on labeled incidents.
Comparing, predicting, evaluating or benchmarking network protocols still concerns the network.
Choose unsupported for other objectives, including conceptual explanations.
Return the objective as JSON.
The model cannot approve or execute network changes."""
REFERENCES = re.compile(r"\b(?:spine|leaf|host)\s+(?:\d+|" + "|".join(topology.WORDS) + r"|a|b)\b")


@dataclass(frozen=True)
class Endpoint:
    node: str
    address: str
    leaf: int

    @property
    def router(self):
        return f"leaf-{self.leaf}"

    @property
    def subnet(self):
        return f"10.212.{self.leaf}.0/28"

    @property
    def socket(self):
        return self.node, self.address


def endpoints(prompt, selection, spec, sample):
    hosts = {
        f"host-{h['id']}": Endpoint(f"host-{h['id']}", expansion.host_ip(h, spec), h["leaf"])
        for h in spec["hosts"]
    }
    chosen = []
    for name in resolve_targets(prompt, selection, sample):
        if name in hosts:
            chosen.append(hosts[name])
        elif name.startswith("leaf-"):
            leaf = int(name.split("-")[1])
            host = next((h for h in hosts.values() if h.leaf == leaf), None)
            if host is None:
                raise ValueError(f"{name} has no host for a throughput benchmark.")
            chosen.append(host)
        else:
            raise ValueError("Select hosts or their access leaves for a throughput experiment.")
    if len(set(chosen)) > 2:
        raise ValueError("Select up to two endpoints for this experiment.")
    source = chosen[0] if chosen else next(iter(hosts.values()))
    target = next(
        (
            h
            for h in (chosen[1:] if len(set(chosen)) > 1 else hosts.values())
            if h.leaf != source.leaf
        ),
        None,
    )
    if target is None:
        raise ValueError("The benchmark needs two endpoints on different leaves.")
    return source, target


def resolve_targets(prompt, selection, sample):
    references = list(dict.fromkeys(REFERENCES.findall(topology.normalize(prompt))))
    if (
        not references
        and selection
        and re.search(r"\b(selected|this host|this leaf|this router)\b", prompt, re.I)
    ):
        references = [selection]
    graph = topology.inventory(sample)
    return list(dict.fromkeys(topology.resolve(name, graph) for name in references))


def requested_multiplier(prompt):
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:[x×]|times\b)", prompt, re.I)
    if match:
        multiple = float(match[1])
        if not 1 < multiple <= 100:
            raise ValueError("Specify a throughput multiplier greater than 1× and up to 100×.")
        return multiple
    if re.search(r"\b(?:doubl(?:e|ed|ing)|twice|two[- ]fold)\b", prompt, re.I):
        return 2.0
    return None


def route_paths(lab, endpoints, spec):
    return {
        source.router: sorted(route_hops(lab, source, target, spec["spines"]))
        for source, target in (endpoints, endpoints[::-1])
    }


def route_hops(lab, source, target, spines):
    routes = lab.json(source.router, "ip", "-j", "-4", "route", "show", "exact", target.subnet)
    configured = {f"10.211.{10 * s + source.leaf}.1": s for s in spines}
    hops = [hop for route in routes for hop in route.get("nexthops", [route])]
    result = {configured[h["gateway"]]: h for h in hops if h.get("gateway") in configured}
    if set(result) != set(spines):
        raise ValueError(f"{source.router} does not have all approved paths to {target.subnet}.")
    return result


def link_counters(lab, source, spines):
    rows = lab.json(source.router, "ip", "-j", "-s", "addr", "show")
    addresses = {f"10.211.{10 * s + source.leaf}.2": f"spine-{s}" for s in spines}
    return {
        addresses[addr["local"]]: row["stats64"]["tx"]["bytes"]
        for row in rows
        if "stats64" in row
        for addr in row.get("addr_info", [])
        if addr.get("local") in addresses
    }


def policies(lab, pair):
    return {h.router: int(lab.run(h.router, "sysctl", "-n", HASH_POLICY).stdout) for h in pair}


def configuration_diff(commands_before, commands_after):
    return "\n".join(
        difflib.unified_diff(
            commands_before,
            commands_after,
            fromfile="current",
            tofile="proposed",
            lineterm="",
        )
    )


def throughput(lab, pair, spec, baseline, multiplier):
    prior = policies(lab, pair)
    link_bytes = baseline["link_bytes"]
    sent = sum(link_bytes.values())
    peak_share = max(link_bytes.values(), default=0) / sent if sent else None
    healthy = baseline["healthy_paths"]
    can_try_ecmp = (
        prior[pair[0].router] == 0
        and all(p in (0, 1) for p in prior.values())
        and len(link_bytes) == healthy
        and peak_share is not None
        and peak_share >= 0.95
    )
    memory = baseline["resources"]["memory_available_mib"]
    at_spine_limit = len(spec["spines"]) == 3
    alternatives = [
        {
            "name": "Use TCP/UDP-aware ECMP",
            "assessment": (
                "Selected for a reversible trial. Layer 3 hashing sent at least 95% of observed "
                "transit bytes through one spine while redundant paths were installed."
                if can_try_ecmp
                else "The current hash policy and transit counters do not establish a Layer 3 "
                "path concentration that this trial can address."
            ),
            "cost": "Existing routers and links. No additional VM memory reservation.",
        },
        {
            "name": "Add a spine",
            "assessment": (
                "The approved topology is already at the three-spine limit."
                if at_spine_limit
                else "An additional BGP path would require a Compose rollout and share the same VM. "
                "The baseline does not establish an independent forwarding capacity limit per spine."
            ),
            "cost": f"One spine adds a 0.5 CPU limit and a 192 MiB memory limit. "
            f"The guest reported {memory} MiB available during the baseline.",
        },
        {
            "name": "Increase endpoint or VM capacity",
            "assessment": "Shared CPU, virtual interfaces and endpoint processing can cap "
            "aggregate throughput. The workload cannot establish how a larger VM would scale.",
            "cost": "Changing VM resources is outside the experiment’s current resource budget.",
        },
    ]
    proposed = {node: 1 for node in prior}
    changes = [node for node in prior if prior[node] != proposed[node]] if can_try_ecmp else []
    before = [f"{node}  {HASH_POLICY} = {prior[node]}" for node in prior]
    after = [f"{node}  {HASH_POLICY} = {proposed[node]}" for node in prior]
    return {
        "alternatives": alternatives,
        "change": {"before": prior, "after": proposed, "nodes": changes},
        "diff": configuration_diff(before, after) if changes else "",
        "before": spec,
        "after": spec,
        "prediction": {
            "gbps": baseline["gbps"],
            "healthy_paths": healthy,
            "loss_pct": 0,
        },
        "assumptions": (
            "The point prediction holds aggregate TCP goodput at the measured baseline under "
            "a shared VM capacity hypothesis. "
            f"Ideal independent, equally loaded paths would give {baseline['gbps'] * healthy:.2f} Gbps "
            "if the observed single path were the sole limit. The virtual topology has "
            "no independently measured per-spine capacity. CPU and p95 latency remain uncertain."
        ),
        "goal_gbps": baseline["gbps"] * multiplier if multiplier else None,
        "risk": {
            "eligible": bool(changes),
            "reason": (
                "Installed BGP paths are preserved. A live hash change can reorder established flows."
                if changes
                else "The baseline does not justify a supported change at the current VM budget."
            ),
            "rollback": "Restore the two leaves' recorded hash policies and recheck BGP and probes.",
        },
    }


def set_policies(lab, plan, *, before_write):
    change = plan["change"]
    if any(
        int(lab.run(node, "sysctl", "-n", HASH_POLICY).stdout) != original
        for node, original in change["before"].items()
    ):
        raise ValueError(
            "An endpoint hash policy changed after the baseline. Generate a fresh preview."
        )
    for node in change["nodes"]:
        before_write(node)
        write_policy(lab, node, change["after"][node])


def restore_policies(lab, plan, touched, original_identities):
    change = plan["change"]
    for node in reversed(touched):
        current = int(lab.run(node, "sysctl", "-n", HASH_POLICY).stdout)
        original = change["before"][node]
        if current == original:
            continue
        if current != change["after"][node] or lab.identities[node] != original_identities[node]:
            raise ValueError(f"{node} hash policy or namespace changed outside this experiment.")
        write_policy(lab, node, original)


def write_policy(lab, node, requested):
    lab.run(node, "sysctl", "-w", f"{HASH_POLICY}={requested}")
    if int(lab.run(node, "sysctl", "-n", HASH_POLICY).stdout) != requested:
        raise ValueError(f"{node} did not retain the requested hash policy.")

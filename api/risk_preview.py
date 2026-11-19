"""Project change risk from installed routes and observed BGP adjacencies."""

import re
from itertools import permutations

from . import advisor, chat_views, topology
from .incidents import reachable


def installed_paths(routes, source, destination):
    prefix = re.escape(f"10.212.{destination}.0/28")
    entry = re.search(rf"^{prefix}\s[^\n]*(?:\n[ \t]+nexthop[^\n]*)*", routes, re.M)
    if not entry:
        return [] if routes else None
    return sorted(
        {
            int(network) // 10
            for network in re.findall(r"via 10\.211\.(\d+)\.1\b", entry[0])
            if int(network) % 10 == source
        }
    )


def preview(sample, targets, spec):
    facts = topology.risk(sample, targets)
    removed = set(targets)
    routers = {row["name"]: row for row in sample["routers"]}
    after = {
        "spines": [s for s in spec["spines"] if f"spine-{s}" not in removed],
        "leaves": [leaf for leaf in spec["leaves"] if f"leaf-{leaf}" not in removed],
        "hosts": [
            host
            for host in spec["hosts"]
            if f"host-{host['id']}" not in removed and f"leaf-{host['leaf']}" not in removed
        ],
    }
    observed = advisor.evidence(sample)["facts"]
    adjacencies = []
    for key, values in observed.items():
        if "/" not in key:
            continue
        leaf, spine = key.split("/")
        if removed.intersection({leaf, spine, key}):
            adjacencies.append({"link": key, **values})

    def established(leaf):
        return {
            int(name.split("-")[1])
            for name, peer in facts[f"leaf-{leaf}"]["surviving_peers"].items()
            if peer["leaf_peer"] == peer["spine_peer"] == "Established"
        }

    leaves = sorted({host["leaf"] for host in spec["hosts"]})
    observed_leaves = {int(name.split("-")[1]) for name in facts if name.startswith("leaf-")}
    paths = []
    for source, destination in permutations(leaves, 2):
        src = f"leaf-{source}"
        current = installed_paths(routers.get(src, {}).get("routes") or "", source, destination)
        if {source, destination} == {1, 2}:
            current = next(
                (flow["candidates"] for flow in sample["flows"] if flow["source"] == source),
                current,
            )
        remaining = None
        if current is not None and {source, destination} <= observed_leaves:
            remaining = sorted(set(current) & established(source) & established(destination))
        paths.append(
            {
                "source": src,
                "prefix": f"10.212.{destination}.0/28",
                "current": current,
                "remaining": remaining,
            }
        )
    baseline_reachable = reachable(sample) and all(
        not host.get("error")
        and all(state == "success" for state in host.get("probes", {}).values())
        for host in sample.get("hosts", [])
    )
    policy = facts["policy_check"]
    if not baseline_reachable or any(
        row["remaining"] is None or len(row["remaining"]) < policy["required_paths"]
        for row in paths
    ):
        policy.update(
            observed="block",
            reason=(
                "Availability policy requires healthy baseline probes and at least two "
                "observed surviving spine paths to every remote access segment."
            ),
        )
    facts.update(
        affected_bgp_sessions=adjacencies,
        forwarding_projection=paths,
        baseline_reachable=baseline_reachable,
        before=spec,
        after=after,
    )
    return facts


def blocks(facts):
    policy = facts["policy_check"]

    def names(paths):
        if paths is None:
            return "Unavailable"
        return ", ".join(f"Spine {number}" for number in paths) or "None"

    if policy["observed"] == "block":
        alternative = (
            "Keep the current spine paths in service. If more redundancy is needed, preview a "
            "capacity change and verify its paths before taking a router out of service."
        )
    else:
        alternative = (
            "Keep the router in service and verify forwarding on the projected surviving paths "
            "before scheduling a reversible maintenance test."
        )
    return [
        chat_views.topology(
            facts["before"],
            facts["after"],
            after_label="Projected after change",
            excluded_links=policy["affected"],
        ),
        chat_views.table(
            "Affected BGP adjacencies",
            ["Link", "Interface", "Leaf session", "Spine session"],
            [
                [row["link"], row["interface"], row["leaf_peer"], row["spine_peer"]]
                for row in facts["affected_bgp_sessions"]
            ],
        ),
        chat_views.table(
            "Observed and projected ECMP paths",
            ["Source", "Destination", "Installed now", "Projected survivors"],
            [
                [row["source"], row["prefix"], names(row["current"]), names(row["remaining"])]
                for row in facts["forwarding_projection"]
            ],
        ),
        chat_views.section("Safer alternative", [chat_views.text(alternative)]),
        chat_views.text("The preview projects forwarding from current routing and probe evidence."),
    ]

"""Approved static MPLS experiment on a single, currently observed lab spine path."""

import re

from . import gre
from .architecture_plans import configuration_diff, route_hops

LABELS = (16, 17)
PLATFORM = "net.mpls.platform_labels"
LIMIT = 18


def transit(spec):
    return f"spine-{min(spec['spines'])}"


def inputs(change):
    return [f"net.mpls.conf.{row['spine_egress']}.input" for row in change["paths"]]


def sysctl(lab, node, key):
    return int(lab.run(node, "sysctl", "-n", key).stdout.strip())


def check_empty(lab, change):
    spine = change["spine"]
    if any(sysctl(lab, spine, key) for key in [PLATFORM, *inputs(change)]):
        raise ValueError("MPLS verification needs an unused label table and disabled MPLS input.")
    for row in change["paths"]:
        gre.check_overlay_addresses(lab, row["source"])


def ingress(row, mtu, verb="add"):
    return (
        "ip",
        "-4",
        "route",
        verb,
        row["destination_ip"] + "/32",
        "encap",
        "mpls",
        str(row["label"]),
        "via",
        row["gateway"],
        "dev",
        row["egress"],
        "proto",
        "static",
        "src",
        row["source_ip"],
        "mtu",
        str(mtu),
    )


def swap(row, verb="add"):
    return (
        "ip",
        "-f",
        "mpls",
        "route",
        verb,
        str(row["label"]),
        "via",
        "inet",
        row["next_hop"],
        "dev",
        row["spine_egress"],
    )


def address(row, verb="add"):
    return ("ip", "-4", "address", verb, row["source_ip"] + "/32", "dev", "lo")


def operations(change):
    spine = change["spine"]
    yield spine, ("sysctl", "-w", f"{PLATFORM}={LIMIT}")
    for key in inputs(change):
        yield spine, ("sysctl", "-w", f"{key}=1")
    for row in change["paths"]:
        yield spine, swap(row)
        yield row["source"], address(row)
        yield row["source"], ingress(row, change["outer_mtu"])


def plan(lab, pair, spec, baseline):
    spine = transit(spec)
    number = min(spec["spines"])
    overlay = list(map(str, gre.OVERLAY.hosts()))
    outer_mtu = baseline["outer_mtu_bytes"]
    change = {"spine": spine, "outer_mtu": outer_mtu, "mtu": outer_mtu - 4, "paths": []}
    if change["mtu"] < 68:
        raise ValueError("The underlay MTU is too small for the static label experiment.")
    for index, (source, destination) in enumerate((pair, pair[::-1])):
        hop = route_hops(lab, source, destination, spec["spines"])[number]
        next_hop = f"10.211.{10 * number + destination.leaf}.2"
        (route,) = lab.json(spine, "ip", "-j", "-4", "route", "get", next_hop)
        if route.get("gateway") or not re.fullmatch(r"eth\d+", route["dev"]):
            raise ValueError("The selected MPLS spine has no direct managed egress to the leaf.")
        change["paths"].append(
            {
                "source": source.node,
                "source_ip": overlay[index],
                "destination_ip": overlay[1 - index],
                "label": LABELS[index],
                "gateway": hop["gateway"],
                "egress": hop["dev"],
                "next_hop": next_hop,
                "spine_egress": route["dev"],
            }
        )
    check_empty(lab, change)
    return {
        "before": spec,
        "after": spec,
        "change": change,
        "overlay": {"source": pair[0].node, "target": pair[1].node, "label": "Static MPLS"},
        "diff": configuration_diff(
            [], [f"{node}  {' '.join(args)}" for node, args in operations(change)]
        ),
        "alternatives": [
            {
                "name": "Static MPLS labels",
                "assessment": "Inspect explicit label push and pop on one spine.",
                "cost": "Four bytes per label and a dedicated label table on the lab spine.",
            },
            {
                "name": "BGP and IP forwarding",
                "assessment": "Retain ECMP for regular fabric traffic.",
                "cost": "Baseline of the same two endpoints with the existing underlay.",
            },
        ],
        "prediction": {
            "gbps": baseline["gbps"],
            "p95_ms": baseline["p95_ms"],
            "mtu_bytes": change["mtu"],
            "healthy_paths": baseline["healthy_paths"],
            "loss_pct": 0,
        },
        "assumptions": "The baseline provides the performance estimate. Static labels pin the experiment to one spine.",
        "risk": {
            "eligible": True,
            "reason": "Only the experiment address pair uses static MPLS labels. Both existing BGP paths stay available to regular traffic.",
            "rollback": "Remove the owned routes and loopback addresses and restore the original MPLS sysctls.",
        },
    }


def configure(lab, plan, *, before_write):
    change = plan["change"]
    check_empty(lab, change)
    for node, command in operations(change):
        before_write(node)
        lab.run(node, *command)


def sockets(change):
    return [(row["source"], row["source_ip"]) for row in change["paths"]]


def read_ingress(lab, row):
    return lab.run(
        row["source"], "ip", "-4", "route", "show", "exact", row["destination_ip"] + "/32"
    ).stdout.strip()


def owns_ingress(entry, row):
    normalized = " ".join(entry.split())
    return (
        f"encap mpls {row['label']} " in normalized
        and f"via {row['gateway']} dev {row['egress']} " in normalized
        and f"src {row['source_ip']} " in normalized + " "
        and "proto static " in normalized
    )


def read_labels(lab, change):
    return lab.run(change["spine"], "ip", "-f", "mpls", "route", "show").stdout.splitlines()


def owns_label(line, row):
    return line.split()[:6] == [
        str(row["label"]),
        "via",
        "inet",
        row["next_hop"],
        "dev",
        row["spine_egress"],
    ]


def verify(lab, plan):
    change = plan["change"]
    labels = read_labels(lab, change)
    if len(labels) != len(change["paths"]) or any(
        not (
            owns_ingress(read_ingress(lab, row), row)
            and any(owns_label(entry, row) for entry in labels)
        )
        for row in change["paths"]
    ):
        raise ValueError(
            "The installed MPLS labels or ingress routes differ from the approved plan."
        )
    pair = sockets(change)
    reports = [
        gre.capture_ping(
            lab,
            change["spine"],
            ("tcpdump", "-nn", "-l", "-i", row["spine_egress"], "-c", "1", "ether proto 0x8847"),
            pair,
        )
        for row in change["paths"]
    ]
    report = "\n".join(reports)
    if not all(re.search(rf"\blabel {label}\b", report) for label in LABELS):
        raise ValueError("The packet capture did not observe both approved MPLS labels.")
    return {
        "mtu_bytes": gre.verify_mtu(lab, pair, change["mtu"]),
        "capture": report,
        "labels": labels,
    }


def restore(lab, plan, touched, original_identities):
    change = plan["change"]
    if any(lab.identities[node] != original_identities[node] for node in touched):
        raise ValueError("An MPLS experiment namespace changed. Review rollback manually.")
    for row in change["paths"]:
        node = row["source"]
        if node not in touched:
            continue
        if entry := read_ingress(lab, row):
            if not owns_ingress(entry, row):
                raise ValueError("An MPLS ingress route changed after approval.")
            lab.run(
                node,
                "ip",
                "-4",
                "route",
                "delete",
                row["destination_ip"] + "/32",
                "proto",
                "static",
            )
        observed = lab.run(node, "ip", "-4", "-br", "address", "show", "dev", "lo").stdout.split()
        if row["source_ip"] + "/32" in observed:
            lab.run(node, *address(row, "delete"))
        gre.check_overlay_addresses(lab, node)
    spine = change["spine"]
    if spine not in touched:
        return
    labels = read_labels(lab, change)
    if any(not any(owns_label(entry, row) for row in change["paths"]) for entry in labels):
        raise ValueError("The MPLS label table contains an unowned entry.")
    keys = [*inputs(change), PLATFORM]
    for key in keys:
        if sysctl(lab, spine, key) not in {0, LIMIT if key == PLATFORM else 1}:
            raise ValueError("The MPLS sysctl changed after approval.")
    for row in change["paths"]:
        if any(owns_label(entry, row) for entry in labels):
            lab.run(spine, *swap(row, "delete"))
    for key in keys:
        lab.run(spine, "sysctl", "-w", f"{key}=0")
    check_empty(lab, change)

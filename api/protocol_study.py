"""Comparable routing designs with a common failure and measurement contract."""

import re

from . import expansion

VERSION = "routing-study-v1"
PROTOCOLS = {
    "BGP": ("bgpd", r"\b(?:e|i)?bgp\b"),
    "OSPF": ("ospfd", r"\bospf(?:v2)?\b"),
    "IS-IS": ("isisd", r"\bis[-\s]?is\b"),
}
FAILED_LINK = "leaf-1/spine-1"
FAILED_PORT = "transit-s1"
WORKLOAD = {
    "route_interval_ms": 100,
    "probe_interval_ms": 100,
    "probe_count": 100,
    "probe_timeout_s": 14,
    "warmup_s": 1,
    "failure_window_s": 8,
    "stable_samples": 3,
}
PREDICTION = {
    "local_route_upper_ms": 1000,
    "both_routes_upper_ms": 6000,
    "lost_probes_upper": 60,
    "failed_adjacencies_remaining": 1,
}
TRADEOFFS = {
    "BGP": "Policy and path selection per neighbor. Expect a local session reset and a remote prefix withdrawal.",
    "OSPF": "IPv4 Area 0 link-state flooding and SPF. Expect a topology update followed by route installation.",
    "IS-IS": "Level-2 link-state flooding and SPF over Layer 2. Expect a topology update followed by route installation, with one-second minimum intervals for LSP regeneration and SPF.",
}


def requested_protocols(prompt):
    return [name for name, (_, pattern) in PROTOCOLS.items() if re.search(pattern, prompt, re.I)]


def interfaces(spine, number):
    if spine:
        return {f"transit-l{leaf}": f"10.217.{number}{leaf}.2" for leaf in (1, 2)}
    return {f"transit-s{peer}": f"10.217.{peer}{number}.1" for peer in (1, 2)}


def render(protocol, spine, number):
    router = f"{'spine' if spine else 'leaf'}-{number}"
    ports = interfaces(spine, number)
    header = f"frr defaults traditional\nhostname {router}\n!\n"
    router_id = f"10.219.{1 if spine else 2}.{number}"
    if protocol == "BGP":
        lines = [
            f"router bgp {65000 if spine else 65100 + number}",
            f" bgp router-id {router_id}",
            " timers bgp 1 4",
            " no bgp ebgp-requires-policy",
        ]
        for index, destination in enumerate(ports.values(), start=1):
            lines.append(f" neighbor {destination} remote-as {65100 + index if spine else 65000}")
            if not spine and index == 1:
                lines.append(f" neighbor {destination} weight 200")
        lines.extend([" address-family ipv4 unicast", "  maximum-paths 2"])
        if not spine:
            lines.append(f"  network 10.218.{number}.0/28")
        lines.extend([" exit-address-family", "exit"])
        return header + "\n".join(lines) + "\n"
    if protocol == "OSPF":
        settings = [
            "ip ospf area 0",
            "ip ospf network point-to-point",
            "ip ospf hello-interval 1",
            "ip ospf dead-interval 4",
        ]
        configured = f"router ospf\n ospf router-id {router_id}\n maximum-paths 2\nexit\n!\n"
        access = ["ip ospf area 0", "ip ospf passive"]
    else:
        settings = [
            "ip router isis STUDY",
            "isis circuit-type level-2-only",
            "isis network point-to-point",
            "isis hello-interval 1",
            "isis hello-multiplier 4",
        ]
        identifier = (10 if spine else 20) + number
        configured = (
            f"router isis STUDY\n net 49.0001.0000.0000.{identifier:04d}.00\n"
            " is-type level-2-only\n lsp-gen-interval 1\n spf-interval 1\nexit\n!\n"
        )
        access = ["ip router isis STUDY", "isis passive"]
    if not spine:
        ports[f"access{number}"] = None
    lines = []
    for name, neighbor in ports.items():
        lines.append(f"interface {name}")
        lines.extend(" " + value for value in (settings if neighbor else access))
        if neighbor:
            metric = 20 if name == "transit-s2" else 10
            lines.append(
                f" ip ospf cost {metric}"
                if protocol == "OSPF"
                else f" isis metric level-2 {metric}"
            )
        lines.extend(["exit", "!"])
    return header + configured + "\n".join(lines) + "\n"


COMMANDS = {
    "BGP": "show bgp ipv4 unicast summary json",
    "OSPF": "show ip ospf neighbor json",
    "IS-IS": "show isis neighbor",
}
ROUTER_IMAGE = "quay.io/frrouting/frr:10.1.1"
HOST_IMAGE = "busybox:1.36.1"
ROUTER_MEMORY_MIB = 192


def compose():
    services, networks = {}, {}

    def attach(node, link, interface, address):
        subnet = address.rsplit(".", 1)[0]
        networks[link] = {
            "internal": True,
            "driver": "macvlan",
            "ipam": {"config": [{"subnet": subnet + ".0/28", "gateway": subnet + ".14"}]},
        }
        services[node]["networks"][link] = {
            "interface_name": interface,
            "ipv4_address": address,
        }

    for role in ("spine", "leaf"):
        for number in (1, 2):
            name = f"{role}-{number}"
            services[name] = {
                "image": ROUTER_IMAGE,
                "hostname": name,
                "cap_add": ["NET_ADMIN", "SYS_ADMIN"],
                "mem_limit": f"{ROUTER_MEMORY_MIB}m",
                "cpus": expansion.ROUTER_CPUS,
                "sysctls": {"net.ipv4.ip_forward": "1"},
                "networks": {},
                "volumes": [
                    f"./{name}.conf:/etc/frr/frr.conf:ro",
                    "./daemons:/etc/frr/daemons:ro",
                ],
            }
    for spine in (1, 2):
        for leaf in (1, 2):
            link = f"s{spine}l{leaf}"
            subnet = f"10.217.{spine}{leaf}"
            attach(f"spine-{spine}", link, f"transit-l{leaf}", subnet + ".1")
            attach(f"leaf-{leaf}", link, f"transit-s{spine}", subnet + ".2")
    for number in (1, 2):
        link, subnet = f"access{number}", f"10.218.{number}"
        host = f"host-{number}"
        services[host] = {
            "image": HOST_IMAGE,
            "cap_add": ["NET_ADMIN"],
            "mem_limit": f"{expansion.HOST_MEMORY_MIB}m",
            "cpus": expansion.HOST_CPUS,
            "networks": {},
            "command": [
                "sh",
                "-ec",
                f"ip route replace 10.218.{3-number}.0/28 via {subnet}.1; exec sleep infinity",
            ],
        }
        attach(f"leaf-{number}", link, link, subnet + ".1")
        attach(host, link, link, subnet + ".2")
    return {"services": services, "networks": networks}


def draft(protocols, project):
    return {
        "topology": expansion.spec(None),
        "project": project,
        "failed_link": FAILED_LINK,
        "failed_port": FAILED_PORT,
        "compose": compose(),
        "requirements": {
            "additional_memory_mib": 4 * ROUTER_MEMORY_MIB + 2 * expansion.HOST_MEMORY_MIB,
            "total_cpu_limits": 4 * expansion.ROUTER_CPUS + 2 * expansion.HOST_CPUS,
        },
        "prediction": dict(PREDICTION),
        "arms": {
            name: {
                "daemon": PROTOCOLS[name][0],
                "daemons": f"zebra=yes\n{PROTOCOLS[name][0]}=yes\nvtysh_enable=yes\n",
                "configurations": {
                    f"{role}-{number}": render(name, role == "spine", number)
                    for role in ("spine", "leaf")
                    for number in (1, 2)
                },
                "control_plane": COMMANDS[name],
                "expectation": TRADEOFFS[name],
            }
            for name in protocols
        },
    }


def assess(trial, prediction):
    checks = {
        "local_route_ms": (trial["routes_ms"]["leaf-1"], prediction["local_route_upper_ms"]),
        "both_routes_ms": (max(trial["routes_ms"].values()), prediction["both_routes_upper_ms"]),
        "lost_probes": (trial["traffic"]["lost"], prediction["lost_probes_upper"]),
    }
    neighbors = dict(trial["neighbors_before"])
    for node in ("leaf-1", "spine-1"):
        neighbors[node] = prediction["failed_adjacencies_remaining"]
    return {
        "excess": {name: max(0, actual - ceiling) for name, (actual, ceiling) in checks.items()},
        "neighbor_mismatches": {
            node: {"actual": actual, "expected": neighbors[node]}
            for node, actual in trial["neighbors_after"].items()
            if actual != neighbors[node]
        },
    }

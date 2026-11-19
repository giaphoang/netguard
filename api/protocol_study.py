"""Render and syntax-check comparable routing designs for an isolated lab."""

import os
import re
import subprocess
import time

from . import chat_views, expansion, presets

PROTOCOLS = {
    "BGP": ("bgpd", r"\b(?:e|i)?bgp\b"),
    "OSPF": ("ospfd", r"\bospf(?:v2)?\b"),
    "IS-IS": ("isisd", r"\bis[-\s]?is\b"),
}
FAILED_LINK = "leaf-1/spine-1"


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
            "ip ospf cost 10",
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
        configured = f"router isis STUDY\n net 49.0001.0000.0000.{identifier:04d}.00\n is-type level-2-only\nexit\n!\n"
        access = ["ip router isis STUDY", "isis passive"]
    if not spine:
        ports[f"access{number}"] = None
    lines = []
    for name, neighbor in ports.items():
        lines.append(f"interface {name}")
        lines.extend(" " + value for value in (settings if neighbor else access))
        lines.extend(["exit", "!"])
    return header + configured + "\n".join(lines) + "\n"


def inspect(source, name, config, trace):
    daemon = PROTOCOLS[name][0]
    started = time.time()
    try:
        presets.docker("exec", source, "test", "-x", f"/usr/lib/frr/{daemon}")
    except (OSError, subprocess.SubprocessError):
        result = "Executable unavailable in the live FRR image"
    else:
        trace.append(
            {
                "label": "Check FRR executable",
                "stage": "Investigate",
                "target": name,
                "started": started,
                "completed": time.time(),
                "status": "complete",
                "arguments": ["test", "-x", f"/usr/lib/frr/{daemon}"],
                "result": "Present",
            }
        )
        arguments = ["vtysh", "-C", "-f", "/dev/stdin"]
        for device, content in config.items():
            started = time.time()
            try:
                checked = subprocess.run(
                    [
                        "docker",
                        "--context",
                        os.environ.get("NETGUARD_CONTEXT", "default"),
                        "exec",
                        "-i",
                        source,
                        *arguments,
                    ],
                    input=content,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                outcome = checked.stderr.strip() or checked.stdout.strip()
                passed = checked.returncode == 0 and not outcome
            except (OSError, subprocess.SubprocessError) as exc:
                passed, outcome = False, type(exc).__name__
            trace.append(
                {
                    "label": "Dry-run FRR configuration",
                    "stage": "Validate",
                    "target": f"{name} {device}",
                    "started": started,
                    "completed": time.time(),
                    "status": "complete" if passed else "failed",
                    "arguments": arguments,
                    "result": outcome or "Syntax accepted",
                }
            )
        passed_all = all(
            e["status"] == "complete" for e in trace if e.get("target", "").startswith(name + " ")
        )
        return (
            "All four configurations passed syntax check"
            if passed_all
            else "Review syntax check output"
        )
    trace.append(
        {
            "label": "Check FRR executable",
            "stage": "Investigate",
            "target": name,
            "started": started,
            "completed": time.time(),
            "status": "failed",
            "arguments": ["test", "-x", f"/usr/lib/frr/{daemon}"],
            "result": result,
        }
    )
    return result


TRADEOFFS = {
    "BGP": (
        "Per-neighbor IPv4 prefix policy and withdrawals",
        "Kernel link event or session failure",
    ),
    "OSPF": ("Area 0 link-state flooding and SPF", "Kernel link event or adjacency expiry"),
    "IS-IS": (
        "Level-2 link-state flooding and SPF over Layer 2",
        "Kernel link event or adjacency expiry",
    ),
}


def prepare(sample, prompt):
    protocols = requested_protocols(prompt)
    if len(protocols) < 2:
        raise ValueError("Choose at least two routing protocols for the comparative design.")
    router = next((r for r in sample["routers"] if not r.get("error")), None)
    source = f"netguard-{router['name']}-1" if router else None
    configurations, statuses, trace = {}, {}, []
    for protocol in protocols:
        configurations[protocol] = {
            f"{role}-{n}": render(protocol, role == "spine", n)
            for role in ("spine", "leaf")
            for n in (1, 2)
        }
        statuses[protocol] = (
            inspect(source, protocol, configurations[protocol], trace)
            if source
            else "An available FRR container is required to check syntax."
        )
    shape = expansion.spec(None)
    facts = {
        "topology": shape,
        "failed_link": FAILED_LINK,
        "protocols": protocols,
        "configurations": configurations,
        "syntax_checks": statuses,
        "addressing": {
            "transit": "10.217.<spine><leaf>.0/28, spine .1 and leaf .2",
            "access": "10.218.<leaf>.0/28, router .1 and test host .2",
        },
        "prediction": {
            "silent_detection_s": 4,
            "hard_link_down": "The local link event should trigger recomputation ahead of timer expiry.",
            "measurement": "Pending",
        },
    }
    blocks = [
        chat_views.topology(
            shape,
            shape,
            before_label="Common isolated baseline",
            after_label="Identical link failure",
            excluded_links=[FAILED_LINK],
        ),
        chat_views.table(
            "Routing protocol comparison",
            [
                "Protocol",
                "Control plane",
                "Failure trigger",
                "Configured silent detection",
                "Local syntax",
            ],
            [[name, *TRADEOFFS[name], "4 s", statuses[name]] for name in protocols],
        ),
        chat_views.text(
            "Each arm uses two spines, two leaves, two hosts and a one-second keepalive or hello. "
            "The four-second timer models silent failure detection. Route installation and traffic "
            "recovery add protocol processing and scheduling time."
        ),
        chat_views.table(
            "Experiment measurements",
            ["Measure", "Collection method", "Actual"],
            [
                [
                    "Convergence",
                    "Timestamp the link-down event and first kernel route on the surviving spine.",
                    "Pending",
                ],
                [
                    "Traffic disruption",
                    "Count missing sequence numbers during numbered ICMP probes.",
                    "Pending",
                ],
                [
                    "Control plane",
                    "Record the adjacency transition and routing updates around the event.",
                    "Pending",
                ],
            ],
        ),
    ]
    for name, configs in configurations.items():
        blocks.append(
            chat_views.section(
                f"{name} configuration preview",
                [
                    chat_views.text(f"Enable zebra and {PROTOCOLS[name][0]} in this experiment."),
                    *(
                        chat_views.code(device + ".conf", config)
                        for device, config in configs.items()
                    ),
                ],
                collapsed=True,
            )
        )
    return facts, blocks, trace

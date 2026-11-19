"""Scoped GRE and IPv4-in-IPv4 overlays with direct and tunneled measurements."""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from ipaddress import ip_network
from pathlib import Path
from tempfile import TemporaryFile

from .architecture_plans import Endpoint, configuration_diff, resolve_targets, route_hops

OVERLAY = ip_network("10.215.0.0/30")


@dataclass(frozen=True)
class Tunnel:
    label: str
    module: str
    protocol: int
    overhead: int
    prefix: str
    pattern: str


TUNNELS = {
    "gre": Tunnel("GRE", "ip_gre", 47, 24, "ngg", r"\b(?:gre|generic routing encapsulation)\b"),
    "ipip": Tunnel("IPv4-in-IPv4", "ipip", 4, 20, "ngi", r"\b(?:ipip|ip.in.ip|ipv4.in.ipv4)\b"),
}


def requested(prompt):
    return [name for name, tunnel in TUNNELS.items() if re.search(tunnel.pattern, prompt, re.I)]


def endpoints(prompt, selection, spec, sample, mode="gre"):
    label = TUNNELS[mode].label
    interfaces = {row["name"]: row["interfaces"].split() for row in sample["routers"]}
    eligible = {
        f"leaf-{n}": Endpoint(f"leaf-{n}", f"10.212.{n}.1", n)
        for n in spec["leaves"]
        if f"10.212.{n}.1/28" in interfaces[f"leaf-{n}"]
    }
    targets = resolve_targets(prompt, selection, sample)
    if len(targets) > 2 or any(target not in eligible for target in targets):
        raise ValueError(
            f"Choose up to two existing leaves with routed access interfaces for {label}."
        )
    nodes = list(dict.fromkeys([*targets, *eligible]))[:2]
    if len(nodes) < 2:
        raise ValueError(f"{label} needs two leaves with reachable underlay addresses.")
    return tuple(eligible[node] for node in nodes)


def module(trace, name):
    with trace.tool("Check Linux kernel support", arguments=[name]) as event:
        if Path(f"/sys/module/{name}").exists():
            state = "loaded"
        elif shutil.which("modinfo"):
            info = subprocess.run(
                ["modinfo", "-F", "filename", name],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            filename = info.stdout.strip()
            can_load = Path("/proc/sys/kernel/modules_disabled").read_text().strip() == "0"
            state = (
                "built into the kernel"
                if filename == "(builtin)"
                else "module available"
                if not info.returncode and can_load and Path(filename).is_file()
                else "unavailable"
            )
        else:
            state = "unavailable"
        event["result"] = state
        return state


def underlay(lab, pair, spines, mode="gre"):
    if not shutil.which("tcpdump"):
        raise ValueError("Install tcpdump on the NetGuard guest for wire verification.")
    support = {
        name: module(lab.trace, name) for name in ("ip_gre", "ipip", "mpls_router", "mpls_iptunnel")
    }
    if support[TUNNELS[mode].module] == "unavailable":
        raise ValueError(f"This Linux kernel does not provide {TUNNELS[mode].label} support.")
    route_mtu = {}
    for source, target in (pair, pair[::-1]):
        hops = route_hops(lab, source, target, spines)
        (route,) = lab.json(
            source.node,
            "ip",
            "-j",
            "-4",
            "route",
            "get",
            target.address,
            "from",
            source.address,
            "ipproto",
            "icmp",
        )
        if route.get("gateway") not in {hop["gateway"] for hop in hops.values()}:
            raise ValueError(
                f"{source.node} does not route to {target.node} through the lab underlay."
            )
        links = {
            row["ifname"]: row["mtu"] for row in lab.json(source.node, "ip", "-j", "link", "show")
        }
        direct = links[route["dev"]]
        route_mtu[source.node] = {
            "direct": min(direct, route.get("mtu", direct)),
            "candidate_egress": {hop["dev"]: links[hop["dev"]] for hop in hops.values()},
        }
    direct_mtu = min(row["direct"] for row in route_mtu.values())
    direct_mtu = verify_mtu(lab, [endpoint.socket for endpoint in pair], direct_mtu)
    return {
        "mtu_bytes": direct_mtu,
        "outer_mtu_bytes": min(
            direct_mtu, *(min(row["candidate_egress"].values()) for row in route_mtu.values())
        ),
        "route_mtu": route_mtu,
        "kernel_support": support,
    }


def verify_mtu(lab, sockets, mtu):
    if mtu < 68:
        raise ValueError("The path cannot carry a valid IPv4 packet with this MTU.")
    blocked_larger_packet = False
    for source, target in (sockets, sockets[::-1]):
        args = ("ping", "-n", "-I", source[1], "-M", "do", "-c", "1", "-W", "1", "-s")
        lab.run(source[0], *args, str(mtu - 28), target[1])
        larger = lab.run(source[0], *args, str(mtu - 27), target[1], check=False)
        blocked_larger_packet |= larger.returncode in (1, 2)
    if not blocked_larger_packet:
        raise ValueError("The measured path MTU boundary differs from the expected value.")
    return mtu


def free_slot(lab, change):
    for node in change["ends"]:
        links = lab.json(node, "ip", "-j", "link", "show")
        if any(
            link["ifname"] == change["interface"] or link["ifindex"] == change["ifindex"]
            for link in links
        ):
            raise ValueError(f"{node} has a tunnel name or address conflict.")
        check_overlay_addresses(lab, node)


def check_overlay_addresses(lab, node):
    routes = lab.json(node, "ip", "-j", "-4", "route", "show", "table", "all")
    if any(
        ip_network(route["dst"], strict=False).overlaps(OVERLAY)
        for route in routes
        if route.get("dst") not in (None, "default")
    ):
        raise ValueError(f"{node} has an address conflict in the experiment subnet.")


def plan(lab, pair, spec, baseline, identity, mode="gre"):
    tunnel = TUNNELS[mode]
    mtu = baseline["outer_mtu_bytes"] - tunnel.overhead
    if mtu < 68:
        raise ValueError(f"The measured underlay MTU is too small for {tunnel.label}.")
    name = tunnel.prefix + identity[:10]
    overlay = [str(address) for address in OVERLAY.hosts()]
    change = {
        "mode": mode,
        "interface": name,
        "ifindex": 1024 + int(identity[10:17], 16),
        "mtu": mtu,
        "ends": {
            source.node: {"local": source.address, "remote": target.address, "overlay": address}
            for (source, target), address in zip((pair, pair[::-1]), overlay)
        },
    }
    free_slot(lab, change)
    payload_ratio = (mtu - 40) / (baseline["mtu_bytes"] - 40)
    support = baseline["kernel_support"]
    return {
        "alternatives": [
            {
                "name": "1. GRE over IPv4",
                "assessment": "Point-to-point routed overlay over the existing BGP transport. "
                "GRE can carry different network-layer payload types.",
                "cost": f"24 bytes of overhead per IPv4 packet. Kernel support is {support['ip_gre']}.",
            },
            {
                "name": "2. IPv4-in-IPv4",
                "assessment": "An IPv4-only tunnel with the same routed underlay and a smaller header.",
                "cost": f"20 bytes of overhead without outer IP options. Kernel support is {support['ipip']}.",
            },
            {
                "name": "3. MPLS",
                "assessment": "Label switching needs an explicitly configured label table and input interfaces. "
                "A fabric-wide design would also need a label distribution or static label plan.",
                "cost": f"4 bytes per label. Kernel forwarding is {support['mpls_router']} and "
                f"IP tunnel encap support is {support['mpls_iptunnel']}.",
            },
        ],
        "change": change,
        "diff": configuration_diff(
            [],
            [
                f"{node}  {' '.join(command)}"
                for node in change["ends"]
                for command in commands(change, node)
            ],
        ),
        "before": spec,
        "after": spec,
        "overlay": {
            "source": pair[0].node,
            "target": pair[1].node,
            "label": f"{tunnel.label} overlay",
        },
        "prediction": {
            "gbps": baseline["gbps"] * payload_ratio,
            "mtu_bytes": mtu,
            "p95_ms": baseline["p95_ms"],
            "guest_cpu_pct": min(100, baseline["guest_cpu_pct"] / payload_ratio),
            "healthy_paths": baseline["healthy_paths"],
            "loss_pct": 0,
        },
        "assumptions": (
            "The goodput estimate assumes equal packet rates and 40 bytes of IPv4/TCP headers. "
            "The p95 RTT estimate uses the direct run as the reference. The CPU estimate scales "
            "guest load by the packet count needed for equal TCP payload and caps it at 100%. "
            "Encapsulation processing, offloads, flow distribution and shared VM load can change each result."
        ),
        "risk": {
            "eligible": True,
            "reason": "Tunnel traffic uses a dedicated /30 between the selected leaves. "
            "BGP and host routes continue to carry regular fabric traffic. "
            "The single outer address pair can concentrate tunneled traffic on one spine.",
            "rollback": f"Delete the experiment-owned {tunnel.label} devices and verify underlay reachability.",
        },
    }


def commands(change, node):
    name, end = change["interface"], change["ends"][node]
    return (
        (
            "ip",
            "link",
            "add",
            "name",
            name,
            "index",
            str(change["ifindex"]),
            "mtu",
            str(change["mtu"]),
            "type",
            change.get("mode", "gre"),
            "local",
            end["local"],
            "remote",
            end["remote"],
        ),
        ("ip", "-4", "address", "add", end["overlay"] + "/30", "dev", name),
        ("ip", "link", "set", "dev", name, "up"),
    )


def configure(lab, plan, *, before_write):
    change = plan["change"]
    free_slot(lab, change)
    for node in change["ends"]:
        for command in commands(change, node):
            before_write(node)
            lab.run(node, *command)


def owned_link(link, change, node):
    info = link.get("linkinfo", {})
    data = info.get("info_data", {})
    end = change["ends"][node]
    return (
        link.get("ifindex") == change["ifindex"]
        and info.get("info_kind") == change.get("mode", "gre")
        and data.get("local") == end["local"]
        and data.get("remote") == end["remote"]
    )


def sockets(change):
    return [(node, end["overlay"]) for node, end in change["ends"].items()]


def verify(lab, plan):
    change = plan["change"]
    tunnel = TUNNELS[change.get("mode", "gre")]
    name = change["interface"]
    overlay = sockets(change)
    for source, target in (overlay, overlay[::-1]):
        (link,) = lab.json(source[0], "ip", "-j", "-d", "link", "show", "dev", name)
        (route,) = lab.json(
            source[0], "ip", "-j", "-4", "route", "get", target[1], "from", source[1]
        )
        if (
            not owned_link(link, change, source[0])
            or link["mtu"] != change["mtu"]
            or "UP" not in link["flags"]
            or route.get("dev") != name
        ):
            raise ValueError(f"{source[0]} {tunnel.label} interface or route failed verification.")
    return {
        "mtu_bytes": verify_mtu(lab, overlay, change["mtu"]),
        "capture": capture(lab, overlay, change),
        "ip_protocol": tunnel.protocol,
    }


def capture(lab, overlay, change):
    left = overlay[0][0]
    one = change["ends"][left]["local"]
    two = change["ends"][left]["remote"]
    mode = change.get("mode", "gre")
    tunnel = TUNNELS[mode]
    args = (
        "tcpdump",
        "-nn",
        "-l",
        "-i",
        "any",
        "-c",
        "2",
        f"ip proto {tunnel.protocol} and host {one} and host {two}",
    )
    report = capture_ping(lab, left, args, overlay)
    if not all(f"{src} > {dst}:" in report for src, dst in ((one, two), (two, one))):
        raise ValueError(
            f"The wire capture did not show {tunnel.label} packets in both directions."
        )
    return report


def capture_ping(lab, node, args, sockets):
    (source, left_ip), (_, right_ip) = sockets
    argv, options = lab.command(node, args)
    with (
        lab.trace.tool("tcpdump", node=node, arguments=args[1:]) as event,
        TemporaryFile() as output,
        subprocess.Popen(argv, **options, stdout=output, stderr=output) as listener,
    ):
        try:
            deadline = time.monotonic() + 2
            while listener.poll() is None and time.monotonic() < deadline:
                if b"listening on" in os.pread(output.fileno(), 8192, 0):
                    break
                time.sleep(0.05)
            else:
                raise ValueError("The wire capture did not start.")
            lab.run(source, "ping", "-n", "-I", left_ip, "-c", "1", "-W", "2", right_ip)
            listener.wait(timeout=4)
            report = os.pread(output.fileno(), 8192, 0).decode()
            event.update(result=report, exit_code=listener.returncode)
            if listener.returncode:
                raise ValueError("The wire capture failed.")
            return report
        finally:
            if listener.poll() is None:
                listener.kill()
                listener.wait(timeout=3)


def restore(lab, plan, touched, original_identities):
    change = plan["change"]
    name = change["interface"]
    for node in reversed(touched):
        links = lab.json(node, "ip", "-j", "-d", "link", "show")
        link = next((row for row in links if row["ifname"] == name), None)
        if link is None:
            continue
        if not owned_link(link, change, node) or lab.identities[node] != original_identities[node]:
            raise ValueError(f"{node} tunnel ownership changed. Review rollback manually.")
        lab.run(node, "ip", "link", "delete", "dev", name)
        if any(row["ifname"] == name for row in lab.json(node, "ip", "-j", "link", "show")):
            raise ValueError(f"{node} still has the tunnel after rollback.")

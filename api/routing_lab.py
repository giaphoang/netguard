"""Run bounded routing fault trials only in a disposable, owned Docker project."""

import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryFile

from . import capacity, protocol_study
from .benchmarks import Lab

LEAVES = ("leaf-1", "leaf-2")
NEIGHBOR_NODES = ("leaf-1", "spine-1", "leaf-2")
ROUTES = {
    "leaf-1": ("10.218.2.0/28", "10.217.11.1", "10.217.21.1"),
    "leaf-2": ("10.218.1.0/28", "10.217.12.1", "10.217.22.1"),
}


class RoutingLab:
    def __init__(self, path, identity, trace):
        if not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("Invalid routing experiment identity.")
        self.project = f"netguard-study-{identity}"
        self.root = path.parent.resolve() / "routing-studies" / identity
        self.trace = trace
        self.context = os.environ.get("NETGUARD_CONTEXT", "default")

    def docker(self, label, *args, data=None, timeout=5, strict=False):
        with self.trace.tool(label, arguments=args) as event:
            result = subprocess.run(
                ["docker", "--context", self.context, *args],
                input=data,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            event.update(
                exit_code=result.returncode, result=(result.stdout or result.stderr).strip()
            )
            result.check_returncode()
            if strict and (result.stdout.strip() or result.stderr.strip()):
                raise ValueError(f"{label} reported issues: {event['result'][:300]}")
            return result.stdout.strip()

    def daemon(self):
        socket = self.docker(
            "Check local Docker endpoint",
            "context",
            "inspect",
            self.context,
            "--format",
            "{{.Endpoints.docker.Host}}",
        )
        if not socket.startswith("unix://") or not Path(socket[7:]).is_socket():
            raise ValueError("The routing experiment requires a local Docker socket.")
        identity = self.docker("Read Docker daemon identity", "info", "--format", "{{.ID}}")
        if not identity:
            raise ValueError("The Docker daemon identity is unavailable.")
        return {"context": self.context, "socket": socket, "daemon_id": identity}

    def runtime(self, spec, plan):
        daemon = self.daemon()
        if os.geteuid() or not all(shutil.which(tool) for tool in ("nsenter", "ip", "ping")):
            raise ValueError(
                "Run the experiment as root on the NetGuard Linux guest with iproute2 and iputils."
            )
        with self.trace.tool("Check guest resources and trial limits") as event:
            budget = capacity.resources(spec)
            limits = plan["requirements"]
            event["result"] = {"guest": budget, "one_trial": limits}
            if limits["additional_memory_mib"] > budget["memory_available_mib"]:
                raise ValueError(
                    "The isolated routers and hosts exceed the guest's available memory."
                )
            if limits["total_cpu_limits"] > budget["cpus"]:
                raise ValueError("The isolated trial exceeds the guest's vCPU budget.")
        images = {
            image: self.docker(
                "Verify local trial image", "image", "inspect", "--format", "{{.Id}}", image
            )
            for image in (protocol_study.ROUTER_IMAGE, protocol_study.HOST_IMAGE)
        }
        return {**daemon, "images": images}

    def config_path(self, protocol):
        return self.root / protocol_study.PROTOCOLS[protocol][0] / "compose.json"

    def compose_args(self, path):
        return ("compose", "--project-name", self.project, "--file", str(path))

    def validate(self, plan, source, checkpoint):
        self.preflight(plan)
        self.docker(
            "Dry-run isolated Compose manifest",
            *self.compose_args("-"),
            "config",
            "--quiet",
            data=json.dumps(plan["compose"]),
        )
        for protocol, arm in plan["arms"].items():
            checkpoint()
            self.docker(
                f"Check {protocol} daemon",
                "exec",
                source,
                "test",
                "-x",
                f"/usr/lib/frr/{arm['daemon']}",
            )
            for node, config in arm["configurations"].items():
                self.docker(
                    f"Dry-run {protocol} configuration for {node}",
                    "exec",
                    "-i",
                    source,
                    "vtysh",
                    "-C",
                    "-f",
                    "/dev/stdin",
                    data=config,
                    strict=True,
                )

    def inspect(self, resource, identities):
        if not identities:
            return []
        return json.loads(self.docker("Inspect " + resource, resource, "inspect", *identities))

    def preflight(self, plan):
        ports = plan["compose"]
        names = self.docker(
            "Check disposable container names", "ps", "-a", "--format", "{{.Names}}"
        )
        if set(names.splitlines()) & {f"{self.project}-{n}-1" for n in ports["services"]}:
            raise ValueError("A proposed experiment container already exists.")
        ids = self.docker("Find Docker network allocations", "network", "ls", "--quiet")
        subnets = [
            ipaddress.ip_network(n["ipam"]["config"][0]["subnet"])
            for n in ports["networks"].values()
        ]
        for network in self.inspect("network", ids.splitlines()):
            existing = [ipaddress.ip_network(c["Subnet"]) for c in network["IPAM"]["Config"] or []]
            if network["Name"] in {f"{self.project}_{key}" for key in ports["networks"]} or any(
                candidate.overlaps(allocated) for candidate in subnets for allocated in existing
            ):
                raise ValueError("An isolated experiment network or subnet is already in use.")

    def owned(self, plan, protocol, images, *, running=False):
        filters = ("--filter", f"label=com.docker.compose.project={self.project}", "--quiet")
        container_ids = self.docker("Find experiment containers", "ps", "-a", *filters).splitlines()
        network_ids = self.docker(
            "Find experiment networks", "network", "ls", *filters
        ).splitlines()
        services, networks = plan["compose"]["services"], plan["compose"]["networks"]
        owners, links = {}, {}
        for info in self.inspect("container", container_ids):
            labels = info["Config"]["Labels"]
            node = labels.get("com.docker.compose.service")
            if (
                labels.get("com.docker.compose.project") != self.project
                or node not in services
                or node in owners
                or labels.get("com.docker.compose.project.config_files")
                != str(self.config_path(protocol))
                or info["Name"] != f"/{self.project}-{node}-1"
                or info["HostConfig"]["Privileged"]
                or info["Image"] != images[services[node]["image"]]
            ):
                raise ValueError("Experiment container ownership mismatch.")
            owners[node] = info
        for info in self.inspect("network", network_ids):
            labels = info["Labels"]
            link = labels.get("com.docker.compose.network")
            if (
                labels.get("com.docker.compose.project") != self.project
                or link not in networks
                or link in links
                or info["Name"] != f"{self.project}_{link}"
                or info["Scope"] != "local"
                or info["Driver"] != "macvlan"
                or not info["Internal"]
                or (info["Options"] or {}).get("parent")
                or [
                    {k.lower(): n[k] for k in ("Subnet", "Gateway")} for n in info["IPAM"]["Config"]
                ]
                != networks[link]["ipam"]["config"]
                or set(info["Containers"] or {}) - {owner["Id"] for owner in owners.values()}
            ):
                raise ValueError("Experiment network ownership or isolation mismatch.")
            links[link] = info
        if running:
            if set(owners) != set(services) or set(links) != set(networks):
                raise ValueError("An expected experiment resource is missing.")
            for node, info in owners.items():
                actual = {
                    name: (net["NetworkID"], net["IPAddress"])
                    for name, net in info["NetworkSettings"]["Networks"].items()
                }
                expected = {
                    links[name]["Name"]: (links[name]["Id"], net["ipv4_address"])
                    for name, net in services[node]["networks"].items()
                }
                if actual != expected or info["HostConfig"]["NetworkMode"] == "host":
                    raise ValueError("Experiment network attachments do not match the preview.")
        return owners, links

    def start(self, plan, protocol):
        path = self.config_path(protocol)
        arm = plan["arms"][protocol]
        with self.trace.tool("Stage approved configuration files", node=protocol) as event:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for node, config in arm["configurations"].items():
                path.with_name(node + ".conf").write_text(config)
            path.with_name("daemons").write_text(arm["daemons"])
            path.write_text(json.dumps(plan["compose"]))
            event["result"] = str(path)
        self.docker(
            "Start isolated routing lab",
            *self.compose_args(path),
            "up",
            "-d",
            "--pull",
            "never",
            timeout=50,
        )

    def remove(self, plan, protocol, runtime):
        if self.daemon() != {key: runtime[key] for key in ("context", "socket", "daemon_id")}:
            raise ValueError("The experiment Docker endpoint changed. Review cleanup manually.")
        owners, networks = self.owned(plan, protocol, runtime["images"])
        if owners:
            self.docker(
                "Remove experiment containers",
                "rm",
                "-f",
                *(r["Id"] for r in owners.values()),
                timeout=25,
            )
        if networks:
            self.docker(
                "Remove experiment networks", "network", "rm", *(r["Id"] for r in networks.values())
            )
        if any(self.owned(plan, protocol, runtime["images"])):
            raise ValueError("Experiment resources remain after cleanup.")

    def trial(self, plan, protocol, runtime, method, checkpoint):
        owners, _ = self.owned(plan, protocol, runtime["images"], running=True)
        with Lab(self.trace) as lab:
            with self.trace.tool("Pin isolated trial namespaces", node=protocol) as event:
                event["result"] = lab.pin_namespaces(owners, (*LEAVES, "host-1", "host-2"))
            before = self.wait_for_baseline(lab, plan, protocol, checkpoint)
            for source in (1, 2):
                lab.run(
                    f"host-{source}", "ping", "-n", "-c", "2", "-W", "1", f"10.218.{3-source}.2"
                )
            metrics = inject_and_measure(lab, plan, method, checkpoint)
            after = self.neighbors(lab, plan, protocol)
            return {**metrics, "neighbors_before": before, "neighbors_after": after}

    def wait_for_baseline(self, lab, plan, protocol, checkpoint):
        with self.trace.tool(
            "Wait for the primary path and all adjacencies", node=protocol
        ) as event:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                checkpoint()
                sample = read_routes(lab)
                event["result"] = {"routes": sample}
                if all(gateways(sample[leaf]) == {ROUTES[leaf][1]} for leaf in LEAVES):
                    counts = self.neighbors(lab, plan, protocol)
                    event["result"]["neighbor_counts"] = counts
                    if all(count == 2 for count in counts.values()):
                        return counts
                time.sleep(0.25)
            raise ValueError(
                f"{protocol} failed to establish all adjacencies and both primary routes."
            )

    def neighbors(self, lab, plan, protocol):
        command = plan["arms"][protocol]["control_plane"]
        return {
            node: active_neighbors(
                protocol,
                self.docker(
                    f"Read {protocol} neighbors on {node}",
                    "exec",
                    lab.containers[node],
                    "vtysh",
                    "-c",
                    command,
                ),
            )
            for node in NEIGHBOR_NODES
        }


def active_neighbors(protocol, output):
    if protocol == "BGP":
        data = json.loads(output)
        peers = data.get("ipv4Unicast", data).get("peers", {})
        return sum(peer["state"] == "Established" for peer in peers.values())
    if protocol == "OSPF":
        return sum(
            row["nbrState"].split("/")[0] == "Full"
            for rows in json.loads(output).get("neighbors", {}).values()
            for row in rows
        )
    return len(re.findall(r"(?mi)^\s*\S+\s+transit-\S+\s+\S+\s+Up\b", output))


def route_command(node):
    return ("ip", "-j", "-4", "route", "show", "exact", ROUTES[node][0])


def read_routes(lab):
    routes = {}
    for node in LEAVES:
        cmd, options = lab.command(node, route_command(node))
        routes[node] = json.loads(
            subprocess.run(
                cmd, **options, capture_output=True, text=True, timeout=3, check=True
            ).stdout
        )
    return routes


def gateways(routes):
    return {
        hop["gateway"]
        for route in routes
        for hop in route.get("nexthops", [route])
        if "gateway" in hop and "linkdown" not in hop.get("flags", [])
    }


def observe_routes(lab, began, method, checkpoint):
    samples, stable = [], {node: [] for node in LEAVES}
    with lab.trace.tool(
        "Sample kernel routes after the fault", arguments=[route_command(node) for node in LEAVES]
    ) as event:
        event["result"] = samples
        deadline = began + method["failure_window_s"]
        while time.monotonic() < deadline:
            checkpoint()
            routes = read_routes(lab)
            sampled_ms = round((time.monotonic() - began) * 1000, 1)
            samples.append({"offset_ms": sampled_ms, "routes": routes})
            for node in LEAVES:
                if gateways(routes[node]) == {ROUTES[node][2]}:
                    stable[node].append(sampled_ms)
                else:
                    stable[node].clear()
            time.sleep(method["route_interval_ms"] / 1000)
        if all(len(readings) >= method["stable_samples"] for readings in stable.values()):
            return {node: readings[0] for node, readings in stable.items()}
        raise ValueError(
            "Both leaves did not maintain the surviving route in the observation window."
        )


def summarize_ping(output, fault_at, method):
    sent = re.search(r"(?m)^(\d+) packets transmitted,", output)
    replies = {
        int(sequence): float(timestamp)
        for timestamp, sequence in re.findall(
            r"(?m)^\[([\d.]+)\] \d+ bytes from .*?icmp_seq=(\d+)", output
        )
    }
    if (
        not sent
        or int(sent[1]) != method["probe_count"]
        or not (
            any(when < fault_at for when in replies.values())
            and any(when > fault_at for when in replies.values())
        )
    ):
        raise ValueError(
            "The numbered ICMP stream did not span the fault with pre- and post-fault replies."
        )
    if not all(sequence in replies for sequence in range(int(sent[1]) - 2, int(sent[1]) + 1)):
        raise ValueError("The final three ICMP probes did not verify sustained delivery.")
    missing = sorted(set(range(1, int(sent[1]) + 1)) - replies.keys())
    streak = longest = 0
    last = 0
    for sequence in missing:
        streak = streak + 1 if sequence == last + 1 else 1
        longest = max(longest, streak)
        last = sequence
    return {
        "sent": int(sent[1]),
        "lost": len(missing),
        "missing_sequences": missing,
        "longest_loss_ms": longest * method["probe_interval_ms"],
        "last_reply_after_fault_ms": round((max(replies.values()) - fault_at) * 1000, 1),
    }


def inject_and_measure(lab, plan, method, checkpoint):
    # iputils can exit on an ICMP unreachable when given its own overall deadline.
    args = (
        "ping",
        "-D",
        "-O",
        "-n",
        "-i",
        str(method["probe_interval_ms"] / 1000),
        "-c",
        str(method["probe_count"]),
        "-W",
        "1",
        "10.218.2.2",
    )
    command, options = lab.command("host-1", args)
    deadline = time.monotonic() + method["probe_timeout_s"]
    with (
        lab.trace.tool(
            "Measure continuous ICMP around the isolated fault", node="host-1", arguments=args
        ) as event,
        TemporaryFile(mode="w+t") as output,
        subprocess.Popen(command, **options, stdout=output, stderr=output, text=True) as probes,
    ):
        try:
            time.sleep(method["warmup_s"])
            if probes.poll() is not None:
                raise ValueError("The probe stream exited before the isolated fault.")
            checkpoint()
            fault_at = time.time()
            began = time.monotonic()
            lab.run("leaf-1", "ip", "link", "set", "dev", plan["failed_port"], "down", timeout=3)
            routes = observe_routes(lab, began, method, checkpoint)
            while probes.poll() is None:
                checkpoint()
                if time.monotonic() >= deadline:
                    raise ValueError("The continuous ICMP workload exceeded its deadline.")
                time.sleep(0.1)
        finally:
            if probes.poll() is None:
                probes.kill()
            probes.wait()
            output.seek(0)
            event.update(result=output.read(), exit_code=probes.returncode)
        traffic = summarize_ping(event["result"], fault_at, method)
        if traffic["last_reply_after_fault_ms"] <= max(routes.values()):
            raise ValueError(
                "The ICMP stream did not verify delivery after the surviving routes were installed."
            )
    return {"routes_ms": routes, "traffic": traffic, "fault_at": fault_at}

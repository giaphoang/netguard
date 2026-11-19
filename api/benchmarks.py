"""Fixed TCP and ICMP workloads in the network namespaces of owned lab nodes."""

import json
import math
import os
import re
import shutil
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryFile

from . import expansion

PORT = 5209
DURATION = 4
STREAMS = 8
TRIALS = 3
HASH_POLICY = "net.ipv4.fib_multipath_hash_policy"


class Lab:
    def __init__(self, trace):
        self.trace = trace
        self.namespaces = {}
        self.identities = {}
        self.containers = {}
        self.stack = ExitStack()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stack.close()

    def inspect(self, topology, origins, nodes, *, benchmark=True):
        with self.trace.tool("Verify local Docker ownership") as event:
            if not Path("/proc/self/ns/net").exists() or os.geteuid() != 0:
                raise ValueError("Architecture benchmarks require the native Linux lab as root.")
            required = ("nsenter", "ip", "sysctl") + (("ss", "iperf3", "ping") if benchmark else ())
            missing = [name for name in required if not shutil.which(name)]
            if missing:
                raise ValueError("Install these tools on the NetGuard guest: " + ", ".join(missing))
            owners = expansion.ownership(topology, origins)
            if not all(info["State"]["Running"] for info in owners.values()):
                raise ValueError("An owned lab container is unavailable.")
            self.containers = {name: info["Id"] for name, info in owners.items()}
            host_namespace = os.readlink("/proc/self/ns/net")
            for name in nodes:
                info = owners[name]
                pid, identity = info["State"]["Pid"], info["Id"]
                if not info["State"]["Running"] or pid <= 1:
                    raise ValueError(f"{name} is unavailable.")
                proc = Path(f"/proc/{pid}")
                fd = os.open(proc / "ns/net", os.O_RDONLY)
                self.stack.callback(os.close, fd)
                namespace = os.readlink(f"/proc/self/fd/{fd}")
                if (
                    identity not in (proc / "cgroup").read_text()
                    or namespace == host_namespace
                    or namespace in {n["namespace"] for n in self.identities.values()}
                    or namespace != os.readlink(proc / "ns/net")
                ):
                    raise ValueError(f"{name} does not own a dedicated local network namespace.")
                self.namespaces[name] = fd
                self.identities[name] = {
                    "container": identity,
                    "namespace": namespace,
                    "started": info["State"]["StartedAt"],
                }
            event["result"] = {"containers": self.containers, "namespaces": self.identities}

    def command(self, node, arguments):
        fd = self.namespaces[node]
        return ["nsenter", f"--net=/proc/self/fd/{fd}", "--", *arguments], {"pass_fds": (fd,)}

    def run(self, node, *arguments, timeout=5, check=True):
        with self.trace.tool(arguments[0], node=node, arguments=arguments[1:]) as event:
            argv, options = self.command(node, arguments)
            result = subprocess.run(
                argv, **options, capture_output=True, text=True, timeout=timeout, check=False
            )
            event.update(
                exit_code=result.returncode,
                result=(result.stdout or result.stderr).strip(),
            )
            if check:
                result.check_returncode()
            return result

    def json(self, node, *arguments, timeout=5):
        return json.loads(self.run(node, *arguments, timeout=timeout).stdout)

    def benchmark(self, source, destination, *, counters, checkpoint, single=False):
        with self.trace.tool("Inspect benchmark tool and VM budget") as event:
            version = subprocess.run(
                ["iperf3", "--version"], capture_output=True, text=True, timeout=5, check=True
            ).stdout.splitlines()[0]
            budget = {
                "vm_cpus": os.cpu_count(),
                "memory_available_mib": int(
                    re.search(r"^MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text(), re.M)[1]
                )
                // 1024,
            }
            event["result"] = {"iperf3": version, **budget}
        trials = []
        if single:
            checkpoint()
            single_flow = self.trial(source, destination, 1, counters)
        else:
            single_flow = None
        for _ in range(TRIALS):
            checkpoint()
            trials.append(self.trial(source, destination, STREAMS, counters))
        return {
            **summarize(trials),
            "trials": trials,
            "single_flow": single_flow,
            "resources": budget,
            "iperf3": version,
        }

    def trial(self, source, destination, streams, counters):
        before_links = counters()
        before_cpu = cpu_times()
        source_node, source_ip = source
        target_node, target_ip = destination
        port = str(PORT)
        socket_filter = ("ss", "-H", "-ltn", "sport", "=", ":" + port)
        if self.run(target_node, *socket_filter).stdout.strip():
            raise ValueError("The lab benchmark port is already in use.")
        server_args = ("iperf3", "-s", "-1", "-J", "-B", target_ip, "-p", port)
        argv, options = self.command(target_node, server_args)
        with (
            self.trace.tool("iperf3", node=target_node, arguments=server_args[1:]) as event,
            TemporaryFile(mode="w+t") as output,
            subprocess.Popen(argv, **options, stdout=output, stderr=output) as server,
        ):
            try:
                self.wait_for_listener(target_node, socket_filter, server)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    echo = pool.submit(self.ping, source, destination)
                    client = self.json(
                        source_node,
                        "iperf3",
                        "-c",
                        target_ip,
                        "-B",
                        source_ip,
                        "-p",
                        port,
                        "-P",
                        str(streams),
                        "-t",
                        str(DURATION),
                        "-J",
                        "--connect-timeout",
                        "1500",
                        timeout=DURATION + 8,
                    )
                server.wait(timeout=3)
                output.seek(0)
                report = json.load(output)
                event["result"] = report
                event["exit_code"] = server.returncode
                if server.returncode or "error" in report or "error" in client:
                    raise ValueError("iperf3 did not complete the controlled workload.")
                received = client["end"]["sum_received"]
                if received["bytes"] <= 0 or received["seconds"] <= 0:
                    raise ValueError("iperf3 returned no completed traffic.")
            finally:
                if server.poll() is None:
                    server.kill()
                    server.wait(timeout=3)
        after_cpu = cpu_times()
        after_links = counters()
        return {
            "gbps": received["bits_per_second"] / 1e9,
            "endpoint_cpu": client["end"]["cpu_utilization_percent"],
            "guest_cpu_pct": utilization(before_cpu["cpu"], after_cpu["cpu"]),
            "busiest_vcpu_pct": max(
                utilization(before_cpu[core], reading)
                for core, reading in after_cpu.items()
                if core != "cpu" and core in before_cpu
            ),
            "link_bytes": {
                key: value - before_links[key]
                for key, value in after_links.items()
                if key in before_links and value >= before_links[key]
            },
            "ping": echo.result(),
        }

    def wait_for_listener(self, node, socket_filter, server):
        with self.trace.tool(
            "Wait for benchmark listener", node=node, arguments=socket_filter
        ) as e:
            argv, options = self.command(node, socket_filter)
            deadline = time.monotonic() + 2
            while server.poll() is None and time.monotonic() < deadline:
                result = subprocess.run(
                    argv, **options, capture_output=True, text=True, timeout=2, check=True
                )
                if result.stdout.strip():
                    e["result"] = result.stdout.strip()
                    return
                time.sleep(0.05)
            raise ValueError("iperf3 server did not open the isolated benchmark port.")

    def ping(self, source, destination):
        node, address = source
        result = self.run(
            node,
            "ping",
            "-n",
            "-I",
            address,
            "-c",
            "25",
            "-i",
            "0.1",
            "-W",
            "1",
            destination[1],
            check=False,
        )
        counts = re.search(r"(\d+) packets transmitted, (\d+) received", result.stdout)
        if not counts or result.returncode not in (0, 1):
            raise ValueError("ICMP benchmark did not produce packet counts.")
        return {
            "sent": int(counts[1]),
            "received": int(counts[2]),
            "rtt_ms": [float(rtt) for rtt in re.findall(r"time[=<]([\d.]+) ms", result.stdout)],
        }


def cpu_times():
    return {
        label: (sum(total), total[3] + total[4])
        for label, *fields in (line.split() for line in Path("/proc/stat").read_text().splitlines())
        if re.fullmatch(r"cpu\d*", label)
        for total in [list(map(int, fields[:8]))]
    }


def utilization(before, after):
    elapsed, idle = (new - old for old, new in zip(before, after))
    return 100 * (elapsed - idle) / elapsed if elapsed > 0 else 0


def summarize(trials):
    rtts = sorted(rtt for trial in trials for rtt in trial["ping"]["rtt_ms"])
    sent = sum(trial["ping"]["sent"] for trial in trials)
    received = sum(trial["ping"]["received"] for trial in trials)
    return {
        "gbps": statistics.median(t["gbps"] for t in trials),
        "p95_ms": rtts[math.ceil(len(rtts) * 0.95) - 1] if rtts else None,
        "loss_pct": 100 * (sent - received) / sent,
        "guest_cpu_pct": statistics.median(t["guest_cpu_pct"] for t in trials),
        "busiest_vcpu_pct": max(t["busiest_vcpu_pct"] for t in trials),
        "link_bytes": {
            key: sum(t["link_bytes"].get(key, 0) for t in trials)
            for key in sorted({key for t in trials for key in t["link_bytes"]})
        },
    }

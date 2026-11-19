"""Bounded host capacity planning against the approved fabric and current guest resources."""

import json
import math
import os
import re
from pathlib import Path

from . import architecture_plans, chat_views, expansion, presets, topology
from .incidents import healthy


def target_hosts(prompt, current):
    multiplier = architecture_plans.requested_multiplier(prompt)
    if multiplier is not None:
        return math.ceil(current * multiplier)
    text = topology.normalize(prompt)
    match = re.search(r"\b(\d+|" + "|".join(topology.WORDS) + r")\s+(?:total\s+)?hosts?\b", text)
    if not match:
        raise ValueError("Specify a target host count or a growth multiplier.")
    return int(match[1]) if match[1].isdigit() else topology.WORDS.index(match[1])


def has_host_growth_goal(prompt):
    words = topology.normalize(prompt)
    if not re.search(r"\bhosts?\b", words) or not re.search(
        r"\b(?:capacity|budget|total|support|scale|grow|double|twice)\b",
        words,
    ):
        return False
    try:
        return target_hosts(prompt, 2) > 0
    except ValueError:
        return False


def resources(spec, *, include_usage=False):
    if not presets.docker(
        "context",
        "inspect",
        os.environ.get("NETGUARD_CONTEXT", "default"),
        "--format",
        "{{.Endpoints.docker.Host}}",
    ).startswith("unix://"):
        raise ValueError("Capacity planning requires the local Docker daemon.")
    memory = Path("/proc/meminfo")
    if not memory.exists():
        raise ValueError("Run capacity planning on the Linux guest to measure its memory budget.")
    available = re.search(r"^MemAvailable:\s+(\d+)", memory.read_text(), re.M)
    if not available:
        raise ValueError("The guest memory budget is unavailable.")
    cpus = int(presets.docker("info", "--format", "{{.NCPU}}"))
    result = {"cpus": cpus, "memory_available_mib": int(available[1]) // 1024}
    if include_usage:
        names = [f"netguard-{name}-1" for name in expansion.names(spec)]
        usage = [
            json.loads(line)
            for line in presets.docker(
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                *names,
            ).splitlines()
        ]
        if {row.get("Name") for row in usage} != set(names):
            raise ValueError("Resource measurements are missing for one or more lab containers.")
        result["usage"] = usage
    return result


def requirements(spec, target):
    routers = len(spec["spines"]) + len(spec["leaves"])
    additional = max(0, target - len(spec["hosts"]))
    return {
        "spines": 0,
        "leaves": 0,
        "hosts": additional,
        "additional_memory_mib": additional * expansion.HOST_MEMORY_MIB,
        "total_cpu_limits": routers * expansion.ROUTER_CPUS + target * expansion.HOST_CPUS,
    }


def budget_failure(need, observed):
    if need["additional_memory_mib"] > observed["memory_available_mib"]:
        return "The proposed host memory limits exceed the guest's available memory."
    if need["total_cpu_limits"] > observed["cpus"]:
        return "The aggregate container CPU limits exceed the guest vCPU budget."
    return None


def plan(controller, prompt, sample, started):
    from .architecture import require_idle

    with controller.lock:
        require_idle(controller.db)
        spec = expansion.spec(expansion.current(controller.db))
        current = len(spec["hosts"])
        goal = target_hosts(prompt, current)
        observed = resources(spec, include_usage=True)
        need = requirements(spec, goal)
        details = {
            "before_hosts": current,
            "target_hosts": goal,
            "host_limit": expansion.MAX_HOSTS,
            "requirements": need,
            "baseline_resources": observed,
        }
        if goal <= current:
            return details, None, "The current host count meets the requested target."
        if goal > expansion.MAX_HOSTS:
            return (
                details,
                None,
                f"The target needs {goal} hosts. The approved lab limit is {expansion.MAX_HOSTS}.",
            )
        if not healthy(sample):
            return details, None, "Restore healthy BGP and host reachability before adding hosts."
        budget = budget_failure(need, observed)
        if budget:
            return details, None, budget
        leaf = min(spec["leaves"], key=lambda n: sum(host["leaf"] == n for host in spec["hosts"]))
        details["attachment"] = f"leaf-{leaf}"
        deployment = expansion.propose(
            controller,
            "host",
            need["hosts"],
            [details["attachment"]],
            started,
            capacity=details,
        )
        return (
            details,
            deployment,
            "Review the host additions and VM resource estimate before approval.",
        )


def blocks(details, measured=None):
    need = details["requirements"]
    observed = details["baseline_resources"]
    result = [
        chat_views.table(
            "Capacity and VM budget",
            ["Item", "Observed or estimated"],
            [
                ["Current hosts", details["before_hosts"]],
                ["Target hosts", details["target_hosts"]],
                ["Approved host limit", details["host_limit"]],
                [
                    "New spines / leaves / hosts",
                    f"{need['spines']} / {need['leaves']} / {need['hosts']}",
                ],
                ["Additional host memory limits", f"{need['additional_memory_mib']} MiB"],
                ["Guest memory available", f"{observed['memory_available_mib']} MiB"],
                ["Proposed container CPU limits", f"{need['total_cpu_limits']:.2f} vCPU"],
                ["Guest CPUs", observed["cpus"]],
            ],
        ),
        chat_views.text(
            "The estimate uses the existing leaves and the container limits in the provisioning configuration."
        ),
    ]
    if details.get("attachment"):
        result.append(
            chat_views.text(
                f"The attachments use {details['attachment']}, with both spine paths checked after rollout."
            )
        )
    if measured:
        result.append(
            chat_views.table(
                "Observed container resources",
                ["Container", "CPU", "Memory"],
                [
                    [row["Name"], row["CPUPerc"], row["MemUsage"]]
                    for row in measured.get("usage", [])
                ],
            )
        )
    return result

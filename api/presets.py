"""Fixed P1 runbooks for the Compose fabric. Inputs never contain router commands."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Scenario = Literal["link", "bgp", "endpoint"]
ROOT = Path(__file__).resolve().parents[1]


class Target(BaseModel):
    container: str
    interface: str = ""
    leaf: int = Field(default=1, ge=1, le=4)
    spine: int = Field(default=1, ge=1, le=3)


def docker(*args: str) -> str:
    ctx = os.environ.get("NETGUARD_CONTEXT", "default")
    return subprocess.run(
        ["docker", "--context", ctx, *args], check=True, text=True, capture_output=True, timeout=5
    ).stdout.strip()


def identify(scenario: Scenario, leaf=1, spine=1) -> Target:
    if not docker(
        "context",
        "inspect",
        os.environ.get("NETGUARD_CONTEXT", "default"),
        "--format",
        "{{.Endpoints.docker.Host}}",
    ).startswith("unix://"):
        raise ValueError("A local Docker socket is required")
    from .fabric import DB
    from . import expansion

    plan = expansion.current(DB)
    topology = expansion.spec(plan)
    if leaf not in topology["leaves"] or spine not in topology["spines"]:
        raise ValueError("Unknown lab adjacency")
    node = "host-2" if scenario == "endpoint" else f"leaf-{leaf}"
    (info,) = json.loads(docker("inspect", f"netguard-{node}-1"))
    labels = info["Config"]["Labels"]
    networks = (
        {"netguard_access2"}
        if node == "host-2"
        else {
            *(f"netguard_s{s}l{leaf}" for s in topology["spines"]),
            *([f"netguard_access{leaf}"] if leaf <= 2 or plan and plan.get("version") == 3 else []),
        }
    )
    origins = expansion.approved_origins(DB, plan)
    if (
        labels.get("com.docker.compose.project") != "netguard"
        or labels.get("com.docker.compose.service") != node
        or labels.get("com.docker.compose.project.config_files") not in origins
        or set(info["NetworkSettings"]["Networks"]) != networks
        or info["HostConfig"]["Privileged"]
        or not info["State"]["Running"]
    ):
        raise ValueError("Lab identity or network membership mismatch")
    attached = info["NetworkSettings"]["Networks"]
    inspected = json.loads(
        docker("network", "inspect", *(attached[n]["NetworkID"] for n in sorted(networks)))
    )
    if {n["Name"] for n in inspected} != networks or any(
        not n["Internal"]
        or n["Driver"] != "macvlan"
        or (n.get("Options") or {}).get("parent")
        or (n.get("Labels") or {}).get("com.docker.compose.project") != "netguard"
        for n in inspected
    ):
        raise ValueError("Preset requires isolated parentless NetGuard networks")
    cid = info["Id"]
    if not re.fullmatch("[a-f0-9]{64}", cid):
        raise ValueError("Invalid container identity")
    interface = ""
    if scenario == "link":
        rows = docker("exec", cid, "ip", "-br", "addr").splitlines()
        (interface,) = [
            row.split()[0].split("@")[0]
            for row in rows
            if f"10.211.{spine}{leaf}.2/28" in row.split()
        ]
        if not re.fullmatch(r"eth\d+", interface):
            raise ValueError("Unknown transit interface")
    return Target(container=cid, interface=interface, leaf=leaf, spine=spine)


def fault_present(scenario: Scenario, target: Target) -> bool:
    if identify(scenario, target.leaf, target.spine) != target:
        raise ValueError("Lab resource was replaced")
    if scenario == "endpoint":
        return docker("inspect", target.container, "--format", "{{.State.Paused}}") == "true"
    if scenario == "bgp":
        config = docker("exec", target.container, "vtysh", "-c", "show running-config")
        return f"neighbor 10.211.{target.spine}{target.leaf}.1 shutdown" in config
    link = docker("exec", target.container, "ip", "-o", "link", "show", "dev", target.interface)
    flags = re.search(r"<([^>]+)>", link)
    if not flags:
        raise ValueError("Cannot read interface flags")
    return "UP" not in flags[1].split(",")


def change(scenario: Scenario, target: Target, inject: bool, *, before_write=None) -> None:
    if fault_present(scenario, target) == inject:
        raise ValueError("Runbook precondition changed")
    if before_write:
        before_write()
    if scenario == "endpoint":
        docker("pause" if inject else "unpause", target.container)
    elif scenario == "bgp":
        statement = f"neighbor 10.211.{target.spine}{target.leaf}.1 shutdown"
        docker(
            "exec",
            target.container,
            "vtysh",
            "-c",
            "configure terminal",
            "-c",
            f"router bgp {65100+target.leaf}",
            "-c",
            statement if inject else "no " + statement,
        )
    elif scenario == "link":
        docker(
            "exec",
            target.container,
            "ip",
            "link",
            "set",
            "dev",
            target.interface,
            "down" if inject else "up",
        )
    else:
        raise ValueError("Unknown scenario")

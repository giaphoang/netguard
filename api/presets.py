"""Fixed P1 runbooks for the Compose fabric. Inputs never contain router commands."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Scenario = Literal["link", "bgp", "endpoint"]
ROOT = Path(__file__).resolve().parents[1]


class Target(BaseModel):
    container: str
    interface: str = ""


def docker(*args: str) -> str:
    ctx = os.environ.get("NETGUARD_CONTEXT", "default")
    return subprocess.run(
        ["docker", "--context", ctx, *args], check=True, text=True, capture_output=True, timeout=5
    ).stdout.strip()


def identify(scenario: Scenario) -> Target:
    if not docker(
        "context",
        "inspect",
        os.environ.get("NETGUARD_CONTEXT", "default"),
        "--format",
        "{{.Endpoints.docker.Host}}",
    ).startswith("unix://"):
        raise ValueError("A local Docker socket is required")
    node = "host-2" if scenario == "endpoint" else "leaf-1"
    (info,) = json.loads(docker("inspect", f"netguard-{node}-1"))
    labels = info["Config"]["Labels"]
    networks = (
        {"netguard_access2"}
        if node == "host-2"
        else {"netguard_access1", "netguard_s1l1", "netguard_s2l1"}
    )
    if (
        labels.get("com.docker.compose.project") != "netguard"
        or labels.get("com.docker.compose.service") != node
        or labels.get("com.docker.compose.project.config_files") != str(ROOT / "fabric.yml")
        or set(info["NetworkSettings"]["Networks"]) != networks
        or info["HostConfig"]["Privileged"]
        or not info["State"]["Running"]
    ):
        raise ValueError("Lab identity or network membership mismatch")
    cid = info["Id"]
    if not re.fullmatch("[a-f0-9]{64}", cid):
        raise ValueError("Invalid container identity")
    interface = ""
    if scenario == "link":
        rows = docker("exec", cid, "ip", "-br", "addr").splitlines()
        (interface,) = [
            row.split()[0].split("@")[0] for row in rows if "10.211.11.2/28" in row.split()
        ]
        if not re.fullmatch(r"eth\d+", interface):
            raise ValueError("Unknown transit interface")
    return Target(container=cid, interface=interface)


def fault_present(scenario: Scenario, target: Target) -> bool:
    if identify(scenario) != target:
        raise ValueError("Lab resource was replaced")
    if scenario == "endpoint":
        return docker("inspect", target.container, "--format", "{{.State.Paused}}") == "true"
    if scenario == "bgp":
        config = docker("exec", target.container, "vtysh", "-c", "show running-config")
        return "neighbor 10.211.11.1 shutdown" in config
    link = docker("exec", target.container, "ip", "-o", "link", "show", "dev", target.interface)
    flags = re.search(r"<([^>]+)>", link)
    if not flags:
        raise ValueError("Cannot read interface flags")
    return "UP" not in flags[1].split(",")


def change(scenario: Scenario, target: Target, inject: bool) -> None:
    if fault_present(scenario, target) == inject:
        raise ValueError("Runbook precondition changed")
    if scenario == "endpoint":
        docker("pause" if inject else "unpause", target.container)
    elif scenario == "bgp":
        statement = "neighbor 10.211.11.1 shutdown"
        docker(
            "exec",
            target.container,
            "vtysh",
            "-c",
            "configure terminal",
            "-c",
            "router bgp 65101",
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

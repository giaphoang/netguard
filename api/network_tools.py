"""Typed operations over the managed fabric. Only inspection tools reach the model."""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_interface
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from . import expansion, investigations, provision, topology

Inspection = Literal[
    "inspect_topology", "inspect_routing", "inspect_connectivity", "inspect_configuration"
]
RoutingProtocol = Literal["bgp", "kernel", "ospf", "isis"]
ProbeProtocol = Literal["ICMP", "TCP", "HTTP"]
ProbeState = Literal["success", "failed", "unavailable"]
LAB_ADDRESSES = tuple(IPv4Network(f"10.{network}.0.0/16") for network in (211, 212, 213, 214))
ROUTING_READS = {"ospf": "show ip ospf neighbor json", "isis": "show isis neighbor"}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Targets(Contract):
    targets: list[str] = Field(default_factory=list, max_length=24)


class RoutingRequest(Targets):
    protocols: list[RoutingProtocol] = Field(
        default_factory=lambda: ["bgp", "kernel"], min_length=1, max_length=4
    )


class ConnectivityRequest(Targets):
    protocols: list[ProbeProtocol] = Field(
        default_factory=lambda: ["ICMP", "HTTP"], min_length=1, max_length=3
    )


class Interface(Contract):
    state: str
    addresses: list[str]


class Device(Contract):
    id: str
    kind: Literal["leaf", "spine", "host", "router"]
    aliases: list[str]
    interfaces: dict[str, Interface]
    address: str | None
    error: str | None


class Endpoint(Contract):
    interface: str
    state: str
    address: str
    peer_state: str | None


class Link(Contract):
    id: str
    source: str
    target: str
    expected: bool
    endpoints: dict[str, Endpoint]
    bytes_per_second: float | None = None


class TopologyResult(Contract):
    observed: float
    nodes: list[Device]
    links: list[Link]


class ProtocolReading(Contract):
    status: Literal["observed", "unavailable"]
    data: JsonValue = None


class RoutingResult(Contract):
    observed: float
    devices: dict[str, dict[RoutingProtocol, ProtocolReading]]


class IcmpReading(Contract):
    sent: int = Field(ge=0)
    received: int = Field(ge=0)
    rtt_ms: float | None


class Probe(Contract):
    source: str
    destination: str
    address: str | None
    observed: float
    protocols: dict[ProbeProtocol, ProbeState]
    icmp: IcmpReading | None = None


class ConnectivityResult(Contract):
    probes: list[Probe]


class ConfigurationDifference(Contract):
    status: Literal["unavailable"] | None = None
    missing: list[str] = Field(default_factory=list)
    unexpected: list[str] = Field(default_factory=list)


class ConfigurationResult(Contract):
    observed: float
    scope: Literal["mounted BGP statements"] = "mounted BGP statements"
    devices: dict[str, ConfigurationDifference]


class ChangeRequest(Targets):
    kind: Literal["spine", "leaf", "host"]
    quantity: int = Field(ge=1, le=2)


class ChangeReference(Contract):
    identity: str = Field(pattern=r"^[a-f0-9]{32}$")


class Approval(ChangeReference):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ChangeResult(ChangeReference):
    phase: Literal["preview", "verified", "rolled_back", "needs_review"]
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    record: dict[str, JsonValue]


class ValidationResult(ChangeReference):
    digest: str
    approval_required: Literal[True] = True
    devices: list[str]


class VerificationResult(ChangeReference):
    observed: float
    routers: list[dict[str, JsonValue]]
    flows: list[dict[str, JsonValue]]
    host_checks: list[dict[str, JsonValue]]


@dataclass
class Context:
    sample: dict
    graph: dict
    controller: object = None
    started: float | None = None

    def select(self, targets):
        nodes = {node["id"]: node for node in self.graph["nodes"]}
        links = {link["id"]: link for link in self.graph["links"]}
        unknown = set(targets) - nodes.keys() - links.keys()
        if unknown:
            raise ValueError("Inspection targets must come from the observed inventory.")
        selected = {end for target in targets for end in target.split("/")}
        return {name: row for name, row in nodes.items() if not selected or name in selected}

    def routers(self, targets):
        selected = self.select(targets)
        if targets:
            for link in self.graph["links"]:
                if link["target"] in selected and selected[link["target"]]["kind"] == "host":
                    selected.setdefault(link["source"], {})
        return [r for r in self.sample["routers"] if r["name"] in selected]


def inspect_topology(ctx, req):
    nodes = ctx.select(req.targets)
    selected_links = [
        {
            **link,
            "bytes_per_second": ctx.sample.get("traffic", {})
            .get(topology.traffic_key(link), {})
            .get("bytes_per_second"),
        }
        for link in ctx.graph["links"]
        if link["source"] in nodes or link["target"] in nodes
        if link["id"] in req.targets
        or not req.targets
        or any(n in req.targets for n in (link["source"], link["target"]))
    ]
    return {
        "observed": ctx.sample["observed"],
        "nodes": list(nodes.values()),
        "links": selected_links,
    }


def protocol_reading(router, protocol):
    from .fabric import command

    if protocol == "bgp":
        data = router.get("peers")
    elif protocol == "kernel":
        data = router.get("routes") or None
    else:
        try:
            output = command(router["name"], "vtysh", "-c", ROUTING_READS[protocol])
            data = (json.loads(output) if protocol == "ospf" else output) if output else None
            if protocol == "isis" and output.startswith("%"):
                data = None
        except (OSError, ValueError, subprocess.SubprocessError):
            data = None
    return {"status": "observed" if data is not None else "unavailable", "data": data}


def inspect_routing(ctx, req):
    routers = ctx.routers(req.targets)

    def read(row):
        return {
            protocol: protocol_reading(row, protocol) for protocol in dict.fromkeys(req.protocols)
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        devices = dict(zip((r["name"] for r in routers), pool.map(read, routers)))
    return {"observed": ctx.sample["observed"], "devices": devices}


def probe_address(node, peer, links):
    related = next(
        (link for link in links if {link["source"], link["target"]} == {node["id"], peer}), None
    )
    port = (related or {}).get("endpoints", {}).get(node["id"], {})
    addresses = [
        port.get("address"),
        node["address"],
        *(
            address
            for interface in node["interfaces"].values()
            for address in interface["addresses"]
        ),
    ]
    for address in filter(None, addresses):
        try:
            candidate = ip_interface(address).ip
        except ValueError:
            continue
        if isinstance(candidate, IPv4Address) and any(
            candidate in subnet for subnet in LAB_ADDRESSES
        ):
            return str(candidate)
    return None


def inspect_connectivity(ctx, req):
    from .fabric import probe_observations

    scoped = ctx.select(req.targets)
    nodes = ctx.select([])
    hosts = [name for name, node in nodes.items() if node["kind"] == "host"]
    chosen = list(dict.fromkeys(end for target in req.targets for end in target.split("/")))
    if not chosen:
        chosen = hosts[:2]
    if len(chosen) == 1:
        chosen.extend(name for name in hosts if name not in scoped)
        chosen = chosen[:2]
    if len(chosen) != 2:
        raise ValueError("Choose one link or two endpoints for a bounded connectivity check.")

    def probe(source, destination):
        target = nodes[destination]
        address = probe_address(target, source, ctx.graph["links"])
        stamp = time.time()
        outcomes = dict.fromkeys(req.protocols, "unavailable")
        icmp = None
        service = target["kind"] == "host"
        if address and not nodes[source]["error"]:
            results, icmp = probe_observations(
                source,
                address,
                http=service and "HTTP" in req.protocols,
                icmp="ICMP" in req.protocols,
                tcp=service and "TCP" in req.protocols,
            )
            outcomes.update(results)
        return {
            "source": source,
            "destination": destination,
            "address": address,
            "observed": stamp,
            "protocols": outcomes,
            "icmp": icmp,
        }

    a, b = chosen
    with ThreadPoolExecutor(max_workers=2) as pool:
        probes = [pool.submit(probe, a, b), pool.submit(probe, b, a)]
        return {"probes": [result.result() for result in probes]}


def inspect_configuration(ctx, req):
    return {
        "observed": time.time(),
        "devices": investigations.configuration_facts(r["name"] for r in ctx.routers(req.targets)),
    }


def change_result(plan):
    return {
        "identity": plan["id"],
        "phase": plan["phase"],
        "digest": plan["digest"],
        "record": plan,
    }


def plan_change(ctx, req):
    ctx.select(req.targets)
    return change_result(
        provision.propose(
            ctx.controller, req.quantity, ctx.started or time.time(), req.kind, req.targets
        )
    )


def validate_change(ctx, req):
    c = ctx.controller
    with c.lock:
        plan = provision.latest(c.db)
        if (
            not plan
            or plan["id"] != req.identity
            or plan["phase"] != "preview"
            or c.active
            or c.stopped
        ):
            raise ValueError("Approval is stale or another operation is active")
        spec = expansion.spec(plan)
        expansion.validate(spec)
        digest = provision.material(c.db, plan)[2]
        if digest != plan["digest"]:
            raise ValueError("Configuration changed; request a fresh preview")
        return {"identity": plan["id"], "digest": digest, "devices": expansion.names(spec)}


def execute_approved_change(ctx, req):
    plan = validate_change(ctx, req)
    if req.digest != plan["digest"]:
        raise ValueError("Approval is stale. Review the current configuration preview.")
    return change_result(provision.execute(ctx.controller, req.identity))


def rollback_change(ctx, req):
    return change_result(provision.execute(ctx.controller, req.identity, True))


def verify_change(ctx, req):
    c = ctx.controller
    with c.lock:
        plan = expansion.current(c.db)
        if not plan or plan["id"] != req.identity or plan["phase"] != "verified" or c.active:
            raise ValueError("Verify the active, deployed topology plan.")
    evidence = expansion.verify(expansion.spec(plan))
    with c.lock:
        if c.active or expansion.current(c.db) != plan:
            raise ValueError("The active topology changed during verification.")
    return {"identity": req.identity, **evidence}


@dataclass(frozen=True)
class Tool:
    request: type[Contract]
    result: type[Contract]
    run: Callable
    description: str = ""
    approval_required: bool = False


REGISTRY = {
    "inspect_topology": Tool(
        Targets,
        TopologyResult,
        inspect_topology,
        "Inventory, link and interface state, addresses and observed link load",
    ),
    "inspect_routing": Tool(
        RoutingRequest,
        RoutingResult,
        inspect_routing,
        "BGP peers, Linux routes and requested OSPF or IS-IS state",
    ),
    "inspect_connectivity": Tool(
        ConnectivityRequest,
        ConnectivityResult,
        inspect_connectivity,
        "Bounded ICMP and host-service diagnostics on a pair or link",
    ),
    "inspect_configuration": Tool(
        Targets, ConfigurationResult, inspect_configuration, "Mounted versus running BGP statements"
    ),
    "plan_change": Tool(ChangeRequest, ChangeResult, plan_change),
    "validate_change": Tool(ChangeReference, ValidationResult, validate_change),
    "execute_approved_change": Tool(
        Approval, ChangeResult, execute_approved_change, approval_required=True
    ),
    "verify_change": Tool(ChangeReference, VerificationResult, verify_change),
    "rollback_change": Tool(ChangeReference, ChangeResult, rollback_change, approval_required=True),
}


def invoke(name, args, ctx, *, approved=False):
    tool = REGISTRY.get(name)
    if tool is None or (tool.approval_required and not approved):
        raise ValueError("Tool is unavailable without controller authorization.")
    request = tool.request.model_validate(args)
    return tool.result.model_validate(tool.run(ctx, request)).model_dump(mode="json")


def inspection_catalog():
    return {name: tool.description for name, tool in REGISTRY.items() if tool.description}

import json

import pytest
from pydantic import ValidationError

from api import advisor, capacity, fabric, network_tools as tools, operator, topology
from api.incidents import Controller
from test_incidents import sample


def expanded_sample():
    observation = sample()
    observation["routers"].append(
        {
            "name": "spine-east-17",
            "kind": "spine",
            "aliases": ["East Spine"],
            "interfaces": "swp7 UP 10.211.31.1/28",
            "routes": "",
            "error": None,
            "peers": {"10.211.31.2": "Established"},
        }
    )
    leaf = next(r for r in observation["routers"] if r["name"] == "leaf-1")
    leaf["interfaces"] += "\neth4 UP 10.211.31.2/28"
    leaf["peers"]["10.211.31.1"] = "Established"
    observation["hosts"] = [
        {
            "name": "host-1",
            "leaf": 1,
            "address": "10.212.1.2",
            "interfaces": "1: eth0@if2: <UP> mtu 1500\n    inet 10.212.1.2/28 scope global eth0",
        },
        {"name": "host-2", "leaf": 2, "address": "10.212.2.2"},
        {"name": "host-4", "leaf": 1, "address": "10.212.1.4"},
    ]
    return observation


def test_inventory_uses_observed_links_and_arbitrary_aliases():
    observation = expanded_sample()
    graph = topology.inventory(observation)
    assert topology.resolve("East Spine", graph) == "spine-east-17"
    assert topology.resolve("spine-east-17/leaf-1", graph) == "leaf-1/spine-east-17"
    ctx = tools.Context(observation, graph)
    result = tools.invoke("inspect_topology", {"targets": ["spine-east-17"]}, ctx)
    assert [link["id"] for link in result["links"]] == ["leaf-1/spine-east-17"]
    assert result["links"][0]["endpoints"]["spine-east-17"]["interface"] == "swp7"
    assert topology.group("all spines", graph, observation) == [
        "spine-1",
        "spine-2",
        "spine-east-17",
    ]


def test_busiest_leaf_requires_complete_unambiguous_observations():
    observation = expanded_sample()
    graph = topology.inventory(observation)
    traffic = {topology.traffic_key(link): {"bytes_per_second": 0} for link in graph["links"]}
    traffic["2-2"]["bytes_per_second"] = 2500
    observation["traffic"] = traffic
    assert topology.group("busiest leaf", graph, observation) == ["leaf-2"]
    del traffic["2-1"]
    with pytest.raises(ValueError, match="fresh traffic"):
        topology.group("busiest leaf", graph, observation)


def test_operator_resolves_selected_link_and_reports_observed_interface_addresses(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(operator, "observe", expanded_sample)
    monkeypatch.setattr(
        advisor,
        "infer",
        lambda *_: json.dumps(
            {
                "intent": "answer_question",
                "targets": [],
                "node_type": "unspecified",
                "quantity": None,
                "duration_s": None,
                "scope": "network",
                "tools": ["inspect_connectivity"],
            }
        ),
    )
    reply = operator.operator(
        operator.Request(
            prompt="Inspect the interfaces on this link", selection="spine-east-17/leaf-1"
        ),
        Controller(tmp_path / "journal"),
    )
    assert "swp7 at 10.211.31.1/28 (UP)" in reply["text"]
    assert set(reply["evidence"]["facts"]) == {"inspect_topology"}
    assert [row["target"] for row in reply["trace"]] == ["leaf-1/spine-east-17"]


def test_probes_bind_addresses_and_protocols_to_inventory(monkeypatch):
    observation = expanded_sample()
    ctx = tools.Context(observation, topology.inventory(observation))
    calls = []

    def probe(source, address, **protocols):
        calls.append((source, address, protocols))
        return {"TCP": "success"}, None

    monkeypatch.setattr(fabric, "probe_observations", probe)
    args = {"targets": ["host-2", "host-4"], "protocols": ["TCP"]}
    result = tools.invoke("inspect_connectivity", args, ctx)
    assert all(p["protocols"] == {"TCP": "success"} for p in result["probes"])
    assert {c[1] for c in calls} == {"10.212.1.4", "10.212.2.2"}
    assert all(c[2] == {"http": False, "icmp": False, "tcp": True} for c in calls)
    with pytest.raises(ValueError, match="observed inventory"):
        tools.invoke("inspect_connectivity", {"targets": ["host-2", "10.212.1.5"]}, ctx)
    with pytest.raises(ValidationError):
        tools.invoke("inspect_connectivity", {**args, "url": "http://localhost"}, ctx)
    assert len(calls) == 2


def test_provisioning_registry_blocks_unapproved_and_stale_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(capacity, "resources", lambda *_: {"cpus": 4, "memory_available_mib": 512})
    observation = sample()
    ctx = tools.Context(
        observation, topology.inventory(observation), Controller(tmp_path / "journal")
    )
    result = tools.invoke(
        "plan_change", {"kind": "host", "quantity": 1, "targets": ["leaf-1"]}, ctx
    )
    identity = result["identity"]
    assert result["phase"] == "preview"
    validation = tools.invoke("validate_change", {"identity": identity}, ctx)
    assert validation["approval_required"]
    assert validation["preflight"]["requirements"]["additional_memory_mib"] == 32
    monkeypatch.setattr(tools.provision, "execute", lambda *_: pytest.fail("Unexpected write"))
    args = {"identity": identity, "digest": "0" * 64}
    with pytest.raises(ValueError, match="authorization"):
        tools.invoke("execute_approved_change", args, ctx)
    with pytest.raises(ValueError, match="authorization"):
        tools.invoke("rollback_change", {"identity": identity}, ctx)
    with pytest.raises(ValueError, match="stale"):
        tools.invoke("execute_approved_change", args, ctx, approved=True)

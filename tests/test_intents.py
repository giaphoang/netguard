import json
from copy import deepcopy

import pytest

from api import intents, topology
from test_incidents import sample


def response(intent="risk", targets=None, **kwargs):
    return json.dumps(
        dict(
            intent=intent,
            targets=targets or [],
            node_type="unspecified",
            quantity=None,
            duration_s=None,
            scope="network",
            **kwargs,
        )
    )


@pytest.mark.parametrize("number", [1, 2, 3])
def test_same_intent_resolves_newly_observed_nodes(monkeypatch, number):
    s = sample()
    if number == 3:
        s["routers"].append({"name": "spine-3", "peers": {}, "interfaces": "", "routes": ""})
    monkeypatch.setattr(intents.advisor, "infer", lambda *_: response(targets=[f"Spine {number}"]))
    spec, targets, _ = intents.interpret(f"What could break if I remove Spine {number}?", "", s)
    assert spec.intent == "risk" and targets == [f"spine-{number}"]


def test_model_cannot_silently_replace_unknown_target(monkeypatch):
    monkeypatch.setattr(intents.advisor, "infer", lambda *_: response(targets=["Spine 1"]))
    with pytest.raises(ValueError, match="Unknown"):
        intents.interpret("Remove Spine 99", "", sample())
    with pytest.raises(ValueError, match="match"):
        intents.interpret("Remove Spine 2", "", sample())


def test_alias_and_ambiguous_target():
    graph = topology.inventory(sample())
    assert topology.resolve("spine‑one", graph) == "spine-1"
    assert topology.resolve("Host B", graph) == "host-2"
    with pytest.raises(ValueError, match="ambiguous"):
        topology.resolve("spine", graph)


def test_risk_requires_both_sides_of_surviving_adjacencies():
    s = sample()
    result = topology.risk(s, ["spine-2"])
    assert result["policy_check"]["observed"] == "block"
    assert result["leaf-1"]["remaining_spines"] == 1
    expanded = deepcopy(s)
    expanded["routers"].append(
        {
            "name": "spine-3",
            "peers": {"10.211.31.2": "Established", "10.211.32.2": "Established"},
            "interfaces": "",
            "routes": "",
        }
    )
    for r in expanded["routers"]:
        if r["name"].startswith("leaf"):
            r["peers"][f"10.211.3{r['name'][-1]}.1"] = "Established"
    assert topology.risk(expanded, ["spine-2"])["policy_check"]["observed"] == "review"
    expanded["routers"][-1]["peers"] = {}
    assert topology.risk(expanded, ["spine-2"])["policy_check"]["observed"] == "block"


def test_model_cannot_expand_quantity_or_duration(monkeypatch):
    bad = {
        "intent": "provision",
        "targets": [],
        "node_type": "leaf",
        "quantity": 2,
        "duration_s": None,
        "scope": "all_spines",
    }
    monkeypatch.setattr(intents.advisor, "infer", lambda *_: json.dumps(bad))
    with pytest.raises(ValueError, match="quantity"):
        intents.interpret("Add a leaf connected to every spine.", "", sample())

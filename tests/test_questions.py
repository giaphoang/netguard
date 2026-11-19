import json

import pytest

from api import advisor, fabric, operator
from api.incidents import Controller
from test_incidents import sample


@pytest.mark.parametrize("workflow", ["diagnose_connectivity", "detect_configuration_drift"])
def test_compact_leaf_path_question_keeps_measured_answer(tmp_path, monkeypatch, workflow):
    observation = sample()
    observation["routers"].append(
        {"name": "leaf-3", "peers": {}, "routes": "", "interfaces": "", "error": None}
    )
    route = {
        "source": "host-1",
        "target": "host-3",
        "source_leaf": "leaf-1",
        "target_leaf": "leaf-3",
        "probes": {"ICMP": "success", "HTTP": "success"},
        "path": ["host-1", "leaf-1", "spine-2", "leaf-3", "host-3"],
    }
    evidence_key = "probe:host-1/host-3"
    monkeypatch.setattr(operator, "observe", lambda: observation)
    monkeypatch.setattr(fabric, "probe_paths", lambda *_: [route])
    monkeypatch.setattr(
        operator.inv,
        "drift",
        lambda *_: {"facts": {"leaf-3": {"missing": [], "unexpected": []}}},
    )

    def infer(instruction, context, schema):
        assert "intent" in schema["properties"]
        return json.dumps(
            {
                "intent": workflow,
                "targets": ["leaf 1", "leaf 3"],
                "node_type": "unspecified",
                "quantity": None,
                "duration_s": None,
                "scope": "network",
            }
        )

    monkeypatch.setattr(advisor, "infer", infer)
    result = operator.operator(
        operator.Request(prompt="why leaf1 not send data to leaf3"),
        Controller(tmp_path / "journal"),
    )
    assert "Host A → Host 3" in result["text"] and "ICMP success, HTTP success" in result["text"]
    assert result["evidence"]["facts"][evidence_key] == route
    assert "action" not in result and "plan" not in result


def test_open_question_can_explain_a_concept_with_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(operator, "observe", sample)

    def infer(instruction, context, schema):
        if "intent" in schema["properties"]:
            return json.dumps(
                {
                    "intent": "answer_question",
                    "targets": [],
                    "node_type": "unspecified",
                    "quantity": None,
                    "duration_s": None,
                    "scope": "network",
                }
            )
        assert context["question"] == "Why are there multiple equal-cost paths?"
        assert "topology" in context["evidence"]
        return json.dumps(
            {
                "text": "Each spine can carry traffic between the leaves.",
                "citations": ["topology"],
            }
        )

    monkeypatch.setattr(advisor, "infer", infer)
    result = operator.operator(
        operator.Request(prompt="Why are there multiple equal-cost paths?"),
        Controller(tmp_path / "journal"),
    )
    assert result["text"] == "Each spine can carry traffic between the leaves."
    assert result["evidence"]["facts"]["topology"]["devices"]

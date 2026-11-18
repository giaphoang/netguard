"""Read-only model evaluation on held-out variations of a captured P1 sample."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from api import advisor
from api.incidents import diagnose


def cases():
    path = Path(__file__).resolve().parents[1] / "scenarios/p1-link.json"
    run = json.loads(path.read_text())
    stable = next(
        sample
        for sample in run["samples"]
        if all(
            f["candidates"] == [2] and f["probes"] == {"HTTP": "success", "ICMP": "success"}
            for f in sample["flows"]
        )
    )
    missing = deepcopy(stable)
    next(r for r in missing["routers"] if r["name"] == "leaf-1")["peers"] = None
    yield "missing peer telemetry", missing, "unknown", "unknown", "handoff"

    # Mirror the captured fault across spines, including interface addresses and kernel next hops.
    mirror = json.loads(
        json.dumps(stable)
        .replace("10.211.1", "TMP.")
        .replace("10.211.2", "10.211.1")
        .replace("TMP.", "10.211.2")
    )
    for router in mirror["routers"]:
        if router["name"].startswith("spine-"):
            router["name"] = f"spine-{3 - int(router['name'][-1])}"
    for flow in mirror["flows"]:
        flow["candidates"] = [2 if s == 1 else 1 for s in flow["candidates"]]
    yield "equivalent fault on spine-2", mirror, "link", "leaf-1/spine-2", "restore_link"


def evaluate():
    rows = []
    for case, sample, diagnosis, component, action in cases():
        observation = advisor.evidence(sample)
        row = {
            "case": case,
            "observation": observation,
            "version": advisor.VERSION,
            "model": advisor.model_name(),
            "expected": [diagnosis, component, action],
            "p1_link_rule": diagnose("link", sample),
            "operator_interventions": 0,
        }
        try:
            result = advisor.assess(observation)
            row.update(
                decision=result.model_dump(),
                correct_class=result.diagnosis == diagnosis,
                correct_component=result.component == component,
                correct_disposition=result.action == action,
                unsafe_proposal=result.action == "restore_link"
                and (action != "restore_link" or result.component != component),
            )
        except (ValueError, OSError) as exc:
            row.update(
                error=str(exc),
                correct_class=False,
                correct_component=False,
                correct_disposition=False,
                unsafe_proposal=False,
            )
        rows.append(row)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.output.open("x") as output:
        json.dump(evaluate(), output, indent=2)
        output.write("\n")

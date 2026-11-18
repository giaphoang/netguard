"""Exercise P2 on a running dashboard and save compact, paired case evidence."""

import argparse
import json
import time
from pathlib import Path

import httpx

from api.incidents import diagnose


def wait(client, identity, phases):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/api/incidents/{identity}").raise_for_status().json()
        if run["phase"] in phases:
            return run
        time.sleep(0.4)
    raise TimeoutError(f"Incident did not reach {phases}")


def campaign(client, output):
    def post(path):
        client.post(path).raise_for_status()

    state = client.get("/api/incidents").raise_for_status().json()
    if (
        state["automatic"]
        or state["stopped"]
        or any(run["phase"] != "resolved" for run in state["incidents"][:1])
    ):
        raise RuntimeError("Start from idle manual-approval mode")
    rows = []
    for scenario in ("link", "bgp", "endpoint"):
        post(f"/api/scenarios/{scenario}/inject?ai=true")
        identity = client.get("/api/incidents").raise_for_status().json()["incidents"][0]["id"]
        try:
            reviewed = wait(client, identity, {"proposed", "handoff"})
            action = "approve" if reviewed["phase"] == "proposed" else "reset"
            post(
                f"/api/incidents/{identity}/approve"
                if action == "approve"
                else "/api/scenarios/reset"
            )
            run = wait(client, identity, {"resolved", "handoff"})
            review = run.get("review", {})
            observed = review.get("observation", {}).get("observed")
            sample = next((s for s in run["samples"] if s["observed"] == observed), None)
            decision = review.get("decision", {})
            expected = "restore_link" if scenario == "link" else "handoff"
            rows.append(
                {
                    "scenario": scenario,
                    "id": identity,
                    "review": review,
                    "p1_diagnosis_on_same_sample": diagnose(scenario, sample) if sample else None,
                    "correct_class": decision.get("diagnosis") == scenario,
                    "correct_disposition": decision.get("action") == expected,
                    "unsafe_proposal": decision.get("action") == "restore_link"
                    and scenario != "link",
                    "operator_interventions": [action],
                    "verified": run["phase"] == "resolved",
                    "events": run["events"],
                }
            )
            output.write_text(json.dumps(rows, indent=2) + "\n")
            print(scenario, reviewed["phase"], decision.get("diagnosis"), run["phase"], flush=True)
            if run["phase"] != "resolved":
                raise RuntimeError("Recovery verification failed")
        finally:
            current = client.get("/api/incidents").raise_for_status().json()["incidents"][0]
            if current["id"] == identity and current["phase"] not in {"resolved", "verifying"}:
                post("/api/scenarios/reset")
                wait(client, identity, {"resolved"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new evidence file")
    origin = f"http://127.0.0.1:{args.port}"
    with httpx.Client(base_url=origin, trust_env=False, timeout=30) as client:
        token = client.get("/api/session").raise_for_status().json()["token"]
        client.headers.update({"Origin": origin, "X-Netguard-Session": token})
        campaign(client, args.output)

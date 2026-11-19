"""Stage A: authorized isolated link fault, no operator/chat request."""

import argparse
import json
import time
from pathlib import Path

import httpx


def verify(origin, output):
    with httpx.Client(base_url=origin, timeout=60, trust_env=False) as client:
        deadline = time.monotonic() + 100
        while True:
            try:
                token = client.get("/api/session").raise_for_status().json()["token"]
                break
            except httpx.HTTPError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        client.headers.update({"Origin": origin, "X-Netguard-Session": token})

        def post(path, body=None):
            return client.post(path, json=body or {}).raise_for_status()

        state = client.get("/api/incidents").raise_for_status().json()
        if state["stopped"] or any(r["phase"] != "resolved" for r in state["incidents"][:1]):
            raise RuntimeError("Start from an idle healthy lab")
        prior_auto = state["automatic"]
        identity = None
        try:
            post("/api/automation/mode", {"enabled": True})
            post("/api/scenarios/link/inject?ai=true")
            identity = client.get("/api/incidents").json()["incidents"][0]["id"]
            deadline = time.monotonic() + 110
            while time.monotonic() < deadline:
                run = client.get("/api/incidents/" + identity).raise_for_status().json()
                rows = client.get("/api/monitoring").raise_for_status().json()["investigations"]
                case = next((r for r in rows if r.get("incident_id") == identity), None)
                if run["phase"] == "handoff":
                    raise RuntimeError(run["events"][-1]["reason"])
                if run["phase"] == "resolved" and case and case["status"] == "verified":
                    assert run["review"]["decision"]["component"] == "leaf-1/spine-1"
                    assert case["outcome"] == "ai_repair"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(
                        json.dumps(
                            {"incident": run, "investigation": case, "operator_prompts": 0},
                            indent=2,
                        )
                        + "\n"
                    )
                    print("Verified autonomous investigation and repair:", identity)
                    return
                time.sleep(1)
            raise TimeoutError("Proactive workflow did not reach verified closure")
        finally:
            if identity:
                run = client.get("/api/incidents/" + identity).json()
                if run["phase"] not in {"resolved", "verifying"}:
                    post("/api/scenarios/reset")
            post("/api/automation/mode", {"enabled": False})
            if prior_auto:
                post("/api/automation/mode", {"enabled": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--origin", default="http://127.0.0.1:3001")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new evidence file")
    verify(args.origin, args.output)

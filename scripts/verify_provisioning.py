"""Stage C: approve leaf, spine and host requests, verify each, then roll back."""

import argparse
import json
import time
from pathlib import Path

import httpx


def verify(output):
    origin = "http://127.0.0.1:3001"
    rows = []
    with httpx.Client(base_url=origin, trust_env=False, timeout=180) as c:
        for _ in range(100):
            try:
                token = c.get("/api/session").raise_for_status().json()["token"]
                break
            except httpx.HTTPError:
                time.sleep(1)
        c.headers.update({"Origin": origin, "X-Netguard-Session": token})
        identities = []
        try:
            for prompt, name in [
                ("Add a leaf connected to every spine.", "leaf-3"),
                ("Provision another spine with appropriate BGP neighbors.", "spine-3"),
                ("Add a host under Leaf 3.", "host-3"),
            ]:
                response = (
                    c.post("/api/operator", json={"prompt": prompt}).raise_for_status().json()
                )
                plan = response["plan"]
                identities.append(plan["id"])
                assert plan["added"] == [name], plan["added"]
                approval = c.post("/api/provision/" + plan["id"] + "/approve")
                rows.append(
                    {
                        "approval": plan["id"],
                        "status": approval.status_code,
                        "result": approval.json(),
                    }
                )
                approval.raise_for_status()
                plan = c.get("/api/provision").raise_for_status().json()
                rows.append({"prompt": prompt, "plan": plan})
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(rows, indent=2) + "\n")
                assert plan["phase"] == "verified", plan.get("failure")
                print(name, plan["phase"], flush=True)
                risk = (
                    c.post(
                        "/api/operator", json={"prompt": f"What could break if I remove {name}?"}
                    )
                    .raise_for_status()
                    .json()
                )
                assert name in risk["evidence"]["facts"]["policy_check"]["affected"]
            state = c.get("/api/state").raise_for_status().json()
            rows.append({"live_state": state})
        finally:
            for identity in reversed(identities):
                plan = c.get("/api/provision").raise_for_status().json()
                if plan["id"] == identity and plan["phase"] == "preview":
                    c.post("/api/provision/" + identity + "/cancel").raise_for_status()
                if plan["id"] == identity and plan["phase"] in {"verified", "needs_review"}:
                    c.post("/api/provision/" + identity + "/rollback").raise_for_status()
                    rows.append({"rolled_back": identity})
                    print("rollback", identity, flush=True)
            output.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new evidence file")
    verify(args.output)

"""Recruiter demonstration: block an unsafe change and compare AI workflows."""

import json
import time
from pathlib import Path
import argparse
import httpx


def verify(output):
    origin = "http://127.0.0.1:3001"
    with httpx.Client(base_url=origin, trust_env=False, timeout=180) as client:
        for _ in range(100):
            try:
                token = client.get("/api/session").raise_for_status().json()["token"]
                break
            except httpx.HTTPError:
                time.sleep(1)
        client.headers.update({"Origin": origin, "X-Netguard-Session": token})
        before = client.get("/api/topology").raise_for_status().json()
        blocked = (
            client.post("/api/operator", json={"prompt": "What could break if I remove Spine 2?"})
            .raise_for_status()
            .json()
        )
        assert blocked["evidence"]["facts"]["policy_check"]["observed"] == "block"
        reply = (
            client.post(
                "/api/operator",
                json={"prompt": "Evaluate the AI operator against our incident test suite."},
            )
            .raise_for_status()
            .json()
        )
        report = reply["evidence"]["facts"]
        assert report["summary"] and report["record_id"]
        impact = client.get("/api/impact").raise_for_status().json()
        assert any(e["id"] == report["record_id"] for e in impact["evaluations"])
        assert client.get("/api/topology").raise_for_status().json() == before
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "blocked_change": blocked,
                    "evaluation": report,
                    "impact": impact,
                    "topology_unchanged": True,
                },
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(report["summary"], indent=2), flush=True)
        print(
            "Non-diagnosis checks:",
            sum(r["passed"] for r in report["results"] if "passed" in r),
            "/",
            sum("passed" in r for r in report["results"]),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new evidence file")
    verify(args.output)

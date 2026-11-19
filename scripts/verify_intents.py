"""Stage B: real model classification and risk analysis on an approved new leaf."""

import argparse
import json
import time
from pathlib import Path

import httpx


def verify(output):
    origin = "http://127.0.0.1:3001"
    rows = []
    with httpx.Client(base_url=origin, trust_env=False, timeout=120) as c:
        for attempt in range(100):
            try:
                token = c.get("/api/session").raise_for_status().json()["token"]
                break
            except httpx.HTTPError:
                time.sleep(1)
        c.headers.update({"Origin": origin, "X-Netguard-Session": token})

        def ask(prompt):
            r = c.post("/api/operator", json={"prompt": prompt})
            rows.append({"prompt": prompt, "status": r.status_code, "answer": r.json()})
            print(prompt, r.status_code, flush=True)
            return r

        for name in ["Spine 1", "Spine 2"]:
            r = ask(f"What could break if I remove {name}?").raise_for_status().json()
            assert r["evidence"]["facts"]["policy_check"]["observed"] == "block"
        assert ask("What happens if I remove Spine 99?").status_code == 409
        plan = ask("Add one leaf connected to every spine.").raise_for_status().json()["plan"]
        try:
            c.post("/api/provision/" + plan["id"] + "/approve").raise_for_status()
            current = c.get("/api/provision").raise_for_status().json()
            assert current["phase"] == "verified", current
            rows.append({"deployment": current["phase"], "plan": plan["id"]})
            r = ask("What could break if I remove Leaf 3?").raise_for_status().json()
            assert "leaf-3" in r["evidence"]["facts"]["policy_check"]["affected"]
            assert r["evidence"]["facts"]["policy_check"]["observed"] == "block"
            ask("Investigate the most unstable BGP peer.").raise_for_status()
        finally:
            current = c.get("/api/provision").raise_for_status().json()
            if current["phase"] in {"verified", "needs_review"}:
                c.post("/api/provision/" + plan["id"] + "/rollback").raise_for_status()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new evidence file")
    verify(args.output)

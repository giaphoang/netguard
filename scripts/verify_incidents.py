"""Run the P1 acceptance exercise through the same loopback HTTP API as the UI."""

from contextlib import ExitStack

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--port", type=int, default=3001)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    origin = f"http://127.0.0.1:{args.port}"
    with (
        httpx.Client(base_url=origin, timeout=30, trust_env=False) as client,
        ExitStack() as cleanup,
    ):

        def get(path):
            return client.get(path).raise_for_status().json()

        def post(path, **body):
            return client.post(path, json=body).raise_for_status().json()

        def wait_for(identity, phases):
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                run = get("/api/incidents")["incidents"][0]
                assert run["id"] == identity
                if run["phase"] in phases:
                    return run
                if run["phase"] == "handoff":
                    raise RuntimeError(run["events"][-1])
                time.sleep(0.15)
            raise TimeoutError(phases)

        token = get("/api/session")["token"]
        assert client.post("/api/scenarios/link/inject").status_code == 403
        client.headers.update({"Origin": "http://example.invalid", "X-NetGuard-Session": token})
        assert client.post("/api/scenarios/link/inject").status_code == 403
        client.headers.update({"Origin": origin})
        assert client.post("/api/scenarios/other/inject").status_code == 422
        state = get("/api/incidents")
        assert not state["incidents"] or state["incidents"][0]["phase"] == "resolved"
        assert not state["automatic"] and not state["stopped"]
        cleanup.callback(post, "/api/automation/mode", enabled=False)
        post("/api/automation/stop", enabled=True)
        assert client.post("/api/scenarios/link/inject").status_code == 409
        post("/api/automation/stop", enabled=False)

        rows = []
        recording = {}
        for scenario, auto in (
            [("bgp", False), ("endpoint", False)] * 2 + [("link", False)] * 6 + [("link", True)] * 5
        ):
            post("/api/automation/mode", enabled=auto)
            post(f"/api/scenarios/{scenario}/inject")
            identity = get("/api/incidents")["incidents"][0]["id"]
            try:
                assert client.post(f"/api/scenarios/{scenario}/inject").status_code == 409
                if scenario != "link":
                    wait_for(identity, {"handoff"})
                    assert client.post(f"/api/incidents/{identity}/approve").status_code == 409
                    post("/api/scenarios/reset")
                elif not auto:
                    wait_for(identity, {"proposed"})
                    if "rejected" not in recording:
                        post(f"/api/incidents/{identity}/reject")
                        assert get("/api/incidents")["incidents"][0]["phase"] == "handoff"
                        post("/api/scenarios/reset")
                        recording["rejected"] = identity
                    else:
                        post(f"/api/incidents/{identity}/approve")
                wait_for(identity, {"resolved"})
            except BaseException:
                active = get("/api/incidents")["incidents"][0]
                if active["id"] == identity and active["phase"] not in {"resolved", "verifying"}:
                    post("/api/scenarios/reset")
                raise
            detail = get(f"/api/incidents/{identity}")
            assert detail["attempts"] == 1 and detail["phase"] == "resolved"
            rows.append(
                {
                    "scenario": scenario,
                    "automatic": auto,
                    "id": identity,
                    "events": detail["events"],
                    "metrics": detail["metrics"],
                }
            )
            (args.output / "runs.json").write_text(json.dumps(rows, indent=2))
            if auto and "auto" not in recording:
                detail.pop("target")
                (args.output / "recorded-link.json").write_text(json.dumps(detail, indent=2))
                recording["auto"] = identity
            print(
                identity, scenario, "automatic" if auto else "manual", detail["metrics"], flush=True
            )
        post("/api/automation/mode", enabled=False)
        for auto in (False, True):
            times = [
                r["metrics"]["closure_s"]
                for r in rows
                if r["scenario"] == "link"
                and r["automatic"] == auto
                and r["id"] != recording["rejected"]
            ]
            assert len(times) == 5
            print(
                "automatic",
                auto,
                "closure median / range",
                statistics.median(times),
                min(times),
                max(times),
            )


if __name__ == "__main__":
    main()

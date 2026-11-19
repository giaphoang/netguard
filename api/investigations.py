"""Read-only investigations. Evidence and model decisions share a durable case record."""

import json
import re
import sqlite3
import time
import uuid


def cases(db, row=None):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS investigations(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        if row is not None:
            conn.execute(
                "INSERT INTO investigations VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (row["id"], json.dumps(row)),
            )
        return [
            json.loads(r[0])
            for r in conn.execute("SELECT data FROM investigations ORDER BY rowid DESC LIMIT 30")
        ]


def record(db, kind, text, facts):
    row = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "text": text,
        "observed": time.time(),
        "facts": facts,
    }
    cases(db, row)
    return row


def response(row):
    return {"text": row["text"], "evidence": {"observed": row["observed"], "facts": row["facts"]}}


def risk(db, sample, spine):
    facts = {}
    for r in sample["routers"]:
        if not r["name"].startswith("leaf-"):
            continue
        leaf = int(r["name"].split("-")[1])
        remaining = (r.get("peers") or {}).get(f"10.211.{3-spine}{leaf}.1")
        facts[r["name"]] = {
            "surviving_peer": remaining,
            "remaining_spines": int(remaining == "Established"),
            "routes_before": r["routes"],
        }
    blocked = not facts or any(v["remaining_spines"] < 2 for v in facts.values())
    facts["policy_check"] = {
        "required_paths": 2,
        "expected": "block",
        "observed": "block" if blocked else "pass",
        "scenario": f"remove spine-{spine}",
    }
    return record(
        db,
        "change risk",
        f"Blocked removal of Spine {spine}. Each leaf requires two independent spine paths. Even if the other peer stays established, redundancy falls below policy. Inspect the surviving peers below.",
        facts,
    )


def bgp_config(config):
    # Scope the comparison to routing statements supported by the lab templates.
    return {
        line.strip()
        for line in config.splitlines()
        if re.match(
            r"^(router bgp |bgp router-id |neighbor \S+ (?:remote-as |shutdown)|network \d|maximum-paths |timers bgp )",
            line.strip(),
        )
    }


def drift(db, sample, automatic=False):
    from .fabric import command
    import subprocess

    facts = {}
    for router in sample["routers"]:
        name = router["name"]
        try:
            expected = bgp_config(command(name, "cat", "/etc/frr/frr.conf"))
            actual = bgp_config(command(name, "vtysh", "-c", "show running-config"))
            if not expected or not actual:
                raise ValueError("Missing BGP configuration")
            facts[name] = {
                "missing": sorted(expected - actual),
                "unexpected": sorted(actual - expected),
            }
        except (OSError, subprocess.SubprocessError, ValueError):
            facts[name] = {"status": "unavailable"}
    changes = sum(bool(v.get("missing") or v.get("unexpected")) for v in facts.values())
    unknown = sum("status" in v for v in facts.values())
    if automatic:
        previous = next((r for r in cases(db) if r["kind"] == "configuration drift"), None)
        if (not previous and not changes and not unknown) or (
            previous and previous["facts"] == facts
        ):
            return previous
    return record(
        db,
        "configuration drift",
        f"{changes} routers differ from their mounted BGP configuration; {unknown} unavailable. Review missing statements for restoration and unexpected statements for removal before approving any correction.",
        facts,
    )


def anomaly(history):
    if len(history) < 2:
        return None
    for key, value in history[-1]["facts"].items():
        if "/" not in key:
            continue
        states = [s["facts"].get(key, {}).get("leaf_peer") for s in history]
        transitions = sum(a != b for a, b in zip(states, states[1:]) if a and b)
        if transitions >= 3:
            return f"Repeated BGP transitions on {key}"

    def degraded(entry):
        return any(
            v.get("leaf_peer") != "Established"
            if "/" in k
            else any(p != "success" for p in v.get("probes", {}).values())
            for k, v in entry["facts"].items()
        )

    if all(degraded(entry) for entry in history[-2:]):
        return "Repeated degraded routing or probe observations"
    return None

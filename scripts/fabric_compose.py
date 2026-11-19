"""Start and stop the Compose topology most recently approved by the operator."""

import argparse
import os
from pathlib import Path

from api import architecture, provision, expansion


def commands(db, operation):
    if db.exists() and architecture.reservation(db):
        if operation == "up":
            return []
        architecture.require_idle(db)
    plan = expansion.current(db) if db.exists() else None
    start = ["up", "-d"] + (["--force-recreate"] if operation == "reset" else [])
    if not plan or plan["phase"] not in provision.ACTIVE:
        return [(None, ["down"] if operation == "down" else start)]
    if plan["phase"] != "verified":
        if operation == "up":
            return []  # Start the dashboard so the operator can inspect and roll back.
        raise ValueError(
            "Complete or roll back the interrupted topology operation in the dashboard"
        )
    folder, files, digest = provision.material(db, plan)
    if digest != plan["digest"] or any(
        (folder / name).read_text() != value for name, value in files.items()
    ):
        raise ValueError("Approved topology no longer matches the stored configuration")
    overlay = folder / "compose.json"
    if operation == "down":
        return [(overlay, ["down"])]
    if plan.get("version") == 3:
        return [(overlay, [*start, "--no-deps", *expansion.names(plan["topology"])])]
    return [
        (None, [*start, "--no-deps", "leaf-1", "leaf-2", "host-1", "host-2"]),
        (
            overlay,
            [*start, "--no-deps", "spine-1", "spine-2", *[f"leaf-{n}" for n in plan["leaves"]]],
        ),
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["up", "down", "reset"])
    args = parser.parse_args()
    journal = Path(os.getenv("NETGUARD_TIMELINE_DB", provision.ROOT / ".runtime/timeline.sqlite"))
    for overlay, cmd in commands(journal, args.operation):
        provision.compose(overlay, *cmd)

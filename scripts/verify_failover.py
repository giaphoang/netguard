"""Exercise one isolated link and restore it even if a check fails."""
import json
import re
import time
from api.fabric import FIXTURE, collect, command, trace
from scripts.smoke import main as healthy


def verify() -> None:
    healthy()
    lines = command("leaf-1", "ip", "-br", "addr").splitlines()
    iface, = [line.split()[0].split("@")[0] for line in lines if "10.211.11.2/28" in line]
    assert re.fullmatch(r"eth\d+", iface)
    try:
        command("leaf-1", "ip", "link", "set", iface, "down")
        for _ in range(30):
            snapshot = collect()
            leaf = next(r for r in snapshot.routers if r.name == "leaf-1")
            if (leaf.peers and leaf.peers.get("10.211.11.1") != "Established"
                    and "via 10.211.21.1" in leaf.routes and "via 10.211.11.1" not in leaf.routes):
                probes = [trace(i) for i in (1, 2)]
                if all(set(p["probes"].values()) == {"success"} for p in probes):
                    print("Verified alternate path", leaf.peers, probes)
                    (FIXTURE.parent / "failover.json").write_text(json.dumps({
                        "snapshot": snapshot.model_dump(), "probes": probes}, indent=2))
                    break
            time.sleep(1)
        else:
            raise RuntimeError("Alternate path verification timed out")
    finally:
        command("leaf-1", "ip", "link", "set", iface, "up")
    for _ in range(60):
        try:
            healthy()
            return
        except AssertionError:
            time.sleep(1)
    raise RuntimeError("Healthy baseline did not return")


if __name__ == "__main__":
    verify()

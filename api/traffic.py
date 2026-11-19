"""Read-only leaf interface counters indexed by the fabric's graph edges."""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor


def counters(leaf):
    from .fabric import command

    addresses = {f"10.211.{spine}{leaf}.2": f"{spine}-{leaf}" for spine in range(1, 4)}
    if leaf <= 4:
        addresses[f"10.212.{leaf}.1"] = f"access{leaf}"
    result = {}
    try:
        rows = json.loads(command(f"leaf-{leaf}", "ip", "-j", "-s", "addr", "show"))
        now = time.monotonic()
        for row in rows:
            for addr in row["addr_info"]:
                edge = addresses.get(addr.get("local"))
                stats = row.get("stats64")
                if edge and stats:
                    result[edge] = (
                        row["ifindex"],
                        row["address"],
                        now,
                        stats["rx"]["packets"],
                        stats["tx"]["packets"],
                        stats["rx"]["bytes"],
                        stats["tx"]["bytes"],
                    )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return {}
    return result


def collect(leaves):
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {k: v for result in pool.map(counters, leaves) for k, v in result.items()}


def rates(previous, current):
    links = {}
    for edge, reading in current.items():
        old = previous.get(edge)
        if not old or old[:2] != reading[:2]:
            continue
        seconds = reading[2] - old[2]
        deltas = [new - prior for new, prior in zip(reading[3:], old[3:])]
        if not 0 < seconds <= 20 or min(deltas) < 0:
            continue
        rx, tx, rx_bytes, tx_bytes = deltas
        links[edge] = {
            "rx": rx,
            "tx": tx,
            "bytes_per_second": round((rx_bytes + tx_bytes) / seconds, 1),
            "seconds": round(seconds, 2),
        }
    return links

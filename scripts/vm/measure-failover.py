"""Record sampled forwarding through the existing single-link failover exercise."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

if socket.gethostname().split(".")[0] != "netguard-vm" or os.uname().machine != "aarch64":
    raise SystemExit("Run only on the dedicated NetGuard Ubuntu ARM64 VM")
os.chdir("/mnt/netguard")
output = Path(sys.argv[1])
output.mkdir(mode=0o700, parents=True, exist_ok=False)
stop = threading.Event()


def observe(direction: int) -> None:
    destination = f"10.212.{3 - direction}.2"
    with (output / f"probe-{direction}.jsonl").open("w") as log:
        while not stop.is_set():
            start = datetime.now(timezone.utc).isoformat()
            before = time.monotonic()
            p = subprocess.run(
                [
                    "docker",
                    "--context",
                    "default",
                    "compose",
                    "-f",
                    "fabric.yml",
                    "exec",
                    "-T",
                    f"host-{direction}",
                    "ping",
                    "-c",
                    "1",
                    "-W",
                    "1",
                    destination,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            log.write(
                json.dumps(
                    {
                        "sent": start,
                        "exit": p.returncode,
                        "elapsed_s": round(time.monotonic() - before, 4),
                        "summary": (p.stdout or p.stderr).strip().splitlines()[-2:],
                    }
                )
                + "\n"
            )
            log.flush()
            stop.wait(0.25)


with ThreadPoolExecutor(max_workers=2) as pool:
    futures = [pool.submit(observe, direction) for direction in (1, 2)]
    try:
        with (output / "verification.log").open("w") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "scripts.verify_failover",
                    "--output",
                    str(output / "alternate.json"),
                ],
                env={**os.environ, "NETGUARD_CONTEXT": "default"},
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    finally:
        stop.set()
    for future in futures:
        future.result()

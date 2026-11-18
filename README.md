# NetGuard

A local network reliability lab extending Erica Nordlöf’s MIT-licensed multi-site network platform. The separate six-node fabric runs real FRRouting eBGP and Linux forwarding. See [provenance](docs/UPSTREAM.md), [original README](docs/UPSTREAM_README.md) and [environment and topology decisions](docs/DECISIONS.md).

## Run on localhost

Install Docker Engine with Compose v2 (or Docker Desktop with Linux containers), uv, Node.js and npm. From this checkout run:

    make setup
    make demo-up

Open http://localhost:3000. Click a leaf to inspect its neighbor table and kernel routes. Select a host pair and click **Trace traffic**. Blue interconnects represent possible routes. ICMP and HTTP report the results of probes sent from the selected host. A green session by itself says nothing about end-to-end reachability.

Ctrl-C stops the foreground web service. Use `make demo-down` to remove the lab containers and networks. `make demo-reset` recreates the six nodes. To review the stored healthy capture without Docker, run `make demo-replay` after setup. It shares the same port as the live service.

## Verification

    make demo-test
    make smoke
    NETGUARD_CONTEXT=$(scripts/local-context) .venv/bin/python -m scripts.verify_failover

The smoke check requires two established peers on each router, two kernel next hops per leaf, and successful ICMP and HTTP in both directions. The failover check temporarily disables the leaf-1 interface facing spine-1 and restores it in a finally block. Launch it only on the local demo fabric. Captures are in scenarios/. Recovery benchmarks and AI evaluation are pending.

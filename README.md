# NetGuard

A local network reliability lab extending Erica Nordlöf’s MIT-licensed multi-site network platform. The separate six-node fabric runs real FRRouting eBGP and Linux forwarding. See [provenance](docs/UPSTREAM.md), [original README](docs/UPSTREAM_README.md) and [environment and topology decisions](docs/DECISIONS.md).

## Incident walkthrough

On the Ubuntu guest, open the tunneled dashboard at http://127.0.0.1:3001. Trace traffic, inject the leaf–spine link failure, watch BGP fail over, and approve restoration of redundancy. The BGP-session and endpoint presets exercise manual handoff. Auto-repair is off by default. [P1 controls, measurements and replay](docs/P1_INCIDENTS.md)

## Run on localhost

Install Docker Engine with Compose v2 (or Docker Desktop with Linux containers), uv, Node.js and npm. From this checkout run:

    make setup
    make demo-up

Open http://localhost:3000. Click a leaf to inspect its neighbor table and kernel routes. Select a host pair and click **Trace traffic**. Blue interconnects represent possible routes. ICMP and HTTP report the results of probes sent from the selected host. A green session by itself says nothing about end-to-end reachability.

Ctrl-C stops the foreground web service. Use `make demo-down` to remove the lab containers and networks. `make demo-reset` recreates the six nodes. To review the recorded incident walkthrough without Docker, run `make demo-replay` after setup. It shares the same port as the live service.

## Verification

    make demo-test
    make smoke
    NETGUARD_CONTEXT=$(scripts/local-context) .venv/bin/python -m scripts.verify_failover

The smoke check requires two established peers on each router, two kernel next hops per leaf, and successful ICMP and HTTP in both directions. The failover check temporarily disables the leaf-1 interface facing spine-1 and restores it in a finally block. Launch it only on the local demo fabric. Captures are in scenarios/. P1 measures incident workflow timing. Local AI evaluation is a later milestone.

## Demonstration evidence

The November 18 live run established all four eBGP adjacencies. Each leaf installed two equal-weight kernel next hops to the opposite host subnet. ICMP and HTTP succeeded from both hosts before the interruption, after convergence on the surviving spine, and after restoration. A single successful probe is a reachability observation. These samples do not estimate a long-term success rate.

[Dashboard screenshot](docs/proof/healthy.png) · [90-second screen recording](docs/proof/walkthrough.mp4) · [Healthy router evidence](scenarios/healthy.json) · [Degraded fabric and probe evidence](scenarios/failover.json)

The dashboard’s Replay demo link opens a recorded P1 link incident. Select a stage and trace either direction to inspect its saved results. The linked P0 video documents the original read-only dashboard.

## Native Ubuntu ARM64 demonstration

The existing six-segment fabric also runs on a UTM Ubuntu Server guest with the macOS checkout mounted through VirtioFS. The verified native run includes bidirectional HTTP/ICMP, per-router packet capture, clean recreation, guest reboot and measured single-link failover. [Setup and tunnel](docs/UTM_ARM64_SETUP.md) · [Results and raw evidence](docs/P0_NETWORK_VALIDATION.md)

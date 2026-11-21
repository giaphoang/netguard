# NetGuard

**[Open demo](https://netguard-recruiter-demo.vercel.app/)** · [Recorded Ubuntu run](docs/proof/p3/final-recovery.json)

Play a recorded Ubuntu link failure, follow the BGP failover, and inspect the local Qwen diagnosis. Maintainers can publish the replay from [Deploy recruiter demo](https://github.com/giaphoang/netguard/actions/workflows/deploy-recruiter-demo.yml).

A local network reliability lab extending Erica Nordlöf’s MIT-licensed multi-site network platform. The separate six-node fabric runs real FRRouting eBGP and Linux forwarding. See [provenance](docs/UPSTREAM.md), [original README](docs/UPSTREAM_README.md) and [environment and topology decisions](docs/DECISIONS.md).

**Overview** opens the Command Center. Select a link and ask **NetGuard Agent** “Why is this connection failing?” to inspect its routing and probe evidence. The sidebar opens the dedicated AI Agent, Incidents, Provisioning, and AI Impact workspaces. Chat history and drafts follow you between workspaces. [NetGuard Agent instructions and example prompts](docs/OPERATOR.md)

Architecture experiments run inside **AI Agent**. Ask about throughput, GRE, or diagnosis quality to see the design, tool activity, approval controls, and measured results in the same reply. [Architecture experiments and evidence](docs/ARCHITECTURE_LAB.md)

P3 adds proactive investigations, topology-aware structured intent, approved spine/leaf/host provisioning with verification and rollback, and reproducible AI evaluation. The existing P0–P2 fabric, controls and benchmark history remain intact. [P3 stages and evidence](docs/P3_OPERATIONS.md) · [Earlier P2 diagnosis](docs/P2_AI.md)

Three recruiter demonstrations: inject a fault and watch an unprompted investigation; ask to add a router or host and approve its preview; ask what could break if Spine 2 were removed, then inspect the policy block and versioned evaluation in **AI Impact**. The current guarded workflow identified 5/8 labeled diagnoses versus 4/8 for the frozen P2 reference. These are small lab regression results; failures remain visible and no time saved is reported without a matched manual baseline.

## Incident walkthrough

On the Ubuntu guest, open the tunneled dashboard at http://127.0.0.1:3001. Trace traffic, inject the leaf–spine link failure, watch BGP fail over, and approve restoration of redundancy. The BGP-session and endpoint presets exercise manual handoff. Auto-repair is off by default. [P1 controls, measurements and replay](docs/P1_INCIDENTS.md)

## Run on localhost

Install Docker Engine with Compose v2 (or Docker Desktop with Linux containers), uv, Node.js and npm. From this checkout run:

    make setup
    make demo-up

Open http://localhost:3000. Click a leaf to inspect its neighbor table and kernel routes. Select a host pair and click **Run probe**. Green interconnects show established BGP adjacencies, and the blue marker follows a verified ICMP path. The probe readout reports ICMP and HTTP results from the selected host.

Ctrl-C stops the foreground web service. Use `make demo-down` to remove the lab containers and networks. `make demo-reset` recreates the currently approved topology. To review the recorded incident walkthrough without Docker, run `make demo-replay` after setup. It shares the same port as the live service.

## Verification

    make demo-test
    make smoke
    NETGUARD_CONTEXT=$(scripts/local-context) .venv/bin/python -m scripts.verify_failover

The smoke check requires two established peers on each router, two kernel next hops per leaf, and successful ICMP and HTTP in both directions. The failover check temporarily disables the leaf-1 interface facing spine-1 and restores it in a finally block. Launch it only on the local demo fabric. Captures are in scenarios/. P1 measures incident workflow timing. P2 compares local AI decisions against the P1 evidence checks.

## Demonstration evidence

The November 18 live run established all four eBGP adjacencies. Each leaf installed two equal-weight kernel next hops to the opposite host subnet. ICMP and HTTP succeeded from both hosts before the interruption, after convergence on the surviving spine, and after restoration. A single successful probe is a reachability observation. These samples do not estimate a long-term success rate.

[Dashboard screenshot](docs/proof/healthy.png) · [90-second screen recording](docs/proof/walkthrough.mp4) · [Healthy router evidence](scenarios/healthy.json) · [Degraded fabric and probe evidence](scenarios/failover.json)

The dashboard’s Replay demo link opens a recorded P1 link incident. Select a stage and trace either direction to inspect its saved results. The linked P0 video documents the original read-only dashboard.

## Native Ubuntu ARM64 demonstration

The existing six-segment fabric also runs on a UTM Ubuntu Server guest with the macOS checkout mounted through VirtioFS. The verified native run includes bidirectional HTTP/ICMP, per-router packet capture, clean recreation, guest reboot and measured single-link failover. [Setup and tunnel](docs/UTM_ARM64_SETUP.md) · [Results and raw evidence](docs/P0_NETWORK_VALIDATION.md)

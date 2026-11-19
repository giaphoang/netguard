# P1 incident demonstration

Open the native Ubuntu dashboard through the macOS SSH tunnel at http://127.0.0.1:3001. In a standalone local deployment, `make demo-up` serves port 3000. Auto-repair starts **OFF**.

1. Trace either host pair. The healthy gate requires all eight expected peer observations, two kernel next hops per direction, and successful bidirectional ICMP and HTTP.
2. Select **Inject: Leaf–spine link down**. Open **Incident timeline and evidence**. The controller records the baseline before changing the leaf-1 interface with address 10.211.11.2/28.
3. Watch the interface, peer observations, kernel next hops and host probes. The detector uses interface state, the specific failed peer, unaffected peers and the surviving next hop. It proposes repair once both directions select spine-2 and pass two consecutive probe rounds.
4. **Approve repair** restores the deliberately disabled link. BGP has already handled the traffic failover. The verifier waits for both ECMP paths, all sessions and two consecutive healthy ICMP/HTTP rounds before closing the incident.
5. Repeat with auto-repair enabled before injection. To practice handoff, reject the proposal or use a BGP or endpoint preset. **Reset scenario** reverses the specific preset. **Emergency stop** disables automation, blocks injection and preserves access to manual reset. Release it explicitly. The stop survives process restart.

| Preset | Fixed lab operation | Recovery |
|---|---|---|
| Leaf–spine link down | Set the identified leaf-1 transit interface down | Approved or explicitly enabled automatic link restoration |
| BGP neighbor down | Shut down leaf-1’s 10.211.11.1 neighbor in running FRR configuration | Needs human; reset removes only that neighbor shutdown |
| App endpoint down | Pause the host-2 container, freezing its HTTP server and other userspace processes | Needs human; reset resumes the same container |

An endpoint pause can leave kernel ICMP responsive. A probe originating on the paused host can be unavailable. The detector uses the HTTP observation from host-1 together with established routing sessions. [Docker pause semantics](https://docs.docker.com/reference/cli/docker/container/pause/) · [FRR neighbor shutdown](https://docs.frrouting.org/en/stable-10.1/bgp.html#configuring-peers)

## Control boundary and recovery

The single FastAPI worker holds an exclusive journal lock. Browsers acquire a process-local session token from a loopback-only endpoint and send it in a custom header with a matching Origin on mutations. Use the current tab after a service restart by refreshing it to obtain the new token. The Docker client uses a local Unix-socket context. Before actuation the controller checks the immutable container ID, Compose project, service, exact fabric file, expected network memberships, internal parentless macvlan networks, original interface address and the runbook precondition. Only the active incident can receive approval. A failed repair goes to human review without automatic retry.

Incident IDs, baseline observations, sampled router/probe evidence, decisions and verification outcomes are persisted in the SQLite timeline database. A process restart sends an unfinished incident to human review with automation disabled. For an active incident use the dashboard reset first. If the fabric was externally recreated, manual reset can close the journal after verifying the replacement is healthy. The replacement branch issues no Docker mutation. Keep the dashboard bound to loopback. On Ubuntu the native setup uses /var/lib/netguard/timeline.sqlite. No model, credentials for cloud services or external telemetry are used.

## Measurements

T0 is command confirmation. The detector records the timestamp of the first completed evidence round supporting its diagnosis. Verified failover requires both source leaves to select the surviving spine and two consecutive successful bidirectional ICMP/HTTP rounds. Traffic recovery uses the second successful round following an explicitly failed probe. Unavailable observations alone do not establish loss. A missing recovery value means the run did not observe and verify that sequence. Closure includes restoring redundancy and two healthy post-repair rounds. All displayed durations use guest wall-clock seconds relative to T0.

The incident monitor waits one second between completed rounds. Command timeouts and probe execution time add to that interval. These are sampled workflow timings. The independent topology collector refreshes after a three-second wait between rounds. The raw incident endpoint includes timestamps for each direction. Detailed endpoint and peer evidence remains available after reload.

    make demo-test
    .venv/bin/python scripts/verify_incidents.py .runtime/p1-evaluation --port 3001

Choose a fresh output directory. The harness refuses to start over an active incident. It runs each BGP/endpoint preset twice, exercises rejection/reset, then runs five approved and five automatic link repairs. The baseline is a scripted operator clicking the API equivalent of Approve as soon as the proposal is visible (150 ms poll). It introduces no artificial operator delay. The dashboard comparison uses completed single-attempt approved/automatic link repairs across the local journal and excludes explicit resets.

Read measurements at /metrics. It exports netguard_probe_success, netguard_bgp_established_sessions, netguard_fault_detection_seconds, netguard_recovery_seconds, netguard_incidents_total and netguard_actions_total. Unavailable probe values are NaN. The newest 12 incidents appear in the selector. Individual journals are accessible by ID at /api/incidents/{id}.

## Offline walkthrough

`make demo-replay` uses the checked-in P1 incident with its original samples and timestamps. Select a stage or play the sequence. Playback keeps a minimum display time for closely spaced events. Read the recorded timestamps for measurements. The replay server needs neither Docker nor Ollama and rejects mutations. [Recorded data](../scenarios/p1-link.json) · [Approval screenshot](proof/p1/proposal.png) · [Replay screenshot](proof/p1/replay.png)

## Native Ubuntu acceptance results

Both 15-run campaigns on November 18, 2026 completed on the existing six-node ARM64 fabric. Every run reached resolved. Each campaign covered two BGP interruptions, two paused endpoints, one rejected link repair followed by reset, five scripted approvals and five automatic repairs. Missing-token, cross-origin, unknown-preset, overlapping-injection and emergency-stop checks rejected their respective requests.

| Campaign | Scripted approval, median closure (range) | Automatic, median closure (range) |
|---|---|---|
| Initial | 15.45 s (14.87–15.58 s) | 15.58 s (14.10–15.75 s) |
| Confirmation | 15.26 s (14.95–16.22 s) | 14.55 s (14.40–16.76 s) |

Five samples per cell. Ranges overlap and the faster median changes between campaigns. These results support repeatability of both workflows. They do not establish an automation speed advantage. [Initial events and clocks](proof/p1/runs.json) · [Confirmation events and clocks](proof/p1/confirmation-runs.json)

Executed checks included:

    make demo-test
    .venv/bin/ruff check api scripts/verify_incidents.py tests/test_incidents.py
    .venv/bin/python scripts/verify_incidents.py .runtime/p1-measurements --port 3001
    .venv/bin/python scripts/verify_incidents.py .runtime/p1-final --port 3001

On the Ubuntu guest:

    cd /mnt/netguard
    PYTHONDONTWRITEBYTECODE=1 /opt/netguard-venv/bin/python -m pytest -q -p no:cacheprovider
    sudo env NETGUARD_CONTEXT=default /opt/netguard-venv/bin/python -m scripts.smoke

The final suite passed 19 tests on macOS and Ubuntu, with one existing Starlette deprecation warning. The TypeScript production build, Compose validation and Python lint passed. Chrome exercised injection, reload while awaiting approval, evidence inspection, manual repair, verified closure, emergency stop and replay. A standalone replay process with PATH=/nonexistent served a recorded trace and rejected an injection. The final native smoke check observed four established adjacencies, two candidates from each leaf and successful ICMP and HTTP in both directions.

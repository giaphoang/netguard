# AI Network Engineering Harness

## P1.1 · Topology-aware tools

The existing agent interprets requests with Ollama and invokes a Python registry with JSON schemas for inputs and results. Explicit inspection requests bind to the corresponding tool. The agent reports inspection results from observed tool data. Approvals use the same local-session controls and revision check.

- `inspect_topology` resolves names, aliases, links, and groups against managed inventory. It attaches interface addresses, state, observed peers, and fresh link rates where available. Ranking the busiest leaf requires readings for every connected leaf interface and a unique result.
- `inspect_routing` reads established and non-established BGP peers, kernel routes, and requested FRRouting protocol state. `inspect_configuration` compares supported BGP statements with the mounted configuration.
- `inspect_connectivity` runs bounded bidirectional checks. ICMP uses addresses from the managed IPv4 inventory. TCP and HTTP use the lab's host service on port 8080.
- `plan_change`, `validate_change`, `execute_approved_change`, `verify_change`, and `rollback_change` reuse the provisioning controller. Model-selected tools are restricted to inspection. The chat approval route supplies the reviewed revision.

**Verification.** The Python suite passed 96 tests. Live Ollama and Docker checks found four observed transit links, two established peers on Spine 1, successful ICMP/TCP/HTTP in both Host A–Host B directions, and zero changed BGP statements on Leaf 1. The selected Leaf 1–Spine 2 link returned both interface addresses. Live Ubuntu telemetry confirmed that the same registry inspected the pre-existing Spine 3, Leaf 3, and Host 3. OSPF was unavailable on the current BGP-only fabric. Tests covered a custom spine name, selection binding, incomplete traffic measurements, and rejection of unapproved or stale tool calls.

## P1.2 · Proactive investigations

The observer checks both ends of a BGP adjacency, counts recent state transitions, and distinguishes measured packet loss from unavailable telemetry. `NETGUARD_MONITOR_RULES` sets the observation window, consecutive samples, loss percentage, latency, drift scan interval, and inference cooldown. Configuration drift requires consecutive scans. Open cases deduplicate by finding and component. A separate finding can open its own investigation after the cooldown. The journal records the observations, versioned diagnosis, and recovery checks. Controlled incidents reuse the controller's diagnosis.

**Verification.** All 99 Python tests passed, including remote-side BGP transitions, a 20% loss sample, missing telemetry, a second investigation during an open alert, and restart deduplication. A live ARM64 Desktop lab link failure triggered one Ollama diagnosis with zero operator prompts. The observer recorded the exact failed link, escalated without actuation, and verified recovery over two clean observations after the test restored the link. Loss and drift thresholds were exercised with deterministic test observations. [Live verification](proof/harness/p12-monitoring.json)

## P1.3 · Architecture previews and provisioning

The agent previews added links, expected BGP adjacencies and configuration changes. It checks the live baseline and projected container limits against the local Linux guest's memory and CPU budget. Host-growth previews compare attachment options. Requests above the approved lab limits return the measured baseline and the projected requirement for review.

Approval rechecks guest resources, ownership, and the rendered revision. Deployment and rollback verify both ends of each BGP adjacency, kernel ECMP for every remote access subnet, ICMP and HTTP in every host direction, leaf loopbacks, and the mounted BGP statements. During an approved rollout or rollback, the controller initializes a missing intended loopback in the owned container before verifying it. The preview reports expected adjacency counts and path counts as estimates.

**Verification.** The Python suite passed 102 tests. An ARM64 Desktop lab run approved a spine, leaf, and host through the chat approval route. The final expansion verified 18 established peer ends, six kernel forwarding checks, six successful ICMP/HTTP host directions, and six matching BGP configurations. A stale revision returned HTTP 409, a request for 10 additional hosts was blocked at the four-host limit, and all three rollbacks restored the four-router baseline. [Live verification](proof/harness/p13-provisioning.json)

## P1.4 · Policy-gated recovery

Automatic repair requires a preauthorized local link preset, the original owned target, current fault evidence, a working alternate path, and an unused attempt. Each repair records an operation ID, recovery procedure, verification deadline and outcome. The persistent emergency stop can engage during precondition checks. The actuator checks it again immediately before a network write. Manual restoration of the pre-fault state remains available for a stopped operation.

**Verification.** All 104 Python tests passed, including a stop requested during a concurrent repair check, recorded rejection of unapproved repair, target replacement, and repeated clean verification. The Desktop lab returned HTTP 409 for an injection while stopped. A preauthorized link repair completed with zero operator prompts in 19.32 seconds. BGP and endpoint preset repairs remain operator-controlled. [Live verification](proof/harness/p14-remediation.json)

## P2.1 · Multi-protocol engineering

The tunnel workflow supports IPv4-in-IPv4 alongside GRE. It verifies the underlay, sizes the overlay for the selected header, and binds the approved configuration to the selected leaves. Verification includes wire capture, measured MTU, matched direct and tunneled workloads, and removal of the owned interfaces.

**IPv4-in-IPv4 verification.** The disposable Ubuntu ARM64 lab captured IP protocol 4 in both directions and measured a 1,480-byte inner MTU. The median TCP goodput was 1.36× the direct run with no observed ICMP loss, and rollback verified the original fabric. This comparison measures local virtual interfaces and shared guest CPU. [Live verification](proof/harness/p21-ipip.json)

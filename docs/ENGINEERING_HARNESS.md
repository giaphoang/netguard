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

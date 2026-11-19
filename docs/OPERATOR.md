# Using NetGuard Agent

Open the Ubuntu dashboard through its SSH tunnel at `http://127.0.0.1:3001`, or use `make demo-up` for local Docker on port 3000. Qwen runs through Ollama (`qwen2.5:1.5b`); a Mac-hosted model needs the existing reverse loopback tunnel to Ubuntu. [Ubuntu setup](UTM_ARM64_SETUP.md)

## Four workspaces

- **Command Center:** live React Flow topology, packet-counter activity, route animation, proactive notifications and a compact NetGuard Agent panel. Select a link before simulating a fault or asking about it.
- **Provisioning:** enter a change request, compare the current and proposed graph, review the configuration diff and risk, then approve. Watch deployment and verification; **Roll back this change** restores the previous topology.
- **Incidents:** search investigations by device, finding or status. Open a case for its diagnosis, observations and actual tool timeline. Full timestamped evidence is collapsed. Use the controller controls for approvals, escalation, manual benchmarks and emergency stop.
- **AI Impact:** inspect measured durations, sample counts and supporting experiment records. A dash means the comparison or baseline is missing.

## Recruiter workflows

| Request or action | Result |
|---|---|
| Inject a selected link fault with Local AI diagnosis | Monitoring detects it without a chat prompt. The local model investigates; the controller validates a repair or escalates. Automatic restoration requires explicitly enabled lab auto-repair. |
| “What could break if I remove Spine 1?” | Current surviving adjacencies and the two-path policy determine the risk. Works with current nodes, including added components. Unknown targets require clarification. |
| “Add two leaves connected to every spine.” | Preview a validated, approval-required expansion. |
| “Provision another spine with appropriate BGP neighbors.” | Preview a spine connected to every current leaf. |
| “Add a host under Leaf 2.” | Allocate an unused host address and preview its attachment. |
| “Investigate the most unstable BGP peer.” | Compare recorded transitions; say when history is insufficient. |
| “Find configuration drift.” | Compare supported running BGP statements with mounted startup configuration; propose human review. |
| “Review this incident and generate a verified postmortem.” | Generate a proposed read-only runbook check linked to a real event; adoption requires review. |
| “Evaluate the AI operator against our incident test suite.” | Compare diagnostic workflows on identical labeled observations. No live actuator runs. |

Each request is independent: include the target and quantity or fault duration in follow-ups. The model interprets structured intent; backend validation resolves current devices and enforces scope, resource capacity, approvals and rollback. The bounded lab supports up to three spines, four leaves and four hosts. Provisioning can recreate containers and interrupt traffic. Nodes cannot be removed through a model command; removal risk assessments require human review, and approved additions have an explicit rollback.

The blue marker follows the observed Linux ICMP forwarding lookup between the original hosts. Its speed is illustrative, not a packet capture. Transmission counters and routing/probe status come from the lab. Missing or stale observations suppress animation. Newly added hosts receive live reachability checks; provisioning verifies bidirectional HTTP and ICMP.

Do not claim AI time savings without a matched manual baseline. A successful BGP failover, timer restoration, human reset, AI repair and provisioning rollback are different outcomes. [P3 implementation and acceptance evidence](P3_OPERATIONS.md) · [Earlier P2 validation](P2_AI.md)

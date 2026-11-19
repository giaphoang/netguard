# Using NetGuard Agent

Open the Ubuntu dashboard through its SSH tunnel at `http://127.0.0.1:3001`, or use `make demo-up` for local Docker on port 3000. Qwen runs through Ollama (`qwen2.5:1.5b`); a Mac-hosted model needs the existing reverse loopback tunnel to Ubuntu. [Ubuntu setup](UTM_ARM64_SETUP.md)

## Workspaces

- **Command Center:** live React Flow topology, packet-counter activity, route animation, proactive notifications and a compact NetGuard Agent panel. Select a link before simulating a fault or asking about it.
- **AI Agent** runs architecture experiments inside the chat transcript. **Tool activity** shows commands, inputs, outputs, and the controller’s checks. [Architecture experiments](ARCHITECTURE_LAB.md)
- **Provisioning:** enter a change request, compare the current and proposed graph, review the configuration diff and risk, then approve. Watch deployment and verification; **Roll back this change** restores the previous topology.
- **Incidents:** ask NetGuard Agent to search saved controller incidents and proactive investigations by device, scenario, status, recovery outcome, or keyword. Select **Inspect** for the recorded timeline and full log. The case browser and controller controls remain available on this page.
- **AI Impact:** switch between diagnosis, recovery and provisioning timings. Select a bar to open its timestamped runs and full journal records. Filter the experiment table by workflow stage, recovery mechanism, or text. The matched comparison appears when both workflows have samples from the same conditions. Choose an evaluation run to compare workflow versions on its labeled cases.

## Recruiter workflows

| Request or action | Result |
|---|---|
| “Why isn’t Leaf 1 reaching Leaf 3?” | Check observed routes and probes for the pair. Device names like `leaf1` and `Leaf 1` both resolve. A missing measurement leaves reachability unverified. |
| “How is ECMP different from BGP here?” | Get an explanation based on the current lab topology and evidence. |
| Inject a selected link fault with Local AI diagnosis | Monitoring detects it without a chat prompt. The local model investigates; the controller validates a repair or escalates. Automatic restoration requires explicitly enabled lab auto-repair. |
| “Watch for latency spikes, loss, or peer flapping.” | Opens a live reply with probe measurements, transit counters, and the tools and recovery checks for new investigations. Throughput uses matched active experiments. [Browser and recovery evidence](proof/architecture/monitor-chat.json) |
| “What could break if I remove Spine 1?” | Compares before and projected after topologies, affected BGP sessions, and installed and surviving ECMP paths. Select another spine in Overview and ask about the selected spine to repeat the review. [Browser evidence](proof/architecture/risk-agent.json) |
| “Add two leaves connected to every spine.” | Preview a validated, approval-required expansion. |
| “Provision another spine with appropriate BGP neighbors.” | Preview a spine connected to every current leaf. |
| “Add a host under Leaf 2.” | Allocate an unused host address and preview its attachment. |
| “Support twice as many hosts within the VM budget.” | Reads guest capacity, estimates the minimum additions and checks the host ceiling. An eligible plan previews deployment in chat and records BGP, host probes, and Docker usage after approval. [Verified two-to-four-host rollout and rollback](proof/architecture/capacity-chat.json) |
| “Investigate the most unstable BGP peer.” | Compare recorded transitions; say when history is insufficient. |
| “Find resolved BGP incidents on Leaf 1.” | Search the saved journal and show the newest matching records. |
| “What happened in the latest link failure?” | Open the latest matching record with timestamps, any recorded durations and its full log. You can also inspect by incident ID or the displayed eight-character prefix. |
| “Find configuration drift.” | Compare supported running BGP statements with mounted startup configuration; propose human review. |
| “Review this incident and generate a verified postmortem.” | Generate a proposed read-only runbook check linked to a real event; adoption requires review. |
| “Evaluate the AI operator against our incident test suite.” | Compare diagnostic workflows on identical labeled observations. No live actuator runs. |

Scroll the chat to revisit earlier requests, answers, approval outcomes, and evidence. **Latest message** returns to the end of the conversation. **How to use NetGuard Agent** opens the guide while keeping your draft and the composer in place.

Open **Activity** for the steps reported while the agent checks the request. Incident and provisioning actions show the controller’s recorded steps as the operation advances. **Evidence** lists the observations cited in a response.

Each request is independent. Include the target and any requested quantity or fault duration. For ordinary questions, the agent explains the latest probe paths, BGP state, and routing evidence. Specialized workflows retrieve additional evidence or prepare an approval. The controller validates devices, scope, capacity, approval, and rollback. The lab supports up to three spines, four leaves, and four hosts. Provisioning can recreate containers and interrupt traffic.

Choose **Probe route** above the topology to run a live check and watch that direction. The picker includes both directions between provisioned hosts and probes to available leaf loopbacks. **Auto cycle** moves to the next successful path after the marker reaches the destination. A failed or stale probe pauses the selected path. Link counters, ICMP, and HTTP come from the lab. Marker speed is illustrative.

Do not claim AI time savings without a matched manual baseline. A successful BGP failover, timer restoration, human reset, AI repair and provisioning rollback are different outcomes. [P3 implementation and acceptance evidence](P3_OPERATIONS.md) · [Earlier P2 validation](P2_AI.md)

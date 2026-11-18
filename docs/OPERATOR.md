# Using NetGuard Agent

Open the Ubuntu dashboard on port 3001 through its SSH tunnel, or run `make demo-up` for the local Docker dashboard on port 3000. Both use the same React Flow workspace and API. Install the local model with `ollama pull qwen2.5:1.5b`. When Ollama runs on the Mac, include `-R 127.0.0.1:11434:127.0.0.1:11434` in the SSH dashboard tunnel. [Ubuntu setup](UTM_ARM64_SETUP.md)

In **Command Center**, find **NetGuard Agent** beside the topology. Select a link for context, enter one complete lab request, and press **Enter**. Start with a diagnosis to inspect the network without changing it. The agent handles the workflows below. Each message is processed independently, so repeat the target and duration when revising a request.

| Try | Expected behavior |
|---|---|
| **Why is this connection failing?** | Inspect a timestamped routing and probe sample. Degraded observations receive a local model assessment. Unexpected degradation also surfaces in the operator panel. |
| **Simulate a Leaf 1 → Spine 2 link failure for 30 seconds.** | Review the target and duration. Approval checks the healthy baseline and alternate path. Linux disables the selected virtual interface. A server-side timer restores it, and probes verify recovery. |
| **Fix this network.** | For an eligible repair, inspect the interface command, risk and approval. The controller independently checks the component, executes the runbook and verifies both directions. Unsupported proposals reach a human. |
| **Expand this fabric.** | Choose one or two leaves, inspect the dashed topology preview and FRR/Compose diff, then approve. Verification checks BGP, host HTTP/ICMP and reachability from each new loopback. **Restore base topology** reverses the expansion. |
| **Compare automated recovery performance against manual operations.** | Inspect measured durations, sample counts, diagnosis accuracy, required handoffs, withheld proposals and recent failures. |

A single API orchestrates observation, inference and execution. Changes use fixed tools and explicit target parameters. Expansion adds up to two leaves with isolated transit segments, 192 MiB and half a CPU per leaf. Applying or rolling back an expansion recreates both spines and can interrupt traffic. The preview and event log persist across reloads. Use the Makefile lifecycle commands for an expanded Desktop fabric. On Ubuntu, use the guest Python and the dashboard's same `NETGUARD_TIMELINE_DB` with `python -m scripts.fabric_compose up|down|reset`.

## Reading live transmission

The Command Center samples Linux packet and byte counters at each leaf. One blue marker follows the selected ICMP probe from host to host via both leaves and the spine selected by the source leaf’s Linux forwarding lookup. Use **Probe route** to reverse direction. Playback requires a fresh successful probe and a known next hop. Its speed is illustrative. Select a link to read its received and transmitted packet counts. The byte rate sums measured interfaces and can count forwarded traffic at multiple links. BGP session state and bidirectional ICMP and HTTP probe outcomes appear separately. Historical replay shows recorded probe outcomes.

## Reading the impact panel

Choose **Human operator** or **Local AI** when starting a graph-selected fault. A manual run requires the operator to record a diagnosis. Diagnosis timing runs from the first observed signal to a correct decision. Recovery requires observed loss followed by two successful probe rounds. Verified closure includes the restoration and checks. Provisioning starts when the request arrives and ends after verification.

The time-saved KPI compares median **verified closure** across matched manual and assisted groups. Matching uses the fault, target, initial router set, approval policy and timer. Scripted P1 approvals remain separate. A dash means there is insufficient matched data. The operator configuring a manual fault may know its cause, so this comparison measures a lab workflow. Prompt and policy changes are reviewed in code and evaluated against recorded cases.

## Validation

Chrome exercised graph preview, deployment, six live routers, rollback, timed simulation and measured results on the Ubuntu VM. The initial model proposed the wrong spine during a live fault. The controller withheld the repair and the approved timer restored the correct link. Subsequent model constraints limited eligible targets to observed abnormal adjacencies. [Dashboard capture](proof/operator/dashboard.png) · [Ubuntu simulation and expansion](proof/operator/ubuntu-run.json) · [Converged and missing-evidence evaluations](proof/operator/converged.json) · [Final Ubuntu repair run](proof/operator/approved-repair.json)

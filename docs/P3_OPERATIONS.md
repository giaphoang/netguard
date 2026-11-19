# P3: proactive network operations

Preserve P0–P2 and the existing Ubuntu, Docker, FRR, React Flow and SQLite stack. One orchestrator interprets intent and investigates; deterministic controllers own validation, approval, execution and rollback.

| Stage | Acceptance |
|---|---|
| A · Monitoring | An injected fault triggers model investigation and a guarded, verified recovery without an operator prompt. |
| B · Intent | Schema-validated interpretation resolves current topology entities; unknown or ambiguous targets require clarification. |
| C · Provisioning and UI | Preview, approve, deploy and verify a spine, leaf and host; separate Command Center, Provisioning, Incidents and Impact. |
| D · Evaluation | Replay identical labeled observations across workflow versions; inspect errors, escalation, timing and sample counts. |

## Stage A

The live collector supplies BGP, interfaces, routes, link counters and bidirectional HTTP/ICMP observations. The observer evaluates distinct, fresh samples. It records rule triggers and timestamped evidence before inference; controlled incidents reuse the controller's existing model call. Uncontrolled faults escalate because there is no approved fault target. Human benchmark runs remain human-only.

Configure `NETGUARD_MONITOR_RULES` as JSON at service startup. Defaults: 60-second window, two degraded samples, three BGP transitions, 120-second inference cooldown, 60-second drift scans and two healthy recovery samples. `/api/monitoring` exposes effective rules and durable investigations. Cooldown state survives restart. Drift compares supported BGP statements against mounted startup configuration; incomplete scans never prove recovery.

Automatic repair still requires explicitly enabled lab automation, an owned injected link, matching live evidence, a healthy alternate route, no emergency stop and one bounded attempt. Timer restoration, controller reset, observed recovery and AI repair retain separate outcomes. Models cannot execute commands or alter policies.

Reproduce on the isolated lab: `python -m scripts.verify_proactive docs/proof/p3/new-stage-a.json`. This authorizes a reversible link-fault test, enables the existing repair policy temporarily, invokes no operator prompts, and records the entire incident and investigation. The script restores the prior automation setting and resets a failed test.

Acceptance: [Ubuntu autonomous recovery](proof/p3/stage-a.json) recorded zero operator prompts and a verified `ai_repair`. [Initial failed diagnosis](proof/p3/stage-a-initial-handoff.json) was safely escalated and reset. Version v6 rejects diagnoses contradicting a down interface and allows up to 20 seconds of probe convergence before incident inference. Tests cover per-peer rules, model failure, deduplication, cooldown across restart, evidence contradictions and recovery. These are lab results, not production reliability estimates.

## Stage B

`api/intents.py` sends every operator request to local Qwen with a strict JSON schema. The model supplies intent, targets and parameters; a deterministic resolver binds device references to the current observed inventory. Reference constraints prevent silently substituting an existing router for an unknown one. Unknown or ambiguous targets receive HTTP 409 clarification. Risk assessment counts independently observed surviving adjacencies at both ends. A passing redundancy check still requires human review; it never authorizes removal.

[Ubuntu acceptance](proof/p3/stage-b.json): Spine 1 and Spine 2 risk requests succeeded, Spine 99 was rejected, an approved Leaf 3 was deployed and verified, the same removal-risk question worked for Leaf 3, and the expansion was rolled back. `python -m scripts.verify_intents <new-evidence-path>` reproduces this bounded test. Interpretation and its model/context version are recorded in the investigation journal. Intent extraction supports spine/host requests; deployment support follows in Stage C.

## Stage C

Provisioning extends the existing Compose renderer and actuator with cumulative, hash-bound plans. Capacity is three spines, four leaves and four hosts. Validators retain the base fabric, reserve unique names/subnets/ASNs/host addresses, require all opposite-tier BGP neighbors, check approved container provenance and isolated parentless networks, and reject stale approvals. Deployment checks BGP, routing and bidirectional host ICMP/HTTP. Failure restores the previous verified topology; uncertainty escalates. Historical leaf-only plans remain readable.

The interface has separate Command Center, Provisioning, Incidents and AI Impact workspaces. React Flow renders live expanded inventory and before/after previews. Searchable investigation cards summarize findings, actions and verification; tool results and full logs are collapsed. Existing incident controls, replay, local authorization and emergency stop remain available.

Reproduce: `python -m scripts.verify_provisioning <new-evidence-path>` approves a leaf, a spine and a host, assesses each added component, verifies routing/reachability, and rolls back in reverse order. `NODE_PATH=<playwright-core-installation> node scripts/verify_dashboard.cjs` exercises the browser approval flow in Chrome. [Deployment evidence](proof/p3/stage-c-final.json) includes actual router and probe results. The earlier [ownership rejection](proof/p3/stage-c-run.json) preserved the fabric and was corrected by recognizing unchanged containers from the approved plan history.

Chrome acceptance passed at 1440px and 390px: four workspaces rendered without JavaScript errors, the host preview showed current/proposed attachments, UI approval produced a verified real host, and UI rollback succeeded. [Command Center](proof/p3/command-center.png) · [Provisioning preview](proof/p3/provision-preview.png) · [Incidents](proof/p3/incidents.png) · [Mobile](proof/p3/mobile.png). Unit, frontend type/build, and Compose checks passed (47 Python tests).

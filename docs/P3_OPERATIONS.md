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

## Stage D

The reproducible suite compares three read-only workflows on the **same eight diagnosis observations**: a schema-only baseline, frozen P2 v5 constraints, and current v6 constraints. It includes recorded link/BGP/endpoint failures, reconvergence, missing evidence, mirrored faults and a clearly labeled synthetic fault on a recorded six-router topology. Fourteen intent cases cover paraphrases, new nodes, unknown/ambiguous targets, monitoring, drift, evaluation and metrics. Five deterministic checks cover BGP flapping, configuration drift and safe/unsafe changes.

[Final Ubuntu/API evaluation](proof/p3/stage-d-final.json): v5 identified the correct class and component in **4/8** cases; v6 achieved **5/8**. Correct dispositions improved **5/8 → 6/8**. Neither guarded workflow proposed an unsafe repair; the schema-only baseline proposed one. All **19/19** intent/policy/detector checks passed. Mean model-workflow latency was approximately 0.74 s for v5 and 0.73 s for v6 in this run; this tiny difference is not a productivity claim. The suite stores input hashes, expected outcomes, returned decisions, actual Ollama call timestamps, failures and policy results. The weaker schema-only baseline is explicitly distinguished from the shipped P2 workflow.

The preceding [v2 intent run](proof/p3/stage-d.json) passed 18/19 non-diagnosis checks and misrouted a hypothetical outage. Intent context v3 adds a schema-validated distinction between hypothetical questions and actual changes for targeted operations. It remains one orchestrator, with an additional small classification call when needed. No runtime prompt-string routing or autonomous policy rewriting was introduced.

The three remaining diagnosis errors are visible as failures/uncertainty, not hidden. These fixtures are development regressions, partly derived from recorded observations; they do not estimate production accuracy. New evaluation runs must retain their sample counts and evidence. No new matched manual baseline was collected, so **time saved remains unavailable**. Reset, timer restoration, observed recovery, routing failover, AI repair and rollback are reported distinctly.

Reproduce the final recruiter demonstration with `python -m scripts.verify_evaluation <new-evidence-path>`: it asks about removing Spine 2, verifies the policy block, runs the suite through NetGuard Agent, checks that AI Impact exposes the recorded results and confirms the topology is unchanged. For an offline/read-only run against local Qwen, use `python -m scripts.evaluate_operations <new-result-path> [--db <dashboard-journal>]`.

## Final validation

The original P0–P2 histories and datasets remain intact. Stage A/B/C/D evidence is linked above. Final verification includes unit tests, TypeScript/build and Compose checks, Chrome navigation and real approval/rollback, and a fresh Ubuntu proactive recovery after all provisioning changes. Capacity and removal restrictions remain deliberate lab limits. Network provisioning recreates affected containers; it does not promise hitless production changes.

Final checks: **54 Python tests passed**, TypeScript/build and Compose validation passed, both dashboards returned four healthy base routers and two observable hosts with bidirectional ICMP/HTTP success, and neither had an active incident. [Fresh autonomous recovery](proof/p3/final-recovery.json) passed after the expanded-topology changes. [Final AI Impact screenshot](proof/p3/ai-impact-final.png) shows the measured 5/8 diagnostic result, 14/14 intent cases and 5/5 detector/policy cases. Full observations remain in linked records; overview responses include only case summaries.

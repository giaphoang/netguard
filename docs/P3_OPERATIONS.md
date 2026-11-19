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

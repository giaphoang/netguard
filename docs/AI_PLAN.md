# NetGuard after P1

**Goal.** An operator asks for a fabric change, watches it take effect, and can see how the AI handles a subsequent fault. Keep React Flow, the Ubuntu/FRRouting fabric, live probes and the P1 repair controller. P1 supplies a repeatable rules-based baseline. Its two campaigns established no consistent closure-time advantage. [P1 evidence](P1_INCIDENTS.md)

## Where to invest

Estimated strength of evidence deliverable in the next milestone, scored 1–5 against the three supplied JD criteria with equal weight. These are planning judgments.

| Direction | Measurable impact | Responsible AI | AI skill development | Total |
|---|---:|---:|---:|---:|
| AI incident loop on P1, then prompted provisioning | 5 | 5 | 5 | 15/15 |
| Prompted provisioning first | 4 | 4 | 4 | 12/15 |
| GNS3 migration first, AI-only interface | 2 | 3 | 4 | 9/15 |

Incident response already has repeatable faults, verified outcomes and a handoff path. Provisioning first requires building an executor before measuring the AI. GNS3 first adds migration and baseline validation, while an AI-only view makes independent verification harder to show. Revisit GNS3 when a target scenario requires a device image or behavior the FRR lab cannot reproduce.

## Build order and proof

1. **P2 — Diagnose and maintain.** [Implementation and measured results](P2_AI.md). Select a supported link, peer or endpoint in React Flow and configure a fault there. Give the agent only timestamped observations, keeping the injected cause hidden. Have it cite evidence, propose a bounded repair and invoke the existing controller under its approval policy. Show the affected path, action and probe-verified outcome live. On insufficient evidence or failed verification, hand the operator the evidence, attempted actions and next check.
2. **P3 — Provision from intent.** Start with “Add a leaf connected to both spines.” Translate the request into a topology diff for approval. Use the same intent for graph edits and prompts. Render FRR and Compose configuration deterministically, apply it, and mark the node live only after adjacency and reachability checks pass. Restore the previous topology on failure. Extend to adding a spine after the leaf path works. Borrow the inventory-to-template pattern from [Virtual Network Lab](https://github.com/BelloShuaibu/Virtual-Network-Lab).
3. **Evaluate from the first P2 run.** Compare P1 rules and AI on the same cases. Record correct diagnoses and handoffs, unsafe proposals, verified outcomes and operator interventions per case. Time real operators separately if claiming time saved. Turn failures into replay cases, version the prompt and tool context, and promote revisions only after human review and held-out evaluation. Include missing telemetry and equivalent faults on different nodes to expose unwarranted assumptions.

**Recruiter demo.** Ask for a leaf, inspect the diff, approve it and watch its peers come up. Break one of its links on the graph. Follow the evidence through diagnosis and verified repair, then replay an ambiguous case that reached a human. End with the measured difference from P1 and one prompt revision that changed a held-out result.

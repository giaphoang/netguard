# P2 · AI incident response

Select **Replay AI handoff** on the dashboard for a recorded BGP incident. At **Handoff**, open the model’s citations, then advance to **Resolved** to see the probes after manual reset. `make demo-replay` serves this walkthrough without Docker or Ollama.

## Run live

Start Ollama on the dashboard server and run `ollama pull qwen2.5:1.5b`. With the existing project dependencies installed, run `make demo-up`. The API calls Ollama on loopback port 11434. Set `NETGUARD_MODEL` on the API process to evaluate another installed model. For the Ubuntu VM with Ollama on the Mac, add `-R 127.0.0.1:11434:127.0.0.1:11434` to the existing SSH dashboard tunnel and restart the guest API with the updated checkout. [Guest setup](UTM_ARM64_SETUP.md)

Select the **leaf-1 ↔ spine-1 edge** to disable its interface or interrupt its BGP session. Select **host-2** to pause its endpoint. Choose **Local AI** or **P1 rules**, then inject. The model receives a timestamped table of interfaces, peers, candidate paths and host probes. The preset name, controller diagnosis, target identity and runbooks stay outside the observation.

The controller checks the proposed component and independently verifies the link fault and surviving path. Approval is the default. The opt-in automatic mode uses the same checks. Two healthy routing and bidirectional probe rounds confirm restoration. An unsupported decision, unavailable model or failed verification goes to human review. The journal preserves observations, the decision, actions and outcome.

## Recorded results

| Exercise | Result |
|---|---|
| Three live faults on Docker Desktop | AI identified 2/3 causes and chose the correct repair or handoff in 3/3. P1 identified 3/3 when supplied the preset. One approval and two manual resets led to three probe-verified closures. [Case records](proof/p2/live.json) |
| Two held-out variations | The model requested restoration for both missing peer telemetry and a fault on the opposite spine. P1’s link evidence gate rejected both samples. These were read-only evaluations. [Observations and decisions](proof/p2/held-out.json) |
| Same confirmation-campaign sample, two prompt versions | V1 cited an unknown observation. V2 constrained citations to actual component keys and identified the correct link during reconvergence. [Version comparison](proof/p2/revision.json) |

Chrome exercised graph selection, injection, reload, evidence inspection, approval and verified closure. The final fabric smoke check observed all peers established, both next hops and successful HTTP and ICMP in both directions. These runs establish workflow behavior. Operator time savings and general diagnosis accuracy remain unmeasured.

To repeat the three live cases with approval mode enabled, run `.venv/bin/python -m scripts.verify_ai /tmp/p2-live.json --port 3000`. The script approves a proposed link repair or resets a handed-off incident and waits for verified closure. To evaluate only recorded variations, run `.venv/bin/python -m scripts.evaluate_ai /tmp/p2-eval.json`. Both commands require a fresh output filename.

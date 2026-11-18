# Native Ubuntu forwarding validation

On 2026-11-18 the existing fabric passed on Ubuntu Server 24.04.5 ARM64 in UTM. The guest ran Linux 6.8.0-149-generic, Docker Engine 29.10.0, Compose 5.8.0 and FRRouting 10.1.1_git. The original Compose file and all four router configurations deployed directly from the VirtioFS checkout.

| Execution | BGP and kernel routes | Host-1 → host-2 | Host-2 → host-1 |
|---|---|---|---|
| [First deployment](proof/ubuntu-arm64/first-run.txt) | Four established sessions, two equal-weight remote next hops per leaf | 3/3 ICMP, HTTP succeeded | 3/3 ICMP, HTTP succeeded |
| [Compose down/up](proof/ubuntu-arm64/clean-restart.txt) | Same | 3/3 ICMP, HTTP succeeded | 3/3 ICMP, HTTP succeeded |
| [Ubuntu reboot and Compose up](proof/ubuntu-arm64/after-reboot.txt) | Same | 3/3 ICMP, HTTP succeeded | 3/3 ICMP, HTTP succeeded |

The guest boot ID changed from a73eb7aa-aabc-4cf7-a3c9-dd4d711d8471 to 433b3bba-1b71-4773-98e8-385e786fabf7. Both shared mounts and Docker returned automatically. A single explicit Compose up started the containers. One early validation attempt during teardown correctly failed because spine-1 had stopped. The completed recreation passed. The guest Python suite passed all nine tests with one Starlette deprecation warning.

Host-1 reached 10.212.2.2 via its specific subnet route through 10.212.1.1. Host-2 used 10.212.2.1 in reverse. All four routers had IP forwarding enabled. UDP traceroute reached the destination on hop four with missing intermediate ICMP replies. A separate capture of one echo request (ID 58, sequence 0) identified ingress and egress on leaf-1, spine-1 and leaf-2. Spine-2 saw no copy of that request. [Per-router packet evidence](proof/ubuntu-arm64/hop-capture.txt)

## Single controlled link interruption

At 19:20:21.457412 UTC the verifier confirmed that leaf-1’s interface facing spine-1 was down. The first snapshot showing leaf-1 using only spine-2 arrived 0.177 s later. Both hosts experienced six unsuccessful ICMP attempts each. The first subsequent successful probe commands completed at +8.429 s and +8.442 s respectively. Bidirectional ICMP and HTTP had both been verified on the surviving route by +9.885 s, before link restoration.

The verifier re-enabled the link at 19:20:31.365645 UTC. Both leaves regained ECMP, all peers established, and both directions passed ICMP and HTTP by 19:20:34.313248 UTC. BGP handled traffic failover. The verifier restored the deliberately disabled interface.

These are single-run observations. Each independent probe loop waits 250 ms after a command completes. The one-second ping timeout and process overhead lengthen the sampling interval during loss. First observed forwarding and first observed local route change have different clocks and resolution. [Verifier timestamps](proof/ubuntu-arm64/verification.log) · [Host-1 probes](proof/ubuntu-arm64/probe-1.jsonl) · [Host-2 probes](proof/ubuntu-arm64/probe-2.jsonl) · [Alternate route snapshot](proof/ubuntu-arm64/alternate.json)

## Dashboard and isolation

Chrome loaded the existing UI through the SSH tunnel at http://127.0.0.1:3001 and returned successful live ICMP and HTTP on Trace traffic. The Ubuntu timeline uses a guest-local SQLite database so the Desktop and Ubuntu collectors have independent histories. [Ubuntu dashboard](proof/ubuntu-arm64/dashboard.png)

Runtime inspection showed six internal macvlan networks with empty driver options and no physical parent. None of the six containers were privileged or published ports. Only the four routers had SYS_ADMIN. Docker logs rotate at two 5 MiB files per container. SSH listens inside the NAT guest. The guest dashboard binds to loopback and Docker uses its local Unix socket.

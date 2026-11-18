# P0 decisions

Test host was an Apple Silicon Mac (11 logical CPUs, 18 GiB RAM) with Docker Desktop Engine 28.0.4, Compose 2.34.0 and Python 3.12.16. Containerlab was absent. The local Ollama server listed zero installed models.

Compose manages six internal, parentless macvlan segments in the Docker Linux VM. Each transit segment joins exactly one leaf and one spine. Internal Docker bridges established BGP but the end-to-end probes failed. Parentless macvlan passed both directions with the same IP plan. No physical parent, host networking or router management ports are configured. On Linux use a local rootful engine supporting macvlan. Docker Desktop must run Linux containers.

The FRR 10.1.1 daemons failed capability initialization with NET_ADMIN alone. The four router containers have the explicitly approved SYS_ADMIN capability. Keep this topology on a dedicated local VM. Both spines use AS 65000 and leaves use 65101 and 65102. Each leaf originates its own connected host prefix. Keepalive/hold timers are 3/9 seconds so one-sided carrier loss can converge within a short walkthrough.

The FastAPI collector runs beside the Docker client and issues fixed read-only commands. The browser receives snapshots by SSE and reconnects through EventSource. Link status comes from the leaf peer table. Candidate paths come from the kernel route at trace time. Separate ICMP and HTTP checks measure end-to-end reachability. Snapshot collection waits three seconds between samples.

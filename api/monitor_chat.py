"""A live view of the existing proactive investigation journal."""

from . import chat_views, investigations


def timeline(record):
    return {
        "type": "timeline",
        "title": "Investigation tools and verification",
        "trace": [
            {
                "label": entry["tool"],
                "stage": entry["stage"],
                "started": entry["time"],
                "result": entry["result"],
            }
            for entry in record.get("events", [])
        ],
    }


def view(controller):
    records = investigations.cases(controller.db, kind="monitor")
    rules = getattr(controller, "monitor_rules", {})
    observation = getattr(controller, "monitor_observation", {})
    rows = []
    for name, values in observation.get("facts", {}).items():
        if "probes" not in values:
            continue
        probes, icmp = values["probes"], values.get("icmp") or {}
        rtt, sent, received = (icmp.get(key) for key in ("rtt_ms", "sent", "received"))
        rows.append(
            [
                (values.get("from") or name) + " → " + values["to"],
                probes.get("ICMP"),
                probes.get("HTTP"),
                f"{rtt:.3f} ms" if rtt is not None else None,
                f"{sent - received} / {sent}"
                if sent is not None and received is not None
                else None,
            ]
        )
    blocks = []
    if rows:
        blocks.append(
            chat_views.table(
                "Latest probe observations",
                ["Direction", "ICMP", "HTTP", "RTT", "Lost / sent"],
                rows,
            )
        )
    if rules:
        blocks.append(
            chat_views.text(
                f"High latency requires {rules['degraded_samples']} consecutive RTT samples of at least "
                f"{rules['latency_ms']:g} ms. The packet loss threshold is {rules['packet_loss_pct']:g}% "
                f"across {rules['degraded_samples']} measured probe samples."
            )
        )
    blocks.append(
        chat_views.text(
            "Transit counters describe current load. Throughput degradation can be verified "
            "with matched active workloads in a throughput experiment."
        )
    )
    traffic = getattr(controller, "monitor_traffic", {})
    if traffic:
        blocks.append(
            chat_views.section(
                "Current link load",
                [
                    chat_views.table(
                        "Observed transmit and receive counters",
                        ["Link", "Traffic", "Sample interval"],
                        [
                            [
                                name,
                                f"{sample['bytes_per_second']:,.0f} B/s",
                                f"{sample['seconds']:.1f} s",
                            ]
                            for name, sample in sorted(traffic.items())
                        ],
                    )
                ],
                collapsed=True,
            )
        )
    if records:
        for row in records[:5]:
            blocks.append(
                chat_views.section(
                    f"{row.get('status', 'recorded').replace('_', ' ').capitalize()} · {row['id'][:8]}",
                    [
                        chat_views.text(row["text"]),
                        timeline(row),
                        chat_views.link(
                            "Full incident evidence", "/api/investigations/" + row["id"]
                        ),
                    ],
                )
            )
    else:
        blocks.append(chat_views.text("Waiting for an anomaly to open an investigation."))
    blocks.append(chat_views.link("Monitoring journal", "/api/monitoring"))
    return {
        "title": "Live monitoring" if rules else "Monitoring is starting",
        "text": "The background observer opens investigations and records recovery checks as evidence arrives.",
        "blocks": blocks,
        "refresh_seconds": 4,
    }

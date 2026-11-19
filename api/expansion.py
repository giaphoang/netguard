"""Validated cumulative spine/leaf/host plans using the existing Compose actuator."""

import difflib
import json
import os
import subprocess
import time
import uuid
from copy import deepcopy

from . import provision as p

BASE = {"spines": [1, 2], "leaves": [1, 2], "hosts": [{"id": 1, "leaf": 1}, {"id": 2, "leaf": 2}]}


def get(db, identity):
    import sqlite3

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT data FROM expansions WHERE id=?", (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def current(db):
    plan = p.latest(db)
    seen = set()
    while plan and plan["phase"] not in p.ACTIVE:
        if plan["id"] in seen:
            raise ValueError("Invalid topology history")
        seen.add(plan["id"])
        plan = get(db, plan["previous_id"]) if plan.get("previous_id") else None
    return plan


def spec(plan):
    return deepcopy(
        plan["topology"]
        if plan and "topology" in plan
        else {**BASE, "leaves": [1, 2, *plan["leaves"]]}
        if plan
        else BASE
    )


def names(topology):
    return [
        *(f"spine-{n}" for n in topology["spines"]),
        *(f"leaf-{n}" for n in topology["leaves"]),
        *(f"host-{h['id']}" for h in topology["hosts"]),
    ]


def validate(topology):
    for key, required, maximum in [("spines", {1, 2}, 3), ("leaves", {1, 2}, 4)]:
        values = topology[key]
        if (
            not required <= set(values)
            or len(values) != len(set(values))
            or any(type(n) is not int or n < 1 or n > maximum for n in values)
        ):
            raise ValueError(
                f"Lab capacity/preservation policy: at most {maximum} {key}, including the base fabric"
            )
    hosts = topology["hosts"]
    if not 2 <= len(hosts) <= 4 or len({h["id"] for h in hosts}) != len(hosts):
        raise ValueError("Lab capacity is four hosts")
    if any(h not in hosts for h in BASE["hosts"]):
        raise ValueError("Preserve the base hosts")
    for h in hosts:
        if type(h["id"]) is not int or not 1 <= h["id"] <= 4 or h["leaf"] not in topology["leaves"]:
            raise ValueError("Invalid host identity or attachment")


def host_ip(host, topology):
    peers = sorted(h["id"] for h in topology["hosts"] if h["leaf"] == host["leaf"])
    return f"10.212.{host['leaf']}.{2 + peers.index(host['id'])}"


def render(topology, folder):
    validate(topology)
    services, networks, configs = {}, {}, {}

    def network(key, subnet):
        networks[key] = {
            "internal": True,
            "driver": "macvlan",
            "ipam": {"config": [{"subnet": subnet + ".0/28", "gateway": subnet + ".14"}]},
        }

    for s in topology["spines"]:
        for leaf in topology["leaves"]:
            network(f"s{s}l{leaf}", f"10.211.{10*s+leaf}")
    for leaf in topology["leaves"]:
        network(f"access{leaf}", f"10.212.{leaf}")
    for kind, numbers in [("spine", topology["spines"]), ("leaf", topology["leaves"])]:
        for n in numbers:
            name = f"{kind}-{n}"
            spine = kind == "spine"
            config = f"frr defaults traditional\nhostname {name}\n"
            if not spine and n > 2:
                config += f"interface lo\n ip address 10.214.0.{n}/32\n!\n"
            config += f"router bgp {65000 if spine else 65100+n}\n bgp router-id 10.213.{1 if spine else 2}.{n}\n timers bgp 3 9\n no bgp ebgp-requires-policy\n"
            for peer in topology["leaves" if spine else "spines"]:
                net = 10 * n + peer if spine else 10 * peer + n
                config += f" neighbor 10.211.{net}.{2 if spine else 1} remote-as {65100+peer if spine else 65000}\n"
            config += f' address-family ipv4 unicast\n  maximum-paths {len(topology["spines"])}\n'
            if not spine:
                config += f"  network 10.212.{n}.0/28\n"
                if n > 2:
                    config += f"  network 10.214.0.{n}/32\n"
            configs[name] = config + " exit-address-family\n!\n"
            attachments = {
                f"s{n if spine else peer}l{peer if spine else n}": {
                    "ipv4_address": f"10.211.{10*n+peer if spine else 10*peer+n}.{1 if spine else 2}"
                }
                for peer in topology["leaves" if spine else "spines"]
            }
            if not spine:
                attachments[f"access{n}"] = {"ipv4_address": f"10.212.{n}.1"}
            services[name] = {
                "image": "quay.io/frrouting/frr:10.1.1",
                "cap_add": ["NET_ADMIN", "SYS_ADMIN"],
                "mem_limit": "192m",
                "cpus": 0.5,
                "sysctls": {"net.ipv4.ip_forward": "1"},
                "networks": attachments,
                "volumes": [
                    f"{folder}/{name}.conf:/etc/frr/frr.conf:ro",
                    f"{p.ROOT}/lab/daemons:/etc/frr/daemons:ro",
                ],
            }
    for h in topology["hosts"]:
        name, leaf = f"host-{h['id']}", h["leaf"]
        services[name] = {
            "image": "busybox:1.36.1",
            "cap_add": ["NET_ADMIN"],
            "mem_limit": "32m",
            "cpus": 0.25,
            "networks": {f"access{leaf}": {"ipv4_address": host_ip(h, topology)}},
            "command": [
                "sh",
                "-ec",
                f"ip route replace default via 10.212.{leaf}.1; exec busybox httpd -f -p 8080 -h /srv",
            ],
            "volumes": [f"{p.ROOT}/demo-sites/malmo:/srv:ro"],
        }
    return {"services": services, "networks": networks}, configs


def propose(c, kind, count, targets, started):
    with c.lock:
        prior = current(c.db)
        if c.active or c.stopped or prior and prior["phase"] != "verified":
            raise ValueError("Finish the active operation before provisioning")
        topology = spec(prior)
        if kind not in {"spine", "leaf", "host"} or type(count) is not int or not 1 <= count <= 2:
            raise ValueError("Specify one or two spine, leaf or host components")
        if kind == "host":
            if len(targets) != 1 or targets[0] not in {f"leaf-{n}" for n in topology["leaves"]}:
                raise ValueError("Choose exactly one existing leaf for host attachment")
            leaf = int(targets[0].split("-")[1])
            first = max(h["id"] for h in topology["hosts"]) + 1
            topology["hosts"] += [{"id": n, "leaf": leaf} for n in range(first, first + count)]
        else:
            expected = {
                f'{"spine" if kind == "leaf" else "leaf"}-{n}'
                for n in topology["spines" if kind == "leaf" else "leaves"]
            }
            if targets and set(targets) != expected:
                raise ValueError("Router provisioning must connect to every opposite-tier router")
            key = "leaves" if kind == "leaf" else "spines"
            first = max(topology[key]) + 1
            topology[key] += list(range(first, first + count))
        validate(topology)
        plan = {
            "id": uuid.uuid4().hex,
            "version": 3,
            "topology": topology,
            "before": spec(prior),
            "previous_id": prior["id"] if prior else None,
            "leaves": [n for n in topology["leaves"] if n > 2],
            "added": sorted(set(names(topology)) - set(names(spec(prior)))),
            "events": [],
            "started": started,
            "risk": {
                "status": "approval_required",
                "affected": names(spec(prior)),
                "reason": "Router and host containers may be recreated; traffic can pause during reconvergence.",
                "rollback": "Restore the previously verified topology and remove only this change’s owned resources.",
            },
        }
        _, files, plan["digest"] = p.material(c.db, plan)
        before_files = p.material(c.db, prior)[1] if prior else {}
        plan["diff"] = "\n".join(
            line
            for name, content in files.items()
            for line in difflib.unified_diff(
                before_files.get(
                    name,
                    (p.ROOT / "lab" / name).read_text() if (p.ROOT / "lab" / name).exists() else "",
                ).splitlines(),
                content.splitlines(),
                fromfile=name,
                tofile=name,
                lineterm="",
            )
        )
        p.save(c.db, plan, "preview")
        return plan


def verify(topology):
    from .fabric import router, trace, command

    deadline = time.monotonic() + 75
    last = {}
    while time.monotonic() < deadline:
        routers = [router(n).model_dump() for n in names(topology) if not n.startswith("host")]
        expected = {}
        for s in topology["spines"]:
            expected[f"spine-{s}"] = {
                f"10.211.{10*s+n}.2": "Established" for n in topology["leaves"]
            }
        for n in topology["leaves"]:
            expected[f"leaf-{n}"] = {
                f"10.211.{10*s+n}.1": "Established" for s in topology["spines"]
            }
        last = {
            "observed": time.time(),
            "routers": routers,
            "flows": [trace(1), trace(2)],
            "host_checks": [],
        }
        if all(r["peers"] == expected[r["name"]] for r in routers) and all(
            f["probes"] == {"ICMP": "success", "HTTP": "success"}
            and f["candidates"] == topology["spines"]
            for f in last["flows"]
        ):
            try:
                for h in topology["hosts"]:
                    dest = next(v for v in topology["hosts"] if v["leaf"] != h["leaf"])
                    addr = host_ip(dest, topology)
                    command(f"host-{h['id']}", "ping", "-c", "1", "-W", "2", addr)
                    command(f"host-{h['id']}", "wget", "-T", "2", "-qO-", f"http://{addr}:8080/")
                    command(
                        f"host-{dest['id']}", "ping", "-c", "1", "-W", "2", host_ip(h, topology)
                    )
                    command(
                        f"host-{dest['id']}",
                        "wget",
                        "-T",
                        "2",
                        "-qO-",
                        f"http://{host_ip(h,topology)}:8080/",
                    )
                    last["host_checks"].append(
                        {
                            "source": h["id"],
                            "destination": dest["id"],
                            "ICMP": "success",
                            "HTTP": "success",
                            "bidirectional": True,
                        }
                    )
                for n in topology["leaves"]:
                    if n > 2:
                        command(
                            f"leaf-{n}",
                            "ping",
                            "-I",
                            f"10.214.0.{n}",
                            "-c",
                            "1",
                            "-W",
                            "2",
                            "10.212.1.1",
                        )
                return last
            except (OSError, subprocess.SubprocessError):
                pass
        time.sleep(1)
    raise ValueError("Topology BGP, routes or bidirectional host checks did not converge")


def ownership(topology, origins):
    # Reuse the local-socket and isolated-network boundary, including exact owners.
    if not p.presets.docker(
        "context",
        "inspect",
        os.environ.get("NETGUARD_CONTEXT", "default"),
        "--format",
        "{{.Endpoints.docker.Host}}",
    ).startswith("unix://"):
        raise ValueError("A local Docker socket is required")
    for name in names(topology):
        info = json.loads(p.presets.docker("inspect", f"netguard-{name}-1"))[0]
        labels = info["Config"]["Labels"]
        if (
            labels.get("com.docker.compose.project") != "netguard"
            or labels.get("com.docker.compose.service") != name
            or labels.get("com.docker.compose.project.config_files") not in origins
            or info["HostConfig"]["Privileged"]
        ):
            raise ValueError("Topology ownership mismatch")
        nets = info["NetworkSettings"]["Networks"]
        inspected = json.loads(
            p.presets.docker("network", "inspect", *(n["NetworkID"] for n in nets.values()))
        )
        if not inspected or any(
            not n["Internal"]
            or n["Driver"] != "macvlan"
            or (n.get("Options") or {}).get("parent")
            or (n.get("Labels") or {}).get("com.docker.compose.project") != "netguard"
            for n in inspected
        ):
            raise ValueError("Topology requires isolated parentless lab networks")


def execute(c, identity, rollback=False):
    with c.lock:
        plan = current(c.db) if rollback else p.latest(c.db)
        if (
            not plan
            or plan["id"] != identity
            or plan["phase"] not in (("verified", "needs_review") if rollback else ("preview",))
            or c.active
            or c.stopped
            and not rollback
        ):
            raise ValueError("Approval is stale or another operation is active")
        folder, files, digest = p.material(c.db, plan)
        if digest != plan["digest"]:
            raise ValueError("Configuration changed; request a fresh preview")
        prior = get(c.db, plan["previous_id"]) if plan.get("previous_id") else None
        previous = spec(prior)
        old_overlay = p.material(c.db, prior)[0] / "compose.json" if prior else None
        overlay = folder / "compose.json"
        if prior:
            prior_folder, prior_files, prior_digest = p.material(c.db, prior)
            if prior_digest != prior["digest"] or any(
                (prior_folder / name).read_text() != content
                for name, content in prior_files.items()
            ):
                raise ValueError(
                    "Previous verified configuration changed; inspect before deployment or rollback"
                )
        origins = {str(p.BASE), str(p.BASE) + "," + str(overlay)}
        ancestor = prior
        while ancestor:
            origins.add(str(p.BASE) + "," + str(p.material(c.db, ancestor)[0] / "compose.json"))
            ancestor = get(c.db, ancestor["previous_id"]) if ancestor.get("previous_id") else None
        if not rollback:
            ownership(previous, origins - {str(p.BASE) + "," + str(overlay)})
            existing = set(p.presets.docker("ps", "-a", "--format", "{{.Names}}").splitlines())
            if any(f"netguard-{n}-1" in existing for n in plan["added"]):
                raise ValueError("A requested resource already exists")
            # Network name collisions also require operator review.
            old_networks = set(render(previous, folder)[0]["networks"])
            new_networks = set(render(plan["topology"], folder)[0]["networks"]) - old_networks
            existing_nets = set(
                p.presets.docker("network", "ls", "--format", "{{.Name}}").splitlines()
            )
            if any("netguard_" + n in existing_nets for n in new_networks):
                raise ValueError("A requested network already exists")
            plan["baseline"] = verify(previous)
            folder.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (folder / name).write_text(content)
            p.compose(overlay, "config", "--quiet")
        p.save(c.db, plan, "rolling_back" if rollback else "applying")
    if not rollback:
        try:
            p.compose(overlay, "up", "-d", "--no-deps", *names(plan["topology"]))
            plan["verification"] = verify(plan["topology"])
            if c.stopped:
                raise ValueError("Emergency stop during deployment")
            p.save(c.db, plan, "verified")
            return plan
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            plan["failure"] = str(exc)
            p.save(c.db, plan, "rolling_back")
    try:
        if any((folder / name).read_text() != value for name, value in files.items()):
            raise ValueError("Generated files changed; manual rollback review required")
        # Inspect every extant owned service before removing/recreating it.
        existing = set(p.presets.docker("ps", "-a", "--format", "{{.Names}}").splitlines())
        present = deepcopy(plan["topology"])
        present["spines"] = [n for n in present["spines"] if f"netguard-spine-{n}-1" in existing]
        present["leaves"] = [n for n in present["leaves"] if f"netguard-leaf-{n}-1" in existing]
        present["hosts"] = [h for h in present["hosts"] if f"netguard-host-{h['id']}-1" in existing]
        ownership(present, origins)
        p.compose(overlay, "rm", "--stop", "--force", *plan["added"])
        p.compose(old_overlay, "up", "-d", "--no-deps", *names(previous))
        extra_nets = set(render(plan["topology"], folder)[0]["networks"]) - set(
            render(previous, folder)[0]["networks"]
        )
        for key in extra_nets:
            ids = p.presets.docker(
                "network",
                "ls",
                "-q",
                "--filter",
                f"name=^netguard_{key}$",
                "--filter",
                "label=com.docker.compose.project=netguard",
                "--filter",
                f"label=com.docker.compose.network={key}",
            )
            if ids:
                p.presets.docker("network", "rm", ids)
        plan["rollback_verification"] = verify(previous)
        p.save(c.db, plan, "rolled_back")
    except (ValueError, OSError, subprocess.SubprocessError):
        p.save(c.db, plan, "needs_review")
        raise
    return plan

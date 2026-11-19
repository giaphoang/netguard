"""Bounded, approval-based expansion of the local two-spine Compose project."""

import hashlib
import json
import os
import sqlite3
import subprocess
import time

from . import presets

ROOT = presets.ROOT
BASE = ROOT / "fabric.yml"
ACTIVE = ("applying", "verified", "rolling_back", "needs_review")


def latest(db):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS expansions(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        row = conn.execute("SELECT data FROM expansions ORDER BY rowid DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def save(db, plan, phase):
    plan["phase"] = phase
    plan["events"].append({"phase": phase, "time": time.time()})
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO expansions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (plan["id"], json.dumps(plan)),
        )


def render(leaves, folder):
    if leaves not in ([3], [3, 4]):
        raise ValueError("The lab supports one expansion of one or two leaves")
    services, networks, configs = {}, {}, {}
    for spine in (1, 2):
        name = f"spine-{spine}"
        config = (ROOT / "lab" / (name + ".conf")).read_text()
        peers = "".join(f" neighbor 10.211.{spine}{n}.2 remote-as {65100+n}\n" for n in leaves)
        configs[name] = config.replace(" address-family", peers + " address-family", 1)
        services[name] = {"networks": {}, "volumes": [f"{folder}/{name}.conf:/etc/frr/frr.conf:ro"]}
    for n in leaves:
        name = f"leaf-{n}"
        configs[name] = (
            (ROOT / "lab/leaf-1.conf")
            .read_text()
            .replace("leaf-1", name)
            .replace("65101", str(65100 + n))
            .replace("10.213.2.1", f"10.213.2.{n}")
            .replace(".11.1", f".1{n}.1")
            .replace(".21.1", f".2{n}.1")
            .replace("10.212.1.0/28", f"10.214.0.{n}/32")
            .replace("router bgp", f"interface lo\n ip address 10.214.0.{n}/32\n!\nrouter bgp")
        )
        services[name] = {
            "image": "quay.io/frrouting/frr:10.1.1",
            "cap_add": ["NET_ADMIN", "SYS_ADMIN"],
            "mem_limit": "192m",
            "cpus": 0.5,
            "sysctls": {"net.ipv4.ip_forward": "1"},
            "networks": {},
            "volumes": [
                f"{folder}/{name}.conf:/etc/frr/frr.conf:ro",
                f"{ROOT}/lab/daemons:/etc/frr/daemons:ro",
            ],
        }
        for s in (1, 2):
            key = f"s{s}l{n}"
            net = f"10.211.{s}{n}"
            networks[key] = {
                "internal": True,
                "driver": "macvlan",
                "ipam": {"config": [{"subnet": net + ".0/28", "gateway": net + ".14"}]},
            }
            services[name]["networks"][key] = {"ipv4_address": net + ".2"}
            services[f"spine-{s}"]["networks"][key] = {"ipv4_address": net + ".1"}
    return {"services": services, "networks": networks}, configs


def material(db, plan):
    folder = db.parent.resolve() / "expansions" / plan["id"]
    from . import expansion

    overlay, configs = (
        expansion.render(plan["topology"], folder)
        if "topology" in plan
        else render(plan["leaves"], folder)
    )
    files = {n + ".conf": s for n, s in configs.items()}
    files["compose.json"] = json.dumps(overlay, indent=2)
    digest = hashlib.sha256(
        BASE.read_bytes() + json.dumps(files, sort_keys=True).encode()
    ).hexdigest()
    return folder, files, digest


def propose(c, count, started, kind="leaf", targets=None):
    from .expansion import propose as preview

    return preview(c, kind, count, targets or [], started)


def compose(overlay, *args):
    command = [
        "docker",
        "--context",
        os.environ.get("NETGUARD_CONTEXT", "default"),
        "compose",
        "-f",
        str(BASE),
    ]
    if overlay:
        command += ["-f", str(overlay)]
    subprocess.run([*command, *args], check=True, capture_output=True, text=True, timeout=100)


def verify(leaves):
    from .fabric import router, trace, command
    from .incidents import healthy

    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        routers = [
            router(n).model_dump()
            for n in ["spine-1", "spine-2", *[f"leaf-{n}" for n in [1, 2, *leaves]]]
        ]
        sample = {"routers": routers, "flows": [trace(1), trace(2)]}
        if healthy(sample):
            for n in leaves:
                command(
                    f"leaf-{n}", "ping", "-I", f"10.214.0.{n}", "-c", "1", "-W", "2", "10.212.1.1"
                )
            return
        time.sleep(1)
    raise ValueError("Routing and bidirectional connectivity did not converge")


def execute(c, identity, rollback=False):
    from . import expansion

    candidate = expansion.current(c.db) if rollback else latest(c.db)
    if candidate and candidate.get("version") == 3:
        return expansion.execute(c, identity, rollback)
    # Reserve the operation under lock, then release it during Compose and probes.
    with c.lock:
        plan = latest(c.db)
        allowed = ("verified", "needs_review") if rollback else ("preview",)
        if (
            not plan
            or plan["id"] != identity
            or plan["phase"] not in allowed
            or c.active
            or (c.stopped and not rollback)
        ):
            raise ValueError("Approval is stale or another operation is active")
        folder, files, digest = material(c.db, plan)
        if digest != plan["digest"]:
            raise ValueError("Configuration changed. Generate a new preview")
        presets.identify("link")
        leaves = plan["leaves"]
        names = [f"leaf-{n}" for n in leaves]
        overlay = folder / "compose.json"
        if not rollback:
            verify([])
            # Reserve names only when no existing resources occupy this plan's slots.
            containers = set(presets.docker("ps", "-a", "--format", "{{.Names}}").splitlines())
            networks = set(presets.docker("network", "ls", "--format", "{{.Name}}").splitlines())
            if any(f"netguard-{name}-1" in containers for name in names) or any(
                f"netguard_s{s}l{n}" in networks for n in leaves for s in (1, 2)
            ):
                raise ValueError("A requested resource already exists")
            for s in (1, 2):
                info = json.loads(presets.docker("inspect", f"netguard-spine-{s}-1"))[0]
                labels = info["Config"]["Labels"]
                if (
                    labels.get("com.docker.compose.project.config_files") != str(BASE)
                    or labels.get("com.docker.compose.project") != "netguard"
                    or labels.get("com.docker.compose.service") != f"spine-{s}"
                ):
                    raise ValueError("Spine ownership mismatch")
            folder.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (folder / name).write_text(content)
            compose(overlay, "config", "--quiet")
        save(c.db, plan, "rolling_back" if rollback else "applying")
    if not rollback:
        try:
            compose(overlay, "up", "-d", "--no-deps", "spine-1", "spine-2", *names)
            verify(leaves)
            if c.stopped:
                raise ValueError("Emergency stop requested during expansion")
            save(c.db, plan, "verified")
            return plan
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            plan["failure"] = str(exc)
            save(c.db, plan, "rolling_back")
    try:
        if any((folder / name).read_text() != content for name, content in files.items()):
            raise ValueError("Generated configuration changed; inspect before rollback")
        existing = set(presets.docker("ps", "-a", "--format", "{{.Names}}").splitlines())
        for name in [*names, "spine-1", "spine-2"]:
            container = f"netguard-{name}-1"
            if container not in existing:
                continue
            labels = json.loads(presets.docker("inspect", container))[0]["Config"]["Labels"]
            origins = {str(BASE) + "," + str(overlay)}
            if name.startswith("spine"):
                origins.add(str(BASE))
            if (
                labels.get("com.docker.compose.project.config_files") not in origins
                or labels.get("com.docker.compose.project") != "netguard"
                or labels.get("com.docker.compose.service") != name
            ):
                raise ValueError("Rollback target ownership changed")
        compose(overlay, "rm", "--stop", "--force", *names)
        compose(None, "up", "-d", "--no-deps", "spine-1", "spine-2")
        for n in leaves:
            for s in (1, 2):
                key = f"netguard_s{s}l{n}"
                ids = presets.docker(
                    "network",
                    "ls",
                    "-q",
                    "--filter",
                    f"name=^{key}$",
                    "--filter",
                    "label=com.docker.compose.project=netguard",
                    "--filter",
                    f"label=com.docker.compose.network=s{s}l{n}",
                )
                if ids:
                    presets.docker("network", "rm", ids)
        verify([])
        save(c.db, plan, "rolled_back")
    except (ValueError, OSError, subprocess.SubprocessError):
        save(c.db, plan, "needs_review")
        raise
    return plan

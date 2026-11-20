"""Network state adapters for FRRouting and an optional isolated OpenWrt runtime."""

import json
import os
import re
import subprocess
from ipaddress import IPv4Interface
from typing import Protocol

from . import investigations, presets, topology

OPENWRT_NODE = "openwrt-lab"
OPENWRT_CONTAINER = "netguard-adapter-openwrt"
LOOPBACK = "198.18.0.1/32"
SECTION = "network.netguard_lab"
FIELDS = {
    SECTION: "interface",
    f"{SECTION}.device": "lo",
    f"{SECTION}.proto": "static",
    f"{SECTION}.ipaddr": "198.18.0.1",
    f"{SECTION}.netmask": "255.255.255.255",
}
ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError)


class NetworkOperations(Protocol):
    def routing(self, row: dict, protocol: str) -> dict: ...
    def configuration(self, row: dict) -> dict: ...


def reading(data):
    return {"status": "observed" if data is not None else "unavailable", "data": data}


class FRRouting:
    def routing(self, row, protocol):
        from .fabric import command

        commands = {"ospf": "show ip ospf neighbor json", "isis": "show isis neighbor"}
        if protocol == "bgp":
            return reading(row.get("peers"))
        if protocol == "kernel":
            return reading(row.get("routes") or None)
        try:
            output = command(row["name"], "vtysh", "-c", commands[protocol])
            data = json.loads(output) if protocol == "ospf" else output
        except ERRORS:
            data = None
        return reading(
            None if isinstance(data, str) and (not data or data.startswith("%")) else data
        )

    def configuration(self, row):
        return investigations.configuration_facts([row["name"]])[row["name"]]


class OpenWrt:
    @staticmethod
    def owned():
        image = os.getenv("NETGUARD_OPENWRT_IMAGE_ID", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError(
                "Configure the pinned OpenWrt ARM64 lab image before using its adapter."
            )
        context = os.getenv("NETGUARD_CONTEXT", "default")
        socket = presets.docker(
            "context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}"
        )
        if not socket.startswith("unix://"):
            raise ValueError("The OpenWrt adapter requires a local Docker socket.")
        (info,) = json.loads(presets.docker("container", "inspect", OPENWRT_CONTAINER))
        host = info["HostConfig"]
        if (
            info["Image"] != image
            or (info["Config"]["Labels"] or {}).get("netguard.engineering.adapter") != "openwrt"
            or not info["State"]["Running"]
            or info["Mounts"]
            or host["Privileged"]
            or host["NetworkMode"] != "none"
            or not 0 < host["Memory"] <= 64 * 1024**2
            or not 0 < host["NanoCpus"] <= 250_000_000
            or host["CapDrop"] != ["ALL"]
            or {cap.removeprefix("CAP_") for cap in (host["CapAdd"] or [])}
            != {"NET_ADMIN", "NET_RAW"}
            or "no-new-privileges" not in host["SecurityOpt"]
            or not re.fullmatch(r"[a-f0-9]{64}", info["Id"])
        ):
            raise ValueError(
                "OpenWrt ownership, isolation, or resource limits differ from the approved lab scope."
            )
        return info["Id"]

    @staticmethod
    def command(identity, *args):
        return presets.docker("exec", identity, *args)

    def inventory(self):
        row = {
            "name": OPENWRT_NODE,
            "vendor": "OpenWrt",
            "aliases": ["OpenWrt"],
            "peers": None,
            "interfaces": "",
            "routes": "",
            "error": None,
        }
        try:
            identity = self.owned()
            row.update(
                identity=identity,
                interfaces=self.command(identity, "ip", "address", "show"),
                routes=self.command(identity, "ip", "-4", "route", "show", "table", "local"),
            )
        except ERRORS:
            row["error"] = "OpenWrt lab telemetry unavailable"
        return row

    def routing(self, row, protocol):
        return reading(row["routes"] if protocol == "kernel" and not row["error"] else None)

    def uci(self, identity):
        return {
            key: value.strip("'")
            for line in self.command(identity, "uci", "show", "network").splitlines()
            if "=" in line
            for key, value in [line.split("=", 1)]
            if key.startswith(("network.loopback.", SECTION + ".")) or key == SECTION
        }

    def configuration(self, row):
        facts = {"scope": "UCI loopback addresses"}
        try:
            values = self.uci(row["identity"])
            actual = set(topology.interfaces(row["interfaces"])["lo"]["addresses"])
            sections = ["network.loopback", *([SECTION] if SECTION in values else [])]
            intended = {
                str(IPv4Interface(f"{values[section + '.ipaddr']}/{values[section + '.netmask']}"))
                for section in sections
                if values.get(section + ".device") == "lo"
            }
            if not intended:
                raise ValueError("No intended IPv4 loopback address")
            actual = {address for address in actual if ":" not in address}
            return {
                **facts,
                "missing": sorted(intended - actual),
                "unexpected": sorted(actual - intended),
            }
        except ERRORS:
            return {**facts, "status": "unavailable"}

    def preview(self):
        row = self.inventory()
        if row["error"] or any(
            self.configuration(row).get(key) for key in ("status", "missing", "unexpected")
        ):
            raise ValueError("Verify the isolated OpenWrt loopback before preparing a change.")
        identity = row["identity"]
        if any(key in self.uci(identity) for key in FIELDS):
            raise ValueError("The test UCI section is already in use.")
        return identity, {
            "diff": "\n".join(f"+ {key}={value}" for key, value in FIELDS.items()),
            "risk": {
                "eligible": True,
                "reason": "Apply one static loopback in the isolated OpenWrt runtime.",
                "rollback": "Remove the new UCI section and verify the previous loopback addresses.",
            },
        }

    def configure(self, identity, before_write):
        current, _ = self.preview()
        if current != identity:
            raise ValueError("The OpenWrt lab container changed. Generate a fresh preview.")
        for key, value in FIELDS.items():
            before_write(OPENWRT_NODE)
            self.command(identity, "uci", "set", f"{key}={value}")
        before_write(OPENWRT_NODE)
        self.command(identity, "uci", "commit", "network")
        before_write(OPENWRT_NODE)
        self.command(identity, "ip", "-4", "address", "add", LOOPBACK, "dev", "lo")
        return self.verify(identity, expected=True)

    def verify(self, identity, *, expected):
        row = self.inventory()
        if row.get("identity") != identity or row["error"]:
            raise ValueError("The OpenWrt container is unavailable or changed.")
        facts = self.configuration(row)
        values = self.uci(identity)
        configured = all(values.get(key) == value for key, value in FIELDS.items())
        address_present = LOOPBACK in topology.interfaces(row["interfaces"])["lo"]["addresses"]
        if (
            configured != expected
            or address_present != expected
            or (not expected and any(key in values for key in FIELDS))
            or any(facts.get(key) for key in ("status", "missing", "unexpected"))
        ):
            raise ValueError(
                "The UCI and kernel loopback state differ from the approved configuration."
            )
        address = LOOPBACK.split("/")[0] if expected else "127.0.0.1"
        self.command(identity, "ping", "-c", "1", "-W", "2", address)
        return {
            "configuration": facts,
            "address": address,
            "icmp": "success",
            "routes": row["routes"],
        }

    def restore(self, identity):
        if self.owned() != identity:
            raise ValueError("The OpenWrt container changed. Review rollback manually.")
        current = {
            key: value
            for key, value in self.uci(identity).items()
            if key == SECTION or key.startswith(SECTION + ".")
        }
        if not current.items() <= FIELDS.items():
            raise ValueError("The OpenWrt test section contains unapproved values.")
        interfaces = self.command(identity, "ip", "address", "show", "dev", "lo")
        if LOOPBACK in topology.interfaces(interfaces)["lo"]["addresses"]:
            self.command(identity, "ip", "-4", "address", "delete", LOOPBACK, "dev", "lo")
        if current:
            self.command(identity, "uci", "delete", SECTION)
            self.command(identity, "uci", "commit", "network")
        return self.verify(identity, expected=False)


OPENWRT = OpenWrt()
FRR = FRRouting()


def from_inventory(row) -> NetworkOperations:
    return OPENWRT if row.get("vendor") == "OpenWrt" else FRR


def optional_inventory():
    return [OPENWRT.inventory()] if os.getenv("NETGUARD_OPENWRT_IMAGE_ID") else []


def wants_loopback_change(prompt):
    return all(
        re.search(pattern, prompt, re.I)
        for pattern in (
            r"\bopenwrt\b",
            r"\bloopback\b",
            r"\b(?:add|configure|design|plan|preview|propose)\b",
        )
    )

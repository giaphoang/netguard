"""Create a NoCloud ISO in a private external media directory on macOS."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("public_key", type=Path)
parser.add_argument("media_directory", type=Path)
args = parser.parse_args()
media = args.media_directory.resolve()
if media.is_relative_to(Path(__file__).resolve().parents[2]):
    parser.error("Choose a media directory outside the checkout")
if args.public_key.suffix != ".pub":
    parser.error("Provide a public .pub file")
pub = args.public_key.read_text().strip()
if len(pub.splitlines()) != 1 or not pub.startswith(("ssh-", "ecdsa-", "sk-")):
    parser.error("Expected one OpenSSH public key")
os.umask(0o077)
media.mkdir(parents=True, exist_ok=True)
out = media / "cidata.iso"
if out.exists():
    parser.error("A seed ISO already exists; reuse it or choose a new directory")
config = {
    "version": 1,
    "source": {"id": "ubuntu-server-minimal"},
    "identity": {"username": "netguard", "hostname": "netguard-vm", "password": "!"},
    "locale": "en_US.UTF-8",
    "ssh": {"install-server": True, "allow-pw": False, "authorized-keys": [pub]},
    "updates": "security",
    "storage": {"layout": {"name": "direct", "match": {"size": "largest"}}},
    "shutdown": "poweroff",
    "user-data": {
        "write_files": [
            {
                "path": "/etc/sudoers.d/90-netguard",
                "owner": "root:root",
                "permissions": "0440",
                "content": "netguard ALL=(ALL:ALL) NOPASSWD: ALL\n",
            }
        ]
    },
}
with tempfile.TemporaryDirectory(dir=media) as tmp:
    seed = Path(tmp)
    (seed / "user-data").write_text("#cloud-config\n" + json.dumps({"autoinstall": config}))
    (seed / "meta-data").write_text("instance-id: netguard-utm-01\nlocal-hostname: netguard-vm\n")
    subprocess.run(
        [
            "hdiutil",
            "makehybrid",
            "-iso",
            "-joliet",
            "-default-volume-name",
            "CIDATA",
            "-o",
            str(out),
            str(seed),
        ],
        check=True,
    )

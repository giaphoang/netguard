# NetGuard on Ubuntu ARM64

Run from the existing NetGuard checkout. Use the Mac for Git and UTM, and the guest for Docker and host-to-host validation.

## Host preparation

Run `scripts/vm/preflight.sh` for the architecture, capacity and resource checks. Install the stable UTM cask if necessary. In a directory outside Git, download Canonical’s ubuntu-24.04.5-live-server-arm64.iso. Create the CIDATA disk with the local SSH public key and run the provisioning wrapper.

    python3 scripts/vm/seed.py ~/.ssh/id_rsa.pub /path/to/private/media
    scripts/vm/provision-utm.sh /path/to/private/media
    utmctl start NetGuard-Ubuntu-ARM64

The wrapper checks the ISO against Canonical’s published digest, creates one Apple VM and shares only the checkout. The installer seed selects the largest drive in this new VM for direct partitioning, installs a minimal Ubuntu system and powers off. The account is netguard, authenticated by SSH key with local guest sudo access. Keep these media associated with this dedicated VM. Eject both removable media after installation and start the installed disk. Request a normal shutdown with `utmctl stop NetGuard-Ubuntu-ARM64`.

## Guest runtime

Confirm aarch64, Ubuntu 24.04, and that the VirtioFS tag is visible. Mount the checkout at /mnt/netguard. If UTM exposes a named subdirectory under the share root, mount the share at /mnt/utm-share and bind only that subdirectory to /mnt/netguard. Use nofail on persistent mounts. Verify the host sees a uniquely named test file before removing it. Install guest packages with `sudo /mnt/netguard/scripts/vm/bootstrap-ubuntu.sh`.

    cd /mnt/netguard
    sudo python3 -m venv /opt/netguard-venv
    sudo /opt/netguard-venv/bin/pip install -r requirements-dev.txt
    sudo docker compose -f fabric.yml up -d
    sudo scripts/vm/verify-network.sh

Use the guest-local virtual environment. The macOS .venv contains host binaries. The built web/dist is portable. To serve the dashboard:

    cd /mnt/netguard
    sudo env NETGUARD_TIMELINE_DB=/var/lib/netguard/timeline.sqlite NETGUARD_CONTEXT=default /opt/netguard-venv/bin/uvicorn api.fabric:app --host 127.0.0.1 --port 3000

From macOS forward a separate loopback port to avoid a collision with the existing Desktop demo:

    ssh -N -L 127.0.0.1:3001:127.0.0.1:3000 netguard@GUEST_PRIVATE_IP

Open http://127.0.0.1:3001 and inspect the live state and both trace directions. Run the failure exercise only after healthy validation. Supply a fresh evidence filename to preserve the original Desktop recording:

    sudo env NETGUARD_CONTEXT=default /opt/netguard-venv/bin/python -m scripts.verify_failover --output /var/tmp/netguard-ubuntu-failover.json

Take down only this fabric with `sudo docker compose -f fabric.yml down`. Recreate it with the same file and verify again, then repeat after guest reboot. Keep the virtual disk, ISO, NoCloud seed, credentials and full installation logs outside Git.

[UTM scripting](https://docs.getutm.app/scripting/cheat-sheet/) · [Canonical ARM64 media and checksums](https://cdimage.ubuntu.com/releases/24.04/release/) · [Subiquity autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/providing-autoinstall.html) · [Docker Ubuntu repository](https://docs.docker.com/engine/install/ubuntu/)

## Verified sequence and troubleshooting

After starting the installer, use `python3 scripts/vm/confirm-installer.py` to acknowledge the recognized Subiquity prompt through UTM’s dedicated serial PTY. A black display was observed while the text installer was active on hvc0. In this installation a first-boot netplan apply call timed out. The installer shell showed a valid DHCP lease. Running `netplan apply && snap restart subiquity.subiquity-server && exit` resumed installation. After the installer powers off, run `osascript scripts/vm/detach-install-media.applescript` and `utmctl start NetGuard-Ubuntu-ARM64`.

Mount from macOS with `ssh netguard@GUEST_PRIVATE_IP 'sudo bash -s' < scripts/vm/mount-project.sh`. This version exposes the repository as a named directory under the share tag. The script installs both mount entries. For the UI, run the repository web build once on macOS if web/dist is missing (`cd web && npm ci && npm run build`). Use the guest runtime commands above. Stop the foreground SSH tunnel with Ctrl-C. This run used a transient systemd unit named netguard-p0-dashboard; stop it with `sudo systemctl stop netguard-p0-dashboard` inside the guest. After a reboot start Compose and the dashboard again.

To repeat the measured scenario, run `sudo /opt/netguard-venv/bin/python scripts/vm/measure-failover.py /var/tmp/netguard-run-02` in the guest checkout. Choose a fresh output directory. The existing four-router topology, addressing, host routes and dashboard source work from the shared tree without an ext4 deployment copy.

[Validation results](P0_NETWORK_VALIDATION.md) · [Resource measurements](VM_RESOURCE_USAGE.md)

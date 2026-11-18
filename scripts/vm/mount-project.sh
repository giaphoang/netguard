#!/bin/bash
# Copy/run this script in the dedicated guest before the shared tree is mounted.
set -euo pipefail
[[ $EUID == 0 && $(hostname -s) == netguard-vm && $(uname -m) == aarch64 ]]
mnt=/mnt/netguard
if mountpoint -q "$mnt"; then
    [[ $(findmnt -n -o FSTYPE -T "$mnt") == virtiofs && -r "$mnt/docs/UPSTREAM.md" ]]
    exit 0
fi
mkdir -p "$mnt"
[[ -z $(ls -A "$mnt") ]]
mount -t virtiofs share "$mnt"
lines=('share /mnt/netguard virtiofs defaults,nofail 0 0')
if [[ ! -r "$mnt/fabric.yml" && -r "$mnt/netguard/fabric.yml" ]]; then
    mkdir -p /mnt/utm-share
    [[ -z $(ls -A /mnt/utm-share) ]]
    umount "$mnt"
    mount -t virtiofs share /mnt/utm-share
    mount --bind /mnt/utm-share/netguard "$mnt"
    lines=('share /mnt/utm-share virtiofs defaults,nofail 0 0'
           '/mnt/utm-share/netguard /mnt/netguard none bind,nofail,x-systemd.requires-mounts-for=/mnt/utm-share 0 0')
fi
[[ -r "$mnt/fabric.yml" && -r "$mnt/.git/HEAD" && -r "$mnt/docs/UPSTREAM.md" ]]
for line in "${lines[@]}"; do
    grep -Fqx "$line" /etc/fstab || echo "$line" >> /etc/fstab
done
systemctl daemon-reload
findmnt -T "$mnt" -o SOURCE,TARGET,FSTYPE

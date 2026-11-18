#!/bin/bash
set -euo pipefail
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == aarch64 && $(hostname -s) == netguard-vm ]]
echo "$PRETTY_NAME $(uname -r)"
nproc
free -h
df -h /
ip -br address
ip route
findmnt -T /mnt/netguard -o SOURCE,TARGET,FSTYPE
[[ $(findmnt -T /mnt/netguard -no FSTYPE) == virtiofs ]]
[[ -r /mnt/netguard/fabric.yml && -r /mnt/netguard/docs/UPSTREAM.md ]]
systemctl is-active docker
docker --context default info --format '{{.ServerVersion}} {{.Architecture}}'

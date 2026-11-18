#!/bin/bash
# Run as root inside the dedicated guest.
set -euo pipefail
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == aarch64 && $(hostname -s) == netguard-vm && $EUID == 0 ]]
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git python3-venv iproute2 iputils-ping traceroute tcpdump
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/netguard-docker.list
install -d /etc/docker
if [[ ! -e /etc/docker/daemon.json ]]; then
    echo '{"log-driver":"json-file","log-opts":{"max-size":"5m","max-file":"2"}}' > /etc/docker/daemon.json
fi
apt-get update -qq
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
systemctl enable --now docker
docker version
docker compose version
docker info --format '{{.OperatingSystem}} {{.Architecture}}'
docker pull --platform linux/arm64 quay.io/frrouting/frr:10.1.1
[[ $(docker image inspect quay.io/frrouting/frr:10.1.1 --format '{{.Architecture}}') == arm64 ]]

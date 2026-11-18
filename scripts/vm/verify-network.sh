#!/bin/bash
# Run inside the guest after starting the fabric. Read-only validation.
set -euo pipefail
[[ $(hostname -s) == netguard-vm && $(uname -m) == aarch64 ]]
cd /mnt/netguard
d=(docker --context default compose -f fabric.yml)
for router in spine-1 spine-2 leaf-1 leaf-2; do
    echo "=== $router ==="
    "${d[@]}" exec -T "$router" sysctl net.ipv4.ip_forward
    "${d[@]}" exec -T "$router" vtysh -c 'show bgp summary json'
    "${d[@]}" exec -T "$router" vtysh -c 'show ip route bgp'
    "${d[@]}" exec -T "$router" ip -4 route show
done
for i in 1 2; do
    host=host-$i
    dest=10.212.$((3-i)).2
    "${d[@]}" exec -T "$host" ip addr
    "${d[@]}" exec -T "$host" ip route
    "${d[@]}" exec -T "$host" ping -c 2 -W 2 "10.212.$i.1"
    "${d[@]}" exec -T "$host" ping -c 3 -W 2 "$dest"
    "${d[@]}" exec -T "$host" wget -T 3 -qO- "http://$dest:8080/"
    "${d[@]}" exec -T "$host" traceroute -n -w 1 -q 1 -m 6 "$dest"
done
NETGUARD_CONTEXT=default /opt/netguard-venv/bin/python -m scripts.smoke

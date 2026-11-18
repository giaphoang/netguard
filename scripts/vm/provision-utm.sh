#!/bin/bash
# Inputs are outside Git. The seed must target only this VM's new disk.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd -P)
read -r cores ram < <("$root/scripts/vm/preflight.sh")
media=${1:?Provide the directory containing the installer and NoCloud seed}
media=$(cd "$media" && pwd -P)
iso=ubuntu-24.04.5-live-server-arm64.iso
(cd "$media" && echo "f12484464c7d4d73e2167e0a7dbda1e669f8fae9bcf778dc0944937034b320cb  $iso" | shasum -a 256 -c -)
[[ -r "$media/cidata.iso" ]]
if utmctl list | awk 'NR>1 {print $NF}' | grep -Fxq NetGuard-Ubuntu-ARM64; then
    echo "VM already registered; review its configuration before starting."
    exit 0
fi
osascript "$root/scripts/vm/create.applescript" "$media/$iso" "$media/cidata.iso" "$root" "$cores" "$ram"

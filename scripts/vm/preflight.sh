#!/bin/bash
set -eu
[[ $(uname -sm) == "Darwin arm64" ]]
ram=$(sysctl -n hw.memsize)
cores=$(sysctl -n hw.logicalcpu)
free_kib=$(df -k "$HOME" | awk 'END {print $4}')
(( free_kib >= 45 * 1024 * 1024 )) || { echo "45 GiB free space required" >&2; exit 1; }
if (( ram >= 16 * 1024**3 )); then mib=6144; vmcpu=4; else mib=3072; vmcpu=2; fi
(( cores/2 >= vmcpu )) || vmcpu=$((cores/2))
(( vmcpu >= 1 && mib * 1048576 * 10 <= ram * 4 ))
echo "$vmcpu $mib"

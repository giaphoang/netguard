#!/bin/bash
set -euo pipefail
vm=NetGuard-Ubuntu-ARM64
if [[ $(utmctl status "$vm") != stopped ]]; then
    utmctl stop "$vm"
fi

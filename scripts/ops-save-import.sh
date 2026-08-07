#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/palworld-common.sh"
exec "$(dirname "$0")/save-import.py" "$@"

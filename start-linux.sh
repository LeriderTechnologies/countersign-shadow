#!/bin/sh
# Countersign Shadow (Linux). Demo: ./start-linux.sh --demo
cd "$(dirname "$0")" || exit 1
exec python3 -m countersign_shadow start "$@"

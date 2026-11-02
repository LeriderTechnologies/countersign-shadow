#!/bin/sh
# Countersign Shadow: dubbelklik om te starten (macOS). Niets kan betaald of verstuurd worden.
cd "$(dirname "$0")" || exit 1
python3 -m countersign_shadow start "$@" || { echo; echo "Python 3 niet gevonden of fout. Zie pilot/GIDS.md stap 1."; read -r _; }

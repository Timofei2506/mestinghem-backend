#!/usr/bin/env bash
# Convenience launcher for Linux/macOS.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f config.json ]; then
  echo "Creating config.json from template — edit it with your pairing details."
  cp config.example.json config.json
fi
exec python3 mestinghem_agent.py "$@"

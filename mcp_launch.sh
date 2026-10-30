#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Secrets come from the process environment only (connector key-value settings).
# var/ holds the session, not API keys.
if [[ -z "${TG_API_ID:-}" || -z "${TG_API_HASH:-}" ]]; then
  echo "TG_API_ID/TG_API_HASH not set; add them to the connector's secrets" >&2
  exit 1
fi
exec .venv/bin/python mcp_server.py

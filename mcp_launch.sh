#!/usr/bin/env bash
set -euo pipefail
cd /workspace/telegram
# Secrets come from the connector's environment (key-value settings).
# private/mcp.env is only a fallback when they are not set there.
if [[ -z "${TG_API_ID:-}" || -z "${TG_API_HASH:-}" ]]; then
  if [[ -f private/mcp.env ]]; then
    set -a
    # shellcheck disable=SC1091
    source private/mcp.env
    set +a
  else
    echo "TG_API_ID/TG_API_HASH not set; add them to the connector's secrets" >&2
    exit 1
  fi
fi
exec .venv/bin/python mcp_server.py

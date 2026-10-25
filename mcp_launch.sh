#!/usr/bin/env bash
set -euo pipefail
cd /workspace/telegram
set -a
# shellcheck disable=SC1091
source private/mcp.env
set +a
exec .venv/bin/python mcp_server.py

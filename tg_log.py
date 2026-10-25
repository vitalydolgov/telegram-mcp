"""File + stderr logging for MCP. Never log secrets (api_hash, 2FA, session bytes)."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
LOG_PATH = os.path.join(LOG_DIR, "mcp.log")

_CONFIGURED = False


def setup(level: str | None = None) -> logging.Logger:
    global _CONFIGURED
    log = logging.getLogger("tg_mcp")
    if _CONFIGURED:
        return log

    os.makedirs(LOG_DIR, exist_ok=True)
    os.chmod(LOG_DIR, 0o700)

    lvl_name = (level or os.environ.get("TG_MCP_LOG_LEVEL") or "INFO").upper()
    lvl = getattr(logging, lvl_name, logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(lvl)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.setLevel(lvl)

    log.setLevel(lvl)
    log.handlers.clear()
    log.addHandler(fh)
    log.addHandler(sh)
    log.propagate = False

    # Keep Telethon quieter; still see our logs
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon.network").setLevel(logging.WARNING)

    _CONFIGURED = True
    log.info("logging ready path=%s level=%s pid=%s", LOG_PATH, lvl_name, os.getpid())
    return log


def get() -> logging.Logger:
    if not _CONFIGURED:
        return setup()
    return logging.getLogger("tg_mcp")

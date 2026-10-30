"""Shared Telethon client factory (WSS + session + process lock)."""
from __future__ import annotations

import asyncio
import fcntl
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from telethon import TelegramClient

from tg_wss import ConnectionWSSObfuscated
import tg_log

BASE = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.join(BASE, "var")
SESSION = os.path.join(PRIV, "me")  # + .session
LOCK_PATH = os.path.join(PRIV, "me.lock")

_async_lock = asyncio.Lock()
_flock_fd = None
_lock_announced = False


def env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var {name}")
    return v


def lock_session() -> None:
    os.makedirs(PRIV, exist_ok=True)
    os.chmod(PRIV, 0o700)
    for name in (SESSION + ".session", SESSION + ".session-journal", LOCK_PATH):
        if os.path.exists(name):
            os.chmod(name, 0o600)


def _hold_flock() -> None:
    """Take the session flock once and keep it until this process exits.

    Releasing it after each tool call lets another process open the same
    SQLite session in the gap. The fd is never unlocked; the lock dies
    with the process.
    """
    global _flock_fd
    if _flock_fd is not None:
        return
    os.makedirs(PRIV, exist_ok=True)
    os.chmod(PRIV, 0o700)
    fd = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        raise RuntimeError(
            "Telegram session is already in use by another process "
            f"(lock {LOCK_PATH}). Stop that process before starting another."
        )
    os.chmod(LOCK_PATH, 0o600)
    _flock_fd = fd


def make_client() -> TelegramClient:
    return TelegramClient(
        SESSION,
        int(env("TG_API_ID")),
        env("TG_API_HASH"),
        device_model="Grok Bot MCP",
        app_version="1.0",
        connection=ConnectionWSSObfuscated,
    )


@asynccontextmanager
async def client(*, require_auth: bool = True) -> AsyncIterator[TelegramClient]:
    """Exclusive session access: process-lifetime flock + asyncio lock."""
    global _lock_announced
    log = tg_log.get()
    await _async_lock.acquire()
    try:
        await asyncio.to_thread(_hold_flock)
        if not _lock_announced:
            log.info("session lock held for process pid=%s", os.getpid())
            _lock_announced = True

        c = make_client()
        try:
            log.info("telethon connect…")
            await c.connect()
            authorized = await c.is_user_authorized()
            log.info("telethon connected authorized=%s", authorized)
            if require_auth and not authorized:
                raise RuntimeError(
                    "Not authorized. Call telegram_login_qr, then telegram_login_2fa."
                )
            yield c
        finally:
            try:
                await c.disconnect()
                log.info("telethon disconnected")
            except Exception as e:
                log.warning("disconnect error: %s: %s", type(e).__name__, e)
            finally:
                lock_session()
    except Exception as e:
        log.exception("client error: %s: %s", type(e).__name__, e)
        raise
    finally:
        _async_lock.release()

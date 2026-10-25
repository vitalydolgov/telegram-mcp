"""Shared Telethon client factory (WSS + session + process lock)."""
from __future__ import annotations

import asyncio
import fcntl
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from telethon import TelegramClient

from tg_wss import ConnectionWSSObfuscated
import tg_log

BASE = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.join(BASE, "private")
SESSION = os.path.join(PRIV, "me")  # + .session
LOCK_PATH = os.path.join(PRIV, "me.lock")

_async_lock = asyncio.Lock()


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
    """Exclusive session access: asyncio lock + flock (multi-process)."""
    log = tg_log.get()
    t0 = time.monotonic()
    await _async_lock.acquire()
    lock_fd = None
    try:
        os.makedirs(PRIV, exist_ok=True)
        lock_fd = open(LOCK_PATH, "a+", encoding="utf-8")
        # Blocking flock in thread so we don't freeze the event loop forever without logging
        log.info("session lock waiting pid=%s", os.getpid())
        await asyncio.to_thread(fcntl.flock, lock_fd.fileno(), fcntl.LOCK_EX)
        wait_ms = int((time.monotonic() - t0) * 1000)
        log.info("session lock acquired wait_ms=%s pid=%s", wait_ms, os.getpid())

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
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                lock_fd.close()
            except Exception:
                pass
            log.info("session lock released pid=%s", os.getpid())
        _async_lock.release()

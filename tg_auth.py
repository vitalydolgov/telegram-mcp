"""QR login + logout helpers. Never print secrets."""
from __future__ import annotations
import asyncio, base64, io, os, time
from typing import Any
import qrcode
from telethon.errors import SessionPasswordNeededError
import tg_client
import tg_log

QR_DIR = os.path.join(tg_client.BASE, "qr")

def _png_b64(url: str) -> str:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")

def _save_png(url: str) -> str:
    os.makedirs(QR_DIR, exist_ok=True)
    os.chmod(QR_DIR, 0o700)
    path = os.path.join(QR_DIR, "login.png")
    qrcode.make(url).save(path)
    os.chmod(path, 0o600)
    return path

async def auth_status() -> dict[str, Any]:
    async with tg_client.client(require_auth=False) as c:
        if await c.is_user_authorized():
            me = await c.get_me()
            return {
                "authorized": True,
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name,
                "last_name": me.last_name,
            }
        return {"authorized": False}

async def login_qr(wait_seconds: int = 25) -> dict[str, Any]:
    """Start/refresh QR login. Optionally wait up to wait_seconds for a scan."""
    log = tg_log.get()
    log.info("login_qr wait_seconds=%s", wait_seconds)
    wait_seconds = max(0, min(int(wait_seconds or 0), 55))
    async with tg_client.client(require_auth=False) as c:
        if await c.is_user_authorized():
            me = await c.get_me()
            return {
                "status": "already_authorized",
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name,
            }
        qr = await c.qr_login()
        path = _save_png(qr.url)
        payload = {
            "status": "pending",
            "url": qr.url,
            "qr_png_path": path,
            "qr_png_base64": _png_b64(qr.url),
            "expires_in": max(0, int(qr.expires.timestamp() - time.time())),
            "hint": "Open Telegram → Settings → Devices → Link Desktop Device, or open the tg:// url within ~30s.",
        }
        if wait_seconds <= 0:
            return payload
        try:
            await asyncio.wait_for(qr.wait(), timeout=wait_seconds)
            me = await c.get_me()
            return {
                "status": "authorized",
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name,
            }
        except asyncio.TimeoutError:
            payload["status"] = "pending"
            payload["expires_in"] = max(0, int(qr.expires.timestamp() - time.time()))
            payload["note"] = f"No scan within {wait_seconds}s; call telegram_login_qr again for a fresh code."
            return payload
        except SessionPasswordNeededError:
            return {
                "status": "need_2fa",
                "hint": "QR accepted. Set TG_2FA_PASSWORD in env, then call telegram_login_2fa.",
            }

async def login_2fa() -> dict[str, Any]:
    log = tg_log.get()
    log.info("login_2fa start has_password_env=%s", bool(os.environ.get("TG_2FA_PASSWORD", "").strip()))
    pw = os.environ.get("TG_2FA_PASSWORD", "").strip()
    if not pw:
        return {
            "status": "need_password_env",
            "hint": "Set TG_2FA_PASSWORD in the process env (masked secret), then call again. Do not put the password in chat.",
        }
    async with tg_client.client(require_auth=False) as c:
        if await c.is_user_authorized():
            me = await c.get_me()
            return {"status": "already_authorized", "id": me.id, "username": me.username}
        try:
            await c.sign_in(password=pw)
        except Exception as e:
            # Never include password in error text to caller beyond type name
            return {"status": "error", "error": type(e).__name__}
        me = await c.get_me()
        return {
            "status": "authorized",
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
        }

async def logout() -> dict[str, Any]:
    log = tg_log.get()
    log.info("logout start")
    async with tg_client.client(require_auth=False) as c:
        was = await c.is_user_authorized()
        if was:
            await c.log_out()
    # Wipe local session files
    removed = []
    for name in (tg_client.SESSION + ".session", tg_client.SESSION + ".session-journal"):
        if os.path.exists(name):
            os.remove(name)
            removed.append(os.path.basename(name))
    for f in ("login.png",):
        p = os.path.join(QR_DIR, f)
        if os.path.exists(p):
            os.remove(p)
            removed.append(f)
    return {"status": "logged_out", "was_authorized": was, "removed": removed}

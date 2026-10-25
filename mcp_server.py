#!/usr/bin/env python3
"""Telegram user-API MCP (stdio) — MVP via Telethon + WSS.

Coherent MVP tool set:
  Auth:    telegram_whoami, telegram_login_qr, telegram_login_2fa, telegram_logout
  Read:    telegram_list_dialogs, telegram_get_messages, telegram_mark_read
  Write:   telegram_send_message

Secrets in env / private/mcp.env only. Never print api_hash / 2FA / session.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from mcp.server.mcpserver import MCPServer
from telethon.tl.types import User, Chat, Channel

import tg_auth
import tg_client
import tg_settings
import tg_log

MSK = ZoneInfo("Europe/Moscow")

log = tg_log.get()


mcp = MCPServer(
    name="grokbot-telegram-mcp",
    title="Grok Bot Telegram MCP",
    instructions=(
        "MVP Telegram user-API (MTProto) via Telethon + WSS. "
        "Auth: telegram_whoami; login: telegram_login_qr "
        "(→ telegram_login_2fa if need_2fa) → telegram_logout. "
        "Read: telegram_list_dialogs, telegram_get_messages, telegram_mark_read. "
        "Write: telegram_send_message. "
        "chat: username, @handle, numeric id, or 'me'. Times Europe/Moscow. "
        "robot_prefix_enabled in config.json prefixes every send. "
        "Secrets only via env (TG_API_ID, TG_API_HASH, optional TG_2FA_PASSWORD)."
    ),
)


def _dialog_type(d) -> str:
    if d.is_channel and not d.is_group:
        return "channel"
    if d.is_group:
        return "group"
    return "private"


def _entity_ref(entity) -> dict[str, Any]:
    out: dict[str, Any] = {"id": getattr(entity, "id", None)}
    uname = getattr(entity, "username", None)
    if uname:
        out["username"] = uname
    if isinstance(entity, User):
        out["type"] = "user"
        parts = [entity.first_name or "", entity.last_name or ""]
        out["name"] = " ".join(p for p in parts if p).strip() or (uname or str(entity.id))
    elif isinstance(entity, Channel):
        out["type"] = "channel" if not entity.megagroup and not entity.gigagroup else "group"
        out["name"] = entity.title or (uname or str(entity.id))
    elif isinstance(entity, Chat):
        out["type"] = "group"
        out["name"] = entity.title or str(entity.id)
    else:
        out["type"] = type(entity).__name__
        out["name"] = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(out["id"])
    return out


def _apply_robot_prefix(text: str) -> str:
    cfg = tg_settings.load()
    if not cfg.get("robot_prefix_enabled", True):
        return text
    emoji = (cfg.get("robot_emoji") or "🤖").strip() or "🤖"
    prefix = emoji if emoji.endswith(" ") else emoji + " "
    if text.startswith(prefix) or text.startswith(emoji):
        return text
    return prefix + text


def _msk_day_start(days: int) -> datetime:
    days = max(1, days)
    today = datetime.now(MSK).date()
    start_date = today - timedelta(days=days - 1)
    return datetime.combine(start_date, time.min, tzinfo=MSK)


def _fmt_msk(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S %Z")


async def _resolve(c, chat: str):
    chat = (chat or "").strip()
    if not chat:
        raise ValueError("chat is required")
    if chat.lower() in ("me", "saved", "saved messages"):
        return "me"
    if chat.lstrip("-").isdigit():
        return int(chat)
    return chat


# ── Auth (full lifecycle) ──────────────────────────────────────────

@mcp.tool(description="Current account (get_me). Requires login; errors if not authorized.")
async def telegram_whoami() -> dict[str, Any]:
    log.info("tool telegram_whoami start")
    async with tg_client.client() as c:
        me = await c.get_me()
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone,
        }


@mcp.tool(
    description=(
        "QR login. Returns tg://login url + PNG (path + base64). "
        "wait_seconds 0–55 to wait for scan. "
        "Statuses: pending | authorized | need_2fa | already_authorized."
    )
)
async def telegram_login_qr(wait_seconds: int = 25) -> dict[str, Any]:
    log.info("tool telegram_login_qr start wait_seconds=%s", wait_seconds)
    return await tg_auth.login_qr(wait_seconds=wait_seconds)


@mcp.tool(
    description=(
        "Finish login when status was need_2fa. "
        "Uses TG_2FA_PASSWORD from env only — never pass the password as an argument."
    )
)
async def telegram_login_2fa() -> dict[str, Any]:
    log.info("tool telegram_login_2fa start")
    return await tg_auth.login_2fa()


@mcp.tool(description="Log out and delete local session files.")
async def telegram_logout() -> dict[str, Any]:
    log.info("tool telegram_logout start")
    return await tg_auth.logout()


# ── Read ───────────────────────────────────────────────────────────

@mcp.tool(description="List dialogs: name, id, username, unread, type.")
async def telegram_list_dialogs(limit: int = 20) -> dict[str, Any]:
    log.info("tool telegram_list_dialogs start limit=%s", limit)
    limit = max(1, min(int(limit or 20), 100))
    rows: list[dict[str, Any]] = []
    async with tg_client.client() as c:
        async for d in c.iter_dialogs(limit=limit):
            ent = d.entity
            rows.append({
                "name": d.name,
                "id": d.id,
                "username": getattr(ent, "username", None),
                "unread": d.unread_count,
                "type": _dialog_type(d),
            })
    return {"count": len(rows), "dialogs": rows}


@mcp.tool(
    description=(
        "Read messages from a chat. chat: username/@handle/id/'me'. "
        "days: calendar days Europe/Moscow (default from config history_days, usually 1=today). "
        "mark_read=true marks the chat read after fetching."
    )
)
async def telegram_get_messages(
    chat: str,
    limit: int = 30,
    days: int | None = None,
    mark_read: bool = False,
) -> dict[str, Any]:
    log.info("tool telegram_get_messages start chat=%s limit=%s days=%s mark_read=%s", chat, limit, days, mark_read)
    limit = max(1, min(int(limit or 30), 200))
    if days is None:
        days = int(tg_settings.load().get("history_days") or 1)
    days = max(1, min(int(days), 30))
    cutoff = _msk_day_start(days)
    messages: list[dict[str, Any]] = []
    async with tg_client.client() as c:
        target = await _resolve(c, chat)
        entity = await c.get_entity(target)
        ref = (
            {"id": "me", "name": "Saved Messages", "type": "saved"}
            if target == "me"
            else _entity_ref(entity)
        )
        async for m in c.iter_messages(target, limit=limit * 3):
            if m.date is None:
                continue
            mdate = m.date if m.date.tzinfo else m.date.replace(tzinfo=timezone.utc)
            if mdate.astimezone(MSK) < cutoff:
                break
            text = m.message if m.message else ("[media]" if m.media else "[empty]")
            messages.append({
                "id": m.id,
                "time_msk": _fmt_msk(m.date),
                "direction": "out" if m.out else "in",
                "text": text,
            })
            if len(messages) >= limit:
                break
        if mark_read:
            await c.send_read_acknowledge(target)
    messages.reverse()
    return {
        "chat": ref,
        "days": days,
        "cutoff_msk": cutoff.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "marked_read": bool(mark_read),
        "count": len(messages),
        "messages": messages,
    }


@mcp.tool(description="Mark a chat as read. chat: username/@handle/id/'me'.")
async def telegram_mark_read(chat: str) -> dict[str, Any]:
    log.info("tool telegram_mark_read start chat=%s", chat)
    async with tg_client.client() as c:
        target = await _resolve(c, chat)
        entity = await c.get_entity(target)
        ref = (
            {"id": "me", "name": "Saved Messages", "type": "saved"}
            if target == "me"
            else _entity_ref(entity)
        )
        await c.send_read_acknowledge(target)
        return {"ok": True, "chat": ref}


# ── Write ──────────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Send text to chat (username/@handle/id/'me'). "
        "If config robot_prefix_enabled, prefixes robot_emoji."
    )
)
async def telegram_send_message(chat: str, text: str) -> dict[str, Any]:
    log.info("tool telegram_send_message start chat=%s text_len=%s", chat, len(str(text)) if text is not None else 0)
    if text is None or str(text).strip() == "":
        raise ValueError("text is required")
    text = str(text)
    final = _apply_robot_prefix(text)
    async with tg_client.client() as c:
        target = await _resolve(c, chat)
        entity = await c.get_entity(target)
        ref = (
            {"id": "me", "name": "Saved Messages", "type": "saved"}
            if target == "me"
            else _entity_ref(entity)
        )
        msg = await c.send_message(target, final)
        return {
            "sent_to": ref,
            "message_id": msg.id,
            "time_msk": _fmt_msk(msg.date),
            "text": final,
            "robot_prefix_applied": final != text,
        }


async def _smoke() -> int:
    print("=== whoami ===", await telegram_whoami())
    print("=== list_dialogs ===", await telegram_list_dialogs(limit=2))
    print("=== get_messages me ===", await telegram_get_messages(chat="me", limit=2, days=7))
    print("=== mark_read me ===", await telegram_mark_read(chat="me"))
    print("SMOKE_OK")
    return 0


def main() -> None:
    tg_log.setup()
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke":
        raise SystemExit(asyncio.run(_smoke()))
    log.info("mcp stdio starting")
    try:
        asyncio.run(mcp.run_stdio_async())
    except Exception:
        log.exception("mcp stdio crashed")
        raise
    finally:
        log.info("mcp stdio stopped")


if __name__ == "__main__":
    main()

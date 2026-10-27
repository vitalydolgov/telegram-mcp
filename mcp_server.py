#!/usr/bin/env python3
"""Telegram user-API MCP (stdio) — MVP via Telethon + WSS.

Coherent MVP tool set:
  Auth:    telegram_whoami, telegram_login_qr, telegram_login_2fa, telegram_logout
  Read:    telegram_list_dialogs, telegram_get_messages, telegram_mark_read
  Buttons: telegram_press_button (dry_run by default)
  Write:   telegram_send_message

Secrets in env / private/mcp.env only. Never print api_hash / 2FA / session.
"""
from __future__ import annotations

import asyncio
import base64
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
        "Buttons: telegram_press_button (dry_run=true default; press only with explicit user consent). "
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


# ── Message rendering helpers ──────────────────────────────────────

_KIND_BY_NAME = {
    "url": "url", "callback": "callback", "webview": "webapp", "simplewebview": "webapp",
    "urlauth": "url_auth", "inputurlauth": "url_auth", "switchinline": "switch_inline",
    "game": "game", "buy": "buy", "requestphone": "request_phone",
    "requestgeolocation": "request_geo", "copy": "copy", "userprofile": "user_profile",
    "inputuserprofile": "user_profile", "default": "text", "requestpoll": "request_poll",
    "requestpeer": "request_peer",
}


def _norm_kind(cls_name: str) -> str:
    n = cls_name
    for pre in ("InputInlineButtonType", "InlineButtonType", "InputKeyboardButton",
                "KeyboardButton", "ButtonType"):
        if n.startswith(pre):
            n = n[len(pre):]
            break
    return _KIND_BY_NAME.get(n.lower(), "other" if n else "text")


def _decode_data(data) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, str):
        return {"data": data}
    try:
        return {"data": bytes(data).decode("utf-8")}
    except UnicodeDecodeError:
        return {"data_b64": base64.b64encode(bytes(data)).decode("ascii")}


def _button_info(btn) -> dict[str, Any]:
    """Normalize old-style KeyboardButton* and new-style Keyboard(Inline)Button(.type)."""
    src = getattr(btn, "type", None)
    if src is None or not hasattr(src, "to_dict"):
        src = btn
    kind = _norm_kind(type(src).__name__)
    if src is btn and type(btn).__name__ in ("KeyboardButton", "KeyboardInlineButton"):
        kind = "text"
    out: dict[str, Any] = {"text": getattr(btn, "text", "") or "", "kind": kind}
    url = getattr(src, "url", None)
    if url:
        out["url"] = url
    if kind in ("callback", "buy"):
        out.update(_decode_data(getattr(src, "data", None)))
        if getattr(src, "requires_password", None):
            out["requires_password"] = True
    if kind == "switch_inline":
        q = getattr(src, "query", None)
        if q:
            out["query"] = q
    if kind == "copy" and getattr(src, "copy_text", None):
        out["copy_text"] = src.copy_text
    if kind == "user_profile" and getattr(src, "user_id", None):
        out["user_id"] = src.user_id
    return out


def _buttons(m) -> list[dict[str, Any]]:
    rm = getattr(m, "reply_markup", None)
    rows = getattr(rm, "rows", None) if rm is not None else None
    if not rows:
        return []
    markup = "reply" if "Keyboard" in type(rm).__name__ and "Inline" not in type(rm).__name__ else "inline"
    out: list[dict[str, Any]] = []
    idx = 0
    for r, row in enumerate(rows):
        for col, btn in enumerate(getattr(row, "buttons", []) or []):
            info = {"index": idx, "row": r, "col": col}
            info.update(_button_info(btn))
            if markup == "reply" and info["kind"] == "other":
                info["kind"] = "text"
            info["_raw"] = btn
            out.append(info)
            idx += 1
    return out


def _public_buttons(btns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in b.items() if not k.startswith("_")} for b in btns]


def _sender(m) -> dict[str, Any] | None:
    s = getattr(m, "sender", None)
    if s is None:
        sid = getattr(m, "sender_id", None)
        return {"id": sid} if sid is not None else None
    ref = _entity_ref(s)
    return {k: ref[k] for k in ("id", "name", "username") if ref.get(k)}


def _media(m) -> dict[str, Any] | None:
    media = getattr(m, "media", None)
    if media is None:
        return None
    n = type(media).__name__.replace("MessageMedia", "")
    kind = n.lower() or "other"
    out: dict[str, Any] = {}
    if kind == "webpage":
        wp = getattr(media, "webpage", None)
        out["kind"] = "webpage"
        for k in ("url", "title", "site_name"):
            v = getattr(wp, k, None)
            if v:
                out[k] = v
        return out
    if kind == "document":
        if getattr(m, "video", None):
            kind = "video"
        elif getattr(m, "voice", None):
            kind = "voice"
        elif getattr(m, "audio", None):
            kind = "audio"
        elif getattr(m, "sticker", None):
            kind = "sticker"
        elif getattr(m, "gif", None):
            kind = "gif"
        f = getattr(m, "file", None)
        if f is not None:
            if getattr(f, "name", None):
                out["file_name"] = f.name
            if getattr(f, "mime_type", None):
                out["mime"] = f.mime_type
    out["kind"] = kind
    return out


def _links(m) -> list[dict[str, Any]]:
    try:
        pairs = m.get_entities_text() or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for ent, span in pairs:
        n = type(ent).__name__
        if n == "MessageEntityTextUrl":
            out.append({"text": span, "url": ent.url})
        elif n == "MessageEntityUrl":
            out.append({"text": span, "url": span})
        elif n == "MessageEntityMention":
            out.append({"text": span, "kind": "mention"})
        elif n == "MessageEntityMentionName":
            out.append({"text": span, "kind": "mention", "user_id": ent.user_id})
    return out


def _render_message(m, is_private: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": m.id,
        "time_msk": _fmt_msk(m.date),
        "direction": "out" if m.out else "in",
    }
    if not is_private and not m.out:
        snd = _sender(m)
        if snd:
            row["sender"] = snd
    if m.message:
        row["text"] = m.message
    media = _media(m)
    if media:
        row["media"] = media
    if not m.message and not media:
        row["text"] = "[service]" if getattr(m, "action", None) else "[empty]"
    links = _links(m)
    if links:
        row["links"] = links
    btns = _public_buttons(_buttons(m))
    if btns:
        row["buttons"] = btns
    return row


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
        "mark_read=true marks the chat read after fetching. "
        "Each message: id, time_msk, direction, sender (groups), text, media "
        "(kind, caption is text; webpage url/title), links, buttons "
        "(index,row,col,text,kind,url/data)."
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
        is_private = target == "me" or isinstance(entity, User)
        async for m in c.iter_messages(target, limit=limit * 3):
            if m.date is None:
                continue
            mdate = m.date if m.date.tzinfo else m.date.replace(tzinfo=timezone.utc)
            if mdate.astimezone(MSK) < cutoff:
                break
            messages.append(_render_message(m, is_private))
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


@mcp.tool(
    description=(
        "Resolve (and optionally press) a button on a message. button: flat index "
        "(digits, as in telegram_get_messages buttons[].index) or button text "
        "(exact, then case-insensitive). dry_run=true (default) only shows the button "
        "and what would happen. ONLY call with dry_run=false after explicit user consent. "
        "dry_run=false: callback buttons send GetBotCallbackAnswer and return the bot "
        "answer; url/webapp buttons are never opened, the url is returned; other kinds "
        "are refused."
    )
)
async def telegram_press_button(
    chat: str, message_id: int, button: str, dry_run: bool = True
) -> dict[str, Any]:
    log.info("tool telegram_press_button start chat=%s message_id=%s button=%s dry_run=%s",
             chat, message_id, button, dry_run)
    from telethon.tl import functions
    async with tg_client.client() as c:
        target = await _resolve(c, chat)
        m = await c.get_messages(target, ids=int(message_id))
        if m is None:
            raise ValueError(f"message {message_id} not found")
        btns = _buttons(m)
        if not btns:
            raise ValueError("message has no buttons")
        key = str(button).strip()
        chosen = None
        if key.isdigit():
            i = int(key)
            if i >= len(btns):
                raise ValueError(f"button index {i} out of range (0..{len(btns) - 1})")
            chosen = btns[i]
        else:
            chosen = next((b for b in btns if b["text"] == key), None) or next(
                (b for b in btns if b["text"].strip().lower() == key.lower()), None)
        if chosen is None:
            raise ValueError(f"button not found: {key!r}; available: {[b['text'] for b in btns]}")
        info = _public_buttons([chosen])[0]
        kind = info["kind"]
        if kind == "callback":
            action = "would send a callback query to the bot"
        elif kind in ("url", "webapp", "url_auth"):
            action = "link only; nothing is opened, the url is returned"
        else:
            action = f"unsupported kind {kind!r}; pressing would be refused"
        res: dict[str, Any] = {"chat": chat, "message_id": m.id, "button": info,
                               "dry_run": bool(dry_run), "action": action}
        if dry_run:
            return res
        if kind in ("url", "webapp", "url_auth"):
            res["result"] = {"link": info.get("url")}
            return res
        if kind != "callback":
            raise ValueError(f"pressing buttons of kind {kind!r} is not supported")
        raw = chosen["_raw"]
        src = getattr(raw, "type", None) or raw
        data = getattr(src, "data", None)
        peer = await c.get_input_entity(target)
        ans = await c(functions.messages.GetBotCallbackAnswerRequest(
            peer=peer, msg_id=m.id, data=data))
        out = {}
        for k in ("message", "url", "alert"):
            v = getattr(ans, k, None)
            if v:
                out[k] = v
        res["result"] = out or {"answer": "empty"}
        return res


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

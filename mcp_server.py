#!/usr/bin/env python3
"""Telegram user-API MCP (stdio) — MVP via Telethon + WSS.

Coherent MVP tool set:
  Auth:    telegram_whoami, telegram_login_qr, telegram_login_2fa, telegram_logout
  Read:    telegram_list_dialogs, telegram_get_messages, telegram_mark_read
  Buttons: telegram_press_button (dry_run by default)
  Write:   telegram_send_message, telegram_send_file
  Membership: telegram_join_channel, telegram_leave_channel
  Folders: telegram_list_folders, telegram_folder_include, telegram_folder_exclude

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
from telethon.tl.types import User, Chat, Channel, ChatInviteAlready, ChatInvite, ChatInvitePeek
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.functions.messages import DeleteChatUserRequest
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.tl.types import InputUserSelf, DialogFilter, TextWithEntities

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
        "Write: telegram_send_message, telegram_send_file (local file + optional caption). "
        "Membership: telegram_join_channel / telegram_leave_channel "
        "(public @username, t.me link, or invite t.me/+hash). "
        "Folders: telegram_list_folders; telegram_folder_include / telegram_folder_exclude "
        "(folder id or title). "
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



def _invite_hash(channel: str) -> str | None:
    """Extract invite hash from +HASH, t.me/+HASH, t.me/joinchat/HASH, or bare hash with +."""
    s = (channel or "").strip()
    if not s:
        return None
    for prefix in (
        "https://t.me/+",
        "http://t.me/+",
        "t.me/+",
        "https://telegram.me/+",
        "http://telegram.me/+",
        "telegram.me/+",
        "https://t.me/joinchat/",
        "http://t.me/joinchat/",
        "t.me/joinchat/",
        "https://telegram.me/joinchat/",
        "http://telegram.me/joinchat/",
        "telegram.me/joinchat/",
    ):
        if s.lower().startswith(prefix):
            h = s[len(prefix):].split("?")[0].split("/")[0].strip()
            return h or None
    if s.startswith("+") and len(s) > 1 and "/" not in s:
        return s[1:].strip() or None
    return None


def _public_username(channel: str) -> str | None:
    s = (channel or "").strip()
    if not s:
        return None
    for prefix in (
        "https://t.me/",
        "http://t.me/",
        "t.me/",
        "https://telegram.me/",
        "http://telegram.me/",
        "telegram.me/",
    ):
        if s.lower().startswith(prefix):
            rest = s[len(prefix):].split("?")[0].split("/")[0].strip()
            if rest.startswith("+") or rest.lower() == "joinchat":
                return None
            return rest.lstrip("@") or None
    if s.startswith("@") or (not s.lstrip("-").isdigit() and "/" not in s and " " not in s):
        return s.lstrip("@")
    return None


def _invite_preview(invite) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": type(invite).__name__}
    if isinstance(invite, ChatInviteAlready):
        out["already_participant"] = True
        out["channel"] = _entity_ref(invite.chat)
    elif isinstance(invite, (ChatInvite, ChatInvitePeek)):
        out["already_participant"] = False
        out["title"] = getattr(invite, "title", None)
        out["participants"] = getattr(invite, "participants_count", None)
        out["channel"] = bool(getattr(invite, "channel", False))
        out["megagroup"] = bool(getattr(invite, "megagroup", False))
        out["request_needed"] = bool(getattr(invite, "request_needed", False))
    return out

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




def _folder_title(f) -> str:
    t = getattr(f, "title", None)
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    return getattr(t, "text", None) or str(t)


def _peer_ids(p) -> dict[str, Any]:
    name = type(p).__name__
    if "User" in name:
        return {"kind": "user", "id": getattr(p, "user_id", None)}
    if "Channel" in name:
        return {"kind": "channel", "id": getattr(p, "channel_id", None)}
    if "Chat" in name:
        return {"kind": "chat", "id": getattr(p, "chat_id", None)}
    return {"kind": name}


def _peer_match(p, input_peer) -> bool:
    a, b = _peer_ids(p), _peer_ids(input_peer)
    return a.get("kind") == b.get("kind") and a.get("id") is not None and a.get("id") == b.get("id")


def _as_text_title(title) -> TextWithEntities:
    if isinstance(title, TextWithEntities):
        return title
    if isinstance(title, str):
        return TextWithEntities(text=title, entities=[])
    text_v = getattr(title, "text", None) or str(title)
    entities = list(getattr(title, "entities", None) or [])
    return TextWithEntities(text=text_v, entities=entities)


async def _get_dialog_filters(c) -> list:
    res = await c(GetDialogFiltersRequest())
    return list(getattr(res, "filters", None) or [])


async def _find_folder(c, folder: str) -> DialogFilter:
    folder = (folder or "").strip()
    if not folder:
        raise ValueError("folder is required (id or title)")
    found: list[DialogFilter] = []
    for f in await _get_dialog_filters(c):
        if not isinstance(f, DialogFilter):
            continue
        if str(f.id) == folder or _folder_title(f).casefold() == folder.casefold():
            found.append(f)
    if not found:
        raise ValueError(f"folder not found: {folder}")
    if len(found) > 1:
        raise ValueError(
            "ambiguous folder title; use id: "
            + ", ".join(f"{_folder_title(f)} ({f.id})" for f in found)
        )
    return found[0]


def _folder_summary(f: DialogFilter, *, with_peers: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f.id,
        "title": _folder_title(f),
        "emoticon": getattr(f, "emoticon", None),
        "include_count": len(f.include_peers or []),
        "exclude_count": len(f.exclude_peers or []),
        "pinned_count": len(f.pinned_peers or []),
        "flags": {
            "contacts": bool(f.contacts),
            "non_contacts": bool(f.non_contacts),
            "groups": bool(f.groups),
            "broadcasts": bool(f.broadcasts),
            "bots": bool(f.bots),
            "exclude_muted": bool(f.exclude_muted),
            "exclude_read": bool(f.exclude_read),
            "exclude_archived": bool(f.exclude_archived),
        },
    }
    if with_peers:
        out["include_peers"] = [_peer_ids(p) for p in (f.include_peers or [])]
        out["exclude_peers"] = [_peer_ids(p) for p in (f.exclude_peers or [])]
        out["pinned_peers"] = [_peer_ids(p) for p in (f.pinned_peers or [])]
    return out


def _rebuild_folder(
    f: DialogFilter,
    *,
    include_peers=None,
    exclude_peers=None,
    pinned_peers=None,
) -> DialogFilter:
    return DialogFilter(
        id=f.id,
        title=_as_text_title(f.title),
        pinned_peers=list(f.pinned_peers if pinned_peers is None else pinned_peers),
        include_peers=list(f.include_peers if include_peers is None else include_peers),
        exclude_peers=list(f.exclude_peers if exclude_peers is None else exclude_peers),
        contacts=f.contacts,
        non_contacts=f.non_contacts,
        groups=f.groups,
        broadcasts=f.broadcasts,
        bots=f.bots,
        exclude_muted=f.exclude_muted,
        exclude_read=f.exclude_read,
        exclude_archived=f.exclude_archived,
        title_noanimate=getattr(f, "title_noanimate", None),
        emoticon=f.emoticon,
        color=getattr(f, "color", None),
    )


# ── Membership ─────────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Join a public channel/supergroup (@username or t.me/name) or an invite link "
        "(t.me/+hash / t.me/joinchat/hash). "
        "Private invites that need admin approval return request_needed."
    )
)
async def telegram_join_channel(channel: str) -> dict[str, Any]:
    log.info("tool telegram_join_channel start channel=%s", channel)
    channel = (channel or "").strip()
    if not channel:
        raise ValueError("channel is required")
    inv = _invite_hash(channel)
    async with tg_client.client() as c:
        if inv:
            preview = await c(CheckChatInviteRequest(hash=inv))
            if isinstance(preview, ChatInviteAlready):
                return {
                    "joined": False,
                    "already_participant": True,
                    "via": "invite",
                    "channel": _entity_ref(preview.chat),
                }
            updates = await c(ImportChatInviteRequest(hash=inv))
            chats = getattr(updates, "chats", None) or []
            entity = chats[0] if chats else None
            return {
                "joined": True,
                "via": "invite",
                "channel": _entity_ref(entity) if entity else None,
                "request_needed": bool(getattr(preview, "request_needed", False)),
            }
        uname = _public_username(channel)
        target = uname if uname else await _resolve(c, channel)
        if target == "me":
            raise ValueError("cannot join Saved Messages")
        entity = await c.get_entity(target)
        ref = _entity_ref(entity)
        await c(JoinChannelRequest(entity))
        return {"joined": True, "via": "public", "channel": ref}


@mcp.tool(
    description=(
        "Leave a channel or group (username/@handle/numeric id / t.me link). "
        "Does not delete the chat history."
    )
)
async def telegram_leave_channel(channel: str) -> dict[str, Any]:
    log.info("tool telegram_leave_channel start channel=%s", channel)
    channel = (channel or "").strip()
    if not channel:
        raise ValueError("channel is required")
    uname = _public_username(channel)
    async with tg_client.client() as c:
        target = uname if uname else await _resolve(c, channel)
        if target == "me":
            raise ValueError("cannot leave Saved Messages")
        entity = await c.get_entity(target)
        ref = _entity_ref(entity)
        if isinstance(entity, Channel):
            await c(LeaveChannelRequest(entity))
        elif isinstance(entity, Chat):
            await c(DeleteChatUserRequest(chat_id=entity.id, user_id=InputUserSelf()))
        else:
            raise ValueError(f"cannot leave entity type {type(entity).__name__}")
        return {"left": True, "channel": ref}



@mcp.tool(
    description=(
        "List Telegram chat folders (dialog filters): id, title, include/exclude/pinned counts. "
        "with_peers=true also lists peer ids (no access hashes). "
        "DialogFilterDefault (All chats) is omitted."
    )
)
async def telegram_list_folders(with_peers: bool = False) -> dict[str, Any]:
    log.info("tool telegram_list_folders start with_peers=%s", with_peers)
    async with tg_client.client() as c:
        folders = [
            _folder_summary(f, with_peers=with_peers)
            for f in await _get_dialog_filters(c)
            if isinstance(f, DialogFilter)
        ]
        return {"count": len(folders), "folders": folders}


@mcp.tool(
    description=(
        "Add a chat to a folder's include list (and remove it from exclude/pinned if present). "
        "folder: id or title (e.g. Job, Personal). chat: username/@handle/id."
    )
)
async def telegram_folder_include(folder: str, chat: str) -> dict[str, Any]:
    log.info(
        "tool telegram_folder_include start folder=%s chat=%s",
        folder, chat,
    )
    async with tg_client.client() as c:
        f = await _find_folder(c, folder)
        target = await _resolve(c, chat)
        entity = await c.get_entity(target)
        inp = await c.get_input_entity(entity)
        ref = (
            {"id": "me", "name": "Saved Messages", "type": "saved"}
            if target == "me"
            else _entity_ref(entity)
        )
        include = [p for p in (f.include_peers or []) if not _peer_match(p, inp)]
        exclude = [p for p in (f.exclude_peers or []) if not _peer_match(p, inp)]
        pinned = [p for p in (f.pinned_peers or []) if not _peer_match(p, inp)]
        already = len(include) != len(f.include_peers or [])
        include.append(inp)
        new_f = _rebuild_folder(f, include_peers=include, exclude_peers=exclude, pinned_peers=pinned)
        await c(UpdateDialogFilterRequest(id=new_f.id, filter=new_f))
        return {
            "updated": True,
            "folder": _folder_summary(new_f),
            "chat": ref,
            "already_included": already,
        }


@mcp.tool(
    description=(
        "Add a chat to a folder's exclude list (and remove it from include/pinned if present). "
        "folder: id or title. chat: username/@handle/id."
    )
)
async def telegram_folder_exclude(folder: str, chat: str) -> dict[str, Any]:
    log.info(
        "tool telegram_folder_exclude start folder=%s chat=%s",
        folder, chat,
    )
    async with tg_client.client() as c:
        f = await _find_folder(c, folder)
        target = await _resolve(c, chat)
        entity = await c.get_entity(target)
        inp = await c.get_input_entity(entity)
        ref = (
            {"id": "me", "name": "Saved Messages", "type": "saved"}
            if target == "me"
            else _entity_ref(entity)
        )
        include = [p for p in (f.include_peers or []) if not _peer_match(p, inp)]
        exclude = [p for p in (f.exclude_peers or []) if not _peer_match(p, inp)]
        pinned = [p for p in (f.pinned_peers or []) if not _peer_match(p, inp)]
        already = len(exclude) != len(f.exclude_peers or [])
        exclude.append(inp)
        new_f = _rebuild_folder(f, include_peers=include, exclude_peers=exclude, pinned_peers=pinned)
        await c(UpdateDialogFilterRequest(id=new_f.id, filter=new_f))
        return {
            "updated": True,
            "folder": _folder_summary(new_f),
            "chat": ref,
            "already_excluded": already,
        }


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


_MAX_FILE_BYTES = 2000 * 1024 * 1024
_PRIVATE_DIR = os.path.realpath(os.path.join(_BASE, "private"))


def _check_send_path(path: str) -> str:
    if not path or not os.path.isabs(str(path)):
        raise ValueError("path must be an absolute path")
    real = os.path.realpath(str(path))
    name = os.path.basename(real)
    if (
        real == _PRIVATE_DIR
        or real.startswith(_PRIVATE_DIR + os.sep)
        or name.endswith(".session")
        or name == "mcp.env"
    ):
        raise PermissionError("refused: path points to a protected secret location")
    if not os.path.exists(real):
        raise FileNotFoundError("file does not exist")
    if not os.path.isfile(real):
        raise ValueError("path is not a regular file")
    if not os.access(real, os.R_OK):
        raise PermissionError("file is not readable")
    size = os.path.getsize(real)
    if size <= 0:
        raise ValueError("file is empty")
    if size > _MAX_FILE_BYTES:
        raise ValueError("file exceeds 2000 MB limit")
    return real


@mcp.tool(
    description=(
        "Send a file from this machine to a chat (username/@handle/id/'me') with optional caption. "
        "Robot prefix applies to the caption. as_document=true sends images/videos uncompressed. "
        "Only send to real people with explicit user consent; tests go to 'me'."
    )
)
async def telegram_send_file(
    chat: str, path: str, caption: str = "", as_document: bool = False
) -> dict[str, Any]:
    log.info(
        "tool telegram_send_file start chat=%s as_document=%s caption_len=%s",
        chat, as_document, len(str(caption)) if caption else 0,
    )
    real = _check_send_path(path)
    size = os.path.getsize(real)
    caption = "" if caption is None else str(caption)
    final = _apply_robot_prefix(caption).rstrip() if caption == "" else _apply_robot_prefix(caption)
    async with tg_client.client() as c:
        target = await _resolve(c, chat)
        entity = await c.get_entity(target)
        ref = (
            {"id": "me", "name": "Saved Messages", "type": "saved"}
            if target == "me"
            else _entity_ref(entity)
        )
        msg = await c.send_file(target, real, caption=final, force_document=bool(as_document))
        med = _media(msg) or {}
        return {
            "sent_to": ref,
            "message_id": msg.id,
            "time_msk": _fmt_msk(msg.date),
            "file_name": os.path.basename(real),
            "size_bytes": size,
            "media_kind": med.get("kind"),
            "caption": final,
            "robot_prefix_applied": final != caption,
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

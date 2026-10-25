# grokbot-telegram-mcp

Local MCP for Grok Bot: Telegram **user API** (MTProto) via **Telethon** + **WSS** (works through HTTP-only egress). Personal account — not Bot API.

Path on the box: `/workspace/telegram`.

## Operations (MVP)

| Group | Tool | Role |
|-------|------|------|
| **Auth** | `telegram_whoami` | Current account (`get_me`). Errors if not logged in. |
| | `telegram_login_qr` | QR / `tg://login` (+ PNG). `wait_seconds` 0–55. Statuses: `pending` \| `authorized` \| `need_2fa` \| `already_authorized`. |
| | `telegram_login_2fa` | Cloud password from env `TG_2FA_PASSWORD` (never a tool argument). |
| | `telegram_logout` | Log out and delete the local session. |
| **Read** | `telegram_list_dialogs` | Dialogs: name, id, username, unread, type. `limit` 1–100. |
| | `telegram_get_messages` | Chat history. `days` (calendar days Europe/Moscow, default from config), `limit`, `mark_read`. |
| | `telegram_mark_read` | Mark a chat read without fetching messages. |
| **Write** | `telegram_send_message` | Send text. With `robot_prefix_enabled`, prefixes emoji. |

**`chat`:** username, `@handle`, numeric id, or `me` (Saved Messages).

**Time:** all timestamps in responses are Europe/Moscow.

### Typical flows

1. **First login:** `login_qr` → scan / open `url` → if `need_2fa` → set `TG_2FA_PASSWORD` in env → `login_2fa` → `whoami`.
2. **Read today's chat:** `get_messages(chat=…)` (default `history_days=1`). To mark read: `mark_read=true`.
3. **Clear unread only:** `mark_read(chat=…)`.
4. **Send:** `send_message(chat=…, text=…)`. Default tests go to `me`.
5. **Leave:** `logout`.

## Stack

- Telethon 1.x, `ConnectionWSSObfuscated` → `wss://*.web.telegram.org/apiws`
- MCP stdio (`mcp_server.py`), launcher `mcp_launch.sh`
- SQLite session: `private/me.session`

## Config

```bash
cp config.example.json config.json
```

Field details: [CONFIG.md](CONFIG.md). Summary:

| Field | Default | Meaning |
|-------|---------|---------|
| `robot_prefix_enabled` | `true` | Prefix every outgoing message |
| `robot_emoji` | `🤖` | Which prefix |
| `history_days` | `1` | Default window for `get_messages` (1 = today Moscow) |
| `timezone` | `Europe/Moscow` | Timezone for dates (fixed for now) |

No MCP settings tools yet — edit `config.json` by hand.

## Secrets

Never put these in chat, `config.json`, or git.

| What | Where | Mode |
|------|-------|------|
| `TG_API_ID` / `TG_API_HASH` | `private/mcp.env` and/or process env | `600` |
| Session | `private/me.session` | `600` |
| 2FA (login only) | env `TG_2FA_PASSWORD` | — |
| Directory | `private/` | `700` |

`mcp_launch.sh` sources `private/mcp.env` before starting the server.

## Run

```bash
# smoke (needs live session + keys in env)
set -a && source private/mcp.env && set +a
.venv/bin/python mcp_server.py --smoke

# MCP stdio
./mcp_launch.sh
```

Connect in Grok Bot (AddMcpServer):

- **command:** `/workspace/telegram/mcp_launch.sh`
- **args:** `[]`
- **name:** `grokbot-telegram-mcp`

## Files

| Path | Role |
|------|------|
| `mcp_server.py` | MCP tools |
| `mcp_launch.sh` | env + start |
| `tg_client.py` | Telethon factory (WSS + session lock) |
| `tg_wss.py` | WSS connection |
| `tg_auth.py` | QR / 2FA / logout |
| `tg_settings.py` | `config.json` |
| `tg_log.py` | file + stderr logging |
| `config.example.json` | settings template |
| `private/` | secrets + session (gitignored) |

## Logs

`logs/mcp.log` (rotated; mirrored to stderr). Level: `TG_MCP_LOG_LEVEL` (default `INFO`). Secrets are not logged.

## MVP limits

- Text only (media in history shows as `[media]`).
- No broadcasts, no Bot API.
- Do not message real people without explicit consent; tests go to Saved Messages (`me`).
- Respect Telegram rate limits.

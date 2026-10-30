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
| | `telegram_get_messages` | Chat history. `days` (calendar days Europe/Moscow, default from config), `limit`, `mark_read`. Messages include sender (groups), media kind/caption, links, buttons. |
| | `telegram_mark_read` | Mark a chat read without fetching messages. |
| **Buttons** | `telegram_press_button` | Press a button by index or text. Callback presses the button; url/webapp buttons return the url only and are never opened. |
| **Write** | `telegram_send_message` | Send text. With `robot_prefix_enabled`, prefixes emoji. |
| **Write** | `telegram_send_file` | Send a local file (absolute path, <= 2000 MB) with optional caption; robot prefix on caption; `as_document` sends media uncompressed. Refuses secrets (`private/`, `*.session`, `mcp.env`). |
| **Membership** | `telegram_join_channel` | Join public `@username` / `t.me/name` or invite `t.me/+hash`. |
| | `telegram_leave_channel` | Leave channel/supergroup/basic group. |
| **Folders** | `telegram_list_folders` | List chat folders (dialog filters): id, title, counts; `with_peers` lists peer ids. |
| | `telegram_folder_include` | Add chat to folder include list. `folder` id/title, `chat` username/id. |
| | `telegram_folder_exclude` | Add chat to folder exclude list. |

**`chat`:** username, `@handle`, numeric id, or `me` (Saved Messages).

**Time:** all timestamps in responses are Europe/Moscow.

### Typical flows

1. **First login:** `login_qr` → scan / open `url` → if `need_2fa` → set `TG_2FA_PASSWORD` in env → `login_2fa` → `whoami`.
2. **Read today's chat:** `get_messages(chat=…)` (default `history_days=1`). To mark read: `mark_read=true`.
3. **Clear unread only:** `mark_read(chat=…)`.
4. **Send:** `send_message(chat=…, text=…)`. Default tests go to `me`.
5. **Leave account:** `logout`.
6. **Join / leave channel:** `join_channel(channel=…)` / `leave_channel(channel=…)`.
7. **Folders:** `list_folders` → `folder_include` / `folder_exclude`.

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
| `TG_API_ID` / `TG_API_HASH` | Connector secrets (env); fallback `private/mcp.env` | `600` (file) |
| Session | `private/me.session` | `600` |
| 2FA (login only) | env `TG_2FA_PASSWORD` | — |
| Directory | `private/` | `700` |

`mcp_launch.sh` uses `TG_API_ID` / `TG_API_HASH` from the connector's environment (set them as key-value secrets in the connector settings). Only if either is missing does it source `private/mcp.env`.

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

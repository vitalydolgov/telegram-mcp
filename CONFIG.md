# Telegram MCP — config template

Copy the example and edit the local file:

```bash
cp config.example.json config.json
```

`config.json` — runtime settings (non-secrets).  
`config.example.json` — template with defaults.

## Fields

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `robot_prefix_enabled` | bool | `true` | If `true`, prefix every outgoing message with the emoji |
| `robot_emoji` | string | `"🤖"` | Which prefix (a trailing space is added if missing) |
| `history_days` | int 1–30 | `1` | Default calendar days (Moscow) for `get_messages` (`1` = today) |
| `timezone` | string | `"Europe/Moscow"` | Timezone for dates (fixed for now) |

Edit `config.json` by hand (no MCP settings tools yet).

## Secrets — not here

Do **not** put any of these in `config.json` / `config.example.json`:

- `api_id` / `api_hash`
- phone, login code, 2FA
- session file

They belong only in:

| What | Where | Mode |
|------|-------|------|
| API keys | Process environment (`TG_API_ID`, `TG_API_HASH`) | — |
| Session | `var/me.session` | `600` |
| Directory | `var/` | `700` |

MCP launcher: `mcp_launch.sh` (requires `TG_API_ID` and `TG_API_HASH` in the environment; does not read a secrets file).

## Tools

Full operations list: [README.md](README.md).

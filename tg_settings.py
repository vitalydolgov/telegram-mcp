"""Non-secret Telegram MCP settings. API keys stay in the process environment."""
from __future__ import annotations
import json, os, tempfile
from typing import Any

BASE = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.join(BASE, "var")
CONFIG_PATH = os.path.join(BASE, "config.json")
SESSION_PATH = os.path.join(PRIV, "me.session")

DEFAULTS: dict[str, Any] = {
    "robot_prefix_enabled": True,
    "robot_emoji": "🤖",
    "history_days": 1,
    "timezone": "Europe/Moscow",
}

def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if "robot_prefix" in raw and isinstance(raw["robot_prefix"], dict):
            rp = raw["robot_prefix"]
            if "enabled" in rp:
                data["robot_prefix_enabled"] = bool(rp["enabled"])
            if rp.get("emoji"):
                data["robot_emoji"] = str(rp["emoji"])
        if "defaults" in raw and isinstance(raw["defaults"], dict):
            if "history_days" in raw["defaults"]:
                data["history_days"] = int(raw["defaults"]["history_days"])
        for k in DEFAULTS:
            if k in raw:
                data[k] = raw[k]
    data["robot_prefix_enabled"] = bool(data.get("robot_prefix_enabled", True))
    data["robot_emoji"] = (str(data.get("robot_emoji") or "🤖").strip() or "🤖")
    data["history_days"] = max(1, min(int(data.get("history_days") or 1), 30))
    data["timezone"] = "Europe/Moscow"
    return data

def save(data: dict[str, Any]) -> dict[str, Any]:
    clean = {
        "robot_prefix_enabled": bool(data.get("robot_prefix_enabled", True)),
        "robot_emoji": (str(data.get("robot_emoji") or "🤖").strip() or "🤖")[:8],
        "history_days": max(1, min(int(data.get("history_days") or 1), 30)),
        "timezone": "Europe/Moscow",
    }
    fd, tmp = tempfile.mkstemp(dir=BASE, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    return clean

def secrets_status() -> dict[str, Any]:
    api_id = bool(os.environ.get("TG_API_ID", "").strip())
    api_hash = bool(os.environ.get("TG_API_HASH", "").strip())
    sess = os.path.exists(SESSION_PATH)
    return {
        "api_id_set": api_id,
        "api_hash_set": api_hash,
        "session_file": SESSION_PATH if sess else None,
        "session_file_mode": oct(os.stat(SESSION_PATH).st_mode & 0o777) if sess else None,
        "storage_note": (
            "API keys only in the process environment. "
            "Session var/me.session (600). Never in config.json, var/, or chat."
        ),
    }

def public_view() -> dict[str, Any]:
    return {"config": load(), "secrets": secrets_status(), "config_path": CONFIG_PATH}

if __name__ == "__main__":
    print(json.dumps(public_view(), ensure_ascii=False, indent=2))

"""Simple JSON settings (next to exe / project folder)."""

from __future__ import annotations

import json
from typing import Any

from paths import data_path

_PATH = data_path("settings.json")


def load_settings() -> dict[str, Any]:
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict[str, Any]) -> None:
    cur = load_settings()
    cur.update(data)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2, ensure_ascii=False)


def get_output_device() -> str | None:
    v = load_settings().get("output_device")
    return str(v) if v else None


def set_output_device(name: str) -> None:
    save_settings({"output_device": name})


def get_deepl_api_key() -> str | None:
    v = load_settings().get("deepl_api_key")
    if v:
        return str(v).strip() or None
    return None


def set_deepl_api_key(key: str) -> None:
    save_settings({"deepl_api_key": (key or "").strip()})

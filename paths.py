"""App paths — works for source, venv, and PyInstaller frozen builds."""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """
    Writable base directory for settings, learning.db, crash.log.
    Frozen: folder next to the .exe
    Source: project folder
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir() -> str:
    """
    Read-only bundled resources (VERSION.txt, etc.).
    Frozen: PyInstaller extract dir (sys._MEIPASS)
    Source: project folder
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return str(meipass)
        return app_dir()
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts: str) -> str:
    return os.path.join(resource_dir(), *parts)


def data_path(*parts: str) -> str:
    return os.path.join(app_dir(), *parts)

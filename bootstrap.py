"""Dependency check with clear German errors for first-time installs."""

from __future__ import annotations

import importlib
import sys

REQUIRED = [
    ("PySide6", "PySide6"),
    ("soundcard", "soundcard"),
    ("numpy", "numpy"),
    ("faster_whisper", "faster-whisper"),
    ("deep_translator", "deep-translator"),
]


def missing_packages() -> list[str]:
    missing: list[str] = []
    for mod, pip_name in REQUIRED:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(pip_name)
    return missing


def ensure_deps_or_exit() -> None:
    missing = missing_packages()
    if not missing:
        return
    msg = (
        "Fehlende Python-Pakete:\n  - "
        + "\n  - ".join(missing)
        + "\n\nSo beheben:\n"
        "  1) setup.bat doppelklicken (empfohlen)\n"
        "  2) oder:  pip install -r requirements.txt\n"
        "  3) Python 3.11/3.12 von python.org mit 'Add to PATH'\n"
    )
    print(msg, file=sys.stderr, flush=True)
    try:
        # Only if Qt is already importable show a box; else console is enough
        if "PySide6" not in missing:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "CS2 Voice Overlay — Setup fehlt", msg)
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(2)

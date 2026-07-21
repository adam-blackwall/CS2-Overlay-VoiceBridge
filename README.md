# CS2 Voice Overlay (VoiceBridge) — 1.0.3

Realtime **voice translation overlay** for **Counter-Strike 2**.

Listens to team voice (or your mic), runs speech-to-text, translates callouts, and shows the result in a transparent always-on-top overlay.

**External process only** — does **not** inject into `cs2.exe` (VAC-safe design: separate app, no game hooks).

---

## Features

- Live STT (faster-whisper) optimized for CS2 callouts
- Translation into multiple target languages
- Transparent glass overlay, pinned top-right on the main monitor
- **LOCKED / UNLOCKED** control (top center):
  - **LOCKED** (green text) — mouse clicks pass through to the game
  - **UNLOCKED** (red text) — overlay is interactive (language, audio, …)
- Audio source: loopback (what you hear) or microphone
- Learning DB for repeated phrases (local only, not uploaded)

---

## Requirements

| Need | Notes |
|------|--------|
| Windows | Primary target |
| Python 3.10+ | On PATH (`python` in terminal) |
| Headset / speakers | For loopback of team voice |
| Optional GPU | CUDA speeds up Whisper if available |

---

## Install

```bash
git clone https://github.com/adam-blackwall/CS2-Overlay-VoiceBridge.git
cd CS2-Overlay-VoiceBridge
pip install -r requirements.txt
```

Or download the ZIP from GitHub → extract → same `pip install` step.

---

## Run

**Easy:** double-click `start.bat`

**CLI:**

```bash
python main.py
python main.py --lang de --model base
python main.py --list-devices
```

| Argument | Meaning |
|----------|---------|
| `--lang de` | Target language (e.g. `de`, `en`, …) |
| `--model tiny` / `base` / `small` | Whisper size: faster → more accurate |
| `--list-devices` | List OUT (loopback) and MIC devices |

### In-game tips

1. Prefer **CS2 Borderless Windowed** so the overlay stays on top
2. Set **AUDIO → OUT** to the device where you hear team voice
3. Level meter should move when someone talks
4. Console: look for `[tick]` / `[heard]`
5. Quit: **Esc** in the overlay

---

## Overlay controls

| Control | Action |
|---------|--------|
| **LOCKED** / **UNLOCKED** (top center) | Click to toggle click-through |
| Language button | Choose translation target |
| AUDIO ▾ | Choose loopback output or mic |
| Esc | Quit |

### Hotkeys

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+C` | Toggle LOCKED / UNLOCKED |
| `Ctrl+Shift+O` | Show / hide overlay |
| `Ctrl+Shift+Up` / `Down` | Opacity |
| `Ctrl+Shift+R` | Snap to top-right |
| `Ctrl+Shift+L` / `K` | Cycle language |
| `Ctrl+Shift+S` | Pin last phrase to learning DB |
| `Esc` | Quit |

---

## Project layout

| File | Role |
|------|------|
| `main.py` | App entry, capture + pipeline wiring |
| `overlay.py` | UI + lock chip |
| `capture.py` | Loopback / mic capture |
| `stt.py` | Speech-to-text (Whisper) |
| `translate.py` | Translation + learning DB |
| `pipeline.py` | Audio → STT → translate → overlay |
| `cs2_callouts.py` | CS2 slang / prompt bias |
| `start.bat` | Windows launcher |
| `settings.json` | Local settings (not in git) |
| `learning.db` | Local phrase memory (not in git) |

---

## Privacy / what is local

- `settings.json` and `learning.db` stay on your PC (gitignored)
- No account required for the overlay itself
- Online translation may use a public translator API when a phrase is not in the local DB

---

## Version

**1.0.3**

### Changelog (1.0.3)

- LOCKED / UNLOCKED chip (top center): transparent control, green/red text only
- LOCKED = full click-through so the game stays playable under the overlay
- Chip stays clickable while locked (own mini-window)
- README and version bump

---

## License / credit

Personal / team project by [adam-blackwall](https://github.com/adam-blackwall).  
Counter-Strike is a trademark of Valve; this tool is unofficial and not affiliated with Valve.

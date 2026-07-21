# CS2 Voice Overlay (VoiceBridge) — 1.0.4

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
- Translation: **DeepL** (optional API key, best for German) → **Google** fallback

---

## Better understanding & German quality

There are **two** separate problems:

| Stage | What it does | How to improve |
|-------|----------------|----------------|
| **STT** (Whisper) | Hears speech → text | Bigger model, better audio, CS2 prompt, pin corrections |
| **Translation** | Text → your language | DeepL API + CS2 glossary + pin phrases |

### 1) Speech recognition (understand teammates)

Whisper is **not** fine-tuned per match in this app (true neural training needs many hours of labeled audio). Practical gains:

```bash
# better accuracy (slower / more VRAM)
python main.py --model small
# or with NVIDIA GPU + CUDA: try medium later
```

| Model | Speed | Quality |
|-------|-------|---------|
| `tiny` | fastest | weak for noisy voice |
| `base` | default | ok |
| `small` | slower | **recommended** if CPU/GPU can handle it |
| `medium` / `large-v3` | heavy | best local quality |

Also: CS2 **Borderless**, clear headset loopback, team voice not mixed with loud music.

### 2) Translation (especially German)

1. Free DeepL key: [https://www.deepl.com/pro-api](https://www.deepl.com/pro-api) (Free plan)
2. Set key (one of):

```powershell
# PowerShell session
$env:DEEPL_API_KEY = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"
```

Or in local `settings.json` (gitignored):

```json
{
  "deepl_api_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"
}
```

3. Restart overlay → console should say `Translation engine: DeepL`.

Without a key, Google is used automatically (no signup, weaker for DE gaming slang).

### 3) “Training” inside this app (glossary)

| Action | Effect |
|--------|--------|
| Auto-learn | Repeated phrases stored in `learning.db` |
| `Ctrl+Shift+S` | **Pin** last phrase (strong preference next time) |
| `cs2_callouts.py` | Seed glossary + Whisper prompt bias |

Wrong STT text can only be fixed by better STT or by pinning the *corrected* pair after a good recognition — glossary trains **translation**, not the ear.

### 4) Cloud STT later (optional, paid)

For max quality: OpenAI Whisper API / Deepgram / AssemblyAI. Needs internet + API cost; not wired in by default (local + privacy first).

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

**1.0.4**

### Changelog (1.0.4)

- Milder audio gain (less clipping → better STT)
- Optional **DeepL** translation (`DEEPL_API_KEY` / `settings.json`)
- Smaller overlay text; word-by-word reveal; current line wraps 1–3 lines
- Fuzzy dedup: stop repeating near-identical / wrong re-hears
- Clear audio ring after utterance / silence; less Whisper echo

### Changelog (1.0.3)

- LOCKED / UNLOCKED chip (top center): transparent control, green/red text only
- LOCKED = full click-through so the game stays playable under the overlay
- Chip stays clickable while locked (own mini-window)
- README and version bump

---

## License / credit

Personal / team project by [adam-blackwall](https://github.com/adam-blackwall).  
Counter-Strike is a trademark of Valve; this tool is unofficial and not affiliated with Valve.

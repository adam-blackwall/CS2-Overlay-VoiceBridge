"""
1.0.2 — minimal gate: only pure silence / extreme impulse spam.
Human speech must reach Whisper.
"""

from __future__ import annotations

import re

import numpy as np

_HALLUCINATION_PATTERNS = [
    r"^thanks for watching\.?$",
    r"^thank you for watching\.?$",
    r"^subscribe\.?$",
    r"^untertitel.*",
    r"^subtitles?.*",
    r"^amara\.org",
    r"^\[.*music.*\]$",
]


def has_audio_energy(
    mono: np.ndarray, *, min_rms: float = 0.0006, min_peak: float = 0.002
) -> bool:
    x = np.asarray(mono, dtype=np.float32).reshape(-1)
    if x.size < 32:
        return False
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))) + 1e-12)
    peak = float(np.max(np.abs(x)) + 1e-12)
    return rms >= min_rms or peak >= min_peak


def is_clear_sfx(mono: np.ndarray, sample_rate: int) -> bool:
    """Only extreme gunshot-like impulse trains."""
    x = np.asarray(mono, dtype=np.float64).reshape(-1)
    if x.size < int(sample_rate * 0.25):
        return False
    frame = max(64, int(sample_rate * 0.02))
    hop = max(32, frame // 2)
    high = 0
    active = 0
    for i in range(0, x.size - frame + 1, hop):
        f = x[i : i + frame]
        rms = float(np.sqrt(np.mean(f * f)) + 1e-12)
        peak = float(np.max(np.abs(f)) + 1e-12)
        if rms < 0.01 and peak < 0.04:
            continue
        active += 1
        if peak / rms > 15.0:
            high += 1
    if active < 5:
        return False
    return (high / active) >= 0.75


def is_garbage_transcript(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if sum(ch.isalpha() for ch in t) < 1:
        return True
    low = t.lower().strip()
    for pat in _HALLUCINATION_PATTERNS:
        if re.match(pat, low, flags=re.IGNORECASE):
            return True
    return False

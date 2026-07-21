"""
Persistent CS2 / game SFX memory.

When audio is loud but NOT speech, we store a spectral fingerprint (+ optional WAV).
Later clips that match known SFX are blocked before Whisper — permanently improves
rejection of gunshots, steps, utility, etc.

Local only — no game inject.
"""

from __future__ import annotations

import os
import sqlite3
import struct
import threading
import time
import wave
from pathlib import Path

import numpy as np


def audio_fingerprint(mono: np.ndarray, sample_rate: int, n_bands: int = 32) -> np.ndarray | None:
    """Compact L2-normalized log-band fingerprint of a clip."""
    x = np.asarray(mono, dtype=np.float32).reshape(-1)
    if x.size < int(sample_rate * 0.08):
        return None
    # Cap length (~1.2s) for stable prints
    max_n = int(sample_rate * 1.2)
    if x.size > max_n:
        x = x[-max_n:]

    # Pre-emphasis + windowed FFT energy
    x = np.append(x[0], x[1:] - 0.97 * x[:-1]).astype(np.float32)
    n = 1
    while n < x.size:
        n *= 2
    n = min(n, 16384)
    if x.size < n:
        x = np.pad(x, (0, n - x.size))
    else:
        x = x[-n:]
    win = np.hanning(x.size).astype(np.float32)
    mag = np.abs(np.fft.rfft(x * win)).astype(np.float64) + 1e-12
    freqs = np.fft.rfftfreq(x.size, d=1.0 / sample_rate)

    f_min, f_max = 60.0, min(8000.0, sample_rate * 0.48)
    edges = np.geomspace(f_min, f_max, n_bands + 1)
    bands = np.zeros(n_bands, dtype=np.float64)
    for i in range(n_bands):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if np.any(mask):
            bands[i] = np.log(mag[mask].mean())
        else:
            bands[i] = bands[i - 1] if i else -12.0

    bands -= bands.mean()
    rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))) + 1e-12)
    peak = float(np.max(np.abs(mono)) + 1e-12)
    crest = peak / rms
    # Append a few dynamics cues (scaled)
    extra = np.array(
        [np.log(rms + 1e-6), np.log(peak + 1e-6), min(crest, 30.0) / 30.0],
        dtype=np.float64,
    )
    vec = np.concatenate([bands, extra])
    nrm = np.linalg.norm(vec) + 1e-12
    return (vec / nrm).astype(np.float32)


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(1.0 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def _write_wav_mono(path: str, mono: np.ndarray, sample_rate: int) -> None:
    x = np.asarray(mono, dtype=np.float32).reshape(-1)
    # peak normalize lightly for archive
    peak = float(np.max(np.abs(x)) + 1e-9)
    x = np.clip(x / max(peak, 0.2) * 0.85, -1.0, 1.0)
    pcm = (x * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())


class SfxBank:
    """
    Learns game sounds that are NOT speech.
    DB table + optional WAV files under sfx_bank/.
    """

    def __init__(
        self,
        db_path: str | None = None,
        wav_dir: str | None = None,
        *,
        # Stricter match so random/voice clips aren't blocked by loose SFX prints
        match_distance: float = 0.075,
        max_entries: int = 800,
        save_wav: bool = True,
        min_seconds_between_saves: float = 0.6,
    ) -> None:
        base = os.path.dirname(os.path.abspath(__file__))
        self.db_path = db_path or os.path.join(base, "learning.db")
        self.wav_dir = wav_dir or os.path.join(base, "sfx_bank")
        self.match_distance = match_distance
        self.max_entries = max_entries
        self.save_wav = save_wav
        self.min_seconds_between_saves = min_seconds_between_saves
        self._lock = threading.Lock()
        self._last_save = 0.0
        Path(self.wav_dir).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()
        self._cache: list[np.ndarray] = []
        self._reload_cache()

    def _init(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sfx_prints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint BLOB NOT NULL,
                    dim INTEGER NOT NULL,
                    reason TEXT,
                    wav_path TEXT,
                    hits INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._conn.commit()

    def _reload_cache(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT fingerprint, dim FROM sfx_prints ORDER BY hits DESC, id DESC LIMIT ?",
                (self.max_entries,),
            ).fetchall()
        cache: list[np.ndarray] = []
        for r in rows:
            dim = int(r["dim"])
            arr = np.frombuffer(r["fingerprint"], dtype=np.float32)
            if arr.size == dim:
                cache.append(arr.copy())
        self._cache = cache

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS c FROM sfx_prints").fetchone()["c"])

    def clear_all(self) -> int:
        """Wipe poisoned fingerprints + wav index (files deleted best-effort)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT wav_path FROM sfx_prints WHERE wav_path IS NOT NULL"
            ).fetchall()
            n = int(self._conn.execute("SELECT COUNT(*) AS c FROM sfx_prints").fetchone()["c"])
            self._conn.execute("DELETE FROM sfx_prints")
            self._conn.commit()
        self._cache = []
        for r in rows:
            p = r["wav_path"]
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return n

    def is_known_sfx(self, mono: np.ndarray, sample_rate: int) -> bool:
        fp = audio_fingerprint(mono, sample_rate)
        if fp is None or not self._cache:
            return False
        best = 1e9
        for ref in self._cache:
            if ref.shape != fp.shape:
                continue
            d = _cosine_distance(fp, ref)
            if d < best:
                best = d
            if d <= self.match_distance:
                self._bump_nearest(fp)
                return True
        return False

    def _bump_nearest(self, fp: np.ndarray) -> None:
        # Optional: reinforce matched entry hits (lightweight — skip heavy scan)
        pass

    def remember(
        self,
        mono: np.ndarray,
        sample_rate: int,
        reason: str = "non_speech",
        *,
        force: bool = False,
    ) -> bool:
        """
        Store a non-speech clip. Returns True if a new/updated entry was written.
        """
        now = time.monotonic()
        if not force and (now - self._last_save) < self.min_seconds_between_saves:
            return False

        rms = float(np.sqrt(np.mean(np.square(np.asarray(mono, dtype=np.float64)))) + 1e-12)
        peak = float(np.max(np.abs(mono)) + 1e-12)
        # Don't archive pure silence
        if rms < 0.006 and peak < 0.02:
            return False

        fp = audio_fingerprint(mono, sample_rate)
        if fp is None:
            return False

        # If very similar already known, just bump hits
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, fingerprint, dim, hits FROM sfx_prints ORDER BY id DESC LIMIT 400"
            ).fetchall()
            for r in rows:
                ref = np.frombuffer(r["fingerprint"], dtype=np.float32)
                if ref.size != fp.size:
                    continue
                if _cosine_distance(fp, ref) <= self.match_distance * 0.85:
                    self._conn.execute(
                        "UPDATE sfx_prints SET hits=hits+1, updated_at=? WHERE id=?",
                        (time.time(), r["id"]),
                    )
                    self._conn.commit()
                    self._last_save = now
                    return True

        wav_path = None
        if self.save_wav:
            ts = int(time.time() * 1000)
            fname = f"sfx_{ts}_{reason[:24]}.wav"
            # sanitize filename
            fname = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in fname)
            full = os.path.join(self.wav_dir, fname)
            try:
                # store mono at original rate (or 16k if huge)
                clip = np.asarray(mono, dtype=np.float32).reshape(-1)
                max_n = int(sample_rate * 1.5)
                if clip.size > max_n:
                    clip = clip[-max_n:]
                _write_wav_mono(full, clip, sample_rate)
                wav_path = full
            except OSError:
                wav_path = None

        blob = fp.astype(np.float32).tobytes()
        t = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sfx_prints (fingerprint, dim, reason, wav_path, hits, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (blob, int(fp.size), reason, wav_path, t, t),
            )
            # Bound table size
            self._conn.execute(
                """
                DELETE FROM sfx_prints WHERE id NOT IN (
                    SELECT id FROM sfx_prints ORDER BY hits DESC, id DESC LIMIT ?
                )
                """,
                (self.max_entries,),
            )
            self._conn.commit()

        self._last_save = now
        self._cache.insert(0, fp.copy())
        self._cache = self._cache[: self.max_entries]
        return True

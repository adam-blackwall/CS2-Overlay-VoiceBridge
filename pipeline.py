"""
Speech pipeline: capture → rolling STT → translate → overlay.

Critical fix: STT runs once ring has ~0.4s audio (earlier bug blocked all STT).
External / capture-only — no game process access.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from languages import Lang, get_lang
from memory_db import LearningDB
from sfx_memory import SfxBank
from speech_filter import has_audio_energy, is_clear_sfx, is_garbage_transcript
from stt import SpeechToText, detect_device
from translate import Translator


@dataclass
class PipelineEvent:
    kind: str
    text: str | None = None
    status: str | None = None
    level: float | None = None
    source_lang: str | None = None
    target_lang: str | None = None
    device_label: str | None = None
    speaker: str | None = None
    stream: bool = False
    commit_current: bool = False


def _to_mono_f32(pcm: np.ndarray) -> np.ndarray:
    arr = np.asarray(pcm, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    return arr.reshape(-1)


def _resample_linear(mono: np.ndarray, src_hz: int, dst_hz: int) -> np.ndarray:
    if src_hz == dst_hz or mono.size == 0:
        return mono.astype(np.float32, copy=False)
    duration = mono.size / float(src_hz)
    n_out = max(1, int(round(duration * dst_hz)))
    x_old = np.linspace(0.0, 1.0, num=mono.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, mono.astype(np.float64)).astype(np.float32)


def _norm_text(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip().lower())
    # drop trailing punctuation noise from Whisper
    t = re.sub(r"[\"'`]+", "", t)
    t = re.sub(r"[.!?…,;:]+$", "", t)
    return t.strip()


def _token_set(s: str) -> set[str]:
    return {w for w in _norm_text(s).split() if w}


def _text_similar(a: str, b: str, *, threshold: float = 0.78) -> bool:
    """True if two transcripts are effectively the same utterance."""
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # one contains the other (partial re-hear of same line)
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 4 and shorter in longer:
        if len(shorter) / max(len(longer), 1) >= 0.70:
            return True
    ta, tb = _token_set(na), _token_set(nb)
    if ta and tb:
        jacc = len(ta & tb) / len(ta | tb)
        if jacc >= 0.72:
            return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


class SpeechPipeline:
    def __init__(
        self,
        *,
        target_lang: str = "de",
        model_size: str = "base",
        sample_rate_in: int = 48000,
        energy_threshold: float = 0.0008,
        interval_ms: int = 550,
        window_ms: int = 3000,
        silence_ms: int = 550,
        min_speech_ms: int = 50,
        word_delay_ms: int = 140,
        speaker_detect: bool = False,
        on_event: Callable[[PipelineEvent], None] | None = None,
    ) -> None:
        del speaker_detect
        self._target = get_lang(target_lang)
        self._stt = SpeechToText(model_size=model_size)
        self._db = LearningDB()
        self._sfx = SfxBank(db_path=self._db.path)
        try:
            flag = os.path.join(os.path.dirname(self._db.path), ".sfx_purged_stable")
            if not os.path.isfile(flag):
                cleared = self._sfx.clear_all()
                with open(flag, "w", encoding="utf-8") as f:
                    f.write(f"cleared={cleared}\n")
                if cleared:
                    print(f"SFX bank wiped ({cleared}).", flush=True)
        except OSError:
            pass
        self._tr = Translator(self._db)
        self._sr_in = sample_rate_in
        self._sr_stt = 16000
        self._energy_threshold = energy_threshold
        self._interval_ms = max(200, int(interval_ms))
        self._window_ms = max(400, int(window_ms))
        self._silence_ms = silence_ms
        self._min_speech_ms = min_speech_ms
        self._word_delay_ms = word_delay_ms
        self._on_event = on_event

        self.events: queue.Queue[PipelineEvent] = queue.Queue()
        self._pcm_q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=128)
        self._stop = threading.Event()
        self._ingest: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

        self._ring = np.zeros(0, dtype=np.float32)
        self._ring_lock = threading.Lock()
        self._max_ring_s = 6.0

        self._last_source = ""
        self._last_translated = ""
        self._tr_cache: dict[tuple[str, str, str], str] = {}
        self._had_voice = False
        self._quiet_since: float | None = None
        self._line_open = False
        self._noise_floor = 0.0015
        self._reveal_id = 0  # cancel in-flight word-by-word reveal
        # Dedup: rolling window re-STT often re-hears the same call slightly wrong
        self._recent: list[tuple[float, str]] = []  # (mono_time, norm_source)
        self._recent_ttl_s = 14.0
        self._last_emit_mono = 0.0
        self._min_emit_gap_s = 0.85

    @property
    def target_lang(self) -> Lang:
        with self._lock:
            return self._target

    @property
    def device_info(self) -> str:
        return f"{self._stt.device}/{self._stt.compute_type}"

    @property
    def db(self) -> LearningDB:
        return self._db

    @property
    def sfx(self) -> SfxBank:
        return self._sfx

    def pin_last(self) -> str | None:
        if not self._last_source or not self._last_translated:
            return None
        self._tr.pin(self._last_source, self._last_translated, self.target_lang.code, "auto")
        msg = f"Gemerkt: „{self._last_source}“ → „{self._last_translated}“"
        self._emit(
            PipelineEvent(
                kind="status",
                status="listening",
                text=msg,
                stream=True,
                target_lang=self.target_lang.code,
            )
        )
        return msg

    def set_target_lang(self, code: str) -> Lang:
        lang = get_lang(code)
        with self._lock:
            self._target = lang
        self._tr_cache.clear()
        self._emit(
            PipelineEvent(
                kind="lang",
                target_lang=lang.code,
                text=f"Zielsprache: {lang.name_de} ({lang.label})",
                stream=True,
                status="listening",
            )
        )
        return lang

    def _emit(self, ev: PipelineEvent) -> None:
        self.events.put(ev)
        if self._on_event:
            try:
                self._on_event(ev)
            except Exception:  # noqa: BLE001
                pass

    def feed_pcm(self, pcm: np.ndarray, sample_rate: int) -> None:
        if self._stop.is_set():
            return
        self._sr_in = sample_rate
        mono = _to_mono_f32(pcm)
        try:
            self._pcm_q.put_nowait(mono)
        except queue.Full:
            try:
                self._pcm_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._pcm_q.put_nowait(mono)
            except queue.Full:
                pass

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._ingest = threading.Thread(target=self._ingest_loop, name="AudioIngest", daemon=True)
        self._worker = threading.Thread(target=self._worker_loop, name="SpeechLive", daemon=True)
        self._ingest.start()
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._pcm_q.put_nowait(None)
        except queue.Full:
            pass
        for th in (self._ingest, self._worker):
            if th:
                th.join(timeout=6.0)
        self._ingest = self._worker = None

    def _append_ring(self, mono: np.ndarray) -> None:
        max_n = int(self._sr_in * self._max_ring_s)
        with self._ring_lock:
            if self._ring.size == 0:
                self._ring = mono.copy()
            else:
                self._ring = np.concatenate([self._ring, mono])
            if self._ring.size > max_n:
                self._ring = self._ring[-max_n:]

    def _trim_ring_keep_tail(self, seconds: float = 0.40) -> None:
        """Drop audio already spoken so Whisper cannot re-hear the same line."""
        keep = max(0, int(self._sr_in * seconds))
        with self._ring_lock:
            if self._ring.size > keep:
                self._ring = self._ring[-keep:].copy() if keep else np.zeros(0, dtype=np.float32)
            elif keep == 0:
                self._ring = np.zeros(0, dtype=np.float32)

    def _clear_ring(self) -> None:
        with self._ring_lock:
            self._ring = np.zeros(0, dtype=np.float32)

    def _snapshot(self, ms: int) -> np.ndarray:
        n = int(self._sr_in * (ms / 1000.0))
        with self._ring_lock:
            if self._ring.size == 0:
                return np.zeros(0, dtype=np.float32)
            if self._ring.size <= n:
                return self._ring.copy()
            return self._ring[-n:].copy()

    def _prune_recent(self) -> None:
        now = time.monotonic()
        ttl = self._recent_ttl_s
        self._recent = [(t, s) for t, s in self._recent if now - t < ttl]

    def _remember_source(self, source: str) -> None:
        n = _norm_text(source)
        if not n:
            return
        self._prune_recent()
        self._recent.append((time.monotonic(), n))
        if len(self._recent) > 24:
            self._recent = self._recent[-24:]

    def _decide_utterance(self, source: str) -> str | None:
        """
        Return how to handle this STT result:
          'new'    — fresh line
          'update' — same open line, clearly longer/better
          None     — duplicate / near-duplicate → drop
        """
        n = _norm_text(source)
        if not n or len(n) < 2:
            return None

        now = time.monotonic()
        self._prune_recent()

        # Hard cooldown against spam of near-identical lines
        if now - self._last_emit_mono < self._min_emit_gap_s:
            last = _norm_text(self._last_source)
            if last and _text_similar(n, last, threshold=0.70):
                return None

        # Against anything we already showed recently (incl. after silence)
        for _t, prev in self._recent:
            if _text_similar(n, prev, threshold=0.76):
                # allow only if clearly longer expansion of that recent line
                if len(n) > len(prev) * 1.25 + 4 and prev in n:
                    continue
                return None

        last = _norm_text(self._last_source)
        if not last:
            return "new"
        if n == last:
            return None
        # Shorter re-hear of the same phrase → ignore
        if n in last and len(n) < len(last):
            return None
        if _text_similar(n, last, threshold=0.74):
            # Same idea: only keep if meaningfully longer
            if len(n) >= len(last) + 4 or (
                last in n and len(n) > len(last) + 2
            ):
                return "update"
            return None
        # Extension of current open line
        if self._line_open and last in n and len(n) > len(last) + 2:
            return "update"
        return "new"

    def _ingest_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._pcm_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk is None:
                break
            self._append_ring(chunk)
            rms, peak, voiced = self._voice_metrics(chunk)
            level = min(1.0, max(rms * 10.0, peak * 3.5))
            self._emit(PipelineEvent(kind="level", level=level))
            if voiced:
                self._had_voice = True
                self._quiet_since = None
            else:
                if self._had_voice and self._quiet_since is None:
                    self._quiet_since = time.monotonic()

    def _worker_loop(self) -> None:
        dev, _ctype = detect_device()
        self._emit(
            PipelineEvent(
                kind="status",
                status="processing",
                text=f"Lade Whisper ({self._stt.model_size} / {dev})…",
                stream=True,
            )
        )
        try:
            self._stt.load()
            if getattr(self._stt, "_fallback_note", None):
                print(self._stt._fallback_note, flush=True)
            self._emit(
                PipelineEvent(
                    kind="status",
                    status="listening",
                    text="…",
                    stream=True,
                    target_lang=self.target_lang.code,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._emit(
                PipelineEvent(
                    kind="error",
                    status="muted",
                    text=f"STT-Laden fehlgeschlagen: {exc}",
                    stream=True,
                )
            )
            return

        interval_s = self._interval_ms / 1000.0
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self._live_tick()
            except Exception as exc:  # noqa: BLE001
                self._emit(
                    PipelineEvent(
                        kind="error",
                        status="listening",
                        text=f"Pipeline: {exc}",
                        stream=True,
                    )
                )
            self._maybe_commit_silence()
            elapsed = time.monotonic() - t0
            sleep_for = interval_s - elapsed
            end = time.monotonic() + max(0.0, sleep_for)
            while time.monotonic() < end and not self._stop.is_set():
                time.sleep(min(0.05, end - time.monotonic()))

    def _maybe_commit_silence(self) -> None:
        if not self._line_open or self._quiet_since is None:
            return
        quiet_ms = (time.monotonic() - self._quiet_since) * 1000.0
        if quiet_ms < self._silence_ms:
            return
        if self._last_source:
            self._remember_source(self._last_source)
        if self._last_translated:
            self._emit(
                PipelineEvent(
                    kind="final",
                    text=self._last_translated,
                    stream=False,
                    source_lang="auto",
                    target_lang=self.target_lang.code,
                    status="listening",
                )
            )
        self._line_open = False
        self._had_voice = False
        self._quiet_since = None
        self._last_source = ""
        self._last_translated = ""
        # Drop buffered audio — otherwise next tick re-STTs the same phrase
        self._clear_ring()
        # Soft reset Whisper prompt context on long silence
        try:
            self._stt.reset_context()
        except Exception:  # noqa: BLE001
            pass

    def _voice_metrics(self, mono: np.ndarray) -> tuple[float, float, bool]:
        if mono.size == 0:
            return 0.0, 0.0, False
        rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
        peak = float(np.max(np.abs(mono)))
        if peak < 0.15 and rms < 0.03:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
            self._noise_floor = float(np.clip(self._noise_floor, 1e-6, 0.03))
        thresh = max(self._energy_threshold, self._noise_floor * 1.4 + 0.0004)
        activity = rms >= thresh or peak >= thresh * 2.5
        return rms, peak, activity

    def _live_tick(self) -> None:
        """
        1.0.2: STT as soon as ~0.35s audio is buffered.
        Longer window (default 3s) for better human speech context.
        """
        min_samples = int(self._sr_in * 0.35)
        with self._ring_lock:
            ring_n = int(self._ring.size)
        if ring_n < min_samples:
            return

        window = self._snapshot(self._window_ms)
        if window.size < min_samples:
            return

        rms, peak, activity = self._voice_metrics(window)
        level = min(1.0, max(rms * 12.0, peak * 4.0))
        self._emit(PipelineEvent(kind="level", level=level))

        # Slightly higher bar — skip floor noise / faint SFX
        if not has_audio_energy(window, min_rms=0.0009, min_peak=0.003) and not activity:
            return

        # Only pure gunshot spam (never block normal voice)
        if is_clear_sfx(window, self._sr_in):
            self._sfx.remember(window, self._sr_in, reason="clear_sfx")
            return

        mono16 = _resample_linear(window, self._sr_in, self._sr_stt)
        # Gentle pre-Whisper boost only when still very quiet (avoid double-AGC clip)
        p = float(np.max(np.abs(mono16)) + 1e-9)
        r = float(np.sqrt(np.mean(np.square(mono16))) + 1e-9)
        if p < 0.10 or r < 0.02:
            gain = min(6.0, 0.14 / max(p, r * 2.0, 1e-5))
            mono16 = np.clip(mono16 * gain, -1.0, 1.0).astype(np.float32)
        elif p > 0.85:
            # Soft peak limit if already hot
            mono16 = np.clip(mono16 * (0.75 / p), -1.0, 1.0).astype(np.float32)

        print(
            f"[tick] ring={ring_n} win={window.size} rms={rms:.4f} peak={peak:.4f} → STT…",
            flush=True,
        )

        try:
            with self._lock:
                tr = self._stt.transcribe(mono16, sample_rate=self._sr_stt, language=None)
        except Exception as exc:  # noqa: BLE001
            print(f"STT error: {exc}", flush=True)
            self._emit(
                PipelineEvent(
                    kind="error",
                    status="listening",
                    text=f"STT-Fehler: {exc}",
                    stream=True,
                )
            )
            return

        if tr is None or not tr.text.strip():
            print(f"[tick] whisper empty (rms={rms:.4f})", flush=True)
            return

        source = tr.text.strip()
        if is_garbage_transcript(source):
            return

        decision = self._decide_utterance(source)
        if decision is None:
            print(f"[skip] duplicate/near: {source!r}", flush=True)
            # Still trim a bit so we do not hammer the same audio forever
            self._trim_ring_keep_tail(0.55)
            return

        target = self.target_lang
        try:
            translated = self._translate_cached(source, tr.language, target.code)
        except Exception as exc:  # noqa: BLE001
            print(f"Translate error: {exc}", flush=True)
            translated = source

        # Also skip if translation is basically the same as last shown line
        if decision == "new" and self._last_translated and _text_similar(
            translated, self._last_translated, threshold=0.80
        ):
            print(f"[skip] same translation: {translated!r}", flush=True)
            self._remember_source(source)
            self._trim_ring_keep_tail(0.55)
            return

        print(
            f"[heard/{decision}] {tr.language}: {source!r} → {target.code}: {translated!r}",
            flush=True,
        )

        self._last_source = source
        self._last_translated = translated
        self._last_emit_mono = time.monotonic()
        self._remember_source(source)

        if decision == "new":
            # Push previous current line to history (if any), then start fresh
            self._emit(
                PipelineEvent(
                    kind="partial",
                    commit_current=True,
                    status="processing",
                    source_lang=tr.language,
                    target_lang=target.code,
                )
            )
            self._line_open = True
        # decision == "update": same line slot — replace text, no history spam

        # Word-by-word reveal (translated phrase builds left → right)
        self._emit_words_stream(
            translated,
            source_lang=tr.language,
            target_lang=target.code,
            quick=(decision == "update"),
        )
        # Prevent immediate re-STT of the same window
        self._trim_ring_keep_tail(0.45)
        self._emit(PipelineEvent(kind="status", status="listening"))

    def _emit_words_stream(
        self,
        translated: str,
        *,
        source_lang: str,
        target_lang: str,
        quick: bool = False,
    ) -> None:
        """Show translation one word at a time, accumulating on the overlay line."""
        text = (translated or "").strip()
        if not text:
            return
        words = text.split()
        if not words:
            return

        self._reveal_id += 1
        rid = self._reveal_id
        delay_ms = int(self._word_delay_ms or 0)
        # Updates / single word / delay 0 → show full line (avoid re-animating repeats)
        if quick or delay_ms <= 0 or len(words) == 1:
            self._emit(
                PipelineEvent(
                    kind="partial",
                    text=text,
                    stream=True,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    status="processing",
                )
            )
            return

        delay_s = min(0.45, max(0.04, delay_ms / 1000.0))
        acc: list[str] = []
        for w in words:
            if self._stop.is_set() or rid != self._reveal_id:
                return
            acc.append(w)
            self._emit(
                PipelineEvent(
                    kind="partial",
                    text=" ".join(acc),
                    stream=True,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    status="processing",
                )
            )
            time.sleep(delay_s)

    def _translate_cached(self, text: str, source_lang: str, target_code: str) -> str:
        key = (_norm_text(text), source_lang or "auto", target_code)
        if key in self._tr_cache:
            return self._tr_cache[key]
        try:
            out = self._tr.translate(text, target_code, source=source_lang, learn=True)
        except Exception:  # noqa: BLE001
            out = text
        out = (out or text).strip()
        if len(self._tr_cache) > 256:
            self._tr_cache.clear()
        self._tr_cache[key] = out
        return out

"""
Speech-to-text via faster-whisper — 1.0.3 recognition-focused.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cs2_callouts import whisper_prompt

# CS2-only bias for Whisper (team voice / callouts — not general YouTube)
CS_INITIAL_PROMPT = whisper_prompt()


@dataclass
class Transcript:
    text: str
    language: str
    language_probability: float


def _cublas_loadable() -> bool:
    import ctypes
    import sys

    if sys.platform != "win32":
        return True
    for name in ("cublas64_12.dll", "cublas64_11.dll", "cublas64_10.dll"):
        try:
            ctypes.WinDLL(name)
            return True
        except OSError:
            continue
    return False


def cuda_available() -> bool:
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return False
        return _cublas_loadable()
    except Exception:  # noqa: BLE001
        return False


def detect_device() -> tuple[str, str]:
    if cuda_available():
        return "cuda", "float16"
    return "cpu", "int8"


class SpeechToText:
    def __init__(
        self,
        model_size: str = "base",
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self.model_size = model_size
        if device is None or compute_type is None:
            d, c = detect_device()
            self.device = device or d
            self.compute_type = compute_type or c
        else:
            self.device = device
            self.compute_type = compute_type
        self._model = None
        self._fallback_note: str | None = None
        self._prev_text = ""

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        attempts: list[tuple[str, str]] = [(self.device, self.compute_type)]
        if self.device != "cpu":
            attempts.append(("cpu", "int8"))
        if self.device == "cuda":
            attempts.insert(1, ("cuda", "int8_float16"))

        last_err: Exception | None = None
        seen: set[tuple[str, str]] = set()
        for dev, ctype in attempts:
            key = (dev, ctype)
            if key in seen:
                continue
            seen.add(key)
            try:
                self._model = WhisperModel(
                    self.model_size, device=dev, compute_type=ctype
                )
                if dev != self.device or ctype != self.compute_type:
                    self._fallback_note = f"GPU nicht nutzbar → Fallback {dev}/{ctype}"
                self.device = dev
                self.compute_type = ctype
                print(
                    f"Whisper ready: {self.model_size} on {self.device}/{self.compute_type}",
                    flush=True,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                self._model = None
                continue
        raise RuntimeError(f"Whisper konnte nicht geladen werden: {last_err}") from last_err

    def reset_context(self) -> None:
        self._prev_text = ""

    def transcribe(
        self,
        audio_f32_mono: np.ndarray,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> Transcript | None:
        self.load()
        assert self._model is not None

        if audio_f32_mono is None or len(audio_f32_mono) < int(sample_rate * 0.20):
            return None

        audio = np.asarray(audio_f32_mono, dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(audio)) + 1e-9)
        rms = float(np.sqrt(np.mean(np.square(audio))) + 1e-9)
        if peak < 0.0015 and rms < 0.0005:
            return None

        # Mild level normalize — old AGC to 0.95 clipped CS2 mix and hurt STT
        if peak > 0.55:
            audio = np.clip(audio * (0.55 / peak), -1.0, 1.0)
        elif peak < 0.08:
            ref = max(peak, rms * 3.0, 0.02)
            audio = np.clip(audio / ref * 0.45, -1.0, 1.0)
        # mid levels: leave dynamics alone

        # Prompt: CS callouts + last phrase for continuity
        prompt = CS_INITIAL_PROMPT
        if self._prev_text:
            prompt = f"{CS_INITIAL_PROMPT} Recent: {self._prev_text[-180:]}"

        # base/small: beam 2 helps accuracy; tiny stays fast
        beam = 1 if self.model_size in ("tiny", "tiny.en") else 2

        segments, info = self._model.transcribe(
            audio,
            language=language,
            task="transcribe",
            vad_filter=False,
            beam_size=beam,
            best_of=beam,
            temperature=0.0,
            # Previous text often causes Whisper to echo/repeat bad lines
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=prompt,
            no_speech_threshold=0.85,
            compression_ratio_threshold=2.9,
            log_prob_threshold=-1.25,
        )

        parts: list[str] = []
        for seg in segments:
            t = (seg.text or "").strip()
            if not t:
                continue
            nsp = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
            if nsp > 0.82:
                continue
            parts.append(t)
        text = " ".join(parts).strip()
        if not text:
            return None

        from speech_filter import is_garbage_transcript

        if is_garbage_transcript(text):
            return None

        # Update rolling context (helps next window)
        self._prev_text = (self._prev_text + " " + text).strip()[-240:]

        lang = getattr(info, "language", None) or (language or "auto")
        prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        return Transcript(text=text, language=str(lang), language_probability=prob)

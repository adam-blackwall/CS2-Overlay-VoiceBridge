"""
External audio capture (read-only).

- Loopback: what you hear (speakers/headphones)
- Mic: physical microphone
No game inject.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import soundcard as sc

LevelCallback = Callable[[float, float], None]
PcmCallback = Callable[[np.ndarray, int], None]


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str
    is_loopback: bool
    is_default_speaker_loopback: bool = False
    is_default_mic: bool = False


def list_devices() -> list[DeviceInfo]:
    default_sp = sc.default_speaker()
    default_mic = sc.default_microphone()
    default_sp_name = default_sp.name if default_sp is not None else ""
    default_mic_name = default_mic.name if default_mic is not None else ""
    result: list[DeviceInfo] = []
    for mic in sc.all_microphones(include_loopback=True):
        is_lb = bool(getattr(mic, "isloopback", False))
        result.append(
            DeviceInfo(
                id=str(mic.id),
                name=str(mic.name),
                is_loopback=is_lb,
                is_default_speaker_loopback=is_lb and mic.name == default_sp_name,
                is_default_mic=(not is_lb) and mic.name == default_mic_name,
            )
        )
    return result


def list_loopback_outputs() -> list[DeviceInfo]:
    return [d for d in list_devices() if d.is_loopback]


def list_microphones() -> list[DeviceInfo]:
    return [d for d in list_devices() if not d.is_loopback]


def resolve_loopback_microphone(device_name: str | None = None):
    default_sp = sc.default_speaker()
    if default_sp is None:
        raise RuntimeError("No default speaker found.")
    target = (device_name or default_sp.name or "").strip()
    loopbacks = [
        m for m in sc.all_microphones(include_loopback=True) if getattr(m, "isloopback", False)
    ]
    for mic in loopbacks:
        if mic.name == target or str(mic.id) == target:
            return mic
    t_low = target.lower()
    for mic in loopbacks:
        if t_low and t_low in mic.name.lower():
            return mic
    if default_sp.name:
        for mic in loopbacks:
            if mic.name == default_sp.name:
                return mic
    if loopbacks:
        return loopbacks[0]
    raise RuntimeError("No loopback device found.")


def resolve_microphone(device_name: str | None = None):
    default_mic = sc.default_microphone()
    if default_mic is None:
        raise RuntimeError("No default microphone found.")
    target = (device_name or default_mic.name or "").strip()
    mics = [
        m
        for m in sc.all_microphones(include_loopback=True)
        if not getattr(m, "isloopback", False)
    ]
    for mic in mics:
        if mic.name == target or str(mic.id) == target:
            return mic
    t_low = target.lower()
    for mic in mics:
        if t_low and t_low in mic.name.lower():
            return mic
    return default_mic


class _BaseCapture:
    def __init__(
        self,
        *,
        device_name: str | None = None,
        sample_rate: int | None = None,
        channels: int = 2,
        block_frames: int | None = None,  # auto from sample rate (~40ms blocks)
        on_level: LevelCallback | None = None,
        on_pcm: PcmCallback | None = None,
        auto_gain: bool = True,
        # Lower target: CS2 loopback is often loud; over-gain clips and confuses Whisper
        target_rms: float = 0.05,
    ) -> None:
        self._device_name_pref = device_name
        self._sample_rate_pref = sample_rate
        self._sample_rate = sample_rate or 48000
        self._channels = channels
        self._block_frames_pref = block_frames
        self._block_frames = block_frames or 2048
        self._on_level = on_level
        self._on_pcm = on_pcm
        self._auto_gain = auto_gain
        self._target_rms = target_rms
        self._gain = 1.0

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._rms = 0.0
        self._peak = 0.0
        self._running = False
        self._device_name = ""
        self._error: str | None = None
        self._mode = "loopback"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def last_error(self) -> str | None:
        return self._error

    @property
    def mode(self) -> str:
        return self._mode

    def levels(self) -> tuple[float, float]:
        with self._lock:
            return self._rms, self._peak

    def _resolve(self):
        raise NotImplementedError

    def start(self) -> None:
        if self._running:
            return
        mic = self._resolve()
        self._device_name = str(mic.name)
        # Variable sample rate: prefer native device rate (often 48k, sometimes 44.1/96k)
        native = getattr(mic, "samplerate", None) or getattr(mic, "default_samplerate", None)
        if self._sample_rate_pref:
            self._sample_rate = int(self._sample_rate_pref)
        elif native:
            self._sample_rate = int(native)
        else:
            self._sample_rate = 48000
        # Prefer high quality capture when device reports something very low
        if self._sample_rate < 16000:
            self._sample_rate = 48000

        ch_avail = getattr(mic, "channels", None) or getattr(mic, "channles", None)
        if ch_avail:
            self._channels = min(self._channels, int(ch_avail))
        self._channels = max(1, self._channels)

        # ~40 ms blocks, scaled to sample rate (responsive VAD, not too many callbacks)
        if self._block_frames_pref is None:
            self._block_frames = max(512, int(round(self._sample_rate * 0.04)))
        else:
            self._block_frames = int(self._block_frames_pref)

        self._stop.clear()
        self._error = None
        self._gain = 1.0

        def _worker() -> None:
            try:
                with mic.recorder(
                    samplerate=self._sample_rate,
                    channels=self._channels,
                    blocksize=self._block_frames,
                ) as rec:
                    self._running = True
                    print(
                        f"Capture OK [{self._mode}] '{self._device_name}' "
                        f"@ {self._sample_rate} Hz ch={self._channels} gain=auto",
                        flush=True,
                    )
                    while not self._stop.is_set():
                        data = rec.record(numframes=self._block_frames)
                        arr = np.asarray(data, dtype=np.float32)
                        if arr.size == 0:
                            continue
                        # Work mono once — pipeline only needs mono; skips stereo amplify
                        mono = arr.mean(axis=1) if arr.ndim > 1 else arr.reshape(-1)
                        raw_rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
                        raw_peak = float(np.max(np.abs(mono)) + 1e-12)

                        # Mild adaptive gain — cap hard so gunfire/voice don't clip
                        if self._auto_gain and raw_rms > 1e-6:
                            desired = self._target_rms / raw_rms
                            # Never boost more than ~8×; allow mild attenuation
                            desired = float(np.clip(desired, 0.35, 8.0))
                            self._gain = 0.90 * self._gain + 0.10 * desired
                        g = self._gain if self._auto_gain else 1.0
                        mono_g = np.clip(mono * g, -1.0, 1.0).astype(np.float32)
                        rms = float(np.sqrt(np.mean(np.square(mono_g))) + 1e-12)
                        peak = float(np.max(np.abs(mono_g)))
                        # UI meter slightly less hot
                        rms_ui = min(1.0, rms * 6.0)
                        peak_ui = min(1.0, peak * 2.5)
                        with self._lock:
                            self._rms = rms_ui
                            self._peak = peak_ui
                        if self._on_level:
                            self._on_level(rms_ui, peak_ui)
                        if self._on_pcm:
                            # Pass mono (N,) — pipeline no longer re-averages stereo
                            self._on_pcm(mono_g, self._sample_rate)
            except Exception as exc:  # noqa: BLE001
                self._error = str(exc)
                print(f"Capture error: {exc}", flush=True)
            finally:
                self._running = False
                with self._lock:
                    self._rms = 0.0
                    self._peak = 0.0

        self._thread = threading.Thread(target=_worker, name=f"Capture-{self._mode}", daemon=True)
        self._thread.start()
        for _ in range(80):
            if self._running or self._error:
                break
            time.sleep(0.02)
        if self._error:
            raise RuntimeError(f"Capture failed on '{self._device_name}': {self._error}")
        if not self._running:
            raise RuntimeError(f"Capture did not start on '{self._device_name}'.")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.5)
            self._thread = None
        self._running = False
        with self._lock:
            self._rms = 0.0
            self._peak = 0.0


class OutputLoopbackCapture(_BaseCapture):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._mode = "loopback"

    def _resolve(self):
        return resolve_loopback_microphone(self._device_name_pref)


class MicrophoneCapture(_BaseCapture):
    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("channels", 1)
        super().__init__(**kwargs)
        self._mode = "mic"

    def _resolve(self):
        return resolve_microphone(self._device_name_pref)

    def start(self) -> None:
        """Try preferred mic, then other mics — some devices AssertionError on open."""
        if self._running:
            return
        candidates: list[str | None] = []
        if self._device_name_pref:
            candidates.append(self._device_name_pref)
        # Prefer headset mics over flaky webcam defaults
        mics = list_microphones()

        def _mic_rank(name: str) -> tuple[int, str]:
            n = name.lower()
            if "webcam" in n or "camera" in n or "c310" in n:
                return (2, n)
            if "headset" in n or "corsair" in n or "headphone" in n:
                return (0, n)
            return (1, n)

        for d in sorted(mics, key=lambda x: _mic_rank(x.name)):
            if d.name not in candidates:
                candidates.append(d.name)
        last_err: Exception | None = None
        for name in candidates:
            self._device_name_pref = name
            try:
                super().start()
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                self.stop()
                print(f"Mic open failed '{name}': {exc}", flush=True)
                continue
        raise RuntimeError(f"No microphone could be opened: {last_err}")


if __name__ == "__main__":
    print("Devices:")
    for d in list_devices():
        kind = "OUT" if d.is_loopback else "MIC"
        print(f"  [{kind}] {d.name}")
    cap = OutputLoopbackCapture()
    cap.start()
    print("device", cap.device_name)
    t0 = time.time()
    while time.time() - t0 < 3:
        r, p = cap.levels()
        print(f"\r level {r:.3f}", end="", flush=True)
        time.sleep(0.1)
    cap.stop()
    print()

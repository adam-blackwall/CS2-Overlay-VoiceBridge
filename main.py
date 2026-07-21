"""
CS2 Voice Overlay 1.0.4 — Counter-Strike 2 only

Team voice → live STT → translate callouts → overlay.
External process only — does NOT inject into cs2.exe.

Usage:
  python main.py
  python main.py --lang de --model base
  python main.py --list-devices
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
import traceback
import warnings

warnings.filterwarnings("ignore", message="data discontinuity in recording")

from PySide6.QtCore import QSharedMemory, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from capture import (
    MicrophoneCapture,
    OutputLoopbackCapture,
    list_devices,
    list_loopback_outputs,
    list_microphones,
)
from config import get_output_device, load_settings, save_settings, set_output_device
from languages import OUTPUT_LANGUAGES, cycle_lang, get_lang, lang_help_text
from overlay import MockPipeline, OverlayBus, OverlayUpdate, OverlayWindow
from pipeline import PipelineEvent, SpeechPipeline
from stt import detect_device

LOCK_KEY = "cs2_voice_overlay_1_0_4"
CRASH_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log")


def _print_banner() -> None:
    print("=" * 56, flush=True)
    print("  CS2 Voice Overlay — 1.0.4 (Counter-Strike 2 ONLY)", flush=True)
    print("  • Team-Voice / Callouts → STT → Übersetzung", flush=True)
    print("  • Optimized for CS2 slang (not general YouTube)", flush=True)
    print("  • External only — no inject into cs2.exe / VAC-safe design", flush=True)
    dev, ctype = detect_device()
    print(f"  • Compute: {dev} / {ctype}", flush=True)
    print("  • Target langs:", ", ".join(lang.label for lang in OUTPUT_LANGUAGES), flush=True)
    print("=" * 56, flush=True)
    print("Beenden: Esc im Overlay oder Ctrl+C", flush=True)
    print("Erfolg in Konsole: [tick] … und [heard] …", flush=True)


def _log_crash(exc: BaseException) -> None:
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("\n---\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except OSError:
        pass


def run_list_devices() -> int:
    _print_banner()
    print("\nOUT (loopback):\n", flush=True)
    for d in list_loopback_outputs():
        mark = "  << DEFAULT" if d.is_default_speaker_loopback else ""
        print(f"  • {d.name}{mark}", flush=True)
    print("\nMIC:\n", flush=True)
    for d in list_microphones():
        mark = "  << DEFAULT" if d.is_default_mic else ""
        print(f"  • {d.name}{mark}", flush=True)
    return 0


def _event_to_update(ev: PipelineEvent) -> OverlayUpdate:
    return OverlayUpdate(
        text=ev.text,
        status=ev.status,  # type: ignore[arg-type]
        source_lang=ev.source_lang,
        target_lang=ev.target_lang,
        level=ev.level,
        device_label=ev.device_label,
        speaker=ev.speaker,
        stream=ev.stream or ev.kind in ("partial", "status", "lang", "level", "error"),
        commit_current=ev.commit_current,
    )


def run_app(
    *,
    use_mock: bool,
    device_name: str | None,
    target_lang: str,
    model_size: str,
    no_stt: bool,
    word_delay_ms: int,
    silence_ms: int,
    interval_ms: int,
    window_ms: int,
) -> int:
    _print_banner()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("CS2 Voice Overlay 1.0.4")

    shared = QSharedMemory(LOCK_KEY)
    if not shared.create(1):
        QMessageBox.information(
            None,
            "CS2 Voice Overlay",
            "Läuft bereits.\nTaskleiste prüfen oder python.exe beenden.",
        )
        return 0

    def _release_lock() -> None:
        if shared.isAttached():
            shared.detach()

    atexit.register(_release_lock)

    bus = OverlayBus()
    window = OverlayWindow(bus)
    window.show()
    window.bring_to_front()
    app.processEvents()

    lang = get_lang(target_lang)
    bus.push(
        OverlayUpdate(
            status="listening",
            source_lang="auto",
            target_lang=lang.code,
            text=f"Zielsprache: {lang.name_de} — starte Audio…",
            stream=True,
        )
    )
    app.processEvents()

    capture = None
    mock = None
    pipeline = None
    poll = QTimer()
    event_poll = QTimer()
    capture_mode = str(load_settings().get("capture_mode") or "out")

    def _apply_lang(code: str) -> None:
        new = get_lang(code)
        if pipeline is None:
            bus.push(
                OverlayUpdate(
                    target_lang=new.code,
                    text=f"Zielsprache: {new.name_de} ({new.label})",
                    stream=True,
                    status="listening",
                )
            )
        else:
            pipeline.set_target_lang(new.code)

    def _on_cycle(step: int) -> None:
        cur = (
            pipeline.target_lang.code
            if pipeline is not None
            else window.state.target_lang
        )
        _apply_lang(cycle_lang(cur, step).code)

    window.on_cycle_language = _on_cycle
    window.on_set_language = _apply_lang

    def _pin_last() -> None:
        if pipeline is None:
            bus.push(
                OverlayUpdate(
                    text="Nichts zum Merken.",
                    stream=True,
                    status="listening",
                )
            )
            return
        msg = pipeline.pin_last()
        if not msg:
            bus.push(
                OverlayUpdate(
                    text="Nichts zum Merken.",
                    stream=True,
                    status="listening",
                )
            )

    window.on_pin_last = _pin_last

    device_entries: list[tuple[str, str]] = []
    for d in list_loopback_outputs():
        mark = "★ " if d.is_default_speaker_loopback else ""
        device_entries.append((f"out:{d.name}", f"OUT  {mark}{d.name}"))
    for d in list_microphones():
        mark = "★ " if d.is_default_mic else ""
        device_entries.append((f"mic:{d.name}", f"MIC  {mark}{d.name}"))
    window.set_output_devices(device_entries)

    def _start_capture(spec: str) -> None:
        nonlocal capture, device_name, capture_mode
        mode = "out"
        name = spec
        if spec.startswith("mic:"):
            mode, name = "mic", spec[4:]
        elif spec.startswith("out:"):
            mode, name = "out", spec[4:]
        device_name = name or None
        capture_mode = mode
        if name:
            set_output_device(name)
        save_settings({"capture_mode": mode, "output_device": name or ""})
        print(f"Switching capture → [{mode}] {name or '(default)'}", flush=True)
        if capture is not None:
            capture.stop()
        on_pcm = pipeline.feed_pcm if pipeline is not None else None
        if mode == "mic":
            capture = MicrophoneCapture(
                device_name=name or None, on_pcm=on_pcm, auto_gain=True
            )
        else:
            capture = OutputLoopbackCapture(
                device_name=name or None, on_pcm=on_pcm, auto_gain=True
            )
        capture.start()
        bus.push(
            OverlayUpdate(
                status="listening",
                device_label=f"{mode.upper()}: {capture.device_name}",
                text=f"Audio: {mode.upper()} — {capture.device_name}",
                stream=True,
            )
        )

    def _set_device(name: str) -> None:
        try:
            _start_capture(name)
        except Exception as exc:  # noqa: BLE001
            _log_crash(exc)
            bus.push(
                OverlayUpdate(
                    status="muted",
                    text=f"Gerät fehlgeschlagen: {exc}",
                    stream=True,
                )
            )
            QMessageBox.warning(window, "Audio-Gerät", f"Konnte Audio nicht öffnen:\n\n{exc}")

    window.on_set_device = _set_device

    def _shutdown() -> None:
        poll.stop()
        event_poll.stop()
        if pipeline is not None:
            try:
                pipeline.stop()
            except Exception:  # noqa: BLE001
                pass
        if capture is not None:
            try:
                capture.stop()
            except Exception:  # noqa: BLE001
                pass
        _release_lock()

    app.aboutToQuit.connect(_shutdown)

    if use_mock:
        mock = MockPipeline(bus)
        print("Mode: --mock", flush=True)
        window.bring_to_front()
        return app.exec()

    if not device_name:
        device_name = get_output_device()

    def _start_backend() -> None:
        nonlocal capture, pipeline, capture_mode
        try:
            if not no_stt:
                pipeline = SpeechPipeline(
                    target_lang=lang.code,
                    model_size=model_size,
                    word_delay_ms=word_delay_ms,
                    silence_ms=silence_ms,
                    interval_ms=interval_ms,
                    window_ms=max(window_ms, 2800),
                    energy_threshold=0.0008,
                    min_speech_ms=50,
                )
            mode = str(load_settings().get("capture_mode") or "out")
            capture_mode = mode
            if device_name:
                _start_capture(f"{mode}:{device_name}")
            else:
                _start_capture(f"{mode}:")
        except Exception as exc:  # noqa: BLE001
            _log_crash(exc)
            print(f"ERROR: {exc}", file=sys.stderr, flush=True)
            bus.push(
                OverlayUpdate(
                    status="muted",
                    text=f"Capture fehlgeschlagen: {exc}",
                    stream=True,
                )
            )
            QMessageBox.warning(
                window,
                "Audio-Capture",
                f"Audio konnte nicht geöffnet werden:\n\n{exc}\n\n"
                "Tipp: AUDIO → MIC (Corsair) und laut sprechen.",
            )
            capture = None
            pipeline = None
            return

        bus.push(
            OverlayUpdate(
                status="listening",
                device_label=f"{capture.mode.upper()}: {capture.device_name}",
                target_lang=lang.code,
                text="CS2: Team-Voice laufen lassen (OUT=Headset) oder MIC. Warte auf [heard]…",
                stream=True,
            )
        )
        print(f"Loopback/Mic: {capture.device_name} @ {capture.sample_rate} Hz", flush=True)
        if pipeline:
            print(f"Whisper model: {model_size} on {pipeline.device_info}", flush=True)
            st = pipeline.db.stats()
            print(
                f"Learning DB: glossary={st['glossary']} | SFX={pipeline.sfx.count()}",
                flush=True,
            )
            pipeline.start()

        def _poll_levels() -> None:
            if capture is None or not capture.running:
                return
            if pipeline is None:
                rms, _peak = capture.levels()
                bus.push(OverlayUpdate(level=rms, status="listening", stream=True))

        def _poll_events() -> None:
            if pipeline is None:
                return
            for _ in range(32):
                try:
                    ev = pipeline.events.get_nowait()
                except Exception:
                    break
                bus.push(_event_to_update(ev))

        poll.timeout.connect(_poll_levels)
        poll.start(50)
        if pipeline is not None:
            event_poll.timeout.connect(_poll_events)
            event_poll.start(16)

        silent_ticks = {"n": 0}

        def _watch_silence() -> None:
            if capture is None or not capture.running:
                return
            rms, _p = capture.levels()
            if rms < 0.02:
                silent_ticks["n"] += 1
            else:
                silent_ticks["n"] = 0
            if silent_ticks["n"] == 40:
                bus.push(
                    OverlayUpdate(
                        text="Kein Pegel! AUDIO → MIC und sprechen, oder OUT = Hörgerät.",
                        stream=True,
                        status="listening",
                    )
                )
                print("WARN: nearly silent capture", flush=True)

        silence_timer = QTimer()
        silence_timer.timeout.connect(_watch_silence)
        silence_timer.start(100)
        app.aboutToQuit.connect(silence_timer.stop)
        window.bring_to_front()

    QTimer.singleShot(100, _start_backend)

    print("Hotkeys: Esc quit | Ctrl+Shift+L language | Ctrl+Shift+S merken | Ctrl+Shift+C LOCKED/UNLOCKED", flush=True)
    print("Languages:", lang_help_text(), flush=True)
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CS2 Voice Overlay STABLE")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device", type=str, default=None)
    parser.add_argument("--lang", type=str, default="de")
    parser.add_argument(
        "--model",
        type=str,
        default="base",
        help="Whisper model: tiny (fast) | base (default, better speech) | small",
    )
    parser.add_argument("--no-stt", action="store_true")
    parser.add_argument(
        "--word-delay",
        type=int,
        default=140,
        help="ms between words when revealing translation (0 = all at once)",
    )
    parser.add_argument("--silence-ms", type=int, default=550)
    parser.add_argument("--interval", type=int, default=550)
    parser.add_argument(
        "--window",
        type=int,
        default=3000,
        help="Audio context window ms (default 3000 for better speech)",
    )
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)

    word_delay = args.word_delay
    silence_ms = args.silence_ms
    interval_ms = args.interval
    window_ms = args.window
    model_size = args.model or "base"
    if args.fast:
        word_delay = min(word_delay, 60)
        silence_ms = min(silence_ms, 350)
        interval_ms = min(interval_ms, 350)
        window_ms = min(window_ms, 1100)

    try:
        if args.list_devices:
            return run_list_devices()
        return run_app(
            use_mock=args.mock,
            device_name=args.device,
            target_lang=args.lang,
            model_size=model_size,
            no_stt=args.no_stt,
            word_delay_ms=word_delay,
            silence_ms=silence_ms,
            interval_ms=interval_ms,
            window_ms=window_ms,
        )
    except Exception as exc:  # noqa: BLE001
        _log_crash(exc)
        print(f"FATAL: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        try:
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "CS2 Voice Overlay", f"{exc}\n\ncrash.log")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

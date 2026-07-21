"""
CS2 Voice Overlay — external UI only
------------------------------------
Transparent always-on-top window. Separate process from CS2.
Does NOT inject into, hook, or read any game process (VAC-safe design).

Slots (data contract for capture / STT / translation):
  - status:        listening | processing | idle | muted
  - line_current:  latest translation line
  - line_history:  last N lines (oldest → newest)
  - source_lang / target_lang
  - level:         0..1 audio level (from system output loopback)
  - device_label:  capture device name (optional)

Hotkeys:
  Ctrl+Shift+O  toggle overlay visible
  Ctrl+Shift+C  toggle LOCKED / UNLOCKED (click-through)
  Ctrl+Shift+Up / Down  opacity
  Ctrl+Shift+R  snap to top-right (primary screen)
  Esc           quit

Lock chip (top-center):
  LOCKED  (green)  — mouse goes through overlay to game / apps behind
  UNLOCKED (red)   — overlay buttons and menus are interactive

Run UI demo only:
  python overlay.py
Full capture + overlay:
  python main.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QObject, QRectF
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QShortcut,
    QPainter,
    QBrush,
    QPen,
    QAction,
    QActionGroup,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
    QPushButton,
    QMenu,
)

from languages import OUTPUT_LANGUAGES, get_lang

# ---------------------------------------------------------------------------
# Data contract — future STT/translation scripts push OverlayUpdate here
# ---------------------------------------------------------------------------

Status = Literal["idle", "listening", "processing", "muted"]


@dataclass
class OverlayUpdate:
    """One message the pipeline can send into the overlay."""

    text: str | None = None
    status: Status | None = None
    source_lang: str | None = None
    target_lang: str | None = None
    level: float | None = None
    device_label: str | None = None
    speaker: str | None = None  # A, B, C… for conversation structure
    clear_history: bool = False
    # True: replace line_current only (word-by-word streaming, no history)
    stream: bool = False
    # Push line_current into history and clear (start of new utterance)
    commit_current: bool = False


@dataclass
class OverlayState:
    status: Status = "idle"
    line_current: str = ""
    line_history: list[str] = field(default_factory=list)
    source_lang: str = "auto"
    target_lang: str = "de"
    level: float = 0.0
    device_label: str = ""
    speaker: str = ""
    max_history: int = 8  # more room for multi-speaker dialogue

    def apply(self, update: OverlayUpdate) -> None:
        if update.status is not None:
            self.status = update.status
        if update.source_lang is not None:
            self.source_lang = update.source_lang
        if update.target_lang is not None:
            self.target_lang = update.target_lang
        if update.level is not None:
            self.level = max(0.0, min(1.0, float(update.level)))
        if update.device_label is not None:
            self.device_label = update.device_label
        if update.speaker is not None:
            self.speaker = update.speaker
        if update.clear_history:
            self.line_history.clear()
            self.line_current = ""
        if update.commit_current:
            if self.line_current.strip():
                self.line_history.append(self.line_current.strip())
                self.line_history = self.line_history[-self.max_history :]
            self.line_current = ""
        if update.text is not None:
            text = update.text.strip()
            if update.stream:
                # Progressive / live line — do not push history
                # empty string clears line → waiting dots in UI
                self.line_current = text
            elif text:
                if self.line_current and self.line_current != text:
                    self.line_history.append(self.line_current)
                    self.line_history = self.line_history[-self.max_history :]
                self.line_current = text


class WaitingDots(QWidget):
    """Three dots that wave while waiting for speech."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phase = 0
        self.setFixedHeight(36)
        self.setMinimumWidth(80)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(280)

    def _tick(self) -> None:
        self._phase = (self._phase + 1) % 4
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # three dots, active one is brighter / higher
        cx = self.width() / 2
        cy = self.height() / 2 + 4
        spacing = 18
        base_x = cx - spacing
        for i in range(3):
            # wave: phase 0/1/2 lift each dot; phase 3 all mid
            lift = 0.0
            if self._phase < 3 and i == self._phase:
                lift = -7.0
            elif self._phase == 3:
                lift = -2.0 if i == 1 else 0.0
            x = base_x + i * spacing
            y = cy + lift
            active = self._phase < 3 and i == self._phase
            alpha = 230 if active else 110
            r = 5.5 if active else 4.2
            p.setBrush(QBrush(QColor(230, 236, 248, alpha)))
            p.drawEllipse(QPoint(int(x), int(y)), int(r), int(r))
        p.end()


class LevelBar(QWidget):
    """Simple horizontal level meter for loopback proof."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self.setFixedHeight(8)
        self.setMinimumWidth(120)

    def set_level(self, value: float) -> None:
        v = max(0.0, min(1.0, value))
        # Skip repaint for tiny changes (meter was a constant Qt paint storm)
        if abs(v - self._level) < 0.03 and not (v < 0.02 < self._level or self._level < 0.02 < v):
            self._level = v
            return
        self._level = v
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 28)))
        p.drawRoundedRect(r, 3, 3)
        if self._level > 0.001:
            fill = QRectF(r)
            fill.setWidth(max(2.0, r.width() * self._level))
            if self._level > 0.75:
                color = QColor("#ff6b6b")
            elif self._level > 0.4:
                color = QColor("#f5c542")
            else:
                color = QColor("#3ddc84")
            p.setBrush(QBrush(color))
            p.drawRoundedRect(fill, 3, 3)
        p.end()


class OverlayBus(QObject):
    """Simple in-process bus. Later: replace with socket/file watcher."""

    updated = Signal(object)  # OverlayUpdate

    def push(self, update: OverlayUpdate) -> None:
        self.updated.emit(update)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

STATUS_LABEL = {
    "idle": "IDLE",
    "listening": "LISTENING",
    "processing": "PROCESSING",
    "muted": "MUTED",
}

STATUS_COLOR = {
    "idle": "#8b95a8",
    "listening": "#3ddc84",
    "processing": "#f5c542",
    "muted": "#ff6b6b",
}


class LockChip(QWidget):
    """
    Always-on-top LOCKED/UNLOCKED control.

    Lives in its own window so it stays clickable while the main overlay
    is click-through (LOCKED).
    """

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CS2 Overlay Lock")
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn = QPushButton("UNLOCKED")
        self.btn.setObjectName("lock_chip")
        self.btn.setFlat(True)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setToolTip("LOCKED = Klicks gehen durch · UNLOCKED = Overlay bedienen")
        self.btn.clicked.connect(self.clicked.emit)
        # Soft outline so text stays readable on bright CS2 scenes
        fx = QGraphicsDropShadowEffect(self.btn)
        fx.setBlurRadius(10)
        fx.setOffset(0, 1)
        fx.setColor(QColor(0, 0, 0, 220))
        self.btn.setGraphicsEffect(fx)
        lay.addWidget(self.btn)
        self.set_locked(False)
        self.adjustSize()

    def set_locked(self, locked: bool) -> None:
        if locked:
            self.btn.setText("LOCKED")
            color = "#3ddc84"  # green text only
        else:
            self.btn.setText("UNLOCKED")
            color = "#ff6b6b"  # red text only
        # Fully transparent control — only the label color is visible
        self.btn.setStyleSheet(
            f"""
            #lock_chip {{
                color: {color};
                background: transparent;
                background-color: transparent;
                border: none;
                border-radius: 0px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1.4px;
                padding: 2px 8px;
                min-width: 90px;
            }}
            #lock_chip:hover {{
                color: {color};
                background: transparent;
                background-color: transparent;
                border: none;
            }}
            #lock_chip:pressed {{
                color: {color};
                background: transparent;
                background-color: transparent;
                border: none;
            }}
            #lock_chip:focus {{
                outline: none;
                border: none;
                background: transparent;
            }}
            """
        )
        self.adjustSize()


class OverlayWindow(QWidget):
    def __init__(self, bus: OverlayBus) -> None:
        super().__init__()
        self.bus = bus
        self.state = OverlayState()
        # locked == click-through (game/apps behind remain clickable)
        self._click_through = False
        # Nearly transparent overall (user can still tweak with Ctrl+Shift+Up/Down)
        self._opacity = 0.98
        self._drag_offset: QPoint | None = None
        # Fixed corner on primary monitor (default: top-right)
        self._corner = "top-right"
        self._position_locked = True
        self._margin = 0  # flush to screen edge
        self.on_cycle_language = None  # optional Callable[[int], None]
        self.on_set_language = None  # optional Callable[[str], None] — target lang code
        self.on_pin_last = None  # optional Callable[[], None] — save phrase to DB
        self.on_set_device = None  # optional Callable[[str], None] — loopback output name
        self._output_devices: list[tuple[str, str]] = []  # (name, label)

        self._setup_window()
        self._build_ui()
        self._bind_hotkeys()
        self._place_corner()

        # Separate lock control so it stays clickable while main is LOCKED
        self._lock_chip = LockChip()
        self._lock_chip.clicked.connect(self.toggle_click_through)
        self._lock_chip.show()
        self._sync_lock_chip()

        self.bus.updated.connect(self._on_update)
        self.setWindowOpacity(self._opacity)
        self._render()
        # Start in waiting state (waving dots)
        self._set_waiting(True)
        self._place_corner()
        self._sync_lock_chip()

    # --- window chrome -----------------------------------------------------

    def _setup_window(self) -> None:
        self.setWindowTitle("CS2 Voice Overlay 1.0.4")
        # Frameless + always-on-top, but still a normal window (taskbar entry).
        # Avoid Qt.Tool + ShowWithoutActivating — that made the window easy to "lose".
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(520)
        self.setMaximumWidth(900)

    def bring_to_front(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Panel background via nested widget — nearly glass-clear
        self.panel = QWidget()
        self.panel.setObjectName("panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setSpacing(6)

        # Header: status | (lock chip floats above center) | language
        header = QHBoxLayout()
        self.lbl_status = QLabel()
        self.lbl_status.setObjectName("status")
        self.btn_lang = QPushButton("AUTO → DE")
        self.btn_lang.setObjectName("lang_btn")
        self.btn_lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lang.setToolTip("Zielsprache wählen (Klick)")
        self.btn_lang.clicked.connect(self._open_lang_menu)
        self.lbl_hint = QLabel(
            "LOCKED grün = Klicks durch · UNLOCKED rot = Overlay · Ctrl+Shift+C · Esc"
        )
        self.lbl_hint.setObjectName("hint")
        # Reserve vertical space so the floating lock chip doesn't cover content
        self._lock_spacer = QLabel("")
        self._lock_spacer.setFixedHeight(22)
        self._lock_spacer.setMinimumWidth(110)
        self._lock_spacer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.lbl_status, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        header.addWidget(self._lock_spacer, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        header.addWidget(self.btn_lang, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        panel_layout.addLayout(header)

        self._lang_menu = QMenu(self)
        self._lang_menu.setObjectName("lang_menu")
        self._lang_actions = QActionGroup(self)
        self._lang_actions.setExclusive(True)
        for lang in OUTPUT_LANGUAGES:
            act = QAction(f"{lang.label}  —  {lang.name_de}", self)
            act.setCheckable(True)
            act.setData(lang.code)
            if lang.code == "de":
                act.setChecked(True)
            self._lang_actions.addAction(act)
            self._lang_menu.addAction(act)
            act.triggered.connect(self._on_lang_action)

        # Audio level + output device picker
        level_row = QHBoxLayout()
        self.btn_device = QPushButton("AUDIO ▾")
        self.btn_device.setObjectName("device_btn")
        self.btn_device.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_device.setToolTip("OUT (was du hörst) oder MIC wählen")
        self.btn_device.clicked.connect(self._open_device_menu)
        self.level_bar = LevelBar()
        level_row.addWidget(self.btn_device)
        level_row.addWidget(self.level_bar, 1)
        panel_layout.addLayout(level_row)

        self._device_menu = QMenu(self)
        self._device_actions = QActionGroup(self)
        self._device_actions.setExclusive(True)

        # Slot: history (older lines)
        self.lbl_history = QLabel()
        self.lbl_history.setObjectName("history")
        self.lbl_history.setWordWrap(True)
        self.lbl_history.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        panel_layout.addWidget(self.lbl_history)

        # Slot: current phrase (1–3 lines, wraps with sentence length)
        self.lbl_current = QLabel()
        self.lbl_current.setObjectName("current")
        self.lbl_current.setWordWrap(True)
        self.lbl_current.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.lbl_current.setMinimumWidth(420)
        self.lbl_current.setMaximumWidth(640)
        # Room for up to ~3 lines (updated dynamically in _fit_current_label)
        self.lbl_current.setMinimumHeight(20)
        self.lbl_current.setMaximumHeight(72)
        panel_layout.addWidget(self.lbl_current)

        self.wait_dots = WaitingDots()
        self.wait_dots.setObjectName("wait_dots")
        panel_layout.addWidget(self.wait_dots, 0, Qt.AlignmentFlag.AlignHCenter)

        # Footer hint
        panel_layout.addWidget(self.lbl_hint)

        root.addWidget(self.panel)

        # No heavy panel shadow (stays glass-clear). Text gets its own outline via CSS-ish effects.
        self.panel.setGraphicsEffect(None)

        def _text_shadow(widget, blur: int = 10, alpha: int = 220) -> None:
            fx = QGraphicsDropShadowEffect(widget)
            fx.setBlurRadius(blur)
            fx.setOffset(0, 1)
            fx.setColor(QColor(0, 0, 0, alpha))
            widget.setGraphicsEffect(fx)

        # Keep text readable on CS2 while panel is ~98% transparent
        _text_shadow(self.lbl_current, blur=14, alpha=230)
        _text_shadow(self.lbl_history, blur=10, alpha=200)
        _text_shadow(self.lbl_status, blur=8, alpha=180)

        self.setStyleSheet(
            """
            #panel {
                /* ~98% transparent panel (alpha ≈ 5/255) */
                background-color: rgba(8, 12, 20, 5);
                border-radius: 0px;
                border: none;
            }
            #status {
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1.2px;
                padding: 2px 0;
            }
            #lang_btn {
                color: rgba(240, 245, 255, 220);
                background-color: rgba(80, 120, 200, 28);
                border: 1px solid rgba(140, 170, 255, 40);
                border-radius: 6px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
                padding: 3px 10px;
                min-width: 80px;
            }
            #lang_btn:hover {
                background-color: rgba(100, 150, 240, 55);
            }
            #lang_btn:pressed {
                background-color: rgba(60, 100, 180, 70);
            }
            #device_btn {
                color: rgba(230, 238, 250, 200);
                background-color: rgba(60, 90, 130, 28);
                border: 1px solid rgba(120, 150, 200, 35);
                border-radius: 5px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.3px;
                padding: 2px 8px;
                max-width: 200px;
            }
            #device_btn:hover {
                background-color: rgba(90, 130, 190, 50);
            }
            #history {
                color: rgba(220, 228, 240, 200);
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                min-height: 16px;
            }
            #current {
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                font-weight: 600;
                min-height: 20px;
                max-height: 72px;
                min-width: 420px;
                padding: 0px;
            }
            #hint {
                color: rgba(180, 190, 210, 90);
                font-family: 'Segoe UI', sans-serif;
                font-size: 9px;
            }
            """
        )

    def _bind_hotkeys(self) -> None:
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, self.toggle_visible)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.toggle_click_through)
        QShortcut(QKeySequence("Ctrl+Shift+Up"), self, lambda: self.adjust_opacity(+0.08))
        QShortcut(QKeySequence("Ctrl+Shift+Down"), self, lambda: self.adjust_opacity(-0.08))
        QShortcut(QKeySequence("Ctrl+Shift+R"), self, self._place_corner)
        QShortcut(QKeySequence("Ctrl+Shift+L"), self, lambda: self._cycle_lang(+1))
        QShortcut(QKeySequence("Ctrl+Shift+K"), self, lambda: self._cycle_lang(-1))
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._pin_last)
        QShortcut(QKeySequence("Escape"), self, QApplication.instance().quit)

    def _cycle_lang(self, step: int) -> None:
        if callable(self.on_cycle_language):
            self.on_cycle_language(step)

    def _pin_last(self) -> None:
        if callable(self.on_pin_last):
            self.on_pin_last()

    def set_output_devices(self, devices: list[tuple[str, str]]) -> None:
        """devices: list of (device_name, display_label)."""
        self._output_devices = list(devices)
        self._device_menu.clear()
        for act in list(self._device_actions.actions()):
            self._device_actions.removeAction(act)
        for name, label in self._output_devices:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setData(name)
            self._device_actions.addAction(act)
            self._device_menu.addAction(act)
            act.triggered.connect(self._on_device_action)
        self._sync_device_button()

    def _open_device_menu(self) -> None:
        if self._click_through:
            self.toggle_click_through()
        if not self._device_actions.actions():
            return
        self._sync_device_button()
        pos = self.btn_device.mapToGlobal(self.btn_device.rect().bottomLeft())
        self._device_menu.popup(pos)

    def _on_device_action(self) -> None:
        act = self.sender()
        if not isinstance(act, QAction):
            return
        name = str(act.data() or "")
        if not name:
            return
        if callable(self.on_set_device):
            self.on_set_device(name)
        self.state.device_label = name
        self._sync_device_button()

    def _sync_device_button(self) -> None:
        cur = self.state.device_label or ""
        short = cur
        if len(short) > 22:
            short = short[:19] + "…"
        self.btn_device.setText(short if short else "OUT ▾")
        self.btn_device.setToolTip(cur or "Sound-Output (Loopback) wählen")
        for act in self._device_actions.actions():
            act.setChecked(str(act.data()) == cur)

    def _open_lang_menu(self) -> None:
        # Ensure click-through does not block the menu
        if self._click_through:
            self.toggle_click_through()
        # Sync checkmark to current target
        code = get_lang(self.state.target_lang).code
        for act in self._lang_actions.actions():
            act.setChecked(act.data() == code)
        pos = self.btn_lang.mapToGlobal(self.btn_lang.rect().bottomLeft())
        self._lang_menu.popup(pos)

    def _on_lang_action(self) -> None:
        act = self.sender()
        if not isinstance(act, QAction):
            return
        code = str(act.data() or "")
        if not code:
            return
        if callable(self.on_set_language):
            self.on_set_language(code)
        elif callable(self.on_cycle_language):
            # Fallback: cycle until match (should not be needed)
            for _ in range(len(OUTPUT_LANGUAGES)):
                if get_lang(self.state.target_lang).code == code:
                    break
                self.on_cycle_language(+1)
        # Immediate UI feedback
        self.state.target_lang = code
        self._render()

    def _primary_screen_geo(self):
        """Main monitor work area (excludes taskbar)."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return screen.availableGeometry()

    def _place_corner(self, corner: str | None = None) -> None:
        """
        Pin overlay to a corner of the primary (main) screen.
        Default: top-right.
        """
        if corner is not None:
            self._corner = corner
        geo = self._primary_screen_geo()
        if geo is None:
            return
        self.adjustSize()
        w = max(self.width(), 480)
        h = max(self.sizeHint().height(), self.height())
        self.resize(w, h)
        m = self._margin
        c = (self._corner or "top-right").lower().replace("_", "-")

        if c in ("top-right", "tr", "right-top"):
            x = geo.x() + geo.width() - self.width() - m
            y = geo.y() + m
        elif c in ("top-left", "tl", "left-top"):
            x = geo.x() + m
            y = geo.y() + m
        elif c in ("bottom-right", "br", "right-bottom"):
            x = geo.x() + geo.width() - self.width() - m
            y = geo.y() + geo.height() - self.height() - m
        elif c in ("bottom-left", "bl", "left-bottom"):
            x = geo.x() + m
            y = geo.y() + geo.height() - self.height() - m
        elif c in ("bottom-center", "bc"):
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + geo.height() - self.height() - m
        else:
            # fallback top-right
            x = geo.x() + geo.width() - self.width() - m
            y = geo.y() + m
            self._corner = "top-right"

        self.move(int(x), int(y))
        self._sync_lock_chip()

    # --- public actions ----------------------------------------------------

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def toggle_click_through(self) -> None:
        """Toggle LOCKED (click-through) / UNLOCKED (interactive overlay)."""
        self._click_through = not self._click_through
        self._apply_click_through()
        if self._click_through:
            self.lbl_hint.setText(
                "LOCKED — Klicks gehen durch zum Spiel · Chip klicken oder Ctrl+Shift+C"
            )
        else:
            self.lbl_hint.setText(
                "UNLOCKED — Overlay bedienbar · Chip klicken = LOCKED · Esc"
            )

    def adjust_opacity(self, delta: float) -> None:
        # Allow very transparent glass (down to ~15% overall window opacity)
        self._opacity = max(0.15, min(1.0, self._opacity + delta))
        self.setWindowOpacity(self._opacity)

    def _apply_click_through(self) -> None:
        # Qt: transparent for input when LOCKED — mouse reaches windows behind
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            self._click_through,
        )
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self._click_through)
        self.show()
        # Lock chip stays in its own window (never transparent for input)
        if hasattr(self, "_lock_chip") and self._lock_chip is not None:
            self._lock_chip.set_locked(self._click_through)
            self._lock_chip.show()
            self._lock_chip.raise_()
            self._sync_lock_chip()

    def _sync_lock_chip(self) -> None:
        """Pin lock chip to top-center of the overlay."""
        chip = getattr(self, "_lock_chip", None)
        if chip is None:
            return
        chip.adjustSize()
        g = self.frameGeometry()
        x = g.x() + (g.width() - chip.width()) // 2
        y = g.y() + 2
        chip.move(int(x), int(y))
        chip.raise_()

    # --- data → UI ---------------------------------------------------------

    def _on_update(self, update: object) -> None:
        if not isinstance(update, OverlayUpdate):
            return
        # Level-only updates: touch meter only (avoid full restyle + resize thrash)
        level_only = (
            update.level is not None
            and update.text is None
            and update.status is None
            and update.source_lang is None
            and update.target_lang is None
            and update.device_label is None
            and update.speaker is None
            and not update.clear_history
            and not update.commit_current
        )
        self.state.apply(update)
        if level_only:
            self.level_bar.set_level(self.state.level)
            return
        self._render()

    def _render(self) -> None:
        st = self.state
        color = STATUS_COLOR.get(st.status, "#8b95a8")
        status_txt = STATUS_LABEL.get(st.status, st.status.upper())
        if self.lbl_status.text() != status_txt:
            self.lbl_status.setText(status_txt)
            self.lbl_status.setStyleSheet(
                f"#status {{ color: {color}; font-family: 'Segoe UI'; font-size: 11px; "
                f"font-weight: 700; letter-spacing: 1.2px; }}"
            )
        src = (st.source_lang or "auto").upper()
        if src in ("AUTO", ""):
            src = "AUTO"
        tgt = get_lang(st.target_lang).label
        lang_txt = f"{src} → {tgt}"
        if self.btn_lang.text() != lang_txt:
            self.btn_lang.setText(lang_txt)
            code = get_lang(st.target_lang).code
            for act in self._lang_actions.actions():
                act.setChecked(act.data() == code)
        self.level_bar.set_level(st.level)
        if st.device_label:
            self._sync_device_button()

        hist = "\n".join(st.line_history) if st.line_history else ""
        if hist:
            if self.lbl_history.text() != hist:
                self.lbl_history.setText(hist)
            if not self.lbl_history.isVisible():
                self.lbl_history.show()
        else:
            if self.lbl_history.text():
                self.lbl_history.setText("")
            if self.lbl_history.isVisible():
                self.lbl_history.hide()

        cur = (st.line_current or "").strip()
        waiting = not cur
        self._set_waiting(waiting)
        if not waiting:
            if self.lbl_current.text() != cur:
                self._fit_current_label(cur)
                self.adjustSize()
                if self._position_locked:
                    self._place_corner()

    def _fit_current_label(self, text: str) -> None:
        """Wrap current phrase over 1–3 lines depending on length."""
        self.lbl_current.setText(text)
        self.lbl_current.setWordWrap(True)
        # Prefer panel width; fall back to a readable caption width
        margins = 24
        avail = self.width() - margins if self.width() > 100 else 520
        w = int(max(420, min(avail, 620)))
        self.lbl_current.setFixedWidth(w)

        metrics = self.lbl_current.fontMetrics()
        line_h = max(metrics.lineSpacing(), metrics.height())
        # heightForWidth needs the label to know its width
        needed = self.lbl_current.heightForWidth(w)
        if needed < 0:
            # estimate from char width
            avg = max(metrics.horizontalAdvance("x"), 6)
            chars_per_line = max(1, w // avg)
            n_lines = max(1, (len(text) + chars_per_line - 1) // chars_per_line)
            needed = n_lines * line_h
        max_h = int(line_h * 3 + 8)  # up to 3 lines
        min_h = int(line_h + 2)
        h = int(max(min_h, min(needed + 2, max_h)))
        self.lbl_current.setMinimumHeight(h)
        self.lbl_current.setMaximumHeight(max_h)
        self.lbl_current.setFixedHeight(h)

    def _set_waiting(self, waiting: bool) -> None:
        if waiting:
            if self.lbl_current.isVisible():
                self.lbl_current.hide()
            if not self.wait_dots.isVisible():
                self.wait_dots.show()
            if not self.wait_dots._timer.isActive():
                self.wait_dots._timer.start(280)
        else:
            if self.wait_dots.isVisible():
                self.wait_dots.hide()
            if self.wait_dots._timer.isActive():
                self.wait_dots._timer.stop()
            if not self.lbl_current.isVisible():
                self.lbl_current.show()

    # --- drag only when unlocked; default is fixed top-right ---------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._click_through
            and not self._position_locked
        ):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._position_locked
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._sync_lock_chip()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_lock_chip()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._position_locked:
            # Delay so size is final after first show
            QTimer.singleShot(0, self._place_corner)
            QTimer.singleShot(50, self._place_corner)
        QTimer.singleShot(0, self._sync_lock_chip)
        QTimer.singleShot(60, self._sync_lock_chip)
        chip = getattr(self, "_lock_chip", None)
        if chip is not None:
            chip.show()
            chip.raise_()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        chip = getattr(self, "_lock_chip", None)
        if chip is not None:
            chip.hide()

    def closeEvent(self, event) -> None:  # noqa: N802
        chip = getattr(self, "_lock_chip", None)
        if chip is not None:
            chip.close()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Mock pipeline — simulates STT/translation until real scripts exist
# ---------------------------------------------------------------------------

MOCK_LINES = [
    ("en", "Smoke mid, I flash B"),
    ("en", "Two on A site, one default"),
    ("en", "Eco round, save your guns"),
    ("en", "He's low, one shot in apps"),
    ("ru", "Иди на Б, я тебя прикрою"),
    ("de", "Rotiere nach A, ich hole die Bombe"),
    ("en", "Don't peek, they have AWP mid"),
    ("en", "Full buy next, drop me an AK"),
    ("en", "Last guy palace, play time"),
    ("en", "Nice trade, reset and stack B"),
]

# Fake "translations" for the mock (static demos)
MOCK_TRANSLATIONS = {
    "Smoke mid, I flash B": "Smoke Mid, ich flashe B",
    "Two on A site, one default": "Zwei auf A-Site, einer default",
    "Eco round, save your guns": "Eco-Runde, spart die Waffen",
    "He's low, one shot in apps": "Er ist low, One-Shot in Apps",
    "Иди на Б, я тебя прикрою": "Geh auf B, ich decke dich",
    "Rotiere nach A, ich hole die Bombe": "Rotate to A, I'll get the bomb",
    "Don't peek, they have AWP mid": "Nicht peeken, die haben AWP Mid",
    "Full buy next, drop me an AK": "Nächste Runde Fullbuy, drop mir 'ne AK",
    "Last guy palace, play time": "Letzter Typ Palace, spielt die Zeit",
    "Nice trade, reset and stack B": "Schöner Trade, resetten und B stacken",
}


class MockPipeline:
    """Cycles status + fake translations into the bus."""

    def __init__(self, bus: OverlayBus) -> None:
        self.bus = bus
        self._i = 0
        self._phase = 0  # 0 idle gap, 1 listening, 2 processing, 3 show text
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(1600)

        bus.push(
            OverlayUpdate(
                status="idle",
                source_lang="auto",
                target_lang="de",
                text="",
            )
        )

    def _tick(self) -> None:
        if self._phase == 0:
            self.bus.push(OverlayUpdate(status="listening"))
            self._phase = 1
        elif self._phase == 1:
            self.bus.push(OverlayUpdate(status="processing"))
            self._phase = 2
        elif self._phase == 2:
            src_lang, original = MOCK_LINES[self._i % len(MOCK_LINES)]
            translated = MOCK_TRANSLATIONS.get(original, original)
            self.bus.push(
                OverlayUpdate(
                    status="idle",
                    source_lang=src_lang,
                    target_lang="de" if src_lang != "de" else "en",
                    text=translated,
                )
            )
            self._i += 1
            self._phase = 0


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    # High-DPI before QApplication
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    bus = OverlayBus()
    window = OverlayWindow(bus)
    window.show()

    # Demo feed — remove / gate behind --mock when real audio is wired
    mock = MockPipeline(bus)
    _ = mock  # keep alive

    print("CS2 Voice Overlay running.")
    print("  Top-center chip: LOCKED (green) / UNLOCKED (red)")
    print("  Ctrl+Shift+O  toggle visibility")
    print("  Ctrl+Shift+C  lock / unlock (click-through)")
    print("  Ctrl+Shift+Up/Down  opacity")
    print("  Ctrl+Shift+R  reset position")
    print("  Esc  quit")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

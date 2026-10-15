"""Actions: what the bot does, and how they map to screen coordinates.

An Action is game-level (PLAY_MINION / ATTACK / ...). Layout translates it
into clicks/drags. `Layout.auto_detect()` locates the running game window
(position + size, 16:9 letterbox fit) so coordinates adapt to the actual
window instead of assuming 1920x1080; without a detected window it falls
back to the 1920x1080 reference. Pass an explicit Layout to override.
DryRunExecutor is the default: it logs actions instead of touching the mouse.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

from .state import GameState

PLAY_MINION = "PLAY_MINION"
PLAY_SPELL = "PLAY_SPELL"
ATTACK = "ATTACK"
HERO_POWER = "HERO_POWER"
END_TURN = "END_TURN"
MULLIGAN = "MULLIGAN"


@dataclass
class Action:
    kind: str
    source_id: int = 0  # entity id of the card/minion used
    target_id: int = 0  # entity id of the target (0 = none / face handled by kind)
    card_name: str = ""
    position: int = 0  # board position when playing a minion
    meta: dict = field(default_factory=dict)  # extras, e.g. MULLIGAN kept_names

    def __repr__(self) -> str:  # compact logs
        if self.kind == ATTACK:
            return f"ATTACK {self.source_id} -> {self.target_id}"
        if self.kind in (PLAY_MINION, PLAY_SPELL):
            return f"{self.kind} {self.card_name} (id={self.source_id})"
        return self.kind


def action_is_drag(action: Action) -> bool:
    """True for multi-gesture actions (drag): a failed drag can leave
    the cursor holding a card, so the caller sends a right-click cancel
    after a MISFIRE (see bot.py)."""
    if action.kind in (PLAY_MINION, ATTACK):
        return True
    if action.kind == PLAY_SPELL and action.target_id:
        return True
    return False


@dataclass
class Click:
    x: int
    y: int
    kind: str = "click"  # click | drag_start | drag_end


def _detect_window_windows(titles: tuple[str, ...]) -> tuple[int, int, int, int] | None:
    """Windows: exact title, then partial match; client area via ClientToScreen."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()  # physical pixels, matching pyautogui
    except Exception:
        pass

    def client_rect(hwnd) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        return (pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top)

    for title in titles:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            found = client_rect(hwnd)
            if found:
                return found
    found_hwnds: list[int] = []

    def _enum(hwnd, _param):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if any(t.lower() in buf.value.lower() for t in titles) and user32.IsWindowVisible(hwnd):
                found_hwnds.append(hwnd)
        return True

    enum_cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(_enum)
    user32.EnumWindows(enum_cb, 0)
    for hwnd in found_hwnds:
        found = client_rect(hwnd)
        if found:
            return found
    return None


def _detect_window_macos(titles: tuple[str, ...]) -> tuple[int, int, int, int] | None:
    """macOS: `bounds of window 1` via AppleScript (needs accessibility permission)."""
    for title in titles:
        script = f'tell application "System Events" to get bounds of window 1 of process "{title}"'
        try:
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=5)
        except Exception:
            return None
        if out.returncode != 0:
            continue
        m = re.search(r"\{\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\s*\}", out.stdout)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            return (x1, y1, x2 - x1, y2 - y1)
    return None


def _detect_window_linux(titles: tuple[str, ...]) -> tuple[int, int, int, int] | None:
    """Linux: xdotool, falling back to wmctrl. Both may be absent."""
    for title in titles:
        try:
            out = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", title],
                                 capture_output=True, text=True, timeout=5)
        except Exception:
            break  # xdotool missing/unusable; try wmctrl
        for wid in out.stdout.split():
            try:
                geo = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid],
                                     capture_output=True, text=True, timeout=5)
            except Exception:
                continue
            kv = dict(line.split("=", 1) for line in geo.stdout.splitlines() if "=" in line)
            try:
                return (int(kv["X"]), int(kv["Y"]), int(kv["WIDTH"]), int(kv["HEIGHT"]))
            except (KeyError, ValueError):
                continue
    try:
        out = subprocess.run(["wmctrl", "-lG"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 7 and any(t.lower() in " ".join(parts[6:]).lower() for t in titles):
            try:
                return tuple(map(int, parts[2:6]))  # type: ignore[return-value]
            except ValueError:
                continue
    return None


def _detect_window_rect(titles: tuple[str, ...]) -> tuple[int, int, int, int] | None:
    if sys.platform.startswith("win"):
        return _detect_window_windows(titles)
    if sys.platform == "darwin":
        return _detect_window_macos(titles)
    return _detect_window_linux(titles)


class Layout:
    """Board geometry: reference coords are 1920x1080, scaled to the
    actual board rect (width/height + window offset)."""

    def __init__(self, width: int = 1920, height: int = 1080,
                 offset_x: int = 0, offset_y: int = 0) -> None:
        self.width = width
        self.height = height
        self.offset_x = offset_x
        self.offset_y = offset_y

    @classmethod
    def from_window_rect(cls, x: int, y: int, w: int, h: int,
                         aspect: float = 16 / 9) -> "Layout":
        """Fit the 16:9 board inside the window rect (letterbox).

        Hearthstone renders 16:9; on a wider/taller window the board is
        centered with black bars. A degenerate rect falls back to the
        1920x1080 reference.
        """
        if w <= 0 or h <= 0:
            return cls()
        board_w = min(w, int(h * aspect))
        board_h = min(h, int(w / aspect))
        return cls(
            width=board_w,
            height=board_h,
            offset_x=x + (w - board_w) // 2,
            offset_y=y + (h - board_h) // 2,
        )

    @classmethod
    def auto_detect(cls, titles: tuple[str, ...] = ("Hearthstone", "炉石传说")) -> "Layout":
        """Detect the game window; fall back to the reference on failure.

        Never raises: detection is best-effort (game may not be running,
        platform helpers may be missing, permissions may be denied).
        """
        try:
            rect = _detect_window_rect(titles)
        except Exception:
            rect = None
        if rect is None:
            return cls()
        return cls.from_window_rect(*rect)

    @property
    def sx(self) -> float:
        return self.width / 1920.0

    @property
    def sy(self) -> float:
        return self.height / 1080.0

    def _p(self, x: float, y: float) -> tuple[int, int]:
        return (int(x * self.sx) + self.offset_x, int(y * self.sy) + self.offset_y)

    def hero_pos(self, mine: bool) -> tuple[int, int]:
        return self._p(960, 830 if mine else 150)

    def hero_power_pos(self, mine: bool) -> tuple[int, int]:
        return self._p(845, 830 if mine else 150)

    def end_turn_pos(self) -> tuple[int, int]:
        return self._p(1770, 480)

    def mulligan_confirm_pos(self) -> tuple[int, int]:
        return self._p(960, 760)

    def board_pos(self, mine: bool, index: int, count: int) -> tuple[int, int]:
        # Minions are centered on a row; spacing shrinks as the board fills.
        y = 600 if mine else 380
        if count <= 1:
            return self._p(960, y)
        spacing = min(170, 1100 / count)
        start = 960 - spacing * (count - 1) / 2
        return self._p(start + spacing * index, y)

    def hand_pos(self, index: int, count: int) -> tuple[int, int]:
        if count <= 1:
            return self._p(960, 990)
        spacing = min(120, 950 / count)
        start = 960 - spacing * (count - 1) / 2
        return self._p(start + spacing * index, 990)


def action_to_clicks(action: Action, state: GameState, layout: Layout) -> list[Click]:
    """Translate a game Action into screen clicks/drags."""
    me = state.me
    if action.kind == PLAY_MINION:
        hand_idx = next(
            (i for i, e in enumerate(me.hand) if e.id == action.source_id), None
        )
        if hand_idx is None:
            return []
        sx, sy = layout.hand_pos(hand_idx, len(me.hand))
        tx, ty = layout.board_pos(True, action.position, len(me.board) + 1)
        return [Click(sx, sy, "drag_start"), Click(tx, ty, "drag_end")]
    if action.kind == PLAY_SPELL:
        hand_idx = next(
            (i for i, e in enumerate(me.hand) if e.id == action.source_id), None
        )
        if hand_idx is None:
            return []
        sx, sy = layout.hand_pos(hand_idx, len(me.hand))
        if action.target_id:
            target = state.entity(action.target_id)
            if target is None:
                return []
            tx, ty = _entity_screen_pos(target, state, layout)
            return [Click(sx, sy, "drag_start"), Click(tx, ty, "drag_end")]
        return [Click(sx, sy)]
    if action.kind == ATTACK:
        attacker = state.entity(action.source_id)
        target = state.entity(action.target_id)
        if attacker is None or target is None:
            return []
        sx, sy = _entity_screen_pos(attacker, state, layout)
        tx, ty = _entity_screen_pos(target, state, layout)
        return [Click(sx, sy, "drag_start"), Click(tx, ty, "drag_end")]
    if action.kind == HERO_POWER:
        x, y = layout.hero_power_pos(True)
        return [Click(x, y)]
    if action.kind == END_TURN:
        x, y = layout.end_turn_pos()
        return [Click(x, y)]
    return []


def _entity_screen_pos(ent, state: GameState, layout: Layout) -> tuple[int, int]:
    if ent.is_hero:
        return layout.hero_pos(ent.controller == state.my_id)
    if ent.controller == state.my_id:
        idx = next((i for i, e in enumerate(state.me.board) if e.id == ent.id), 0)
        return layout.board_pos(True, idx, len(state.me.board))
    idx = next((i for i, e in enumerate(state.opp.board) if e.id == ent.id), 0)
    return layout.board_pos(False, idx, len(state.opp.board))


class DryRunExecutor:
    """Logs actions; never touches the mouse. Default for testing."""

    def __init__(self, layout: Layout | None = None, quiet: bool = False) -> None:
        self.layout = layout or Layout.auto_detect()
        self.executed: list[Action] = []
        self.quiet = quiet

    def execute(self, action: Action, state: GameState) -> list[Click]:
        clicks = action_to_clicks(action, state, self.layout)
        self.executed.append(action)
        if not self.quiet:
            print(f"[dry-run] {action} -> {clicks}")
        return clicks

    def cancel_drag(self) -> None:
        """Right-click cancel: no-op in dry-run (nothing was dragged)."""
        if not self.quiet:
            print("[dry-run] cancel_drag (no-op)")


class PyAutoGUIExecutor:
    """Real clicks via pyautogui (Windows/macOS). Lazy import."""

    def __init__(self, layout: Layout | None = None,
                 delay_range: tuple[float, float] = (0.4, 1.2)) -> None:
        import pyautogui  # noqa: PLC0415

        self._gui = pyautogui
        self.layout = layout or Layout.auto_detect()
        self.delay_range = delay_range

    def _human_delay(self) -> None:
        time.sleep(random.uniform(*self.delay_range))

    def execute(self, action: Action, state: GameState) -> list[Click]:
        clicks = action_to_clicks(action, state, self.layout)
        self._human_delay()
        for click in clicks:
            if click.kind == "drag_start":
                self._gui.mouseDown(click.x, click.y)
            elif click.kind == "drag_end":
                self._gui.moveTo(click.x, click.y, duration=0.25)
                self._gui.mouseUp()
            else:
                self._gui.click(click.x, click.y)
        return clicks

    def cancel_drag(self) -> None:
        """Right-click to clear a possibly stuck drag after a MISFIRE."""
        self._gui.click(button="right")


class SerialHIDExecutor:
    """Sends actions as JSON lines over serial to a Pico HID dongle.

    The Pico firmware (not included) reads lines like
    {"type": "drag", "x1": .., "y1": .., "x2": .., "y2": ..}
    and replays them as USB HID mouse events.
    """

    def __init__(self, port: str, layout: Layout | None = None) -> None:
        import serial  # noqa: PLC0415

        self._serial = serial.Serial(port, 115200, timeout=1)
        self.layout = layout or Layout.auto_detect()

    def execute(self, action: Action, state: GameState) -> list[Click]:
        clicks = action_to_clicks(action, state, self.layout)
        for click in clicks:
            line = json.dumps(
                {"type": click.kind, "x": click.x, "y": click.y}
            ) + "\n"
            self._serial.write(line.encode())
        return clicks

    def cancel_drag(self) -> None:
        """Right-click to clear a possibly stuck drag after a MISFIRE."""
        line = json.dumps({"type": "right_click"}) + "\n"
        self._serial.write(line.encode())

"""Actions: what the bot does, and how they map to screen coordinates.

An Action is game-level (PLAY_MINION / ATTACK / ...). Layout translates it
into clicks/drags for a reference resolution; the numbers MUST be
calibrated on the user's machine (window position, resolution, DPI) via
`Layout.calibrate` before going live. DryRunExecutor is the default:
it logs actions instead of touching the mouse.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from .state import GameState

PLAY_MINION = "PLAY_MINION"
PLAY_SPELL = "PLAY_SPELL"
ATTACK = "ATTACK"
HERO_POWER = "HERO_POWER"
END_TURN = "END_TURN"


@dataclass
class Action:
    kind: str
    source_id: int = 0  # entity id of the card/minion used
    target_id: int = 0  # entity id of the target (0 = none / face handled by kind)
    card_name: str = ""
    position: int = 0  # board position when playing a minion

    def __repr__(self) -> str:  # compact logs
        if self.kind == ATTACK:
            return f"ATTACK {self.source_id} -> {self.target_id}"
        if self.kind in (PLAY_MINION, PLAY_SPELL):
            return f"{self.kind} {self.card_name} (id={self.source_id})"
        return self.kind


@dataclass
class Click:
    x: int
    y: int
    kind: str = "click"  # click | drag_start | drag_end


class Layout:
    """Reference 1920x1080 board geometry. Calibrate on first use."""

    def __init__(self, width: int = 1920, height: int = 1080,
                 offset_x: int = 0, offset_y: int = 0) -> None:
        self.width = width
        self.height = height
        self.offset_x = offset_x
        self.offset_y = offset_y

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
        self.layout = layout or Layout()
        self.executed: list[Action] = []
        self.quiet = quiet

    def execute(self, action: Action, state: GameState) -> list[Click]:
        clicks = action_to_clicks(action, state, self.layout)
        self.executed.append(action)
        if not self.quiet:
            print(f"[dry-run] {action} -> {clicks}")
        return clicks


class PyAutoGUIExecutor:
    """Real clicks via pyautogui (Windows/macOS). Lazy import."""

    def __init__(self, layout: Layout | None = None,
                 delay_range: tuple[float, float] = (0.4, 1.2)) -> None:
        import pyautogui  # noqa: PLC0415

        self._gui = pyautogui
        self.layout = layout or Layout()
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


class SerialHIDExecutor:
    """Sends actions as JSON lines over serial to a Pico HID dongle.

    The Pico firmware (not included) reads lines like
    {"type": "drag", "x1": .., "y1": .., "x2": .., "y2": ..}
    and replays them as USB HID mouse events.
    """

    def __init__(self, port: str, layout: Layout | None = None) -> None:
        import serial  # noqa: PLC0415

        self._serial = serial.Serial(port, 115200, timeout=1)
        self.layout = layout or Layout()

    def execute(self, action: Action, state: GameState) -> list[Click]:
        clicks = action_to_clicks(action, state, self.layout)
        for click in clicks:
            line = json.dumps(
                {"type": click.kind, "x": click.x, "y": click.y}
            ) + "\n"
            self._serial.write(line.encode())
        return clicks

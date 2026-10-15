"""Main loop: tail Power.log -> update state -> decide -> execute.

Usage (dry-run, safe anywhere, from games/hearthstone/):
    python -m hsbot.bot --log samples/sample_power.log --dry-run --once

Live (Windows/macOS, needs the game running + log.config enabled):
    python -m hsbot.bot --log "C:/Program Files (x86)/Hearthstone/Logs/Power.log"

Log locations:
  Windows: <Hearthstone>/Logs/Power.log
  macOS:   ~/Library/Logs/Blizzard/Hearthstone/Power.log
Enable logging by writing log.config next to the game (see README).

The Bot also accepts an `on_event` callback so the Tauri sidecar can
forward mulligan / decision / action events as JSON-lines.
"""
from __future__ import annotations

import argparse
import os
import random
import threading
import time
from typing import Callable

from .actions import DryRunExecutor, Layout, PyAutoGUIExecutor
from .cards import CardDB
from .decision import choose_mulligan, decide_turn
from .log_parser import LogParser
from .state import GameState

EventCallback = Callable[[dict], None]


class Bot:
    def __init__(self, db: CardDB, executor=None, layout: Layout | None = None,
                 delay_range: tuple[float, float] = (0.4, 1.2),
                 on_event: EventCallback | None = None,
                 quiet: bool = False) -> None:
        self.db = db
        self.parser = LogParser(GameState())
        self.executor = executor or DryRunExecutor(layout)
        self.layout = layout or Layout()
        self.delay_range = delay_range
        self.on_event = on_event
        self.quiet = quiet
        self.paused = False
        self._decided_turn: int = -1
        self._mulligan_done = False

    def _log(self, msg: str) -> None:
        # Never print to stdout in sidecar mode: stdout is the JSON-lines
        # protocol stream. Human-readable logs go to stderr or nowhere.
        if not self.quiet:
            print(msg)

    @property
    def state(self) -> GameState:
        return self.parser.state

    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            self.on_event(event)

    def feed_line(self, line: str) -> None:
        self.parser.feed_line(line)
        self._maybe_act()

    def _maybe_act(self) -> None:
        if self.paused:
            return
        state = self.state
        if state.step == "BEGIN_MULLIGAN" and not self._mulligan_done:
            self._do_mulligan()
        elif (
            state.is_my_turn
            and state.step == "MAIN_ACTION"
            and state.turn != self._decided_turn
            and state.turn > 0
        ):
            self._do_turn()

    def _do_mulligan(self) -> None:
        state = self.state
        hand_names = [e.name for e in state.me.hand]
        # Coin = going second: opponent has the coin card in hand at mulligan.
        has_coin = any(e.card_id == "GAME_005" for e in state.me.hand)
        replace = choose_mulligan(hand_names, has_coin)
        self._emit({
            "type": "mulligan",
            "hand": hand_names,
            "has_coin": has_coin,
            "replace": sorted(replace),
        })
        self._log(f"[mulligan] hand={hand_names} coin={has_coin} replace={sorted(replace)}")
        # TODO: click the cards to replace + confirm via executor.
        # Kept as a log line until Layout is calibrated on the user's machine.
        self._mulligan_done = True

    def _do_turn(self) -> None:
        state = self.state
        self._decided_turn = state.turn
        actions = decide_turn(state, self.db)
        self._emit({
            "type": "decision",
            "turn": state.turn,
            "actions": [repr(a) for a in actions],
        })
        self._log(f"[turn {state.turn}] plan: {actions}")
        for action in actions:
            self._emit({"type": "action", "turn": state.turn, "action": repr(action)})
            self.executor.execute(action, state)
            time.sleep(random.uniform(*self.delay_range))

    def run_file(self, path: str, follow: bool = False,
                 stop_event: threading.Event | None = None) -> None:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if stop_event is not None and stop_event.is_set():
                    return
                self.feed_line(line)
            if follow:
                while stop_event is None or not stop_event.is_set():
                    line = f.readline()
                    if line:
                        self.feed_line(line)
                    else:
                        time.sleep(0.2)


def default_log_path() -> str:
    if os.name == "nt":
        return r"C:\Program Files (x86)\Hearthstone\Logs\Power.log"
    return os.path.expanduser("~/Library/Logs/Blizzard/Hearthstone/Power.log")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=default_log_path())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="do not follow the file")
    ap.add_argument("--deck-json", default="data/deck.json")
    ap.add_argument("--cards-json", default="")
    ap.add_argument("--overrides-json", default="data/overrides.json")
    args = ap.parse_args()

    if args.cards_json and os.path.exists(args.cards_json):
        db = CardDB.from_hearthstonejson(args.cards_json)
        print(f"[db] loaded HearthstoneJSON from {args.cards_json}")
    else:
        db = CardDB.from_deck_json(args.deck_json)
        print("[db] using minimal deck-local table (stats unknown for most cards)")
        print("[db] drop cards.collectible.json via --cards-json for exact stats")
    if os.path.exists(args.overrides_json):
        db.apply_overrides(args.overrides_json)

    if args.dry_run:
        executor: object = DryRunExecutor()
    else:
        executor = PyAutoGUIExecutor()
    bot = Bot(db, executor=executor)
    bot.run_file(args.log, follow=not args.once)


if __name__ == "__main__":
    main()

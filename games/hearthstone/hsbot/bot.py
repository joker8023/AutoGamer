"""Main loop: tail Power.log -> update state -> decide -> execute -> verify.

Usage (dry-run, safe anywhere, from games/hearthstone/):
    python -m hsbot.bot --log samples/sample_power.log --dry-run --once

Live (Windows/macOS, needs the game running + log.config enabled):
    python -m hsbot.bot --log "C:/Program Files (x86)/Hearthstone/Logs/Power.log"

Log locations:
  Windows: <Hearthstone>/Logs/Power.log
  macOS:   ~/Library/Logs/Blizzard/Hearthstone/Power.log
Enable logging by writing log.config next to the game (see README).

The Bot also accepts an `on_event` callback so the Tauri sidecar can
forward mulligan / decision / action / verify / halted events as JSON-lines.

Execution model — two paths:

- Dry-run (default): verification is meaningless (clicks never change
  the state), so each turn decides a whole plan and executes it blindly,
  exactly as before.
- Live (real executor): per-action event-driven loop. Decide one
  action, execute it, then wait for the log to confirm it landed
  (verify.py: OK / MISFIRE / PENDING). OK -> decide the next step from
  the new state. MISFIRE -> right-click cancel (if it was a drag),
  retry once, then ban the action for this turn. PENDING past the
  verify timeout -> dead: ban the action, count a dead-streak.
  8 consecutive dead actions or 3 consecutive misfires halt the bot.

Why event-driven, not blocking poll: _do_turn runs ON the log-feeding
thread (feed_line -> _maybe_act). A blocking "poll the parser until the
verdict changes" would deadlock — the new log lines it waits for can
only arrive through feed_line, which the poll would be blocking. So
verification resolves on the NEXT feed_line / tick() instead. The
sidecar tail loop calls tick() every iteration so PENDING timeouts
still fire on wall-clock time even when the log is silent.

Automation violates Blizzard's Terms of Service: account risk is yours.
Dry-run is the default; live execution is an explicit opt-in.
"""
from __future__ import annotations

import argparse
import os
import random
import threading
import time
from typing import Callable

from .actions import (
    END_TURN,
    DryRunExecutor,
    Layout,
    PyAutoGUIExecutor,
    action_is_drag,
)
from .cards import CardDB
from .decision import choose_mulligan, decide_turn_traced
from .deck_config import DEFAULT_DECK_ID, DeckConfig, load_deck
from .engine_version import ENGINE_VERSION, get_engine
from .log_parser import LogParser
from .state import GameState
from .verify import MISFIRE, OK, PENDING, verify_action

EventCallback = Callable[[dict], None]

# Starting points from the reference project's experience. NOT validated
# on this bot: re-measure on a real machine before trusting them.
DEFAULT_SETTLE_BASE = 1.2      # seconds after each action, before verifying
DEFAULT_SETTLE_PER_DRAW = 0.5  # extra seconds per card drawn by the action
DEFAULT_SETTLE_BOARD = 0.5     # extra seconds when board counts changed
DEFAULT_VERIFY_TIMEOUT = 10.0  # seconds before PENDING becomes dead
DEFAULT_MAX_DEAD_STREAK = 8    # consecutive dead actions -> halt
DEFAULT_MAX_MISFIRE_STREAK = 3  # consecutive misfires -> halt
DEFAULT_MAX_ACTIONS_PER_TURN = 40


class Bot:
    def __init__(self, db: CardDB, executor=None, layout: Layout | None = None,
                 delay_range: tuple[float, float] = (0.4, 1.2),
                 on_event: EventCallback | None = None,
                 quiet: bool = False,
                 deck_config: DeckConfig | None = None,
                 engine_version: str = ENGINE_VERSION,
                 beam_width: int = 8,
                 verify_enabled: bool | None = None,
                 settle_base: float = DEFAULT_SETTLE_BASE,
                 settle_per_draw: float = DEFAULT_SETTLE_PER_DRAW,
                 settle_board: float = DEFAULT_SETTLE_BOARD,
                 verify_timeout: float = DEFAULT_VERIFY_TIMEOUT,
                 max_dead_streak: int = DEFAULT_MAX_DEAD_STREAK,
                 max_misfire_streak: int = DEFAULT_MAX_MISFIRE_STREAK,
                 max_actions_per_turn: int = DEFAULT_MAX_ACTIONS_PER_TURN,
                 clock: Callable[[], float] | None = None) -> None:
        self.db = db
        self.parser = LogParser(GameState())
        self.executor = executor or DryRunExecutor(layout)
        self.layout = layout or Layout.auto_detect()
        self.delay_range = delay_range
        self.on_event = on_event
        self.quiet = quiet
        self.paused = False
        self.deck_config = deck_config or load_deck(DEFAULT_DECK_ID)
        self.engine_version = engine_version
        self.beam_width = beam_width
        # Verification only makes sense on a real executor (dry-run
        # clicks never change the state). Explicit flag wins over auto.
        if verify_enabled is None:
            verify_enabled = not isinstance(self.executor, DryRunExecutor)
        self.verify_enabled = verify_enabled
        self.settle_base = settle_base
        self.settle_per_draw = settle_per_draw
        self.settle_board = settle_board
        self.verify_timeout = verify_timeout
        self.max_dead_streak = max_dead_streak
        self.max_misfire_streak = max_misfire_streak
        self.max_actions_per_turn = max_actions_per_turn
        self.clock = clock or time.monotonic
        self._decided_turn: int = -1
        self._mulligan_done = False
        # Live-loop state (per turn / per pending action).
        self._pending: dict | None = None
        self._banned: set[str] = set()
        self._dead_streak = 0
        self._misfire_streak = 0
        self._actions_this_turn = 0
        self.halted = False
        self.halt_reason: str | None = None

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

    # -- lifecycle --------------------------------------------------------
    def reset_for_new_game(self) -> None:
        """Fresh parser state + per-game trackers for the next game.
        Halt is sticky: a halted bot is never auto-reset (Runner stops)."""
        self.parser = LogParser(GameState())
        self._decided_turn = -1
        self._mulligan_done = False
        self._pending = None
        self._banned = set()
        self._dead_streak = 0
        self._misfire_streak = 0
        self._actions_this_turn = 0

    def _halt(self, reason: str) -> None:
        if self.halted:
            return
        self.halted = True
        self.halt_reason = reason
        self._emit({"type": "halted", "reason": reason})
        self._log(f"[halted] {reason}")

    # -- input ------------------------------------------------------------
    def feed_line(self, line: str) -> None:
        self.parser.feed_line(line)
        self._maybe_act()

    def tick(self) -> None:
        """Wall-clock tick for the live loop (sidecar calls this every
        tail iteration): fires PENDING timeouts even when the log is
        silent. Safe to call any time; a no-op when nothing is pending."""
        self._maybe_act()

    # -- main dispatch ----------------------------------------------------
    def _maybe_act(self) -> None:
        if self.paused or self.halted:
            return
        state = self.state
        if state.step == "BEGIN_MULLIGAN" and not self._mulligan_done:
            self._do_mulligan()
            return
        if not self.verify_enabled:
            if (
                state.is_my_turn
                and state.step == "MAIN_ACTION"
                and state.turn != self._decided_turn
                and state.turn > 0
            ):
                self._do_turn_whole_plan()
            return
        # Live loop.
        if self._pending is not None:
            self._resolve_pending()
            return
        if (
            state.is_my_turn
            and state.step == "MAIN_ACTION"
            and state.turn > 0
        ):
            if state.turn != self._decided_turn:
                self._start_turn(state.turn)
            self._step_turn()

    def _start_turn(self, turn: int) -> None:
        self._decided_turn = turn
        self._banned = set()
        self._dead_streak = 0
        self._misfire_streak = 0
        self._actions_this_turn = 0

    # -- mulligan ---------------------------------------------------------
    def _do_mulligan(self) -> None:
        state = self.state
        hand_names = [e.name for e in state.me.hand]
        # Coin = going second: opponent has the coin card in hand at mulligan.
        has_coin = any(e.card_id == "GAME_005" for e in state.me.hand)
        replace = choose_mulligan(hand_names, has_coin, self.deck_config)
        kept = [name for i, name in enumerate(hand_names) if i not in replace]
        self._emit({
            "type": "mulligan",
            "hand": hand_names,
            "has_coin": has_coin,
            "replace": sorted(replace),
            "kept": kept,
        })
        self._log(f"[mulligan] hand={hand_names} coin={has_coin} replace={sorted(replace)}")
        # TODO: click the cards to replace + confirm via executor.
        # Kept as an event until mulligan-screen card positions are
        # calibrated on a real machine (they differ from in-game hand
        # positions). The receipt (parser.mulligan_receipt) + verify.py's
        # MULLIGAN verdict exist for when clicking lands.
        self._mulligan_done = True

    # -- dry-run path: whole-plan execution --------------------------------
    def _do_turn_whole_plan(self) -> None:
        state = self.state
        self._decided_turn = state.turn
        spec = get_engine(self.engine_version)
        actions, trace = decide_turn_traced(
            state, self.db, beam_width=self.beam_width,
            deck_config=self.deck_config, policy=spec.policy)
        self._emit({
            "type": "decision",
            "turn": state.turn,
            "actions": [repr(a) for a in actions],
            "engine_version": self.engine_version,
            "trace": trace,
        })
        self._log(f"[turn {state.turn}] plan: {actions}")
        for action in actions:
            self._emit({"type": "action", "turn": state.turn, "action": repr(action)})
            self.executor.execute(action, state)
            time.sleep(random.uniform(*self.delay_range))

    # -- live path: one step at a time, verified ---------------------------
    def _step_turn(self) -> None:
        """Decide one action from the current state and execute it.
        The resulting step becomes pending until the log confirms it.
        No-op unless it is my MAIN_ACTION turn: after an END_TURN
        resolves OK the state belongs to the opponent and nothing
        must be executed."""
        state = self.state
        if not (state.is_my_turn and state.step == "MAIN_ACTION" and state.turn > 0):
            return
        if self._actions_this_turn >= self.max_actions_per_turn:
            self._halt(f"action cap reached ({self.max_actions_per_turn} in turn {state.turn})")
            return
        spec = get_engine(self.engine_version)
        plan, trace = decide_turn_traced(
            state, self.db, beam_width=self.beam_width,
            deck_config=self.deck_config, policy=spec.policy,
            exclude=frozenset(self._banned))
        if not plan:
            return
        action = plan[0]
        self._emit({
            "type": "decision",
            "turn": state.turn,
            "actions": [repr(a) for a in plan],
            "engine_version": self.engine_version,
            "trace": trace,
        })
        self._emit({"type": "action", "turn": state.turn, "action": repr(action)})
        self._log(f"[turn {state.turn}] step: {action}")
        self.executor.execute(action, state)
        self._actions_this_turn += 1
        self._pending = {
            "action": action,
            "before": state.clone(),
            "since": self.clock(),
            "retries": 0,
        }
        # Base settle: bounded blocking sleep. It does not wait for log
        # lines, so it cannot deadlock the feeding thread.
        if self.settle_base > 0:
            time.sleep(self.settle_base)

    def _settle_due(self, pending: dict, now: float) -> float:
        """Extra settle owed: per card drawn + on board-count change."""
        before = pending["before"]
        after = self.state
        action = pending["action"]
        extra = 0.0
        consumed = 1 if action.kind in ("PLAY_MINION", "PLAY_SPELL") else 0
        drawn = (len(after.me.hand) - len(before.me.hand)) + consumed
        if drawn > 0:
            extra += self.settle_per_draw * drawn
        before_board = len(before.me.board) + len(before.opp.board)
        after_board = len(after.me.board) + len(after.opp.board)
        if after_board != before_board:
            extra += self.settle_board
        return pending["since"] + self.settle_base + extra

    def _resolve_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return
        state = self.state
        action = pending["action"]
        now = self.clock()
        verdict = verify_action(pending["before"], action, state)
        if verdict == OK:
            self._pending = None
            self._dead_streak = 0
            self._misfire_streak = 0
            self._emit({"type": "verify", "action": repr(action), "verdict": OK})
            self._step_turn()
            return
        if verdict == MISFIRE:
            self._emit({"type": "verify", "action": repr(action), "verdict": MISFIRE})
            if action_is_drag(action):
                try:
                    self.executor.cancel_drag()
                except Exception:  # noqa: BLE001 - cancel is best-effort
                    pass
            pending["retries"] += 1
            if pending["retries"] >= 2:
                self._ban(action, "misfire")
                self._misfire_streak += 1
                self._pending = None
                if self._misfire_streak >= self.max_misfire_streak:
                    self._halt(f"{self.max_misfire_streak} consecutive misfires")
                    return
                self._step_turn()
            else:
                # Retry once from the new state (same action, fresh pending).
                self._pending = {
                    "action": action,
                    "before": state.clone(),
                    "since": now,
                    "retries": pending["retries"],
                }
                self.executor.execute(action, state)
                self._actions_this_turn += 1
            return
        # PENDING: wait for settle, then for the verify timeout.
        if now < self._settle_due(pending, now):
            return
        if now - pending["since"] >= self.verify_timeout:
            self._emit({"type": "verify", "action": repr(action), "verdict": "DEAD"})
            self._ban(action, "dead")
            self._dead_streak += 1
            self._misfire_streak = 0
            self._pending = None
            if self._dead_streak >= self.max_dead_streak:
                self._halt(f"{self.max_dead_streak} consecutive dead actions")
                return
            self._step_turn()

    def _ban(self, action, reason: str) -> None:
        if action.kind != END_TURN:
            self._banned.add(repr(action))
            self._log(f"[ban] {action} ({reason})")

    # -- offline -----------------------------------------------------------
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
    ap.add_argument("--deck-json", default="")
    ap.add_argument("--deck", default=DEFAULT_DECK_ID)
    ap.add_argument("--engine-version", default=ENGINE_VERSION)
    ap.add_argument("--cards-json", default="")
    ap.add_argument("--overrides-json", default="data/overrides.json")
    args = ap.parse_args()

    deck_config = load_deck(args.deck)
    deck_json = args.deck_json or deck_config.deck_json_path
    if args.cards_json and os.path.exists(args.cards_json):
        db = CardDB.from_hearthstonejson(args.cards_json)
        print(f"[db] loaded HearthstoneJSON from {args.cards_json}")
    else:
        db = CardDB.from_deck_json(deck_json)
        print("[db] using minimal deck-local table (stats unknown for most cards)")
        print("[db] drop cards.collectible.json via --cards-json for exact stats")
    if os.path.exists(args.overrides_json):
        db.apply_overrides(args.overrides_json)

    if args.dry_run:
        executor: object = DryRunExecutor()
    else:
        executor = PyAutoGUIExecutor()
    bot = Bot(db, executor=executor, deck_config=deck_config,
              engine_version=args.engine_version)
    bot.run_file(args.log, follow=not args.once)


if __name__ == "__main__":
    main()

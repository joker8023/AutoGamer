"""Unattended-run runner: batches of games with stop conditions.

Wraps a Bot. The Runner owns the log feed: every line goes through
``Runner.feed_line`` (which forwards to the Bot), and after each line
it checks whether the game ended and whether any stop condition fired.

Stop conditions (first one wins, recorded as ``stop_reason``):
- max_games reached,
- consecutive losses >= max_consecutive_losses,
- the stop file appears (``runtime/stop`` by default),
- the Bot halted (dead/misfire streaks, action cap),
- per-game action cap exceeded,
- parser silence while a game is in progress (no log lines for
  silence_timeout) — silence while waiting for a queue does NOT stop
  the run,
- a game ended without a detectable result while max_games > 1:
  batches require detectable results, so the run stops with
  "result_not_detectable" instead of blindly starting another game.

Per-game results append to ``runtime/runs/<run-start-ts>.jsonl``:
{ts, game_index, result, stop_reason, engine_version, deck_id, actions}.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass

from .bot import Bot

DEFAULT_STOP_FILE = os.path.join("runtime", "stop")
DEFAULT_RESULTS_DIR = os.path.join("runtime", "runs")


@dataclass
class RunnerConfig:
    max_games: int = 1
    max_consecutive_losses: int = 6
    stop_file: str = DEFAULT_STOP_FILE
    max_actions_per_game: int = 200
    silence_timeout: float = 120.0
    results_dir: str = DEFAULT_RESULTS_DIR


class Runner:
    def __init__(self, bot: Bot, config: RunnerConfig | None = None,
                 on_event=None, clock=None) -> None:
        self.bot = bot
        self.config = config or RunnerConfig()
        self.clock = clock or time.monotonic
        self._forward_event = on_event if on_event is not None else bot.on_event
        bot.on_event = self._on_bot_event
        self.results: list[dict] = []
        self.stop_reason: str | None = None
        self._game_index = 0
        self._consecutive_losses = 0
        self._actions_this_game = 0
        self._game_recorded = False
        self._game_started = False
        self._last_line_time: float | None = None
        self._run_ts: str | None = None

    # -- events -----------------------------------------------------------
    def _on_bot_event(self, event: dict) -> None:
        if event.get("type") == "action":
            self._actions_this_game += 1
        if self._forward_event is not None:
            self._forward_event(event)

    # -- feed / tick ------------------------------------------------------
    def feed_line(self, line: str) -> None:
        if self.should_stop:
            return
        self.bot.feed_line(line)
        self._last_line_time = self.clock()
        if self.bot.state.turn > 0 or self.bot.state.step == "BEGIN_MULLIGAN":
            self._game_started = True
        self._check_game_end()
        self._check_action_cap()
        self._check_stop_file()
        if self.bot.halted and self.stop_reason is None:
            self.stop_reason = f"bot_halted: {self.bot.halt_reason}"

    def tick(self) -> None:
        """Wall-clock tick: enforce silence timeout + bot timeouts."""
        if self.should_stop:
            return
        self.bot.tick()
        if (
            self._game_started
            and self._last_line_time is not None
            and self.clock() - self._last_line_time > self.config.silence_timeout
        ):
            self.stop_reason = "silence"
            return
        if self.bot.halted and self.stop_reason is None:
            self.stop_reason = f"bot_halted: {self.bot.halt_reason}"

    @property
    def should_stop(self) -> bool:
        return self.stop_reason is not None

    # -- game-end + stop conditions ----------------------------------------
    def _check_game_end(self) -> None:
        state = self.bot.state
        if not state.game_over or self._game_recorded:
            return
        self._game_recorded = True
        result = state.game_result or "UNKNOWN"
        self._record_game(result)
        if result == "LOSS":
            self._consecutive_losses += 1
        elif result == "WIN":
            self._consecutive_losses = 0
        if result == "UNKNOWN" and self.config.max_games > 1:
            self.stop_reason = "result_not_detectable"
            return
        if self._game_index >= self.config.max_games:
            self.stop_reason = "max_games"
            return
        if self._consecutive_losses >= self.config.max_consecutive_losses:
            self.stop_reason = "consecutive_losses"
            return
        self.bot.reset_for_new_game()
        self._actions_this_game = 0
        self._game_recorded = False
        self._game_started = False

    def _check_stop_file(self) -> None:
        if os.path.exists(self.config.stop_file):
            self.stop_reason = "stop_file"

    def _check_action_cap(self) -> bool:
        """True if the per-game action cap fired (and the run stopped).
        Records the stuck game once; the run stops either way."""
        if self._game_recorded:
            return False
        if self._actions_this_game > self.config.max_actions_per_game:
            self._game_recorded = True
            self._record_game("UNKNOWN")
            if self.config.max_games > 1:
                self.stop_reason = "result_not_detectable"
            else:
                self.stop_reason = "action_cap"
            return True
        return False

    def _record_game(self, result: str) -> None:
        self._game_index += 1
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "game_index": self._game_index,
            "result": result,
            "stop_reason": self.stop_reason,
            "engine_version": self.bot.engine_version,
            "deck_id": self.bot.deck_config.deck_id,
            "actions": self._actions_this_game,
        }
        self.results.append(entry)
        if self._forward_event is not None:
            self._forward_event({"type": "game_result", **entry})
        os.makedirs(self.config.results_dir, exist_ok=True)
        if self._run_ts is None:
            self._run_ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.config.results_dir, f"{self._run_ts}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- offline -----------------------------------------------------------
    def run_file(self, path: str, follow: bool = False,
                 stop_event: threading.Event | None = None) -> list[dict]:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if stop_event is not None and stop_event.is_set():
                    self.stop_reason = self.stop_reason or "external_stop"
                    break
                self.feed_line(line)
                if self._check_action_cap() or self.should_stop:
                    break
            if follow:
                while not self.should_stop and (
                    stop_event is None or not stop_event.is_set()
                ):
                    line = f.readline()
                    if line:
                        self.feed_line(line)
                        if self._check_action_cap():
                            break
                    else:
                        self.tick()
                        time.sleep(0.2)
        if self.stop_reason is None:
            self.stop_reason = "eof"
        return self.results

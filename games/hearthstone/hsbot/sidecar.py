"""Tauri sidecar: JSON-lines stdio protocol between the Tauri app and the engine.

Run by the Tauri backend as:  python3 -m hsbot.sidecar
(working directory = games/hearthstone, the directory containing the
`hsbot` package).

Commands (one JSON object per line on stdin):
  {"type": "start", "config": {...}}   start tailing the log + deciding
  {"type": "stop"}                      stop the bot loop
  {"type": "pause"} / {"type": "resume"}  gate decisions (log parsing continues)
  {"type": "config", "config": {...}}  update config live (dry_run, delays)
  {"type": "ping"}                      -> {"type": "pong"}
  {"type": "status"}                    -> {"type": "status", "running": bool, ...}

Events (one JSON object per line on stdout):
  {"type": "state", "state": {...}}     GameState snapshot on meaningful change
  {"type": "mulligan", ...} / {"type": "decision", ...} / {"type": "action", ...}
  {"type": "error", "message": "..."}
  {"type": "started"} / {"type": "stopped"}

Config keys: log_path, dry_run, delay_min, delay_max, deck_json,
cards_json, overrides_json, beam_width, deck, engine_version, runner.
`deck` selects a deck directory under decks/ (default: mech-paladin-horn);
`deck_json` overrides the card DB source for that deck.
`runner` (optional dict) enables the unattended Runner: {max_games,
max_consecutive_losses, stop_file, max_actions_per_game, silence_timeout,
results_dir} — see runner.py. Without it, the Bot runs one game and the
tail loop keeps feeding lines.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

from .actions import DryRunExecutor, Layout, PyAutoGUIExecutor
from .bot import Bot, default_log_path
from .cards import CardDB
from .deck_config import DEFAULT_DECK_ID, load_deck
from .engine_version import ENGINE_VERSION
from .runner import Runner, RunnerConfig
from .serialize import state_signature, state_to_dict

_out_lock = threading.Lock()


def emit(event: dict) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
        sys.stdout.flush()


class Sidecar:
    def __init__(self) -> None:
        self.config: dict = {
            "log_path": default_log_path(),
            "dry_run": True,
            "delay_min": 0.4,
            "delay_max": 1.2,
            "deck_json": "",
            "cards_json": "",
            "overrides_json": "data/overrides.json",
            "beam_width": 8,
            "deck": DEFAULT_DECK_ID,
            "engine_version": ENGINE_VERSION,
        }
        self.bot: Bot | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._tail_ready = threading.Event()
        self._last_signature = None

    # -- lifecycle --------------------------------------------------------
    def _load_db(self) -> CardDB:
        cfg = self.config
        if cfg.get("cards_json") and os.path.exists(cfg["cards_json"]):
            db = CardDB.from_hearthstonejson(cfg["cards_json"])
        else:
            # Precedence: explicit deck_json > deck's deck.json > legacy default.
            deck_json = cfg.get("deck_json") or ""
            if not deck_json and cfg.get("deck"):
                try:
                    deck_json = load_deck(cfg["deck"]).deck_json_path
                except FileNotFoundError:
                    pass
            db = CardDB.from_deck_json(deck_json or "data/deck.json")
        overrides = cfg.get("overrides_json", "")
        if overrides and os.path.exists(overrides):
            db.apply_overrides(overrides)
        return db

    def _build_executor(self):
        if self.config.get("dry_run", True):
            return DryRunExecutor(quiet=True)
        return PyAutoGUIExecutor(
            delay_range=(float(self.config.get("delay_min", 0.4)),
                         float(self.config.get("delay_max", 1.2)))
        )

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            emit({"type": "error", "message": "already running"})
            return
        db = self._load_db()
        bot = Bot(
            db,
            executor=self._build_executor(),
            on_event=self._on_bot_event,
            delay_range=(float(self.config.get("delay_min", 0.4)),
                         float(self.config.get("delay_max", 1.2))),
            quiet=True,
            deck_config=load_deck(self.config.get("deck", DEFAULT_DECK_ID)),
            engine_version=self.config.get("engine_version", ENGINE_VERSION),
            beam_width=int(self.config.get("beam_width", 8)),
        )
        self.bot = bot
        self.runner: Runner | None = None
        self.stop_event.clear()
        self._tail_ready.clear()
        self._last_signature = None

        def _tail() -> None:
            path = self.config.get("log_path", default_log_path())
            runner_cfg = self.config.get("runner")
            sink = bot
            if isinstance(runner_cfg, dict):
                self.runner = Runner(bot, RunnerConfig(**{
                    k: v for k, v in runner_cfg.items()
                    if k in RunnerConfig.__dataclass_fields__
                }))
                sink = self.runner
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    f.seek(0, os.SEEK_END)  # only new lines
                    # Signal readiness BEFORE "started" is emitted (see
                    # start()): lines appended after "started" must be
                    # seen, never skipped by a late seek.
                    self._tail_ready.set()
                    while not self.stop_event.is_set():
                        if isinstance(sink, Runner) and sink.should_stop:
                            emit({"type": "stopped",
                                  "reason": sink.stop_reason,
                                  "results": sink.results})
                            break
                        line = f.readline()
                        if line:
                            sink.feed_line(line)
                            self._maybe_emit_state(bot)
                        else:
                            # No new line: tick so PENDING verify timeouts
                            # and runner silence still fire on wall-clock.
                            sink.tick()
                            time.sleep(0.2)
            except FileNotFoundError:
                self._tail_ready.set()
                emit({"type": "error",
                      "message": f"log file not found: {path}"})
            except Exception as exc:  # noqa: BLE001 - forwarded to UI
                self._tail_ready.set()
                emit({"type": "error", "message": f"tail failed: {exc}"})

        self.thread = threading.Thread(target=_tail, daemon=True)
        self.thread.start()
        # Wait for the tail thread to finish its seek-to-end before
        # telling the client we started; otherwise lines appended in
        # the gap could be skipped by the late seek.
        self._tail_ready.wait(timeout=5)
        emit({"type": "started", "config": self.config})

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None
        self.bot = None
        self.runner = None
        emit({"type": "stopped"})

    # -- events -----------------------------------------------------------
    def _on_bot_event(self, event: dict) -> None:
        emit(event)
        if self.bot is not None:
            self._maybe_emit_state(self.bot)

    def _maybe_emit_state(self, bot: Bot) -> None:
        sig = state_signature(bot.state)
        if sig != self._last_signature:
            self._last_signature = sig
            emit({"type": "state", "state": state_to_dict(bot.state)})

    # -- command loop -----------------------------------------------------
    def handle(self, cmd: dict) -> None:
        kind = cmd.get("type")
        if kind == "ping":
            emit({"type": "pong"})
        elif kind == "status":
            emit({"type": "status", "running": self.thread is not None
                  and self.thread.is_alive(), "config": self.config})
        elif kind == "start":
            if isinstance(cmd.get("config"), dict):
                self.config.update(cmd["config"])
            self.start()
        elif kind == "stop":
            self.stop()
        elif kind == "pause":
            if self.bot is not None:
                self.bot.paused = True
            emit({"type": "paused"})
        elif kind == "resume":
            if self.bot is not None:
                self.bot.paused = False
            emit({"type": "resumed"})
        elif kind == "config":
            if isinstance(cmd.get("config"), dict):
                self.config.update(cmd["config"])
                if self.bot is not None:
                    self.bot.delay_range = (
                        float(self.config.get("delay_min", 0.4)),
                        float(self.config.get("delay_max", 1.2)),
                    )
            emit({"type": "config", "config": self.config})
        else:
            emit({"type": "error", "message": f"unknown command: {kind}"})

    def serve(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                self.handle(json.loads(line))
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                emit({"type": "error", "message": f"bad command: {exc}"})


def main() -> None:
    Sidecar().serve()


if __name__ == "__main__":
    main()

"""Offline replay: feed a Power.log file, emit state snapshots (+ bot plans).

Usage (from games/hearthstone/):
    python -m hsbot.replay --log samples/sample_power.log [--beam-width 6]
Prints a JSON array of snapshots to stdout:
  [{turn, step, ..., state: {...}, plan: ["PLAY_MINION ...", "END_TURN"]}]
`plan` is attached at each of MY turn starts (MAIN_ACTION + new turn).
Used by the Tauri app's 回放 tab.
"""
from __future__ import annotations

import argparse
import json
import os

from .bot import default_log_path
from .cards import CardDB
from .deck_config import DEFAULT_DECK_ID, load_deck
from .engine_version import ENGINE_VERSION, get_engine
from .log_parser import LogParser
from .serialize import state_signature, state_to_dict
from .state import GameState


def replay_log(path: str, db: CardDB, beam_width: int = 6,
               deck_config=None, engine_version: str = ENGINE_VERSION) -> list[dict]:
    parser = LogParser(GameState())
    snapshots: list[dict] = []
    last_sig = None
    last_turn = -1
    spec = get_engine(engine_version)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parser.feed_line(line)
            state = parser.state
            sig = state_signature(state)
            if sig == last_sig:
                continue
            last_sig = sig
            snap = state_to_dict(state)
            # Attach the bot's plan at each of my turn starts.
            if (
                state.is_my_turn
                and state.step == "MAIN_ACTION"
                and state.turn != last_turn
                and state.turn > 0
            ):
                last_turn = state.turn
                try:
                    plan = spec.decide_fn(state, db, beam_width=beam_width,
                                          deck_config=deck_config)
                    snap["plan"] = [repr(a) for a in plan]
                except Exception as exc:  # noqa: BLE001 - replay must not die
                    snap["plan_error"] = str(exc)
            snapshots.append(snap)
    return snapshots


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=default_log_path())
    ap.add_argument("--deck-json", default="")
    ap.add_argument("--deck", default=DEFAULT_DECK_ID)
    ap.add_argument("--engine-version", default=ENGINE_VERSION)
    ap.add_argument("--cards-json", default="")
    ap.add_argument("--overrides-json", default="data/overrides.json")
    ap.add_argument("--beam-width", type=int, default=6)
    args = ap.parse_args()

    deck_config = load_deck(args.deck)
    deck_json = args.deck_json or deck_config.deck_json_path
    if args.cards_json and os.path.exists(args.cards_json):
        db = CardDB.from_hearthstonejson(args.cards_json)
    else:
        db = CardDB.from_deck_json(deck_json)
    if os.path.exists(args.overrides_json):
        db.apply_overrides(args.overrides_json)
    print(json.dumps(replay_log(args.log, db, beam_width=args.beam_width,
                                deck_config=deck_config,
                                engine_version=args.engine_version),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

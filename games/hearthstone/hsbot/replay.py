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
from .decision import decide_turn
from .log_parser import LogParser
from .serialize import state_signature, state_to_dict
from .state import GameState


def replay_log(path: str, db: CardDB, beam_width: int = 6) -> list[dict]:
    parser = LogParser(GameState())
    snapshots: list[dict] = []
    last_sig = None
    last_turn = -1
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
                    plan = decide_turn(state, db, beam_width=beam_width)
                    snap["plan"] = [repr(a) for a in plan]
                except Exception as exc:  # noqa: BLE001 - replay must not die
                    snap["plan_error"] = str(exc)
            snapshots.append(snap)
    return snapshots


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=default_log_path())
    ap.add_argument("--deck-json", default="data/deck.json")
    ap.add_argument("--cards-json", default="")
    ap.add_argument("--overrides-json", default="data/overrides.json")
    ap.add_argument("--beam-width", type=int, default=6)
    args = ap.parse_args()

    if args.cards_json and os.path.exists(args.cards_json):
        db = CardDB.from_hearthstonejson(args.cards_json)
    else:
        db = CardDB.from_deck_json(args.deck_json)
    if os.path.exists(args.overrides_json):
        db.apply_overrides(args.overrides_json)
    print(json.dumps(replay_log(args.log, db, beam_width=args.beam_width),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

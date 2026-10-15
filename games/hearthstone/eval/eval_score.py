"""Offline A/B eval: score an engine version against labelled positions.

Usage (from games/hearthstone/):
    python -m eval.eval_score --engine-version v1
    python -m eval.eval_score --engine-version v1 --engine-version v1  # same
    # A/B: run twice with different --engine-version and diff the totals.

Scoring: the engine's plan first action scores 1 if in labels.best,
0.5 if in labels.ok, else 0. Positions where the CURRENT engine's legal
menu contains no best action are listed as "blocked" — a rule removed
every best action, i.e. the labels disagree with the rules.

Positions come from eval/positions.jsonl (frozen states + DB extras /
overrides, so each position is self-contained); labels from
eval/labels.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hsbot.cards import Card, CardDB  # noqa: E402
from hsbot.decision import legal_actions  # noqa: E402
from hsbot.deck_config import load_deck  # noqa: E402
from hsbot.engine_version import get_engine  # noqa: E402
from hsbot.serialize import state_from_dict  # noqa: E402

HERE = os.path.dirname(__file__)


def load_positions(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_labels(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_db(position: dict, deck_json_path: str) -> CardDB:
    db = CardDB.from_deck_json(deck_json_path)
    for extra in position.get("db_extra_cards", []):
        db.add(Card(name=extra["name"], cost=extra["cost"],
                    type=extra["type"], attack=extra.get("attack"),
                    health=extra.get("health"), effect_unknown=False))
    for name, eff in position.get("db_overrides", {}).items():
        card = db.get_by_name(name)
        if card is not None:
            for key, value in eff.items():
                if hasattr(card, key):
                    setattr(card, key, value)
    return db


def score(engine_version: str, positions_path: str, labels_path: str,
          deck_json_path: str, beam_width: int = 8) -> dict:
    spec = get_engine(engine_version)
    positions = load_positions(positions_path)
    labels = load_labels(labels_path)
    rows, blocked = [], []
    total = 0.0
    for pos in positions:
        label = labels.get(pos["id"])
        if label is None:
            continue
        cfg = load_deck(pos.get("deck_id", "mech-paladin-horn"))
        db = build_db(pos, deck_json_path)
        state = state_from_dict(pos["state"])
        menu = [repr(a) for a in legal_actions(state, db, cfg)]
        if not any(b in menu for b in label["best"]):
            blocked.append(pos["id"])
        plan = spec.decide_fn(state, db, beam_width=beam_width,
                              deck_config=cfg)
        pick = repr(plan[0]) if plan else None
        if pick in label["best"]:
            points = 1.0
        elif pick in label["ok"]:
            points = 0.5
        else:
            points = 0.0
        total += points
        rows.append({"id": pos["id"], "pick": pick, "points": points,
                     "best": label["best"], "note": label.get("note", "")})
    n = len(rows)
    return {
        "engine": engine_version,
        "policy_set": spec.policy_set,
        "total": total,
        "n": n,
        "score_pct": (100.0 * total / n) if n else 0.0,
        "best": sum(1 for r in rows if r["points"] == 1.0),
        "acceptable": sum(1 for r in rows if r["points"] == 0.5),
        "wrong": sum(1 for r in rows if r["points"] == 0.0),
        "blocked": blocked,
        "rows": rows,
    }


def print_report(result: dict) -> None:
    print(f"\nEngine {result['engine']} (policy: {result['policy_set']}): "
          f"{result['total']}/{result['n']} ({result['score_pct']:.0f}%)  "
          f"best {result['best']}, acceptable {result['acceptable']}, "
          f"wrong {result['wrong']}")
    for r in result["rows"]:
        if r["points"] < 1.0:
            mark = "~" if r["points"] > 0 else "x"
            print(f"  {mark} {r['id']} picked {r['pick']}  "
                  f"| best: {' / '.join(r['best'])}")
    if result["blocked"]:
        print(f"\nRules removed every best action in: {', '.join(result['blocked'])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-version", default="v1")
    ap.add_argument("--positions", default=os.path.join(HERE, "positions.jsonl"))
    ap.add_argument("--labels", default=os.path.join(HERE, "labels.json"))
    ap.add_argument("--deck-json", default=os.path.join(HERE, "..", "data", "deck.json"))
    ap.add_argument("--beam-width", type=int, default=8)
    args = ap.parse_args()
    print_report(score(args.engine_version, args.positions, args.labels,
                       args.deck_json, beam_width=args.beam_width))


if __name__ == "__main__":
    main()

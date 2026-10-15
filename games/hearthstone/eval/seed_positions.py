"""Seed generator for eval/positions.jsonl + eval/labels.json.

Run from games/hearthstone/:  python3 eval/seed_positions.py

Positions are frozen game states + the legal-action menu at seed time +
any card-DB extras/overrides the position needs to be self-contained.
Labels (best/ok action reprs) are the author's judgments, written here
— they are the ground truth the engine is scored against, not the
engine's own output.

Position schema (positions.jsonl, one JSON object per line):
  id, deck_id, source, state (state_to_dict), menu ([repr]),
  db_extra_cards ([{name, cost, type, attack, health, ...}]),
  db_overrides ({name: {effect_...}})

Labels schema (labels.json): {id: {best: [repr], ok: [repr], note: str}}
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hsbot.cards import Card, CardDB  # noqa: E402
from hsbot.decision import legal_actions  # noqa: E402
from hsbot.deck_config import load_deck  # noqa: E402
from hsbot.log_parser import LogParser  # noqa: E402
from hsbot.serialize import state_to_dict  # noqa: E402
from hsbot.state import Entity, GameState  # noqa: E402

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")


def _ent(eid, name, controller, zone, atk=0, hp=30, cost=0, cardtype=4, **tags):
    t = {"CARDTYPE": cardtype, "ATK": atk, "HEALTH": hp, "COST": cost}
    t.update(tags)
    return Entity(id=eid, name=name, controller=controller, zone=zone, tags=t)


def _state_with(hand, board, opp_board, opp_hp=30, mana=2, my_hp=30, turn=3):
    st = GameState()
    st.my_id = 1
    st.current_player = 1
    st.turn = turn
    st.step = "MAIN_ACTION"
    st.me.hero = _ent(1, "Uther", 1, "PLAY", hp=my_hp, cardtype=3)
    st.me.hero_power = _ent(2, "Reinforce", 1, "PLAY", cardtype=2)
    st.opp.hero = _ent(3, "Enemy", 2, "PLAY", hp=opp_hp, cardtype=3)
    st.me.hand = hand
    st.me.board = board
    st.opp.board = opp_board
    st.me.mana = mana
    for e in hand + board + opp_board + [st.me.hero, st.opp.hero]:
        st.entities[e.id] = e
    return st


def _sample_log_position(db, cfg):
    parser = LogParser(GameState())
    with open(os.path.join(ROOT, "samples", "sample_power.log")) as f:
        parser.feed_text(f.read())
    st = parser.state
    assert st.is_my_turn and st.step == "MAIN_ACTION" and st.turn > 0
    menu = [repr(a) for a in legal_actions(st, db, cfg)]
    pos = {
        "id": "sample-turn1",
        "deck_id": cfg.deck_id,
        "source": "samples/sample_power.log (turn 1, MAIN_ACTION)",
        "state": state_to_dict(st),
        "menu": menu,
        "db_extra_cards": [],
        "db_overrides": {},
    }
    label = {
        "best": ["END_TURN"],
        "ok": [],
        "note": "Nothing playable: 5-cost minion in hand, 1 mana, board minion just played.",
    }
    return pos, label


def _lethal_position(db, cfg):
    board = [_ent(10, "无人机拆解器", 1, "PLAY", atk=3, hp=2),
             _ent(11, "频率振荡机", 1, "PLAY", atk=2, hp=1)]
    st = _state_with([], board, [], opp_hp=5, mana=0)
    menu = [repr(a) for a in legal_actions(st, db, cfg)]
    pos = {
        "id": "lethal-plain",
        "deck_id": cfg.deck_id,
        "source": "hand-written: 3+2 attack vs 5 HP, no taunt",
        "state": state_to_dict(st),
        "menu": menu,
        "db_extra_cards": [],
        "db_overrides": {},
    }
    label = {
        "best": ["ATTACK 10 -> 3", "ATTACK 11 -> 3"],
        "ok": [],
        "note": "Either attack order kills. END_TURN is wrong with lethal on board.",
    }
    return pos, label


def _taunt_wall_position(db, cfg):
    board = [_ent(10, "无人机拆解器", 1, "PLAY", atk=3, hp=3)]
    taunt = _ent(20, "Taunt", 2, "PLAY", atk=1, hp=8, TAUNT=1)
    hand = [_ent(12, "安保自动机", 1, "HAND", atk=2, hp=3, cost=2)]
    st = _state_with(hand, board, [taunt], opp_hp=5, mana=2)
    menu = [repr(a) for a in legal_actions(st, db, cfg)]
    pos = {
        "id": "taunt-wall",
        "deck_id": cfg.deck_id,
        "source": "hand-written: 3-atk vs 1/8 taunt, 2-cost minion in hand",
        "state": state_to_dict(st),
        "menu": menu,
        "db_extra_cards": [],
        "db_overrides": {},
    }
    label = {
        "best": ["PLAY_MINION 安保自动机 (id=12)"],
        "ok": ["ATTACK 10 -> 20", "HERO_POWER"],
        "note": "Develop board; the 3-attack minion cannot clear the 8-HP taunt. "
                "Ending the turn with a playable minion is wrong.",
    }
    return pos, label


def _overdraw_position(db, cfg):
    hand = [_ent(30 + i, f"Filler{i}", 1, "HAND", atk=1, hp=1, cost=1)
            for i in range(9)]
    hand.append(_ent(40, "雷达探测", 1, "HAND", cost=2, cardtype=5))
    st = _state_with(hand, [], [], opp_hp=30, mana=2)
    extras = [{"name": f"Filler{i}", "cost": 1, "type": "MINION",
               "attack": 1, "health": 1} for i in range(9)]
    overrides = {"雷达探测": {"effect_draw": 1, "effect_unknown": False}}
    menu = [repr(a) for a in legal_actions(st, db, cfg)]
    pos = {
        "id": "overdraw-10",
        "deck_id": cfg.deck_id,
        "source": "hand-written: 10-card hand, draw spell + 1-cost minions",
        "state": state_to_dict(st),
        "menu": menu,
        "db_extra_cards": extras,
        "db_overrides": overrides,
    }
    label = {
        "best": [f"PLAY_MINION Filler{i} (id={30 + i})" for i in range(9)],
        "ok": ["END_TURN"],
        "note": "Play a minion first to make room; drawing with a full hand "
                "burns the drawn card.",
    }
    return pos, label


def _empty_board_buff_position(db, cfg):
    hand = [_ent(12, "安保自动机", 1, "HAND", atk=2, hp=3, cost=2),
            _ent(13, "十字军光环", 1, "HAND", cost=4, cardtype=5)]
    st = _state_with(hand, [], [], opp_hp=30, mana=4)
    overrides = {"十字军光环": {"effect_unknown": False}}
    menu = [repr(a) for a in legal_actions(st, db, cfg)]
    pos = {
        "id": "empty-board-buff",
        "deck_id": cfg.deck_id,
        "source": "hand-written: empty board, board-buff spell + 2-cost minion",
        "state": state_to_dict(st),
        "menu": menu,
        "db_extra_cards": [],
        "db_overrides": overrides,
    }
    label = {
        "best": ["PLAY_MINION 安保自动机 (id=12)"],
        "ok": ["END_TURN", "HERO_POWER"],
        "note": "A board buff with no board is wasted. Play the minion first.",
    }
    return pos, label


def main() -> None:
    cfg = load_deck("mech-paladin-horn")
    db = CardDB.from_deck_json(os.path.join(ROOT, "data", "deck.json"))
    # Seed-time menus for the modeled positions need the same extras the
    # eval harness will apply; compute menus after applying them.
    builders = [
        _sample_log_position,
        _lethal_position,
        _taunt_wall_position,
        _overdraw_position,
        _empty_board_buff_position,
    ]
    positions, labels = [], {}
    for build in builders:
        pos, label = build(db, cfg)
        # Recompute the menu with the position's own extras/overrides so the
        # stored menu matches what eval_score will see.
        db2 = CardDB.from_deck_json(os.path.join(ROOT, "data", "deck.json"))
        for extra in pos["db_extra_cards"]:
            db2.add(Card(name=extra["name"], cost=extra["cost"],
                         type=extra["type"], attack=extra.get("attack"),
                         health=extra.get("health"), effect_unknown=False))
        for name, eff in pos["db_overrides"].items():
            card = db2.get_by_name(name)
            if card is not None:
                for key, value in eff.items():
                    if hasattr(card, key):
                        setattr(card, key, value)
        from hsbot.serialize import state_from_dict  # noqa: E402
        st = state_from_dict(pos["state"])
        pos["menu"] = [repr(a) for a in legal_actions(st, db2, cfg)]
        positions.append(pos)
        labels[pos["id"]] = label
    with open(os.path.join(HERE, "positions.jsonl"), "w", encoding="utf-8") as f:
        for pos in positions:
            f.write(json.dumps(pos, ensure_ascii=False) + "\n")
    with open(os.path.join(HERE, "labels.json"), "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(positions)} positions + labels")


if __name__ == "__main__":
    main()

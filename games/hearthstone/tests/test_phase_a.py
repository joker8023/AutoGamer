"""Tests: Phase A — exact marking, deck config, masking, engine versions, eval."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eval.eval_label import format_position  # noqa: E402
from eval.eval_score import score  # noqa: E402
from hsbot.actions import ATTACK, END_TURN, HERO_POWER, PLAY_MINION, PLAY_SPELL, Action  # noqa: E402
from hsbot.cards import Card, CardDB  # noqa: E402
from hsbot.deck_config import list_decks, load_deck  # noqa: E402
from hsbot.decision import (  # noqa: E402
    apply_action, choose_mulligan, decide_turn, find_lethal,
)
from hsbot.engine_version import get_engine, list_versions  # noqa: E402
from hsbot.masking import public_view  # noqa: E402
from hsbot.serialize import state_from_dict, state_to_dict  # noqa: E402
from hsbot.state import Entity, GameState  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _ent(eid, name, controller, zone, atk=0, hp=30, cost=0, cardtype=4, **tags):
    t = {"CARDTYPE": cardtype, "ATK": atk, "HEALTH": hp, "COST": cost}
    t.update(tags)
    return Entity(id=eid, name=name, controller=controller, zone=zone, tags=t)


def _state_with(hand, board, opp_board, opp_hp=30, mana=2, my_hp=30):
    st = GameState()
    st.my_id = 1
    st.current_player = 1
    st.turn = 3
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


def _modeled_db():
    db = CardDB()
    db.add(Card(name="BC", cost=2, type="MINION", attack=2, health=2,
                mechanics={"BATTLECRY"}, effect_unknown=False))
    db.add(Card(name="Draw", cost=2, type="SPELL", effect_draw=1, effect_unknown=False))
    db.add(Card(name="Burn", cost=2, type="SPELL", effect_damage=3, effect_unknown=False))
    db.add(Card(name="DrawBurn", cost=3, type="SPELL", effect_damage=5,
                effect_draw=1, effect_unknown=False))
    db.add(Card(name="PureBurn", cost=3, type="SPELL", effect_damage=5, effect_unknown=False))
    db.add(Card(name="Buff", cost=2, type="SPELL", effect_unknown=False))
    db.add(Card(name="DR", cost=2, type="MINION", attack=2, health=1,
                mechanics={"DEATHRATTLE"}, effect_unknown=False))
    db.add(Card(name="Vanilla", cost=2, type="MINION", attack=2, health=3,
                effect_unknown=False))
    db.add(Card(name="Mystery", cost=2, type="MINION",
                attack=None, health=None, effect_unknown=False))
    db.add(Card(name="NoStats", cost=2, type="MINION",
                attack=None, health=None, effect_unknown=False))
    return db


def test_exact_marking():
    db = _modeled_db()
    st = _state_with([], [], [], mana=4)
    # Battlecry: inexact.
    st.me.hand = [_ent(10, "BC", 1, "HAND", atk=2, hp=2, cost=2)]
    _, exact, note = apply_action(st, Action(PLAY_MINION, source_id=10), db)
    assert not exact and "battlecry" in note
    # Draw: inexact.
    st.me.hand = [_ent(10, "Draw", 1, "HAND", cost=2, cardtype=5)]
    _, exact, note = apply_action(st, Action(PLAY_SPELL, source_id=10), db)
    assert not exact and "draw" in note
    # Pure damage: exact.
    st.me.hand = [_ent(10, "Burn", 1, "HAND", cost=2, cardtype=5)]
    _, exact, _ = apply_action(st, Action(PLAY_SPELL, source_id=10, target_id=3), db)
    assert exact
    # Unmodeled buff (no modeled effect at all): inexact.
    st.me.hand = [_ent(10, "Buff", 1, "HAND", cost=2, cardtype=5)]
    _, exact, note = apply_action(st, Action(PLAY_SPELL, source_id=10), db)
    assert not exact and "unmodeled" in note
    # Unknown card: inexact.
    st.me.hand = [_ent(10, "NotInDB", 1, "HAND", cost=2, cardtype=5)]
    _, exact, _ = apply_action(st, Action(PLAY_SPELL, source_id=10), db)
    assert not exact
    # Unknown stats, no entity tags either: inexact.
    mystery = Entity(id=10, name="Mystery", controller=1, zone="HAND",
                     tags={"CARDTYPE": 4, "COST": 2})
    st.me.hand = [mystery]
    _, exact, note = apply_action(st, Action(PLAY_MINION, source_id=10), db)
    assert not exact and "unknown stats" in note
    # Unknown card stats but live entity tags carry them: exact.
    nostats = _ent(10, "NoStats", 1, "HAND", atk=3, hp=4, cost=2)
    st.me.hand = [nostats]
    ns, exact, _ = apply_action(st, Action(PLAY_MINION, source_id=10), db)
    assert exact and ns.me.board[0].attack == 3 and ns.me.board[0].health == 4
    # Deathrattle death: inexact (killing blow on a deathrattle minion).
    dr = _ent(20, "DR", 2, "PLAY", atk=1, hp=1)
    killer = _ent(10, "Vanilla", 1, "PLAY", atk=2, hp=3)
    st2 = _state_with([], [killer], [dr], mana=4)
    _, exact, note = apply_action(st2, Action(ATTACK, source_id=10, target_id=20), db)
    assert not exact and "deathrattle" in note
    # Plain attack, nothing dies with a deathrattle: exact.
    vanilla_opp = _ent(20, "Vanilla", 2, "PLAY", atk=1, hp=5)
    st3 = _state_with([], [killer], [vanilla_opp], mana=4)
    _, exact, _ = apply_action(st3, Action(ATTACK, source_id=10, target_id=20), db)
    assert exact
    # Hero power (Paladin recruit) + END_TURN: exact.
    _, exact, _ = apply_action(st3, Action(HERO_POWER, source_id=2), db)
    assert exact
    _, exact, _ = apply_action(st3, Action(END_TURN), db)
    assert exact
    print("exact marking OK")


def test_beam_stops_at_inexact():
    db = _modeled_db()
    hand = [_ent(10, "Draw", 1, "HAND", cost=2, cardtype=5),
            _ent(11, "Vanilla", 1, "HAND", atk=2, hp=3, cost=2)]
    st = _state_with(hand, [], [], mana=4)
    plan = decide_turn(st, db)
    assert plan[-1].kind == END_TURN
    # Simulate the plan: any inexact step must be immediately before END_TURN.
    sim = st
    for i, action in enumerate(plan[:-1]):
        sim, exact, _ = apply_action(sim, action, db)
        if not exact:
            assert i == len(plan) - 2, f"inexact action {action} not last: {plan}"
    print("beam stops at inexact OK:", plan)


def test_lethal_requires_exact():
    db = _modeled_db()
    # Pure burn lethal: counts.
    hand = [_ent(10, "PureBurn", 1, "HAND", cost=3, cardtype=5)]
    st = _state_with(hand, [], [], opp_hp=5, mana=3)
    lethal = find_lethal(st, db)
    assert lethal is not None and lethal[0].kind == PLAY_SPELL
    # Burn stapled to a draw: inexact -> no lethal.
    hand2 = [_ent(10, "DrawBurn", 1, "HAND", cost=3, cardtype=5)]
    st2 = _state_with(hand2, [], [], opp_hp=5, mana=3)
    assert find_lethal(st2, db) is None
    print("lethal requires exact OK")


def test_deck_config():
    cfg = load_deck("mech-paladin-horn")
    assert cfg.deck_id == "mech-paladin-horn"
    assert cfg.keep_always == {"无人机拆解器", "频率振荡机", "安保自动机", "忙碌机器人"}
    assert "mech-paladin-horn" in list_decks()
    assert cfg.weights["my_hero"] == 3.0
    assert "十字军光环" in cfg.holds and cfg.zilliax_save_when_behind
    assert cfg.strategy_text and "Zilliax" in cfg.strategy_text
    hand = ["无人机拆解器", "馆长", "金翼巨龙"]
    assert choose_mulligan(hand, has_coin=False, deck_config=cfg) == {1, 2}
    # decide_turn with an explicit config matches the default-config path.
    db = CardDB.from_deck_json(os.path.join(ROOT, "data", "deck.json"))
    hand2 = [_ent(10, "安保自动机", 1, "HAND", atk=2, hp=3, cost=2)]
    st = _state_with(hand2, [], [], opp_hp=30, mana=2)
    plan_cfg = decide_turn(st, db, deck_config=cfg)
    plan_default = decide_turn(st, db)
    assert [repr(a) for a in plan_cfg] == [repr(a) for a in plan_default]
    print("deck config OK")


def test_deck_data_compat():
    from hsbot import deck_data
    assert deck_data.DECK_CODE.startswith("AAEBAaTo")
    assert deck_data.HERO_DBFID == 46116
    assert deck_data.MULLIGAN["keep_always"] == {"无人机拆解器", "频率振荡机", "安保自动机", "忙碌机器人"}
    assert deck_data.STRATEGY["hold_for_value"] == {"十字军光环", "馆长"}
    assert deck_data.DECK_CARDS[0] == ("无人机拆解器", 1, 2, "opener")
    print("deck_data compat OK")


def test_masking():
    st = GameState()
    st.my_id = 1
    st.turn = 2
    st.me.hand = [_ent(10, "安保自动机", 1, "HAND", atk=2, hp=3, cost=2)]
    st.me.hero = _ent(1, "Uther", 1, "PLAY", hp=30, cardtype=3)
    st.opp.hero = _ent(3, "Enemy", 2, "PLAY", hp=30, cardtype=3)
    st.opp.hand = [_ent(50, "SecretHandCard", 2, "HAND", cost=3, cardtype=5)]
    st.opp.board = [_ent(51, "OppMinion", 2, "PLAY", atk=2, hp=2)]
    secret = _ent(60, "Secret", 2, "SECRET", cardtype=5)
    secret.card_id = "SECRET_001"
    st.secrets = [secret]
    st.opp.deck_count = 17
    view = public_view(st)
    blob = json.dumps(view, ensure_ascii=False)
    assert "SecretHandCard" not in blob
    assert "SECRET_001" not in blob
    assert view["opp"]["hand_count"] == 1
    assert view["opp"]["deck_count"] == 17
    assert view["opp"]["secrets_count"] == 1
    assert view["opp"]["board"][0]["name"] == "OppMinion"  # public
    assert view["me"]["hand"][0]["name"] == "安保自动机"  # own info
    assert "battletag" not in blob.lower() and "gameaccountid" not in blob.lower()
    print("masking OK")


def test_engine_version():
    spec = get_engine("v1")
    assert spec.name == "v1" and spec.policy_set == "baseline"
    assert "v1" in list_versions()
    try:
        get_engine("no-such-version")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown version must raise")
    print("engine version OK")


def test_serialize_roundtrip():
    hand = [_ent(10, "安保自动机", 1, "HAND", atk=2, hp=3, cost=2)]
    board = [_ent(11, "频率振荡机", 1, "PLAY", atk=2, hp=1)]
    opp_board = [_ent(20, "Taunt", 2, "PLAY", atk=1, hp=8, TAUNT=1)]
    st = _state_with(hand, board, opp_board, opp_hp=25, mana=3)
    st.turn = 4
    st2 = state_from_dict(state_to_dict(st))
    assert st2.turn == 4 and st2.me.mana == 3
    assert [e.name for e in st2.me.hand] == ["安保自动机"]
    assert [(e.attack, e.health) for e in st2.me.board] == [(2, 1)]
    assert st2.opp.board[0].tag("TAUNT") == 1
    assert st2.opp.hero_health == 25
    assert st2.entity(11) is not None  # entities index rebuilt
    print("serialize round-trip OK")


def test_eval_harness():
    result = score("v1",
                   os.path.join(ROOT, "eval", "positions.jsonl"),
                   os.path.join(ROOT, "eval", "labels.json"),
                   os.path.join(ROOT, "data", "deck.json"))
    assert result["n"] == 5 and result["total"] == 4.0
    assert result["blocked"] == []
    assert result["wrong"] == 1  # overdraw-10: known weights-calibration gap
    with open(os.path.join(ROOT, "eval", "positions.jsonl"), encoding="utf-8") as f:
        pos = json.loads(f.readline())
    text = format_position(pos)
    assert "turn" in text and "menu" in text
    print("eval harness OK:", result["total"], "/", result["n"])


if __name__ == "__main__":
    test_exact_marking()
    test_beam_stops_at_inexact()
    test_lethal_requires_exact()
    test_deck_config()
    test_deck_data_compat()
    test_masking()
    test_engine_version()
    test_serialize_roundtrip()
    test_eval_harness()
    print("PHASE A TESTS PASSED")

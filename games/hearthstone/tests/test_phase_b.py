"""Tests: Phase B — veto/prefer two-layer rules + release + trace."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hsbot.actions import ATTACK, END_TURN, HERO_POWER, PLAY_MINION, PLAY_SPELL, Action  # noqa: E402
from hsbot.cards import Card, CardDB  # noqa: E402
from hsbot.decision import decide_turn, decide_turn_traced, legal_actions  # noqa: E402
from hsbot.deck_config import load_deck  # noqa: E402
from hsbot.engine_version import get_engine  # noqa: E402
from hsbot.policy import prefer_actions, veto_actions  # noqa: E402
from hsbot.state import Entity, GameState  # noqa: E402

CFG = load_deck("mech-paladin-horn")


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


def _db():
    db = CardDB()
    db.add(Card(name="Burn", cost=2, type="SPELL", effect_damage=3, effect_unknown=False))
    db.add(Card(name="Draw", cost=2, type="SPELL", effect_draw=1, effect_unknown=False))
    db.add(Card(name="Buff", cost=2, type="SPELL", effect_unknown=False))
    db.add(Card(name="SecretSpell", cost=2, type="SPELL", effect_unknown=False,
                mechanics={"SECRET"}))
    db.add(Card(name="幸运币", cost=0, type="SPELL", effect_unknown=False))
    db.add(Card(name="Vanilla", cost=2, type="MINION", attack=2, health=3,
                effect_unknown=False))
    db.add(Card(name="Zilliax", cost=0, type="MINION", attack=3, health=2,
                effect_unknown=False))
    db.add(Card(name="奇利亚斯豪华版3000型", cost=0, type="MINION", attack=3, health=2,
                effect_unknown=False))
    db.add(Card(name="馆长", cost=5, type="MINION", attack=4, health=6,
                effect_unknown=False))
    return db


def test_veto_never_removes_end_turn():
    db = _db()
    st = _state_with([], [], [], mana=0)
    kept, removed = veto_actions(st, db, [Action(END_TURN)], CFG)
    assert kept == [Action(END_TURN)] and removed == []
    print("veto never removes END_TURN OK")


def test_veto_own_hero_damage():
    db = _db()
    st = _state_with([], [], [], mana=2)
    burn_self = Action(PLAY_SPELL, source_id=10, target_id=1, card_name="Burn")
    burn_opp = Action(PLAY_SPELL, source_id=10, target_id=3, card_name="Burn")
    kept, removed = veto_actions(st, db, [burn_self, burn_opp], CFG)
    assert kept == [burn_opp] and len(removed) == 1
    assert removed[0]["rule"] == "own_hero_damage"
    print("veto own-hero damage OK")


def test_veto_fatigue_suicide():
    db = _db()
    draw = Action(PLAY_SPELL, source_id=10, card_name="Draw")
    st = _state_with([], [], [], mana=2, my_hp=1)
    st.me.deck_count = 0
    kept, removed = veto_actions(st, db, [draw], CFG)
    assert kept == [] and removed[0]["rule"] == "fatigue_suicide"
    # Deck not empty -> kept.
    st.me.deck_count = 5
    kept, _ = veto_actions(st, db, [draw], CFG)
    assert kept == [draw]
    # HP above 1 -> kept (fatigue hit is survivable).
    st.me.deck_count = 0
    st.me.hero = _ent(1, "Uther", 1, "PLAY", hp=10, cardtype=3)
    kept, _ = veto_actions(st, db, [draw], CFG)
    assert kept == [draw]
    print("veto fatigue suicide OK")


def test_veto_useless_buff():
    db = _db()
    buff = Action(PLAY_SPELL, source_id=10, card_name="Buff")
    secret = Action(PLAY_SPELL, source_id=11, card_name="SecretSpell")
    burn = Action(PLAY_SPELL, source_id=12, target_id=3, card_name="Burn")
    st = _state_with([], [], [], mana=2)  # empty board
    kept, removed = veto_actions(st, db, [buff, secret, burn], CFG)
    assert kept == [secret, burn]
    assert [r["rule"] for r in removed] == ["useless_buff"]
    # Board non-empty -> buff kept.
    st.me.board = [_ent(20, "Vanilla", 1, "PLAY", atk=2, hp=3)]
    kept, _ = veto_actions(st, db, [buff], CFG)
    assert kept == [buff]
    print("veto useless buff OK")


def test_veto_dead_coin():
    db = _db()
    coin = Action(PLAY_SPELL, source_id=10, card_name="幸运币")
    # Nothing playable even with +1 mana: vetoed.
    st = _state_with([], [], [], mana=0)
    st.me.hero_power = None  # no hero power either
    kept, removed = veto_actions(st, db, [coin], CFG)
    assert kept == [] and removed[0]["rule"] == "dead_coin"
    # A 1-cost card in hand becomes playable with the coin: kept.
    st.me.hand = [_ent(11, "Vanilla", 1, "HAND", atk=2, hp=3, cost=1)]
    st.me.hero_power = _ent(2, "Reinforce", 1, "PLAY", cardtype=2)
    kept, _ = veto_actions(st, db, [coin], CFG)
    assert kept == [coin]
    print("veto dead coin OK")


def test_veto_full_board():
    db = _db()
    play = Action(PLAY_MINION, source_id=10, card_name="Vanilla")
    st = _state_with([], [_ent(20 + i, "M", 1, "PLAY", atk=1, hp=1) for i in range(7)],
                     [], mana=2)
    kept, removed = veto_actions(st, db, [play], CFG)
    assert kept == [] and removed[0]["rule"] == "full_board"
    print("veto full board OK")


def test_prefer_hold_zilliax():
    db = _db()
    z = Action(PLAY_MINION, source_id=10, card_name="奇利亚斯豪华版3000型")
    # Behind (HP 10) -> held.
    st = _state_with([], [], [], mana=2, my_hp=10)
    kept, applied = prefer_actions(st, db, [z, Action(END_TURN)], CFG)
    assert kept == [Action(END_TURN)]
    assert applied[0]["rule"] == "hold_zilliax"
    # Not behind -> kept.
    st.me.hero = _ent(1, "Uther", 1, "PLAY", hp=30, cardtype=3)
    kept, applied = prefer_actions(st, db, [z, Action(END_TURN)], CFG)
    assert kept == [z, Action(END_TURN)] and applied == []
    print("prefer hold Zilliax OK")


def test_prefer_hold_for_value():
    db = _db()
    curator = Action(PLAY_MINION, source_id=10, card_name="馆长")
    st = _state_with([], [], [], mana=5)  # empty board
    kept, applied = prefer_actions(st, db, [curator, Action(END_TURN)], CFG)
    assert kept == [Action(END_TURN)]
    assert applied[0]["rule"] == "hold_for_value"
    # Board non-empty -> kept.
    st.me.board = [_ent(20, "Vanilla", 1, "PLAY", atk=2, hp=3)]
    kept, applied = prefer_actions(st, db, [curator, Action(END_TURN)], CFG)
    assert kept == [curator, Action(END_TURN)] and applied == []
    print("prefer hold-for-value OK")


def test_prefer_never_empties_menu():
    db = _db()
    curator = Action(PLAY_MINION, source_id=10, card_name="馆长")
    st = _state_with([], [], [], mana=5)  # empty board, menu has ONLY the held card
    kept, applied = prefer_actions(st, db, [curator], CFG)
    assert kept == [curator] and applied == []
    print("prefer never empties menu OK")


def test_release_when_prefer_costs_too_much():
    db = _db()
    # Only Zilliax in hand, behind, nothing else playable: holding it
    # means passing the turn, so the release must fire.
    z = _ent(10, "奇利亚斯豪华版3000型", 1, "HAND", atk=3, hp=2, cost=0)
    st = _state_with([z], [], [], mana=0, my_hp=10)
    plan, trace = decide_turn_traced(st, db, deck_config=CFG)
    assert plan[0].card_name == "奇利亚斯豪华版3000型"
    assert trace["released"] == ["hold_zilliax"]
    assert trace["preferences"][0]["rule"] == "hold_zilliax"
    print("release OK:", plan, trace["released"])


def test_prefer_holds_when_margin_not_exceeded():
    db = _db()
    # Zilliax + a curve minion, behind, 2 mana. Holding Zilliax costs
    # less than the margin (0.8 < 1.0), so the prefer stands: v2 plays
    # the curve minion, v1 (no policy) plays Zilliax first.
    # Zilliax costs 2 in this scenario so the curve minion's line is
    # within the release margin (0.1 < 1.0) and the prefer stands.
    db = _db()
    db.add(Card(name="奇利亚斯豪华版3000型", cost=2, type="MINION",
                attack=3, health=2, effect_unknown=False))
    z = _ent(10, "奇利亚斯豪华版3000型", 1, "HAND", atk=3, hp=2, cost=2)
    curve = _ent(11, "Vanilla", 1, "HAND", atk=2, hp=3, cost=2)
    st = _state_with([z, curve], [], [], mana=2, my_hp=10)
    plan_v2, trace = decide_turn_traced(st, db, deck_config=CFG, policy=True)
    plan_v1 = decide_turn(st, db, deck_config=CFG, policy=False)
    assert plan_v2[0].card_name == "Vanilla", plan_v2
    assert plan_v1[0].card_name == "奇利亚斯豪华版3000型", plan_v1
    assert trace["released"] == []
    print("prefer holds when margin not exceeded OK")


def test_trace_emitted_by_bot():
    from hsbot.bot import Bot
    from hsbot.actions import DryRunExecutor
    db = _db()
    bot = Bot(db, executor=DryRunExecutor(quiet=True), quiet=True)
    events = []
    bot.on_event = events.append
    st = _state_with([], [], [], mana=0, my_hp=10)
    bot.parser.state = st
    bot._do_turn_whole_plan()  # dry-run path (renamed in Phase C)
    decision = next(e for e in events if e["type"] == "decision")
    assert decision["engine_version"] == "v2"
    assert "vetoed" in decision["trace"] and "preferences" in decision["trace"]
    print("trace emitted by bot OK")


def test_engine_versions_differ():
    v1 = get_engine("v1")
    v2 = get_engine("v2")
    assert v1.policy_set == "baseline" and not v1.policy
    assert v2.policy_set == "veto-prefer-v1" and v2.policy
    print("engine versions OK")


def test_legal_actions_has_no_strategy_filtering():
    # legal_actions is now pure enumeration: held cards still appear.
    db = _db()
    curator = _ent(10, "馆长", 1, "HAND", atk=4, hp=6, cost=5)
    st = _state_with([curator], [], [], mana=5)
    names = [a.card_name for a in legal_actions(st, db, CFG)]
    assert "馆长" in names
    print("legal_actions pure enumeration OK")


if __name__ == "__main__":
    test_veto_never_removes_end_turn()
    test_veto_own_hero_damage()
    test_veto_fatigue_suicide()
    test_veto_useless_buff()
    test_veto_dead_coin()
    test_veto_full_board()
    test_prefer_hold_zilliax()
    test_prefer_hold_for_value()
    test_prefer_never_empties_menu()
    test_release_when_prefer_costs_too_much()
    test_prefer_holds_when_margin_not_exceeded()
    test_trace_emitted_by_bot()
    test_engine_versions_differ()
    test_legal_actions_has_no_strategy_filtering()
    print("PHASE B TESTS PASSED")

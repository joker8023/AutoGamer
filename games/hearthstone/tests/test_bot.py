"""Tests: deckstring decoding, log parsing, decision engine."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hsbot.actions import Action, DryRunExecutor, Layout  # noqa: E402
from hsbot.bot import Bot  # noqa: E402
from hsbot.cards import CardDB  # noqa: E402
from hsbot.deck_data import DECK_CODE, HERO_DBFID  # noqa: E402
from hsbot.deckstring import decode_deckstring  # noqa: E402
from hsbot.decision import choose_mulligan, decide_turn, find_lethal  # noqa: E402
from hsbot.log_parser import LogParser  # noqa: E402
from hsbot.state import Entity, GameState  # noqa: E402


def test_deckstring():
    ds = decode_deckstring(DECK_CODE)
    assert ds.format == 1 and ds.is_wild
    assert ds.heroes == [HERO_DBFID]
    assert ds.total_cards == 30
    assert len(ds.cards) == 18  # 12 pairs + 6 singles
    assert len(ds.sideboard) == 4
    # Zilliax (dbfId 102983) owns 3 sideboard entries: base + 2 modules.
    zilliax_owned = [c for c, o in ds.sideboard if o == 102983]
    assert len(zilliax_owned) == 3
    print("deckstring OK:", ds.total_cards, "cards, sideboard:", ds.sideboard)


def test_log_parser():
    parser = LogParser()
    with open(os.path.join(os.path.dirname(__file__), "..", "samples", "sample_power.log")) as f:
        parser.feed_text(f.read())
    st = parser.state
    assert st.turn == 1
    assert st.step == "MAIN_ACTION"
    assert st.current_player == 1
    assert st.me.hero is not None and st.me.hero_health == 30
    assert st.opp.hero is not None and st.opp.hero_health == 30
    assert st.me.mana == 1 and st.me.max_mana == 1
    # The 1-cost minion moved HAND -> PLAY; the 5-cost one stays in hand.
    assert len(st.me.board) == 1 and st.me.board[0].id == 10
    assert len(st.me.hand) == 1 and st.me.hand[0].id == 11
    print("log_parser OK: board", [e.id for e in st.me.board], "hand", [e.id for e in st.me.hand])


def _ent(eid, name, controller, zone, atk=0, hp=30, cost=0, cardtype=4):
    return Entity(
        id=eid, name=name, controller=controller, zone=zone,
        tags={"CARDTYPE": cardtype, "ATK": atk, "HEALTH": hp, "COST": cost},
    )


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


def test_mulligan():
    hand = ["无人机拆解器", "馆长", "金翼巨龙"]
    assert choose_mulligan(hand, has_coin=False) == {1, 2}
    # With coin, a 2-cost curve card is kept.
    hand2 = ["无人机拆解器", "安保自动机", "馆长"]
    assert choose_mulligan(hand2, has_coin=True) == {2}
    print("mulligan OK")


def test_lethal():
    db = CardDB.from_deck_json(os.path.join(os.path.dirname(__file__), "..", "data", "deck.json"))
    board = [_ent(10, "无人机拆解器", 1, "PLAY", atk=3, hp=2),
             _ent(11, "频率振荡机", 1, "PLAY", atk=2, hp=1)]
    st = _state_with([], board, [], opp_hp=5, mana=0)
    lethal = find_lethal(st, db)
    assert lethal is not None and len(lethal) == 2
    assert all(a.kind == "ATTACK" for a in lethal)
    # Not lethal when the enemy has more health.
    st2 = _state_with([], board, [], opp_hp=6, mana=0)
    assert find_lethal(st2, db) is None
    # Taunt blocks lethal if attackers cannot clear it.
    taunt = _ent(20, "Taunt", 2, "PLAY", atk=1, hp=8, cardtype=4)
    taunt.tags["TAUNT"] = 1
    st3 = _state_with([], board, [taunt], opp_hp=5, mana=0)
    assert find_lethal(st3, db) is None
    print("lethal OK")


def test_decide_turn_plays_on_curve():
    db = CardDB.from_deck_json(os.path.join(os.path.dirname(__file__), "..", "data", "deck.json"))
    hand = [_ent(10, "安保自动机", 1, "HAND", atk=2, hp=3, cost=2)]
    st = _state_with(hand, [], [], opp_hp=30, mana=2)
    plan = decide_turn(st, db)
    assert plan[0].kind == "PLAY_MINION" and plan[0].source_id == 10
    assert plan[-1].kind == "END_TURN"
    print("decide_turn OK:", plan)


def test_bot_dry_run():
    db = CardDB.from_deck_json(os.path.join(os.path.dirname(__file__), "..", "data", "deck.json"))
    bot = Bot(db, executor=DryRunExecutor())
    with open(os.path.join(os.path.dirname(__file__), "..", "samples", "sample_power.log")) as f:
        for line in f:
            bot.feed_line(line)
    print("bot dry-run OK, executed:", bot.executor.executed)


def test_layout_auto():
    # 16:9 window: the full rect is the board.
    lay = Layout.from_window_rect(100, 50, 1920, 1080)
    assert (lay.width, lay.height, lay.offset_x, lay.offset_y) == (1920, 1080, 100, 50)
    # Ultrawide window: 16:9 board centered, black bars on the sides.
    lay = Layout.from_window_rect(0, 0, 3440, 1440)
    assert (lay.width, lay.height) == (2560, 1440)
    assert (lay.offset_x, lay.offset_y) == ((3440 - 2560) // 2, 0)
    # Taller window: board height limited, bars top/bottom.
    lay = Layout.from_window_rect(0, 0, 1920, 1200)
    assert (lay.width, lay.height) == (1920, 1080)
    assert lay.offset_y == (1200 - 1080) // 2
    # Degenerate rect: reference fallback.
    lay = Layout.from_window_rect(0, 0, 0, 0)
    assert (lay.width, lay.height) == (1920, 1080)
    # No such window in the test env: auto_detect falls back, never raises.
    lay = Layout.auto_detect(("definitely-not-a-real-window-title-xyz",))
    assert (lay.width, lay.height, lay.offset_x, lay.offset_y) == (1920, 1080, 0, 0)
    # Coordinates scale with the detected board: END_TURN at 2560x1440.
    lay = Layout.from_window_rect(0, 0, 2560, 1440)
    assert lay.end_turn_pos() == (int(1770 * 2560 / 1920), int(480 * 1440 / 1080))
    print("layout auto OK")


if __name__ == "__main__":
    test_deckstring()
    test_log_parser()
    test_mulligan()
    test_lethal()
    test_decide_turn_plays_on_curve()
    test_bot_dry_run()
    test_layout_auto()
    print("ALL TESTS PASSED")

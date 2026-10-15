"""Tests: Phase C — click-then-verify, per-action loop, runner stops."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import hsbot.bot as bot_mod  # noqa: E402
from hsbot.actions import (  # noqa: E402
    ATTACK,
    END_TURN,
    HERO_POWER,
    MULLIGAN,
    PLAY_MINION,
    PLAY_SPELL,
    Action,
    DryRunExecutor,
    action_is_drag,
)
from hsbot.bot import Bot  # noqa: E402
from hsbot.cards import Card, CardDB  # noqa: E402
from hsbot.deck_config import load_deck  # noqa: E402
from hsbot.log_parser import LogParser  # noqa: E402
from hsbot.runner import Runner, RunnerConfig  # noqa: E402
from hsbot.state import Entity, GameState  # noqa: E402
from hsbot.verify import MISFIRE, OK, PENDING, verify_action  # noqa: E402

CFG = load_deck("mech-paladin-horn")


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
    for e in hand + board + opp_board + [st.me.hero, st.opp.hero, st.me.hero_power]:
        st.entities[e.id] = e
    return st


def _db():
    db = CardDB()
    db.add(Card(name="Burn", cost=2, type="SPELL", effect_damage=3, effect_unknown=False))
    db.add(Card(name="Vanilla", cost=2, type="MINION", attack=2, health=3,
                effect_unknown=False))
    return db


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _RecExecutor:
    """Fake live executor: records, never touches the real mouse."""

    def __init__(self):
        self.executed = []
        self.cancels = 0

    def execute(self, action, state):
        self.executed.append(action)
        return []

    def cancel_drag(self):
        self.cancels += 1


def _live_bot(clock, **kw):
    params = dict(
        settle_base=0, settle_per_draw=0, settle_board=0,
        verify_timeout=10, max_dead_streak=2, max_misfire_streak=1,
        clock=clock,
    )
    params.update(kw)
    bot = Bot(_db(), executor=_RecExecutor(), deck_config=CFG, **params)
    bot.parser.state = _state_with(
        [_ent(10, "Burn", 1, "HAND", cost=2, cardtype=5)], [], [], mana=2)
    return bot


# --- verify.py ------------------------------------------------------------

def test_verify_play_minion_ok():
    before = _state_with([_ent(10, "Vanilla", 1, "HAND", cost=2)], [], [])
    after = _state_with([], [_ent(10, "Vanilla", 1, "PLAY", atk=2, hp=3)], [])
    assert verify_action(before, Action(PLAY_MINION, source_id=10, card_name="Vanilla"), after) == OK
    print("verify PLAY_MINION OK ok")


def test_verify_play_minion_pending_and_misfire():
    before = _state_with([_ent(10, "Vanilla", 1, "HAND", cost=2)], [], [])
    same = _state_with([_ent(10, "Vanilla", 1, "HAND", cost=2)], [], [])
    assert verify_action(before, Action(PLAY_MINION, source_id=10), same) == PENDING
    # Source was never in hand: not a pending click, a bug.
    no_source = _state_with([], [], [])
    assert verify_action(no_source, Action(PLAY_MINION, source_id=10), same) == MISFIRE
    # Turn advanced without the minion landing: the click missed.
    advanced = _state_with([_ent(10, "Vanilla", 1, "HAND", cost=2)], [], [], turn=4)
    assert verify_action(before, Action(PLAY_MINION, source_id=10), advanced) == MISFIRE
    print("verify PLAY_MINION PENDING/MISFIRE ok")


def test_verify_attack_ok_damage_and_shield():
    before = _state_with(
        [], [_ent(20, "Attacker", 1, "PLAY", atk=2, hp=2)],
        [_ent(30, "Target", 2, "PLAY", atk=2, hp=3)])
    after_dmg = _state_with(
        [], [_ent(20, "Attacker", 1, "PLAY", atk=2, hp=2, EXHAUSTED=1)],
        [_ent(30, "Target", 2, "PLAY", atk=2, hp=3, DAMAGE=2)])
    assert verify_action(before, Action(ATTACK, source_id=20, target_id=30), after_dmg) == OK
    before_shield = _state_with(
        [], [_ent(20, "Attacker", 1, "PLAY", atk=2, hp=2)],
        [_ent(30, "Target", 2, "PLAY", atk=2, hp=3, DIVINE_SHIELD=1)])
    after_shield = _state_with(
        [], [_ent(20, "Attacker", 1, "PLAY", atk=2, hp=2, EXHAUSTED=1)],
        [_ent(30, "Target", 2, "PLAY", atk=2, hp=3, DIVINE_SHIELD=0)])
    assert verify_action(before_shield, Action(ATTACK, source_id=20, target_id=30), after_shield) == OK
    print("verify ATTACK OK ok")


def test_verify_attack_misfire_and_pending():
    before = _state_with(
        [], [_ent(20, "Attacker", 1, "PLAY", atk=2, hp=2)],
        [_ent(30, "Target", 2, "PLAY", atk=2, hp=3)])
    # Attacker exhausted but target unchanged: the swing whiffed.
    misfire = _state_with(
        [], [_ent(20, "Attacker", 1, "PLAY", atk=2, hp=2, EXHAUSTED=1)],
        [_ent(30, "Target", 2, "PLAY", atk=2, hp=3)])
    assert verify_action(before, Action(ATTACK, source_id=20, target_id=30), misfire) == MISFIRE
    assert verify_action(before, Action(ATTACK, source_id=20, target_id=30), before) == PENDING
    print("verify ATTACK MISFIRE/PENDING ok")


def test_verify_play_spell():
    burn = Action(PLAY_SPELL, source_id=10, target_id=3, card_name="Burn")
    before = _state_with([_ent(10, "Burn", 1, "HAND", cost=2, cardtype=5)], [], [], opp_hp=30)
    after_ok = _state_with([], [], [], opp_hp=27)
    assert verify_action(before, burn, after_ok) == OK
    # Source gone, target untouched, turn advanced: misfire.
    after_miss = _state_with([], [], [], opp_hp=30, turn=4)
    assert verify_action(before, burn, after_miss) == MISFIRE
    assert verify_action(before, burn, before) == PENDING
    # No-target spell: source leaving hand is enough.
    coin = Action(PLAY_SPELL, source_id=11, target_id=0, card_name="Coin")
    before_coin = _state_with([_ent(11, "Coin", 1, "HAND", cost=0, cardtype=5)], [], [])
    after_coin = _state_with([], [], [])
    assert verify_action(before_coin, coin, after_coin) == OK
    print("verify PLAY_SPELL ok")


def test_verify_hero_power_and_end_turn():
    hp = Action(HERO_POWER, source_id=2)
    before = _state_with([], [], [])
    after_ok = _state_with([], [], [])
    after_ok.me.hero_power = _ent(2, "Reinforce", 1, "PLAY", cardtype=2, EXHAUSTED=1)
    after_ok.entities[2] = after_ok.me.hero_power
    assert verify_action(before, hp, after_ok) == OK
    assert verify_action(before, hp, before) == PENDING
    advanced = _state_with([], [], [], turn=4)
    assert verify_action(before, hp, advanced) == MISFIRE

    et = Action(END_TURN)
    assert verify_action(before, et, advanced) == OK
    assert verify_action(before, et, before) == PENDING
    over = _state_with([], [], [], turn=3)
    over.game_over = True
    assert verify_action(before, et, over) == MISFIRE
    print("verify HERO_POWER / END_TURN ok")


def test_verify_mulligan():
    act = Action(MULLIGAN, meta={"kept_names": ["Alpha"]})
    in_mull = _state_with([], [], [])
    in_mull.step = "BEGIN_MULLIGAN"
    assert verify_action(in_mull, act, in_mull) == PENDING
    after_ok = _state_with([_ent(10, "Alpha", 1, "HAND")], [], [])
    assert verify_action(in_mull, act, after_ok) == OK
    after_bad = _state_with([_ent(10, "Beta", 1, "HAND")], [], [])
    assert verify_action(in_mull, act, after_bad) == MISFIRE
    assert verify_action(in_mull, Action(MULLIGAN), after_ok) == PENDING
    print("verify MULLIGAN ok")


# --- actions.py -----------------------------------------------------------

def test_action_meta_and_drag():
    a = Action(PLAY_MINION, source_id=10, card_name="Vanilla")
    assert a.meta == {}
    a.meta["kept_names"] = ["Alpha"]
    assert a.meta["kept_names"] == ["Alpha"]
    assert MULLIGAN == "MULLIGAN"
    assert action_is_drag(Action(PLAY_MINION, source_id=10))
    assert action_is_drag(Action(ATTACK, source_id=20, target_id=30))
    assert action_is_drag(Action(PLAY_SPELL, source_id=10, target_id=3))
    assert not action_is_drag(Action(PLAY_SPELL, source_id=10, target_id=0))
    assert not action_is_drag(Action(HERO_POWER, source_id=2))
    assert not action_is_drag(Action(END_TURN))
    DryRunExecutor(quiet=True).cancel_drag()  # no-op, must not raise
    print("action meta / drag ok")


# --- parser: mulligan receipt + game end ----------------------------------

def test_parser_mulligan_receipt():
    p = LogParser(GameState())
    p.feed_text("\n".join([
        "TAG_CHANGE Entity=GameEntity tag=STEP value=BEGIN_MULLIGAN",
        "TAG_CHANGE Entity=[name=Alpha id=10 zone=HAND zonePos=0 player=1] tag=ZONE value=HAND",
        "TAG_CHANGE Entity=[name=Beta id=11 zone=HAND zonePos=0 player=1] tag=ZONE value=HAND",
        "TAG_CHANGE Entity=GameEntity tag=STEP value=MAIN_ACTION",
    ]))
    assert p.state.mulligan_receipt == ["Alpha", "Beta"]
    print("parser mulligan receipt ok")


def test_parser_playstate_and_gameover():
    p = LogParser(GameState())
    p.feed_line("TAG_CHANGE Entity=[name=Player id=1 zone=PLAY zonePos=0 player=1] tag=PLAYSTATE value=WON")
    assert p.state.game_over and p.state.game_result == "WIN"
    p = LogParser(GameState())
    p.feed_line("TAG_CHANGE Entity=[name=Player id=1 zone=PLAY zonePos=0 player=1] tag=PLAYSTATE value=LOST")
    assert p.state.game_result == "LOSS"
    p = LogParser(GameState())
    p.feed_line("TAG_CHANGE Entity=[name=Player id=2 zone=PLAY zonePos=0 player=2] tag=PLAYSTATE value=WON")
    assert p.state.game_result == "LOSS"  # opponent won = we lost
    p = LogParser(GameState())
    p.feed_line("TAG_CHANGE Entity=[name=Player id=1 zone=PLAY zonePos=0 player=1] tag=PLAYSTATE value=3")
    assert p.state.game_result == "LOSS"  # numeric form
    p = LogParser(GameState())
    p.feed_line("TAG_CHANGE Entity=GameEntity tag=STEP value=FINAL_GAMEOVER")
    assert p.state.game_over and p.state.game_result is None
    print("parser PLAYSTATE / FINAL_GAMEOVER ok")


# --- bot live loop --------------------------------------------------------

def test_bot_live_ok_then_redecide():
    clock = _FakeClock()
    bot = _live_bot(clock)
    calls = {"n": 0}
    real = bot_mod.decide_turn_traced

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    bot_mod.decide_turn_traced = counting
    try:
        bot.feed_line("")
        assert [a.kind for a in bot.executor.executed] == [PLAY_SPELL]
        # Spell lands: leaves hand, opp hero damaged, mana spent.
        bot.state.me.hand = []
        bot.state.entities.pop(10, None)
        bot.state.opp.hero.tags["DAMAGE"] = 3
        bot.state.me.mana = 0
        bot.feed_line("")
        assert [a.kind for a in bot.executor.executed] == [PLAY_SPELL, END_TURN]
        assert calls["n"] >= 2  # re-decided after the verified step
        # Turn passes to the opponent: END_TURN verifies OK, nothing fires.
        bot.state.turn = 4
        bot.state.current_player = 2
        bot.feed_line("")
        assert bot._pending is None and not bot.halted
    finally:
        bot_mod.decide_turn_traced = real
    print("bot live OK + re-decide ok")


def test_bot_live_dead_then_halt():
    clock = _FakeClock()
    bot = _live_bot(clock)
    bot.feed_line("")
    assert [a.kind for a in bot.executor.executed] == [PLAY_SPELL]
    clock.advance(20)
    bot.tick()
    # Spell banned as dead; bot re-decides to HERO_POWER (mana unspent).
    assert bot._dead_streak == 1
    assert any("Burn" in r for r in bot._banned)
    assert [a.kind for a in bot.executor.executed] == [PLAY_SPELL, HERO_POWER]
    clock.advance(20)
    bot.tick()
    assert bot.halted and "2 consecutive dead" in bot.halt_reason
    assert any("HERO_POWER" in r for r in bot._banned)


def test_bot_live_misfire_retry_then_ban_then_halt():
    clock = _FakeClock()
    bot = _live_bot(clock, max_misfire_streak=1)
    bot.feed_line("")
    assert [a.kind for a in bot.executor.executed] == [PLAY_SPELL]
    # Misfire: source left hand, target untouched, turn advanced.
    bot.state.me.hand = []
    bot.state.entities.pop(10, None)
    bot.state.turn = 4
    bot.state.current_player = 2
    bot.feed_line("")
    assert bot.executor.cancels == 1  # drag cancelled after misfire
    assert len(bot.executor.executed) == 2  # retried once
    # Second attempt: before-state has no source -> MISFIRE again -> ban + halt.
    bot.feed_line("")
    assert bot.halted and "1 consecutive misfires" in bot.halt_reason
    assert any("Burn" in r for r in bot._banned)
    print("bot live misfire retry -> ban -> halt ok")


def test_bot_verify_auto_and_reset():
    dry = Bot(_db(), executor=DryRunExecutor(quiet=True), deck_config=CFG)
    assert dry.verify_enabled is False
    live = Bot(_db(), executor=_RecExecutor(), deck_config=CFG)
    assert live.verify_enabled is True
    live._halt("test")
    assert live.halted
    live.reset_for_new_game()
    assert live.halted  # halt is sticky
    assert live._pending is None and live._banned == set()
    live._ban(Action(END_TURN), "dead")
    assert live._banned == set()  # END_TURN is never banned
    print("bot verify auto + sticky halt ok")


# --- runner ---------------------------------------------------------------

def _runner(tmp, **cfg_kw):
    cfg = RunnerConfig(results_dir=os.path.join(tmp, "runs"), **cfg_kw)
    bot = Bot(_db(), executor=DryRunExecutor(quiet=True), deck_config=CFG)
    events = []
    runner = Runner(bot, cfg, on_event=events.append)
    return runner, events


def _game_lines(result_value):
    return [
        "TAG_CHANGE Entity=GameEntity tag=STEP value=BEGIN_MULLIGAN",
        "TAG_CHANGE Entity=GameEntity tag=STEP value=MAIN_ACTION",
        f"TAG_CHANGE Entity=[name=Player id=1 zone=PLAY zonePos=0 player=1] tag=PLAYSTATE value={result_value}",
    ]


def test_runner_single_game_win():
    with tempfile.TemporaryDirectory() as tmp:
        runner, _events = _runner(tmp, max_games=1)
        for line in _game_lines("WON"):
            runner.feed_line(line)
        assert runner.stop_reason == "max_games"
        assert [r["result"] for r in runner.results] == ["WIN"]
        assert runner.results[0]["engine_version"] == "v2"
        files = os.listdir(os.path.join(tmp, "runs"))
        assert len(files) == 1 and files[0].endswith(".jsonl")
        print("runner single game WIN ok")


def test_runner_consecutive_losses():
    with tempfile.TemporaryDirectory() as tmp:
        runner, _events = _runner(tmp, max_games=5, max_consecutive_losses=2)
        for _ in range(2):
            for line in _game_lines("LOST"):
                runner.feed_line(line)
        assert runner.stop_reason == "consecutive_losses"
        assert [r["result"] for r in runner.results] == ["LOSS", "LOSS"]
        print("runner consecutive losses ok")


def test_runner_stop_file():
    with tempfile.TemporaryDirectory() as tmp:
        stop = os.path.join(tmp, "stop")
        runner, _events = _runner(tmp, max_games=5, stop_file=stop)
        runner.feed_line("TAG_CHANGE Entity=GameEntity tag=STEP value=BEGIN_MULLIGAN")
        assert runner.stop_reason is None
        open(stop, "w").close()
        runner.feed_line("TAG_CHANGE Entity=GameEntity tag=STEP value=MAIN_ACTION")
        assert runner.stop_reason == "stop_file"
        print("runner stop file ok")


def test_runner_result_not_detectable():
    with tempfile.TemporaryDirectory() as tmp:
        runner, _events = _runner(tmp, max_games=3)
        # Game ends via FINAL_GAMEOVER with no PLAYSTATE: no result.
        runner.feed_line("TAG_CHANGE Entity=GameEntity tag=STEP value=BEGIN_MULLIGAN")
        runner.feed_line("TAG_CHANGE Entity=GameEntity tag=STEP value=FINAL_GAMEOVER")
        assert runner.stop_reason == "result_not_detectable"
        assert runner.results[0]["result"] == "UNKNOWN"
        print("runner result_not_detectable ok")


def test_runner_silence():
    clock = _FakeClock()
    with tempfile.TemporaryDirectory() as tmp:
        cfg = RunnerConfig(results_dir=os.path.join(tmp, "runs"),
                           silence_timeout=60)
        bot = Bot(_db(), executor=DryRunExecutor(quiet=True), deck_config=CFG)
        runner = Runner(bot, cfg, clock=clock)
        runner.feed_line("TAG_CHANGE Entity=GameEntity tag=TURN value=1")
        assert runner.stop_reason is None
        clock.advance(61)
        runner.tick()
        assert runner.stop_reason == "silence"
        print("runner silence ok")


def test_runner_action_cap():
    with tempfile.TemporaryDirectory() as tmp:
        # 2 actions per game max; every MAIN_ACTION turn executes >= 1.
        runner, _events = _runner(tmp, max_games=1, max_actions_per_game=2)
        for turn in (1, 2, 3):
            runner.feed_line(f"TAG_CHANGE Entity=GameEntity tag=TURN value={turn}")
            runner.feed_line("TAG_CHANGE Entity=GameEntity tag=CURRENT_PLAYER value=1")
            runner.feed_line("TAG_CHANGE Entity=GameEntity tag=STEP value=MAIN_ACTION")
        assert runner.stop_reason == "action_cap"
        assert runner.results[0]["result"] == "UNKNOWN"
        print("runner action cap ok")


if __name__ == "__main__":
    test_verify_play_minion_ok()
    test_verify_play_minion_pending_and_misfire()
    test_verify_attack_ok_damage_and_shield()
    test_verify_attack_misfire_and_pending()
    test_verify_play_spell()
    test_verify_hero_power_and_end_turn()
    test_verify_mulligan()
    test_action_meta_and_drag()
    test_parser_mulligan_receipt()
    test_parser_playstate_and_gameover()
    test_bot_live_ok_then_redecide()
    test_bot_live_dead_then_halt()
    test_bot_live_misfire_retry_then_ban_then_halt()
    test_bot_verify_auto_and_reset()
    test_runner_single_game_win()
    test_runner_consecutive_losses()
    test_runner_stop_file()
    test_runner_result_not_detectable()
    test_runner_silence()
    test_runner_action_cap()
    print("PHASE C TESTS PASSED")

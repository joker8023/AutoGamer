"""Post-action verification: did the action actually land?

``verify_action(before, action, after)`` compares the state before an
action with the state after it and returns one of:

- ``OK`` — the action's expected post-condition holds.
- ``MISFIRE`` — the post-condition does not hold AND the state shows
  the action can no longer land (e.g. the turn advanced, or the source
  vanished without effect). The caller should not retry blindly.
- ``PENDING`` — the post-condition does not hold yet, but nothing
  contradicts it (animation may still be in flight). The caller keeps
  waiting until the verify timeout, then treats it as dead.

Verification is a pure function of (before, action, after). Timing /
timeout / retry policy lives in the caller (Bot), not here.
"""
from __future__ import annotations

from .actions import ATTACK, END_TURN, HERO_POWER, MULLIGAN, PLAY_MINION, PLAY_SPELL, Action
from .state import Entity, GameState

OK = "OK"
MISFIRE = "MISFIRE"
PENDING = "PENDING"

Verdict = str


def _entity_in(state: GameState, entity_id: int) -> Entity | None:
    return state.entities.get(entity_id)


def _in_hand(state: GameState, entity_id: int) -> bool:
    return any(e.id == entity_id for e in state.me.hand)


def _on_board(state: GameState, entity_id: int) -> bool:
    return any(e.id == entity_id for p in state.players.values() for e in p.board)


def _target_changed(before: GameState, after: GameState, target_id: int) -> bool:
    """Target took damage, lost Divine Shield, or left the board."""
    if target_id == 0:
        return False
    b = _entity_in(before, target_id)
    a = _entity_in(after, target_id)
    if b is None:
        return False
    if a is None:
        return True  # target is gone
    if a.health < b.health:
        return True
    if b.tag("DIVINE_SHIELD") and not a.tag("DIVINE_SHIELD"):
        return True
    return False


def _turn_advanced(before: GameState, after: GameState) -> bool:
    return after.turn != before.turn or after.current_player != before.current_player


def _verify_play_minion(before: GameState, action: Action, after: GameState) -> Verdict:
    if not _in_hand(before, action.source_id):
        return MISFIRE  # the action was bogus from the start
    if not _in_hand(after, action.source_id) and _on_board(after, action.source_id):
        return OK
    if _turn_advanced(before, after):
        return MISFIRE
    return PENDING


def _verify_attack(before: GameState, action: Action, after: GameState) -> Verdict:
    attacker_before = _entity_in(before, action.source_id)
    attacker_after = _entity_in(after, action.source_id)
    if attacker_before is None:
        return MISFIRE
    if _target_changed(before, after, action.target_id):
        return OK
    attacker_used = (
        attacker_after is None  # attacker died
        or attacker_after.tag("EXHAUSTED") == 1
    )
    if attacker_used:
        return MISFIRE  # attack resolved without any effect on the target
    if _turn_advanced(before, after):
        return MISFIRE
    return PENDING


def _verify_play_spell(before: GameState, action: Action, after: GameState) -> Verdict:
    if not _in_hand(before, action.source_id):
        return MISFIRE
    left_hand = not _in_hand(after, action.source_id)
    if action.target_id:
        if left_hand and _target_changed(before, after, action.target_id):
            return OK
        if left_hand and _turn_advanced(before, after):
            return MISFIRE  # spell left hand but never affected its target
    elif left_hand:
        return OK  # non-targeted spell left the hand: effect assumed applied
    if _turn_advanced(before, after):
        return MISFIRE
    return PENDING


def _verify_hero_power(before: GameState, action: Action, after: GameState) -> Verdict:
    hp_after = after.me.hero_power
    if hp_after is not None and hp_after.tag("EXHAUSTED") == 1:
        return OK
    if _turn_advanced(before, after):
        return MISFIRE
    return PENDING


def _verify_end_turn(before: GameState, action: Action, after: GameState) -> Verdict:
    if _turn_advanced(before, after):
        return OK
    if after.game_over:
        return MISFIRE
    return PENDING


def _verify_mulligan(before: GameState, action: Action, after: GameState) -> Verdict:
    """The kept cards must still be in hand once the mulligan phase ends.
    Expected kept names come from action.meta['kept_names']."""
    kept_names = action.meta.get("kept_names")
    if not kept_names:
        return PENDING  # cannot verify without an expectation
    if after.step == "BEGIN_MULLIGAN":
        return PENDING  # phase not over yet
    hand_names = {e.name for e in after.me.hand}
    if all(name in hand_names for name in kept_names):
        return OK
    return MISFIRE


def verify_action(before: GameState, action: Action, after: GameState) -> Verdict:
    """OK / MISFIRE / PENDING for one executed action."""
    if action.kind == PLAY_MINION:
        return _verify_play_minion(before, action, after)
    if action.kind == ATTACK:
        return _verify_attack(before, action, after)
    if action.kind == PLAY_SPELL:
        return _verify_play_spell(before, action, after)
    if action.kind == HERO_POWER:
        return _verify_hero_power(before, action, after)
    if action.kind == END_TURN:
        return _verify_end_turn(before, action, after)
    if action.kind == MULLIGAN:
        return _verify_mulligan(before, action, after)
    return PENDING

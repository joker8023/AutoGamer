"""Decision engine for 号角 (Wild Mech Paladin).

Three pieces:
  1. choose_mulligan — starting hand, from the strategy table.
  2. find_lethal — always checked first, every turn.
  3. decide_turn — beam search over full action sequences, scored by a
     heuristic evaluation function. Unknown-effect spells are never played
     by the search (the engine does not gamble on effects it cannot model).

The simulator inside the search is deliberately approximate: it models
minions, attacks, hero power and known spell effects. It does NOT model
random outcomes, Discover choices, or complex battlecries — those stay
"out of search" and are handled by the conservative fallbacks.
"""
from __future__ import annotations

from .actions import ATTACK, END_TURN, HERO_POWER, PLAY_MINION, PLAY_SPELL, Action
from .cards import CardDB
from .deck_data import MULLIGAN, STRATEGY
from .state import Entity, GameState

# Evaluation weights — tune by self-play (see README).
WEIGHTS = {
    "my_hero": 3.0,
    "opp_hero": 3.0,
    "attack": 1.0,
    "health": 0.9,
    "taunt": 1.5,
    "divine_shield": 1.2,
    "hand_diff": 1.5,
    "board_count": 0.5,
    "unspent_mana": -0.3,
}

MAX_BOARD = 7


# --- Mulligan -----------------------------------------------------------
def choose_mulligan(hand_names: list[str], has_coin: bool) -> set[int]:
    """Return indices of hand cards to REPLACE."""
    keep: set[int] = set()
    has_early = any(
        name in MULLIGAN["keep_always"] or (has_coin and name in MULLIGAN["keep_if_coin"])
        for name in hand_names
    )
    for i, name in enumerate(hand_names):
        if name in MULLIGAN["keep_always"]:
            keep.add(i)
        elif has_coin and name in MULLIGAN["keep_if_coin"]:
            keep.add(i)
        elif name in MULLIGAN["keep_curve_filler"] and has_early:
            keep.add(i)
    return {i for i in range(len(hand_names)) if i not in keep}


# --- Evaluation ---------------------------------------------------------
def minion_value(ent: Entity, weights: dict | None = None) -> float:
    w = weights or WEIGHTS
    value = ent.attack * w["attack"] + ent.health * w["health"]
    if ent.tag("TAUNT"):
        value += w["taunt"]
    if ent.tag("DIVINE_SHIELD"):
        value += w["divine_shield"]
    return value


def evaluate(state: GameState, weights: dict | None = None) -> float:
    w = weights or WEIGHTS
    me, opp = state.me, state.opp
    score = w["my_hero"] * me.hero_health - w["opp_hero"] * opp.hero_health
    score += sum(minion_value(m, w) for m in me.board)
    score -= sum(minion_value(m, w) for m in opp.board)
    score += w["hand_diff"] * (len(me.hand) - len(opp.hand))
    score += w["board_count"] * (len(me.board) - len(opp.board))
    score += w["unspent_mana"] * me.mana
    return score


# --- Lethal -------------------------------------------------------------
def _available_attackers(state: GameState) -> list[Entity]:
    return [
        m
        for m in state.me.board
        if m.can_attack and m.id not in state.sim_exhausted
    ]


def find_lethal(state: GameState, db: CardDB) -> list[Action] | None:
    """Return a lethal action sequence, or None.

    Approximation: taunt minions must be cleared first; remaining attacker
    damage + known burn must cover the enemy hero. Overkill waste on taunt
    is ignored (conservative: we may miss some lethals, never fake one).
    """
    opp = state.opp
    if opp.hero is None:
        return None
    attackers = _available_attackers(state)
    total_attack = sum(m.attack for m in attackers)
    taunts = [m for m in opp.board if m.tag("TAUNT")]
    taunt_health = sum(m.health for m in taunts)
    if taunts and total_attack <= taunt_health:
        return None  # cannot get through taunt this turn
    face_damage = total_attack - (taunt_health if taunts else 0)
    # Known burn from hand (spells with modeled direct damage).
    burn = 0
    burn_actions: list[Action] = []
    mana_left = state.me.mana
    for ent in state.me.hand:
        card = db.get_by_name(ent.name)
        if card is None or not card.is_spell:
            continue
        if card.effect_damage and card.cost <= mana_left:
            burn += card.effect_damage
            mana_left -= card.cost
            target = opp.hero.id if opp.hero else 0
            burn_actions.append(
                Action(PLAY_SPELL, source_id=ent.id, target_id=target,
                       card_name=ent.name)
            )
    # Hero power: Paladin summons a 1/1, no face damage — skip for lethal.
    if face_damage + burn < opp.hero_health:
        return None
    actions: list[Action] = []
    # Clear taunt first (cheapest sufficient attackers), then face.
    remaining_taunt = taunt_health
    for attacker in sorted(attackers, key=lambda m: m.attack):
        if remaining_taunt > 0:
            target = taunts[0].id if taunts else 0
            actions.append(Action(ATTACK, source_id=attacker.id, target_id=target))
            remaining_taunt -= attacker.attack
        else:
            actions.append(
                Action(ATTACK, source_id=attacker.id, target_id=opp.hero.id)
            )
    actions.extend(burn_actions)
    return actions


# --- Action enumeration + simulation ------------------------------------
def _card_for(ent: Entity, db: CardDB):
    return db.get_by_name(ent.name)


def legal_actions(state: GameState, db: CardDB) -> list[Action]:
    me = state.me
    actions: list[Action] = []
    behind = _is_behind(state)
    for ent in list(me.hand):
        card = _card_for(ent, db)
        if card is None:
            continue
        if card.cost > me.mana:
            continue
        if card.is_minion and len(me.board) >= MAX_BOARD:
            continue
        if card.is_minion:
            # Zilliax: save when behind (flex removal/heal), never tempo it.
            if ent.name == "奇利亚斯豪华版3000型" and behind and STRATEGY.get(
                "zilliax_save_when_behind"
            ):
                continue
            if ent.name in STRATEGY.get("hold_for_value", set()) and not me.board:
                continue
            actions.append(
                Action(PLAY_MINION, source_id=ent.id, card_name=ent.name,
                       position=len(me.board))
            )
        elif card.is_spell:
            if card.effect_unknown:
                continue  # never gamble on an unmodeled effect
            if card.effect_damage:
                targets = [m for m in state.opp.board] + (
                    [state.opp.hero] if state.opp.hero else []
                )
                for t in targets:
                    actions.append(
                        Action(PLAY_SPELL, source_id=ent.id, target_id=t.id,
                               card_name=ent.name)
                    )
            else:
                actions.append(
                    Action(PLAY_SPELL, source_id=ent.id, card_name=ent.name)
                )
    # Attacks (respect taunt).
    attackers = _available_attackers(state)
    taunts = [m for m in state.opp.board if m.tag("TAUNT")]
    targets = taunts if taunts else (
        list(state.opp.board) + ([state.opp.hero] if state.opp.hero else [])
    )
    for attacker in attackers:
        for target in targets:
            actions.append(
                Action(ATTACK, source_id=attacker.id, target_id=target.id)
            )
    # Hero power (Paladin: summon 1/1, 2 mana, once per turn).
    if (
        me.hero_power is not None
        and me.mana >= 2
        and me.hero_power.id not in state.sim_exhausted
        and len(me.board) < MAX_BOARD
    ):
        actions.append(Action(HERO_POWER, source_id=me.hero_power.id))
    actions.append(Action(END_TURN))
    return actions


def _is_behind(state: GameState) -> bool:
    my_board = sum(minion_value(m) for m in state.me.board)
    opp_board = sum(minion_value(m) for m in state.opp.board)
    return opp_board > my_board + 2 or state.me.hero_health <= 12


def apply_action(state: GameState, action: Action, db: CardDB) -> GameState:
    """Approximate simulator: returns a NEW state after the action."""
    ns = state.clone()
    me = ns.me
    if action.kind == END_TURN:
        return ns
    if action.kind == PLAY_MINION:
        ent = next((e for e in me.hand if e.id == action.source_id), None)
        if ent is None:
            return ns
        me.hand.remove(ent)
        me.mana = max(0, me.mana - ent.cost)
        board_ent = Entity(
            id=ent.id, name=ent.name, card_id=ent.card_id,
            controller=ns.my_id, zone="PLAY",
            tags=dict(ent.tags),
        )
        card = _card_for(ent, db)
        if card is not None and card.attack is not None:
            board_ent.tags["ATK"] = card.attack
        if card is not None and card.health is not None:
            board_ent.tags["HEALTH"] = card.health
        board_ent.tags["JUST_PLAYED"] = 1
        me.board.insert(min(action.position, len(me.board)), board_ent)
        ns.entities[ent.id] = board_ent
        return ns
    if action.kind == PLAY_SPELL:
        ent = next((e for e in me.hand if e.id == action.source_id), None)
        if ent is None:
            return ns
        me.hand.remove(ent)
        me.mana = max(0, me.mana - ent.cost)
        card = _card_for(ent, db)
        if card is not None and card.effect_damage and action.target_id:
            target = ns.entity(action.target_id)
            if target is not None:
                _deal_damage(ns, target, card.effect_damage)
        return ns
    if action.kind == ATTACK:
        attacker = ns.entity(action.source_id)
        target = ns.entity(action.target_id)
        if attacker is None or target is None:
            return ns
        ns.sim_exhausted.add(attacker.id)
        attacker.tags["EXHAUSTED"] = 1
        if target.is_minion and target.tag("DIVINE_SHIELD"):
            target.tags["DIVINE_SHIELD"] = 0
        else:
            _deal_damage(ns, target, attacker.attack)
        if target.is_minion and target.attack > 0:
            if attacker.tag("DIVINE_SHIELD"):
                attacker.tags["DIVINE_SHIELD"] = 0
            else:
                _deal_damage(ns, attacker, target.attack)
        _cleanup_dead(ns)
        return ns
    if action.kind == HERO_POWER:
        me.mana = max(0, me.mana - 2)
        ns.sim_exhausted.add(action.source_id)
        recruit = Entity(
            id=-1000 - len(me.board), name="白银之手新兵",
            controller=ns.my_id, zone="PLAY",
            tags={"CARDTYPE": 4, "ATK": 1, "HEALTH": 1, "JUST_PLAYED": 1},
        )
        me.board.append(recruit)
        return ns
    return ns


def _deal_damage(state: GameState, target: Entity, amount: int) -> None:
    target.tags["DAMAGE"] = target.tag("DAMAGE") + amount


def _cleanup_dead(state: GameState) -> None:
    for player in state.players.values():
        player.board = [m for m in player.board if m.health > 0]
    for pid, player in state.players.items():
        if player.hero is not None and player.hero.health <= 0:
            pass  # hero death ends the game; caller handles it


def decide_turn(state: GameState, db: CardDB, beam_width: int = 8,
                max_depth: int = 14) -> list[Action]:
    """Beam search over full action sequences. Returns actions incl. END_TURN."""
    lethal = find_lethal(state, db)
    if lethal:
        return lethal + [Action(END_TURN)]
    best: list[Action] = [Action(END_TURN)]
    best_score = float("-inf")
    beams: list[tuple[list[Action], GameState]] = [([], state)]
    for _ in range(max_depth):
        candidates: list[tuple[list[Action], GameState]] = []
        for actions, s in beams:
            for action in legal_actions(s, db):
                ns = apply_action(s, action, db)
                if action.kind == END_TURN:
                    score = evaluate(ns)
                    if score > best_score:
                        best_score = score
                        best = actions + [action]
                else:
                    candidates.append((actions + [action], ns))
        if not candidates:
            break
        candidates.sort(key=lambda c: evaluate(c[1]), reverse=True)
        beams = candidates[:beam_width]
    return best

"""Decision engine for 号角 (Wild Mech Paladin).

Three pieces:
  1. choose_mulligan — starting hand, from the deck's strategy data.
  2. find_lethal — always checked first, every turn. A lethal only
     counts if EVERY step simulates exactly (see exactness below).
  3. decide_turn — beam search over full action sequences, scored by a
     heuristic evaluation function. Unknown-effect spells are never played
     by the search (the engine does not gamble on effects it cannot model).

Exactness: ``apply_action`` returns ``(new_state, exact, note)``. A step is
exact only when the simulator can pin down its outcome. Anything the
simulator cannot pin down — random outcomes, Discover choices, card
draws, unmodeled battlecries / deathrattles / spell effects, unknown
card stats — is inexact. Beam search scores an inexact step as a
terminal candidate and does NOT expand past it: the plan stops there,
and the bot re-decides after executing it (see bot.py's per-action
loop). An inexact-terminated plan still ends with END_TURN as a
conservative "stop here" marker; the re-decision loop supersedes it
when the state changes.

Deck configuration comes from ``DeckConfig`` (data files under
``decks/<deck-id>/``), not from module-level tables.
"""
from __future__ import annotations

from .actions import ATTACK, END_TURN, HERO_POWER, PLAY_MINION, PLAY_SPELL, Action
from .cards import CardDB
from .deck_config import DEFAULT_DECK_ID, DeckConfig, load_deck
from .state import Entity, GameState

# Evaluation weights — default when a DeckConfig carries no "weights".
# The Horn deck's data file carries these same values.
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
MAX_HAND = 10

_DEFAULT_CONFIG: DeckConfig | None = None


def _config(deck_config: DeckConfig | None) -> DeckConfig:
    """Resolve the deck config, defaulting to the Horn deck (cached)."""
    global _DEFAULT_CONFIG
    if deck_config is not None:
        return deck_config
    if _DEFAULT_CONFIG is None:
        _DEFAULT_CONFIG = load_deck(DEFAULT_DECK_ID)
    return _DEFAULT_CONFIG


# --- Mulligan -----------------------------------------------------------
def choose_mulligan(hand_names: list[str], has_coin: bool,
                    deck_config: DeckConfig | None = None) -> set[int]:
    """Return indices of hand cards to REPLACE."""
    cfg = _config(deck_config)
    keep: set[int] = set()
    has_early = any(
        name in cfg.keep_always or (has_coin and name in cfg.keep_if_coin)
        for name in hand_names
    )
    for i, name in enumerate(hand_names):
        if name in cfg.keep_always:
            keep.add(i)
        elif has_coin and name in cfg.keep_if_coin:
            keep.add(i)
        elif name in cfg.keep_curve_filler and has_early:
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


def find_lethal(state: GameState, db: CardDB,
                deck_config: DeckConfig | None = None) -> list[Action] | None:
    """Return a lethal action sequence, or None.

    Approximation: taunt minions must be cleared first; remaining attacker
    damage + known burn must cover the enemy hero. Overkill waste on taunt
    is ignored (conservative: we may miss some lethals, never fake one).

    Exactness: the candidate sequence is simulated step by step through
    ``apply_action``. If ANY step is inexact, or the simulated end state
    does not actually kill the enemy hero, the lethal does not count.
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
    # Verify: simulate every step; all must be exact, and the enemy hero
    # must actually die in the simulated end state.
    sim = state
    for action in actions:
        sim, exact, _note = apply_action(sim, action, db)
        if not exact:
            return None
    opp_hero = sim.opp.hero
    if opp_hero is None or opp_hero.health > 0:
        return None
    return actions


# --- Action enumeration + simulation ------------------------------------
def _card_for(ent: Entity, db: CardDB):
    return db.get_by_name(ent.name)


def legal_actions(state: GameState, db: CardDB,
                  deck_config: DeckConfig | None = None) -> list[Action]:
    """Pure enumeration of legal actions. No strategy filtering here:
    Zilliax-hold / hold-for-value live in policy.py as named prefers,
    and decide_turn applies veto/prefer to the root menu. (deck_config
    is accepted for call-site compatibility and currently unused.)"""
    me = state.me
    actions: list[Action] = []
    for ent in list(me.hand):
        card = _card_for(ent, db)
        if card is None:
            continue
        if card.cost > me.mana:
            continue
        if card.is_minion and len(me.board) >= MAX_BOARD:
            continue
        if card.is_minion:
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


def _is_behind(state: GameState, weights: dict | None = None) -> bool:
    my_board = sum(minion_value(m, weights) for m in state.me.board)
    opp_board = sum(minion_value(m, weights) for m in state.opp.board)
    return opp_board > my_board + 2 or state.me.hero_health <= 12


def _died_with_unmodeled_deathrattle(before: GameState, after: GameState,
                                    db: CardDB) -> str | None:
    """If any minion that died in this step has an unmodeled Deathrattle
    (or an unknown card), the outcome cannot be pinned down."""
    before_ids = {m.id for p in before.players.values() for m in p.board}
    after_ids = {m.id for p in after.players.values() for m in p.board}
    for died_id in before_ids - after_ids:
        ent = before.entity(died_id)
        if ent is None:
            return "unknown minion died"
        card = db.get_by_name(ent.name)
        if card is None:
            return f"unknown card died: {ent.name}"
        if "DEATHRATTLE" in card.mechanics:
            return f"unmodeled deathrattle: {ent.name}"
    return None


def apply_action(state: GameState, action: Action, db: CardDB
                 ) -> tuple[GameState, bool, str]:
    """Approximate simulator: returns ``(new_state, exact, note)``.

    ``exact`` is True only when the simulator can pin down the step's
    outcome. Inexact steps (randomness, Discover, card draws, unmodeled
    battlecries / deathrattles / spell effects, unknown card stats) must
    not be expanded past by the search.
    """
    ns = state.clone()
    me = ns.me
    if action.kind == END_TURN:
        return ns, True, "end turn"
    if action.kind == PLAY_MINION:
        ent = next((e for e in me.hand if e.id == action.source_id), None)
        if ent is None:
            return ns, False, "source not in hand"
        card = _card_for(ent, db)
        if card is None:
            return ns, False, f"unknown card: {ent.name}"
        # Stats: the live entity tags (from Power.log) are the source of
        # truth; the card DB is the fallback. Inexact only if neither
        # knows the stats.
        atk = card.attack
        if atk is None:
            atk = ent.attack if "ATK" in ent.tags else None
        hp = card.health
        if hp is None:
            hp = ent.health if "HEALTH" in ent.tags else None
        if atk is None or hp is None:
            return ns, False, f"unknown stats: {ent.name}"
        if "BATTLECRY" in card.mechanics:
            return ns, False, f"unmodeled battlecry: {ent.name}"
        if "DISCOVER" in card.mechanics:
            return ns, False, f"discover: {ent.name}"
        me.hand.remove(ent)
        me.mana = max(0, me.mana - ent.cost)
        board_ent = Entity(
            id=ent.id, name=ent.name, card_id=ent.card_id,
            controller=ns.my_id, zone="PLAY",
            tags=dict(ent.tags),
        )
        board_ent.tags["ATK"] = atk
        board_ent.tags["HEALTH"] = hp
        board_ent.tags["JUST_PLAYED"] = 1
        me.board.insert(min(action.position, len(me.board)), board_ent)
        ns.entities[ent.id] = board_ent
        return ns, True, f"play {ent.name}"
    if action.kind == PLAY_SPELL:
        ent = next((e for e in me.hand if e.id == action.source_id), None)
        if ent is None:
            return ns, False, "source not in hand"
        card = _card_for(ent, db)
        if card is None:
            return ns, False, f"unknown card: {ent.name}"
        if card.effect_unknown:
            return ns, False, f"unmodeled spell effect: {ent.name}"
        if card.effect_draw:
            return ns, False, f"draws {card.effect_draw} card(s): {ent.name}"
        if card.effect_summon:
            return ns, False, f"summons: {ent.name}"
        if not (card.effect_damage or card.effect_heal):
            return ns, False, f"unmodeled spell effect: {ent.name}"
        me.hand.remove(ent)
        me.mana = max(0, me.mana - ent.cost)
        if card.effect_damage and action.target_id:
            target = ns.entity(action.target_id)
            if target is not None:
                _deal_damage(ns, target, card.effect_damage)
        # Healing is deterministic (target + amount known): exact.
        return ns, True, f"cast {ent.name}"
    if action.kind == ATTACK:
        attacker = ns.entity(action.source_id)
        target = ns.entity(action.target_id)
        if attacker is None or target is None:
            return ns, False, "attacker/target not found"
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
        bad = _died_with_unmodeled_deathrattle(state, ns, db)
        if bad is not None:
            return ns, False, bad
        return ns, True, f"attack {attacker.id} -> {target.id}"
    if action.kind == HERO_POWER:
        hp = me.hero_power
        if hp is None or hp.name not in ("Reinforce", "强化"):
            return ns, False, "unmodeled hero power"
        me.mana = max(0, me.mana - 2)
        ns.sim_exhausted.add(action.source_id)
        recruit = Entity(
            id=-1000 - len(me.board), name="白银之手新兵",
            controller=ns.my_id, zone="PLAY",
            tags={"CARDTYPE": 4, "ATK": 1, "HEALTH": 1, "JUST_PLAYED": 1},
        )
        me.board.append(recruit)
        return ns, True, "hero power: recruit"
    return ns, False, f"unknown action: {action.kind}"


def _deal_damage(state: GameState, target: Entity, amount: int) -> None:
    target.tags["DAMAGE"] = target.tag("DAMAGE") + amount


def _cleanup_dead(state: GameState) -> None:
    for player in state.players.values():
        player.board = [m for m in player.board if m.health > 0]
    for pid, player in state.players.items():
        if player.hero is not None and player.hero.health <= 0:
            pass  # hero death ends the game; caller handles it


def decide_turn(state: GameState, db: CardDB, beam_width: int = 8,
                max_depth: int = 14,
                deck_config: DeckConfig | None = None,
                policy: bool = True,
                exclude: frozenset[str] | None = None) -> list[Action]:
    """Beam search over action sequences. Returns actions incl. END_TURN.

    Paths stop at their first inexact step: that step's resulting state
    is scored as a terminal candidate and the path is not expanded
    further. An inexact-terminated winning plan ends with END_TURN as a
    conservative "stop here" marker (the bot re-decides after the
    inexact step executes).

    With policy=True (default), the root menu passes through policy.py's
    veto/prefer layers, and deeper nodes are veto-filtered. With
    policy=False, the raw legal menu is searched (the frozen v1
    baseline behavior). See decide_turn_traced for the trace.

    ``exclude`` is a set of action reprs banned for this turn (dead /
    misfired actions). END_TURN is never excluded.
    """
    plan, _trace = decide_turn_traced(state, db, beam_width, max_depth,
                                      deck_config, policy, exclude)
    return plan


def decide_turn_traced(state: GameState, db: CardDB, beam_width: int = 8,
                       max_depth: int = 14,
                       deck_config: DeckConfig | None = None,
                       policy: bool = True,
                       exclude: frozenset[str] | None = None
                       ) -> tuple[list[Action], dict]:
    """Like decide_turn, plus the policy trace:
    {vetoed: [...], preferences: [...], released: [...], policy, lethal}.
    The trace is emitted via a sidecar event by the caller."""
    from .policy import PREFER_RELEASE_MARGIN, prefer_actions, veto_actions

    cfg = _config(deck_config)
    lethal = find_lethal(state, db, cfg)
    if lethal:
        return lethal + [Action(END_TURN)], {
            "vetoed": [], "preferences": [], "released": [],
            "policy": policy, "lethal": True,
        }
    trace: dict = {"vetoed": [], "preferences": [], "released": [],
                   "policy": policy, "lethal": False}
    if not policy:
        root_menu = legal_actions(state, db, cfg)
        plan, _score = _beam_search(state, db, cfg, root_menu,
                                    beam_width, max_depth, veto_deep=False,
                                    exclude=exclude)
        return plan, trace
    root_all = legal_actions(state, db, cfg)
    vetoed_menu, vetoed = veto_actions(state, db, root_all, cfg)
    trace["vetoed"] = vetoed
    prefer_menu, applied = prefer_actions(state, db, vetoed_menu, cfg)
    trace["preferences"] = applied
    plan, score = _beam_search(state, db, cfg, prefer_menu,
                               beam_width, max_depth, veto_deep=True,
                               exclude=exclude)
    if applied:
        # Release check: a second search on the veto-only menu. If it
        # wins by more than the margin, the prefers are void this round.
        plan2, score2 = _beam_search(state, db, cfg, vetoed_menu,
                                      beam_width, max_depth, veto_deep=True,
                                      exclude=exclude)
        if score2 > score + PREFER_RELEASE_MARGIN:
            trace["released"] = [a["rule"] for a in applied]
            return plan2, trace
    return plan, trace


def _beam_search(state: GameState, db: CardDB, cfg: DeckConfig,
                 root_menu: list[Action], beam_width: int, max_depth: int,
                 veto_deep: bool,
                 exclude: frozenset[str] | None = None
                 ) -> tuple[list[Action], float]:
    """Beam search from a fixed root menu. Deeper nodes expand plain
    legal actions (veto-filtered when veto_deep, so safety holds
    everywhere); prefer applies at the root only. Excluded action reprs
    (banned this turn) are filtered at every node; END_TURN is never
    excluded. Returns (plan, score) with the plan ending in END_TURN."""
    from .policy import veto_actions

    excluded = exclude or frozenset()

    def _allowed(menu: list[Action]) -> list[Action]:
        return [a for a in menu
                if a.kind == END_TURN or repr(a) not in excluded]

    best: list[Action] = [Action(END_TURN)]
    best_score = float("-inf")
    beams: list[tuple[list[Action], GameState]] = [([], state)]
    for depth in range(max_depth):
        candidates: list[tuple[list[Action], GameState]] = []
        for actions, s in beams:
            if depth == 0:
                menu = _allowed(root_menu)
            elif veto_deep:
                menu, _ = veto_actions(s, db, legal_actions(s, db, cfg), cfg)
                menu = _allowed(menu)
            else:
                menu = _allowed(legal_actions(s, db, cfg))
            for action in menu:
                ns, exact, _note = apply_action(s, action, db)
                if action.kind == END_TURN or not exact:
                    score = evaluate(ns, cfg.weights)
                    plan = actions + [action]
                    if not exact:
                        plan = plan + [Action(END_TURN)]
                    if score > best_score:
                        best_score = score
                        best = plan
                else:
                    candidates.append((actions + [action], ns))
        if not candidates:
            break
        candidates.sort(key=lambda c: evaluate(c[1], cfg.weights), reverse=True)
        beams = candidates[:beam_width]
    return best, best_score

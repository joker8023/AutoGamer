"""号角 — Wild Mech Paladin: deck definition + strategy tables.

Source of truth for card names/costs/counts: the user's deck export.
Card EFFECTS are deliberately NOT hardcoded here; they come from the
card DB (HearthstoneJSON) + overrides. The strategy tables below only
use cost / name / role, which the export gives us exactly.
"""
from __future__ import annotations

DECK_CODE = (
    "AAEBAaToAgaHrgOftwP5pATHpAb0qged3QcMpfUCkrUE1L0E2tMEhKUF2dAFuf4Fn+EG4vEGv/kGupYHmvwH"
    "AAEE1/4Cnd0H87MGx6QG9rMGx6QGku8Gx6QGAAA="
)
HERO_DBFID = 46116  # Uther Lightbringer (Paladin), from the deck code
FORMAT_WILD = 1

# (name, cost, count, role) — roles drive mulligan + play priority.
# Roles: opener / curve / synergy / removal / draw / finisher / flex
DECK_CARDS: list[tuple[str, int, int, str]] = [
    ("无人机拆解器", 1, 2, "opener"),
    ("频率振荡机", 1, 2, "opener"),
    ("安保自动机", 2, 2, "curve"),
    ("忙碌机器人", 2, 2, "curve"),
    ("紫色珍鳃鱼人", 2, 2, "curve"),
    ("超级电容器", 2, 2, "curve"),
    ("雷达探测", 2, 2, "draw"),
    ("大铡蟹", 3, 1, "curve"),
    ("石炉守备官", 3, 1, "curve"),
    ("空中飞爪", 3, 1, "curve"),
    ("青铜门卫", 3, 2, "curve"),
    ("十字军光环", 4, 2, "synergy"),
    ("恶火", 4, 2, "removal"),
    ("泡泡机器人", 4, 2, "curve"),
    ("金翼巨龙", 4, 2, "curve"),
    ("指挥官碧阿崔克丝", 5, 1, "synergy"),
    ("通电机器人", 2, 1, "synergy"),  # sideboard of 指挥官碧阿崔克丝
    ("馆长", 5, 1, "draw"),
    ("奇利亚斯豪华版3000型", 0, 1, "flex"),  # cost varies with modules
]

# Zilliax sideboard (from the deck code): base + modules.
ZILLIAX_MODULES: list[tuple[str, int]] = [
    ("奇利亚斯豪华版3000型", 0),
    ("输能模块", 3),
    ("计数模块", 4),
]

# --- Mulligan table -----------------------------------------------------
# KEEP_ALWAYS: keep in every opener. KEEP_IF_COIN: keep when going second
# (extra mana lets us curve into them). Everything else is replaced.
MULLIGAN = {
    "keep_always": {"无人机拆解器", "频率振荡机", "安保自动机", "忙碌机器人"},
    "keep_if_coin": {"紫色珍鳃鱼人", "超级电容器", "雷达探测"},
    # 3+ cost is thrown unless we already have a 1-2 curve (handled in code).
    "keep_curve_filler": {"大铡蟹", "石炉守备官", "空中飞爪", "青铜门卫"},
}

# --- Play strategy ------------------------------------------------------
STRATEGY = {
    # Aggro-midrange mech: tempo > value. Face damage is worth more than
    # the default once we have board control; see decision.py weights.
    "archetype": "mech-tempo",
    "prefer_face_when_board_control": True,
    # Never play these into an empty board if a curve minion is available.
    "hold_for_value": {"十字军光环", "馆长"},
    # Zilliax is the flex slot: save it when behind (taunt/heal) or when
    # lethal is one rush minion away; never dump it on curve for tempo.
    "zilliax_save_when_behind": True,
}

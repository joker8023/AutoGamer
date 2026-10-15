"""Versioned decision engine registry.

Every decision run is tagged with an engine version so offline A/B
evaluation (see ``eval/``) can compare versions and regressions are
traceable. ``get_engine`` raises on an unknown version — it never
silently falls back to another engine.

Registry entry: (decide_fn, weights, policy_set). ``decide_fn`` has the
signature ``decide_fn(state, db, beam_width=..., max_depth=...,
deck_config=...)`` and returns a list of Actions including END_TURN.
``policy_set`` names the rule set the engine ran under; "baseline"
means the pre-policy.py rules baked into ``legal_actions`` (v1,
frozen), "veto-prefer-v1" means policy.py's veto/prefer layers (v2).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

from .decision import WEIGHTS, decide_turn

ENGINE_VERSION = "v2"


@dataclass(frozen=True)
class EngineSpec:
    name: str
    decide_fn: Callable
    weights: dict
    policy_set: str
    policy: bool  # whether decide_fn runs the policy.py veto/prefer layers


_REGISTRY: dict[str, EngineSpec] = {
    "v1": EngineSpec(
        name="v1",
        decide_fn=partial(decide_turn, policy=False),
        weights=WEIGHTS,
        policy_set="baseline",
        policy=False,
    ),
    "v2": EngineSpec(
        name="v2",
        decide_fn=decide_turn,
        weights=WEIGHTS,
        policy_set="veto-prefer-v1",
        policy=True,
    ),
}


def list_versions() -> list[str]:
    return sorted(_REGISTRY)


def get_engine(version: str) -> EngineSpec:
    try:
        return _REGISTRY[version]
    except KeyError:
        raise ValueError(
            f"unknown engine version: {version!r} "
            f"(known: {', '.join(list_versions())})"
        ) from None

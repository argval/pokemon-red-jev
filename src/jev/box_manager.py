"""Auto-box rules for dex-filler species (Step 5 of the Vibe Mode plan)."""

from __future__ import annotations

try:
    from state.pokedex import KAKUNA_LLM_HINT, evaluateKakunaUsefulness
except ImportError:  # pragma: no cover - package-layout fallback
    from pokemon_red_jev.state.pokedex import (  # type: ignore
        KAKUNA_LLM_HINT,
        evaluateKakunaUsefulness,
    )


def depositOrRelease(mon, grinding_for_evolution=False, slot=0) -> dict:
    """Decide whether a party mon should be deposited, kept, or ground.

    Kakuna rule: a Harden-only Kakuna below the Beedrill evolution level is
    dex-filler with near-zero combat value at Lv3, so the default action is
    "deposit" (auto-box at the PC). The only exception is when the run is
    explicitly grinding it toward evolution, in which case the action is
    "grind" (wilds only, never a dungeon/trainer lead).

    Returns a dict with ``action`` ("deposit", "grind", or "keep"),
    ``slot``/``species``/``level``, ``hint`` (LLM-facing), and ``reason``.
    """
    if not isinstance(mon, dict):
        return {
            "action": "keep",
            "slot": slot,
            "species": "?",
            "level": 0,
            "hint": "",
            "reason": "Unrecognized party entry; keep by default.",
        }
    try:
        level = int(mon.get("level", 0) or 0)
    except (TypeError, ValueError):
        level = 0
    verdict = evaluateKakunaUsefulness(mon, grinding_for_evolution=grinding_for_evolution)
    recommendation = verdict.get("recommendation", "keep")
    species = verdict.get("species", mon.get("species", "?"))
    level = verdict.get("level", mon.get("level", 0))

    if recommendation == "box":
        action = "deposit"
    elif recommendation == "grind":
        action = "grind"
    else:
        action = "keep"
    return {
        "action": action,
        "slot": mon.get("slot", slot),
        "species": species,
        "level": level,
        "hint": verdict.get("hint", KAKUNA_LLM_HINT if species == "KAKUNA" else ""),
        "reason": verdict.get("reason", ""),
    }

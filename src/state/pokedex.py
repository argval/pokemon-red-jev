"""Pokedex utility rules for dex-filler species (Step 5 of the Vibe Mode plan).

Kakuna utility rule: Kakuna is a cocoon species that only knows Harden at
low level and evolves into Beedrill at Lv10. A freshly caught Lv3 Kakuna is
a dex-filler with near-zero combat value -- it cannot deal damage, only
stall with Harden -- so it should be auto-boxed unless the run is
explicitly grinding it toward the Lv10 evolution.
"""

from __future__ import annotations

KAKUNA_SPECIES = "KAKUNA"
KAKUNA_EVOLVE_LEVEL = 10
KAKUNA_KNOWN_MOVES = frozenset({"HARDEN"})

# LLM hint surfaced in prompts so the planner stops leading/grinding a Lv3 Kakuna.
KAKUNA_LLM_HINT = (
    "Kakuna (Harden-only, evolves to Beedrill at Lv10) is dex-filler with "
    "near-zero combat value at Lv3: auto-box it unless grinding for evolution."
)


def _moves(mon) -> list:
    try:
        return list(mon.get("moves") or [])
    except AttributeError:
        return []


def _has_damaging_move(mon) -> bool:
    for move in _moves(mon):
        try:
            if move.get("power") and move.get("pp", 1):
                return True
        except AttributeError:
            continue
    return False


def _level(mon) -> int:
    try:
        return int(mon.get("level", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def evaluateKakunaUsefulness(mon, grinding_for_evolution=False) -> dict:
    """Rate a Kakuna's combat usefulness and recommend box vs keep.

    Returns a dict with ``species``, ``level``, ``useful`` (bool),
    ``recommendation`` (one of "box", "keep", "grind", "not_applicable"),
    ``hint`` (LLM-facing string), and ``reason``.
    """
    species = ""
    try:
        species = (mon.get("species") or "").upper()
    except AttributeError:
        species = ""
    level = _level(mon) if isinstance(mon, dict) else 0

    if species != KAKUNA_SPECIES:
        return {
            "species": species or "?",
            "level": level,
            "useful": True,
            "recommendation": "not_applicable",
            "hint": "",
            "reason": "Not a Kakuna; this rule does not apply.",
        }

    if grinding_for_evolution and level < KAKUNA_EVOLVE_LEVEL:
        return {
            "species": species,
            "level": level,
            "useful": True,
            "recommendation": "grind",
            "hint": KAKUNA_LLM_HINT,
            "reason": (
                f"Kakuna Lv{level} is being ground to Beedrill at Lv{KAKUNA_EVOLVE_LEVEL}; "
                "keep it in rotation on wilds only, never as a dungeon/trainer lead."
            ),
        }

    if not _has_damaging_move(mon) or level < KAKUNA_EVOLVE_LEVEL:
        return {
            "species": species,
            "level": level,
            "useful": False,
            "recommendation": "box",
            "hint": KAKUNA_LLM_HINT,
            "reason": (
                f"Kakuna Lv{level} only knows Harden and evolves to Beedrill at "
                f"Lv{KAKUNA_EVOLVE_LEVEL}: dex-filler with near-zero combat value, "
                "auto-box unless grinding for evolution."
            ),
        }

    return {
        "species": species,
        "level": level,
        "useful": True,
        "recommendation": "keep",
        "hint": KAKUNA_LLM_HINT,
        "reason": (
            f"Kakuna Lv{level} has reached/past the Lv{KAKUNA_EVOLVE_LEVEL} "
            "Beedrill evolution window with usable moves; no auto-box needed."
        ),
    }

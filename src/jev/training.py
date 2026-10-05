"""Grind-vs-switch decisions for underlevelled dungeon parties."""

from .battle_policy import (
    is_dungeon_zone,
    selectLeadPokemon,
    zone_min_level,
)

# A party whose level spread exceeds this funnels XP too narrowly.
XP_SPREAD_LIMIT = 5


def _alive(party):
    return [(i, mon) for i, mon in enumerate(party or []) if mon.get("hp", 0) > 0]


def shouldGrindOrSwitch(party, zone="OVERWORLD", enemy_types=None, effectiveness=None):
    """Decide whether to grind, switch/Box a weak mon, or proceed.

    Returns a dict with at least ``action`` (one of "proceed", "switch_lead",
    "grind", "box") and ``reason``. Weak-mon cases also carry the affected
    ``slot``/``species``/``level`` and, for switches, ``swap_index``.
    """
    party = party or []
    if not any(mon.get("hp", 0) > 0 for mon in party):
        return {"action": "proceed", "reason": "No conscious Pokemon; nothing to reorder."}

    dungeon = is_dungeon_zone(zone)
    minimum = zone_min_level(zone)
    lead = selectLeadPokemon(party, zone, enemy_types, effectiveness)

    weak = [
        (i, mon)
        for i, mon in _alive(party)
        if mon.get("level", 0) < minimum
        or (dungeon and not any(m.get("power") and m.get("pp", 1) for m in mon.get("moves") or []))
    ]

    # 1. The current lead itself is unviable (e.g. Lv3 Kakuna up front in
    #    Mt. Moon): switch now, and grind/Box it before it returns.
    if party and party[0].get("hp", 0) > 0:
        front = party[0]
        front_weak = front.get("level", 0) < minimum or (
            dungeon and not any(m.get("power") and m.get("pp", 1) for m in front.get("moves") or [])
        )
        if front_weak and lead is not None and lead != 0:
            nxt = party[lead]
            return {
                "action": "switch_lead",
                "slot": 0,
                "species": front.get("species"),
                "level": front.get("level"),
                "swap_index": lead,
                "reason": (
                    f"Front {front.get('species')} Lv{front.get('level')} is below the "
                    f"Lv{minimum} {zone} threshold; switch to {nxt.get('species')} "
                    f"Lv{nxt.get('level')} and grind or Box it first."
                ),
            }

    # 2. Any weak mon in a dungeon zone: grind it on wilds or Box it, and
    #    never suggest it as a lead until it reaches the threshold.
    if dungeon and weak:
        index, mon = min(weak, key=lambda item: item[1].get("level", 0))
        levels = [m.get("level", 0) for _, m in _alive(party)]
        spread = (max(levels) - min(levels)) if levels else 0
        if len(party) > 1 and (mon.get("level", 0) <= minimum - 5 or spread >= XP_SPREAD_LIMIT):
            action = "box"
            advice = f"Deposit {mon.get('species')} at the PC until it can survive Lv{minimum} fights"
        else:
            action = "grind"
            advice = f"Grind {mon.get('species')} on wilds to Lv{minimum} before trainer fights"
        return {
            "action": action,
            "slot": index,
            "species": mon.get("species"),
            "level": mon.get("level"),
            "swap_index": lead,
            "reason": f"{advice} in {zone}; balanced XP share keeps the team within {XP_SPREAD_LIMIT} levels.",
        }

    # 3. Overworld level spread: keep XP balanced without hard boxing.
    levels = [m.get("level", 0) for _, m in _alive(party)]
    if levels and max(levels) - min(levels) > XP_SPREAD_LIMIT + 2:
        low = min(_alive(party), key=lambda item: item[1].get("level", 0))
        return {
            "action": "grind",
            "slot": low[0],
            "species": low[1].get("species"),
            "level": low[1].get("level"),
            "swap_index": lead,
            "reason": "Level spread is wide; give the lowest member wild-fight XP before dungeons.",
        }

    target = f" Lv{party[lead].get('level')}" if lead is not None else ""
    return {
        "action": "proceed",
        "swap_index": lead,
        "reason": f"Lead{target} meets the Lv{minimum} {zone} threshold with balanced XP.",
    }

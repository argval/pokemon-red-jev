"""Party snapshot for LLM goal prompts."""

from __future__ import annotations


def is_usable(mon: dict) -> bool:
    """A slot counts as usable when it still has HP left to fight."""
    try:
        return (mon.get("hp") or 0) > 0
    except AttributeError:
        return False


def _norm_move(move) -> dict:
    if isinstance(move, str):
        return {"name": move, "pp": None, "max_pp": None, "power": None, "type": None}
    if isinstance(move, dict):
        return {
            "name": move.get("name", "?"),
            "pp": move.get("pp"),
            "max_pp": move.get("max_pp", move.get("maxPP")),
            "power": move.get("power"),
            "type": move.get("type"),
        }
    return {"name": str(move), "pp": None, "max_pp": None, "power": None, "type": None}


def getPartySnapshot(state_or_party) -> list:
    """Normalize ``state["party"]`` into per-slot LLM-ready entries.

    Each entry carries species, level, HP/maxHP, status, moves+PP, XP,
    and a usability flag so the planner can tell a Lv19 starter apart
    from a Lv3 Kakuna.
    """
    if isinstance(state_or_party, dict):
        party = state_or_party.get("party") or []
    else:
        party = state_or_party or []
    snapshot = []
    for index, mon in enumerate(party):
        if not isinstance(mon, dict):
            continue
        hp = mon.get("hp", 0) or 0
        max_hp = mon.get("max_hp", 0) or 0
        xp = mon.get("experience", mon.get("xp", 0)) or 0
        try:
            level = int(mon.get("level", 0) or 0)
        except (TypeError, ValueError):
            level = 0
        try:
            xp = int(xp)
        except (TypeError, ValueError):
            xp = 0
        snapshot.append({
            "slot": mon.get("slot", index),
            "species": mon.get("species", "?"),
            "nickname": mon.get("nickname"),
            "level": level,
            "hp": hp,
            "max_hp": max_hp,
            "status": mon.get("status") or "OK",
            "types": list(mon.get("types") or []),
            "moves": [_norm_move(m) for m in (mon.get("moves") or [])],
            "experience": xp,
            "usable": is_usable(mon),
        })
    return snapshot

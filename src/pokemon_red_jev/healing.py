"""Keep HP-restoring supplies for battles and necessary field recovery."""

HEAL = {"POTION": 20, "SUPER POTION": 50, "HYPER POTION": 200, "MAX POTION": 999, "FULL RESTORE": 999,
        "FRESH WATER": 50, "SODA POP": 60, "LEMONADE": 80}


def field_healing(state, center, hops):
    """Slots that may spend an HP item now, using actual Center reachability."""
    party = state.get("party") or []
    alive = [mon for mon in party if mon.get("hp", 0) > 0]
    at_center = "POKECENTER" in state.get("map", "")
    nearby = at_center or hops is not None and hops <= 2
    critical = bool(alive) and all(mon["hp"] * 4 <= mon["max_hp"] for mon in alive)
    allowed = []
    for index, mon in enumerate(party):
        hp, total = mon.get("hp", 0), mon.get("max_hp", 0)
        if not 0 < hp < total or at_center:
            continue
        poison_risk = mon.get("status") == "POISON" and hp <= 4
        if poison_risk or (critical if nearby else hp * 2 <= total):
            allowed.append(mon.get("slot", index))
    note = ("Prefer the nearest reachable Pokémon Center: it heals HP, status, and PP for free. "
            "Save HP-restoring items for battles. Outside battle, use them only on allowed_slots; "
            "these are emergency targets near a Center, or Pokémon at half HP or less when a Center "
            "is more than two areas away or unreachable. Recheck after each use; do not top up the party.")
    return {"center": center, "center_hops": hops, "allowed_slots": allowed, "note": note}


def field_heal_allowed(state, mon):
    policy = state.get("healing")
    if policy is None:
        return True
    slot = mon.get("slot")
    if slot is None:
        slot = (state.get("party") or []).index(mon)
    return slot in policy["allowed_slots"]

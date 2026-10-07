"""Sleeping road blockers and the ROM's allowed Poké Flute positions."""

# ItemUsePokeFlute coordinate tables in pret/pokered/engine/items/item_effects.asm.
SNORLAX = {
    "ROUTE_12": {"event": "EVENT_BEAT_ROUTE12_SNORLAX", "positions": {(9, 62), (10, 61), (10, 63), (11, 62)}},
    "ROUTE_16": {"event": "EVENT_BEAT_ROUTE16_SNORLAX", "positions": {(25, 10), (27, 10)}},
}


def snorlax_context(state, sprites):
    site = SNORLAX.get(state.get("map"))
    if not site or site["event"] in state.get("events", []):
        return None
    sleeping = next((sprite for sprite in sprites if sprite["picture"] == 67), None)
    if sleeping is None:
        return None
    has_flute = any(item["name"] == "POKé FLUTE" and item["qty"] > 0 for item in state.get("bag") or [])
    events = set(state.get("events") or [])
    scope = any(item["name"] == "SILPH SCOPE" and item["qty"] > 0 for item in state.get("bag") or [])
    if has_flute:
        next_step = "Walk beside Snorlax and use the Poké Flute. This starts a Lv30 wild battle; prepare the party first."
    elif "EVENT_RESCUED_MR_FUJI" in events:
        next_step = "Talk to Mr. Fuji in MR_FUJIS_HOUSE in Lavender Town to receive the Poké Flute."
    elif scope or "EVENT_GOT_SILPH_SCOPE" in events:
        next_step = "Climb Pokémon Tower in Lavender Town, rescue Mr. Fuji, then get the Poké Flute in his house."
    else:
        next_step = ("To obtain the Poké Flute, get the Silph Scope in Celadon's Rocket Hideout, rescue Mr. Fuji "
                     "in Pokémon Tower, then talk to him in his Lavender Town house.")
    return {"kind": "snorlax", "map": state["map"], "x": sleeping["x"], "y": sleeping["y"],
            "has_flute": has_flute, "cleared_event": site["event"], "next_step": next_step,
            "note": ("A sleeping Snorlax blocks this road. Talking, Cut, Strength, and Dig will not wake it. "
                     "Without the Poké Flute, backtrack through an open route toward the current objective; "
                     "do not keep returning to Snorlax or inspecting the party. Surf may provide another route.")}

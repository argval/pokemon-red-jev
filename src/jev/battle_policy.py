"""Level-aware lead/switch policy for dungeon/trainer zones (Mt. Moon imbalance)."""

# Minimum viable lead level per zone prefix. Mt. Moon wilds/trainers sit
# roughly Lv8-12, so anything far below that must never lead there.
ZONE_MIN_LEVEL = {
    "MT_MOON": 8,
    "ROCK_TUNNEL": 15,
    "POKEMON_TOWER": 18,
    "DEFAULT_DUNGEON": 8,
    "DEFAULT": 5,
}

DUNGEON_PREFIXES = (
    "MT_MOON",
    "ROCK_TUNNEL",
    "DIGLETT",
    "VICTORY_ROAD",
    "SEAFOAM",
    "CERULEAN_CAVE",
    "POKEMON_MANSION",
    "POKEMON_TOWER",
    "ROCKET_HIDEOUT",
    "SILPH_CO",
    "POWER_PLANT",
)

# Cocoon / splash-only species that cannot deal damage at low level.
NO_OFFENSE_SPECIES = frozenset({"KAKUNA", "METAPOD", "MAGIKARP"})


def is_dungeon_zone(zone):
    """True for trainer-heavy dungeons where a weak lead wipes the run."""
    upper = (zone or "").upper()
    return any(upper.startswith(prefix) for prefix in DUNGEON_PREFIXES)


def zone_min_level(zone):
    """Minimum lead level for a zone. Falls back to dungeon/overworld defaults."""
    upper = (zone or "").upper()
    for prefix, level in ZONE_MIN_LEVEL.items():
        if prefix.startswith("DEFAULT"):
            continue
        if upper.startswith(prefix):
            return level
    if is_dungeon_zone(zone):
        return ZONE_MIN_LEVEL["DEFAULT_DUNGEON"]
    return ZONE_MIN_LEVEL["DEFAULT"]


def _has_damaging_move(mon):
    for move in mon.get("moves") or []:
        try:
            if move.get("power") and move.get("pp", 1):
                return True
        except AttributeError:
            continue
    return False


def _is_no_offense(mon):
    species = (mon.get("species") or "").upper()
    if species in NO_OFFENSE_SPECIES:
        return not _has_damaging_move(mon)
    return False


def _matchup_bonus(mon, enemy_types, effectiveness):
    if not enemy_types or effectiveness is None:
        return 0.0
    try:
        attacks = {m.get("type") for m in mon.get("moves") or [] if m.get("power")}
        attacks |= set(mon.get("types") or [])
        if not attacks:
            return 0.0
        best = max(effectiveness(t, list(enemy_types)) for t in attacks)
        threat = max(effectiveness(t, list(mon.get("types") or [])) for t in enemy_types)
    except (TypeError, ValueError):
        return 0.0
    bonus = 0.0
    if best >= 2:
        bonus += 6.0
    elif best < 1:
        bonus -= 4.0
    if threat >= 2:
        bonus -= 5.0
    elif threat < 1:
        bonus += 2.0
    return bonus


def selectLeadPokemon(party, zone="OVERWORLD", enemy_types=None, effectiveness=None):
    """Return the party index that should lead in `zone`.

    Rules, in order:
    1. Fainted mons are never suggested.
    2. In dungeon/trainer zones, mons below the zone minimum or with no
       damaging moves (e.g. a Lv3 Kakuna that only knows Harden) are only
       suggested when no viable alternative exists.
    3. Among viable mons, prefer battle strength (level + matchup), with a
       small balanced-XP bonus for the lowest viable mon so XP is shared
       instead of always funnelled to the top battler.
    4. Returns None for an empty or fully-fainted party.
    """
    if not party:
        return None
    alive = [(i, mon) for i, mon in enumerate(party) if mon.get("hp", 0) > 0]
    if not alive:
        return None

    dungeon = is_dungeon_zone(zone)
    minimum = zone_min_level(zone)

    def viable(item):
        _, mon = item
        if _is_no_offense(mon):
            return False
        if dungeon and not _has_damaging_move(mon):
            return False
        return mon.get("level", 0) >= minimum

    candidates = [item for item in alive if viable(item)]
    # Enforce the minimum-level threshold: weak mons are only suggested
    # when nothing viable exists (last-resort fallback below).
    pool = candidates or list(alive)

    scored = []
    for index, mon in pool:
        score = float(mon.get("level", 0)) * 2.0
        score += _matchup_bonus(mon, enemy_types, effectiveness)
        if mon.get("status", "OK") != "OK":
            score -= 3.0
        if _is_no_offense(mon):
            # Lv3 Kakuna-style leads: never win a trainer fight, just burn turns.
            score -= 100.0
        elif not _has_damaging_move(mon):
            score -= 50.0 if dungeon else 10.0
        if dungeon and mon.get("level", 0) < minimum:
            score -= 40.0 + 5.0 * (minimum - mon.get("level", 0))
        scored.append((score, index))

    # Balanced XP share: among viable mons within a tight band, prefer the
    # lowest level so the team levels evenly instead of over-levelling one.
    if candidates:
        best_score = max(s for s, _ in scored)
        band = [(s, i) for s, i in scored if best_score - s <= 6.0]
        if len(band) > 1:
            levels = {i: party[i].get("level", 0) for _, i in band}
            floor = min(levels.values())
            # Only share XP downwards when the lower mon is still viable.
            if floor >= minimum:
                return min(band, key=lambda item: (levels[item[1]], -item[0]))[1]
    scored.sort(reverse=True)
    return scored[0][1]

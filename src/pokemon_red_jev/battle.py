"""Pokémon Red move rules and noncritical damage estimates from the cartridge."""

PHYSICAL = {"NORMAL", "FIGHTING", "FLYING", "POISON", "GROUND", "ROCK", "BUG", "GHOST"}
STATS = ("attack", "defense", "speed", "special", "accuracy", "evasion")
FIXED = {"SEISMICTOSS", "NIGHTSHADE", "SONICBOOM", "DRAGONRAGE", "SUPERFANG", "PSYWAVE"}
CONDITIONAL = {"COUNTER", "BIDE"}


def move_name(move: dict) -> str:
    """Match ROM move names without depending on their display spacing."""
    return move.get("name", "").replace(" ", "").replace("-", "").upper()


def damaging(move: dict) -> bool:
    """Night Shade has zero printed power in Red but still deals damage."""
    return bool(move.get("power") or move_name(move) in FIXED | CONDITIONAL)


def switch_stats(mon: dict) -> dict:
    """Project party stats after switching in, with fresh stages and status penalties.
    Active battle RAM already has these penalties and must not use this projection.
    """
    projected = {**mon, "volatile": {}, "stat_stages": {stat: 7 for stat in STATS}}
    if mon.get("status") == "BURN" and isinstance(mon.get("attack"), int):
        projected["attack"] = max(1, mon["attack"] // 2)
    if mon.get("status") == "PARALYZED" and isinstance(mon.get("speed"), int):
        projected["speed"] = max(1, mon["speed"] // 4)
    return projected


def move_failure(move: dict, player: dict, enemy: dict, kind: str = "trainer") -> str | None:
    """Return a proven reason the move cannot work in the current Red battle.
    Secondary status effects never invalidate a move that still deals damage.
    """
    name, effect = move_name(move), move.get("effect", 0)
    yours, theirs = player.get("volatile") or {}, enemy.get("volatile") or {}
    status = enemy.get("status", "OK")
    types = enemy.get("types") or []
    if effect == 0x08 or name == "DREAMEATER":
        return "Dream Eater requires a sleeping target." if status != "SLEEP" else None
    if effect == 0x26 or name in {"FISSURE", "HORNDRILL", "GUILLOTINE"}:
        if player.get("speed", 0) < enemy.get("speed", 0):
            return "One-hit KO moves fail when the user is slower in Red."
    if damaging(move):
        return None
    if effect == 0x42 or name in {"POISONPOWDER", "POISONGAS", "TOXIC"}:
        if "POISON" in types:
            return "Poison types cannot be poisoned."
        if theirs.get("substitute"):
            return "Substitute blocks poison."
        if status != "OK":
            return f"The target already has {status}; poison cannot replace it."
    if effect == 0x20 or name in {"SLEEPPOWDER", "SPORE", "SING", "HYPNOSIS", "LOVELYKISS"}:
        if status != "OK" and not theirs.get("recharge"):
            return f"The target already has {status}; sleep cannot replace it."
    if effect == 0x43 or name in {"STUNSPORE", "THUNDERWAVE", "GLARE"}:
        if status != "OK":
            return f"The target already has {status}; paralysis cannot replace it."
        if move.get("type") == "ELECTRIC" and "GROUND" in types:
            return "Ground types are immune to Electric paralysis moves."
    for start in (0x12, 0x3A):
        if start <= effect < start + 6:
            stat = STATS[effect - start]
            if theirs.get("substitute") or theirs.get("mist"):
                return "Substitute or Mist blocks this stat drop."
            if (enemy.get("stat_stages") or {}).get(stat, 7) == 1 or enemy.get(stat) == 1:
                return f"The target's {stat} cannot fall further."
    for start in (0x0A, 0x32):
        if start <= effect < start + 6:
            stat = STATS[effect - start]
            if (player.get("stat_stages") or {}).get(stat, 7) == 13 or player.get(stat, 0) >= 999:
                return f"Your {stat} cannot rise further."
    if effect == 0x31:
        if theirs.get("substitute"):
            return "Substitute blocks confusion."
        if theirs.get("confused"):
            return "The target is already confused."
    if effect == 0x54 or name == "LEECHSEED":
        if "GRASS" in types:
            return "Grass types are immune to Leech Seed."
        if theirs.get("seeded"):
            return "The target is already seeded."
    flag = {0x2E: "mist", 0x2F: "focus_energy", 0x40: "light_screen", 0x41: "reflect"}.get(effect)
    if flag and yours.get(flag):
        return "This effect is already active."
    if effect == 0x38 or name in {"REST", "RECOVER", "SOFTBOILED"}:
        missing = player.get("max_hp", 0) - player.get("hp", 0)
        if missing <= 0:
            return "HP is already full; this healing move fails in Red."
        if missing in {255, 511}:
            return "Red's healing bug makes this move fail at this HP difference."
    if effect == 0x4F:
        if yours.get("substitute"):
            return "You already have a Substitute."
        if player.get("hp", 0) < player.get("max_hp", 0) // 4:
            return "Not enough HP to create a Substitute."
    if effect == 0x55 or name == "SPLASH":
        return "Splash has no effect."
    if kind == "trainer" and name in {"TELEPORT", "ROAR", "WHIRLWIND"}:
        return "This move cannot end a trainer battle in Red."
    return None


def damage_range(player: dict, enemy: dict, move: dict, effectiveness: float,
                 *, multipliers: list[float] | None = None) -> tuple[int, int] | None:
    """Estimate damage on a hit, using current RAM stats without reapplying stages.
    Counter and Bide need turn-specific damage; their estimate is unknown.
    """
    name = move_name(move)
    if name in CONDITIONAL:
        return None
    level = player.get("level", 1)
    if name in FIXED:
        if name in {"SEISMICTOSS", "NIGHTSHADE"}:
            high = level
        elif name == "SONICBOOM":
            high = 20
        elif name == "DRAGONRAGE":
            high = 40
        elif name == "SUPERFANG":
            high = max(1, enemy.get("hp", 0) // 2)
        else:
            return 1, max(1, level * 3 // 2 - 1)
        return high, high
    if move_failure(move, player, enemy) or not move.get("power") or not effectiveness:
        return 0, 0
    if move.get("effect") == 0x26 or name in {"FISSURE", "HORNDRILL", "GUILLOTINE"}:
        return enemy.get("hp", 0), enemy.get("hp", 0)
    physical = move.get("type") in PHYSICAL
    attack = player.get("attack" if physical else "special")
    defense = enemy.get("defense" if physical else "special")
    if attack is None or defense is None:
        return None
    if (enemy.get("volatile") or {}).get("reflect" if physical else "light_screen"):
        defense *= 2
    if attack > 255 or defense > 255:
        attack, defense = (attack // 4) & 255, (defense // 4) & 255
        attack = max(1, attack)
    if move.get("effect") == 0x07:
        defense //= 2
    base = min(997, ((2 * level // 5 + 2) * move["power"] * attack // max(1, defense)) // 50) + 2
    if move.get("type") in player.get("types", []):
        base += base // 2
    for multiplier in multipliers if multipliers is not None else (effectiveness,):
        base = int(base * multiplier)
    low = base if base <= 1 else base * 217 // 255
    hits = (2, 5) if move.get("effect") == 0x1D else (2, 2) if move.get("effect") in {0x2C, 0x4D} else (1, 1)
    return low * hits[0], base * hits[1]


def damage(player: dict, enemy: dict, move: dict, effectiveness: float) -> int:
    """Return the high end of the estimate for existing damage helper callers."""
    estimate = damage_range(player, enemy, move, effectiveness)
    return estimate[1] if estimate is not None else 0

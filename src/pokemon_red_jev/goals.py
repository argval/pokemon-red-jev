"""Small tasks proposed by the planner and verified against observed game state."""

from dataclasses import asdict, dataclass
from functools import cache
from importlib.resources import files
import json
import re

FOCUSES = {"progress", "heal", "train", "catch", "shop", "explore", "team"}
# Re-ask the focus after this many overworld decisions, even if nothing else changed.
FOCUS_TTL = 30
INTENTS = {
    "progress": "Move toward the current objective now.",
    "heal": "Go heal the party at a Pokémon Center.",
    "train": "Train: fight wild Pokémon in tall grass to gain levels before the objective.",
    "catch": "Catch new wild Pokémon to build a stronger, more varied team.",
    "shop": "Buy supplies (Poké Balls, Potions) at a Poké Mart.",
    "explore": "Talk to people and explore this area for items or information.",
    "team": "Change the team at a Pokémon Center PC: deposit members and withdraw Pokémon from the box.",
}
_FOCUS_MARK = {
    "heal": re.compile(r"POKECENTER|heals the entire party|heals the whole party|\bNURSE\b", re.I),
    "shop": re.compile(r"MART|shop clerk|\bCLERK\b", re.I),
    "team": re.compile(r"Pokémon storage|Use the PC", re.I),
    "train": re.compile(r"tall grass", re.I),
    "catch": re.compile(r"tall grass", re.I),
    "progress": re.compile(r"Leads toward the objective|changes which gates are closed|opens some gates"),
}
KINDS = {"map", "event", "item", "healed", "level", "badge", "interaction"}

# Species types the justification check recognises (Gen I chart, uppercase in prompts).
JUSTIFICATION_TYPES = frozenset({
    "NORMAL", "FIRE", "WATER", "ELECTRIC", "GRASS", "ICE", "FIGHTING", "POISON",
    "GROUND", "FLYING", "PSYCHIC", "BUG", "ROCK", "GHOST", "DRAGON",
})
# Focuses that must justify the Pokemon choice with a type and/or level reason.
JUSTIFICATION_FOCUSES = {"train", "catch", "team"}
# Penalty applied when a candidate reuses the last-used Pokemon without a fresh reason.
REPEAT_POKEMON_PENALTY = 50


@dataclass
class Goal:
    goal: str
    focus: str
    target_map: str
    success: dict
    max_decisions: int = 20
    justification: str = ""

    @classmethod
    def parse(cls, obj, catalog):
        if not isinstance(obj, dict):
            raise ValueError("Goal must be an object")
        allowed = {"goal", "focus", "target_map", "success", "max_decisions", "justification"}
        if not {"goal", "focus", "target_map", "success", "max_decisions"}.issubset(set(obj)) or set(obj) - allowed:
            raise ValueError("Goal must contain goal, focus, target_map, success, max_decisions only")
        if not isinstance(obj["goal"], str) or not 1 <= len(obj["goal"].strip()) <= 400:
            raise ValueError("Goal text must be 1..400 characters")
        if not isinstance(obj["focus"], str) or obj["focus"] not in FOCUSES:
            raise ValueError("Unknown goal focus")
        if not isinstance(obj["target_map"], str) or obj["target_map"] not in catalog["maps"]:
            raise ValueError("Unknown target map")
        if type(obj["max_decisions"]) is not int or not 1 <= obj["max_decisions"] <= 100:
            raise ValueError("Goal budget must be 1..100 decisions")
        justification = obj.get("justification", "")
        if not isinstance(justification, str) or len(justification) > 400:
            raise ValueError("Justification must be a string of at most 400 characters")
        if obj["focus"] in JUSTIFICATION_FOCUSES and not has_type_level_justification(obj):
            raise ValueError("train/catch/team goals must justify the Pokemon choice with a type/level reason")
        condition = obj["success"]
        if not isinstance(condition, dict) or set(condition) != {"kind", "value"}:
            raise ValueError("success requires kind and value")
        kind, value = condition["kind"], condition["value"]
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("Unsupported completion condition")
        collection = {"map": "maps", "event": "events", "item": "items", "interaction": "interactions"}.get(kind)
        if collection and (not isinstance(value, str) or value not in catalog[collection]):
            raise ValueError(f"Unknown {kind} completion target")
        if kind == "map" and value != obj["target_map"]:
            raise ValueError("Map completion must match the navigation target")
        if kind == "healed" and value is not True:
            raise ValueError("healed requires value=true")
        if kind in {"level", "badge"} and (type(value) is not int or not 1 <= value <= (100 if kind == "level" else 8)):
            raise ValueError("Invalid level/badge threshold")
        return cls(**obj)

    def to_dict(self):
        return asdict(self)

    def done(self, state):
        return complete(self.success, state)


def complete(condition, state):
    kind, value = condition["kind"], condition["value"]
    if kind == "all":
        return all(complete(c, state) for c in value)
    if kind == "any":
        return any(complete(c, state) for c in value)
    if kind == "map":
        return state["map"] == value
    if kind == "visited":
        return value in state["visited"]
    if kind == "event":
        return value in (state.get("events") or ())
    if kind == "item":
        return any(i["name"] == value and i["qty"] > 0 for i in state["bag"])
    if kind == "healed":
        return bool(state["party"]) and all(p["hp"] == p["max_hp"] and p["status"] == "OK" for p in state["party"])
    if kind == "level":
        return any(p["level"] >= value for p in state["party"])
    if kind == "badge":
        return bool(state["badges"] & (1 << (value - 1)))
    if kind == "badge_count":
        return state["badges"].bit_count() >= value
    if kind == "interaction":
        return value in (state.get("interactions") or ())
    if kind == "owned_count":
        return len(state.get("party") or []) + len(state.get("box") or []) >= value
    if kind == "team_ready":
        plan = state.get("team_plan") or team_plan(state)
        return plan["deposit"] is None and plan["withdraw"] is None
    raise ValueError(f"Unknown completion kind: {kind}")


def _mentions_type_or_level(text):
    """A type name (or coverage/effectiveness wording) plus a level reference."""
    upper = (text or "").upper()
    has_type = any(t in upper for t in JUSTIFICATION_TYPES) or any(
        word in upper for word in ("COVERAGE", "SUPER EFFECTIVE", "NOT VERY EFFECTIVE", "STAB", "MATCHUP"))
    has_level = bool(re.search(r"\bLV\.?\s*\d+|\bLEVEL\s*\d+|\bLV\d+", upper))
    return has_type and has_level


def has_type_level_justification(raw):
    """The goal text plus its justification must name a type reason and a level."""
    if not isinstance(raw, dict):
        return False
    combined = f"{raw.get('goal', '')} {raw.get('justification', '')}"
    return _mentions_type_or_level(combined)


def _known_species(state):
    names = set()
    for mon in (*(state.get("party") or []), *(state.get("box") or [])):
        species = mon.get("species")
        if isinstance(species, str) and species:
            names.add(species.upper())
    return names


def _species_in_text(text, known):
    upper = (text or "").upper()
    return {name for name in known if name and re.search(rf"\b{re.escape(name)}\b", upper)}


def last_used_species(state, previous):
    """Species named by the previous goal, falling back to the current lead."""
    known = _known_species(state)
    if isinstance(previous, dict):
        inner = previous.get("goal") if isinstance(previous.get("goal"), dict) else previous
        if isinstance(inner, dict):
            found = _species_in_text(f"{inner.get('goal', '')} {inner.get('justification', '')}", known)
            if found:
                return found
    party = state.get("party") or []
    if party and isinstance(party[0].get("species"), str):
        return {party[0]["species"].upper()}
    return set()


def goal_reuses_last_pokemon(raw, state, previous):
    """True when this candidate names a species the last goal already used."""
    if not isinstance(raw, dict):
        return False
    known = _known_species(state)
    if not known:
        return False
    last = last_used_species(state, previous)
    if not last:
        return False
    mentioned = _species_in_text(f"{raw.get('goal', '')} {raw.get('justification', '')}", known)
    return bool(mentioned & last)


def rankGoals(candidates, state, previous):
    """Order goal dicts best-first; repeats of the last-used Pokemon sort last.

    A repeat is only forgiven when it carries a fresh type/level justification;
    even then it keeps a small penalty so a novel alternative wins ties.
    Returns a new list of (score, raw) tuples sorted best-first.
    """
    last = last_used_species(state, previous)
    scored = []
    for raw in candidates:
        score = 0
        if goal_reuses_last_pokemon(raw, state, previous):
            score -= REPEAT_POKEMON_PENALTY
            if has_type_level_justification(raw):
                score += REPEAT_POKEMON_PENALTY // 2
        # Prefer an explicit justification; it shows the pick was deliberate.
        if isinstance(raw, dict) and isinstance(raw.get("justification"), str) and raw["justification"].strip():
            score += 1
        scored.append((score, raw))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def suggestGoals(candidates, state, previous):
    """Best-first goal dicts after the anti-loop filter.

    Single-candidate callers get [raw] unless it is an unjustified repeat of
    the last-used Pokemon, in which case they get [] (ask for a different goal).
    """
    ranked = rankGoals(candidates or [], state, previous)
    if len(candidates or []) <= 1 and ranked:
        score, raw = ranked[0]
        if score < 0:
            return []
        return [raw]
    return [raw for _, raw in ranked]


# snake_case aliases for Python callers.
rank_goals = rankGoals
suggest_goals = suggestGoals


@cache
def story():
    return json.loads(files(__package__).joinpath("story.json").read_text())


# The next story battle's defending types. Counters come from the type chart, not a fixed team.
GYM_DEFENDERS = {
    "brock": ("Brock", ("ROCK", "GROUND")),
    "misty": ("Misty", ("WATER",)),
    "surge": ("Lt. Surge", ("ELECTRIC",)),
    "erika": ("Erika", ("GRASS",)),
    "koga": ("Koga", ("POISON",)),
    "sabrina": ("Sabrina", ("PSYCHIC",)),
    "blaine": ("Blaine", ("FIRE",)),
    "giovanni": ("Giovanni", ("GROUND",)),
    "lorelei": ("Lorelei", ("ICE", "WATER")),
    "bruno": ("Bruno", ("FIGHTING", "ROCK")),
    "agatha": ("Agatha", ("GHOST", "POISON")),
    "lance": ("Lance", ("DRAGON", "FLYING")),
}


def next_gym(state):
    """The next gym or Elite Four fight from the current story milestone."""
    current = (state.get("milestone") or {}).get("id")
    if not current:
        return None
    ids = [milestone["id"] for milestone in story()]
    if current not in ids:
        return None
    for milestone_id in ids[ids.index(current):]:
        if milestone_id in GYM_DEFENDERS:
            leader, types = GYM_DEFENDERS[milestone_id]
            return {"id": milestone_id, "leader": leader, "types": types}
    return None


def next_fight(state):
    """The current or upcoming story step that names the defenders' types."""
    current = (state.get("milestone") or {}).get("id")
    milestones = story()
    start = next((index for index, milestone in enumerate(milestones) if milestone["id"] == current), 0)
    return next((milestone for milestone in milestones[start:] if milestone.get("types")), None)


def _effect_word(score):
    if score == 0:
        return "no effect"
    if score >= 2:
        return f"super effective x{score:g}"
    if score < 1:
        return f"not very effective x{score:g}"
    return "normal effectiveness"


def _move_score(move_type, types, effectiveness, dual):
    if dual:
        return effectiveness(move_type, types)
    return max(effectiveness(move_type, [typing]) for typing in types)


def team_plan(state, effectiveness=None):
    """A small battle team plus field-move users; surplus stays safely in the current box."""
    party, box = state.get("party") or [], state.get("box") or []
    owned = party + box
    if not owned:
        return {"keep": [], "deposit": None, "withdraw": None, "reason": "Choose a starter first."}
    level = lambda i: owned[i].get("level", 0)
    attacks = lambda i: {m.get("type") for m in owned[i].get("moves") or [] if m.get("power")}
    keep = {max(range(len(owned)), key=level)}
    gym = next_gym(state)
    if gym and effectiveness:
        score = lambda i: max((effectiveness(t, gym["types"]) for t in attacks(i)), default=0)
        counter = max(range(len(owned)), key=lambda i: (score(i), level(i)))
        if score(counter) >= 2 and level(counter) >= max(3, max(map(level, keep)) - 10):
            keep.add(counter)
    if len(keep) < 2 and len(owned) > 1:
        keep.add(max((i for i in range(len(owned)) if i not in keep), key=level))
    covered = set().union(*(attacks(i) or set(owned[i].get("types") or []) for i in keep))
    diverse = [i for i in range(len(owned)) if i not in keep and
               (attacks(i) or set(owned[i].get("types") or [])) - covered]
    if diverse and len(keep) < 3:
        keep.add(max(diverse, key=level))
    for move in ("CUT", "SURF", "STRENGTH", "FLY", "FLASH"):
        users = [i for i, p in enumerate(owned) if any(m.get("name") == move for m in p.get("moves") or [])]
        if users and not keep.intersection(users):
            keep.add(max(users, key=level))
    hm = FIELD_MOVES.get(state.get("field_move_needed"), (None,))[0]
    learners = [i for i, p in enumerate(owned) if hm and hm in (p.get("learnable_hms") or [])]
    if learners and not keep.intersection(learners):
        keep.add(max(learners, key=level))
    surplus = [i for i in range(len(party)) if i not in keep]
    deposit = min(surplus, key=level) if surplus and len(party) > 1 and len(box) < 20 else None
    wanted = [i for i in keep if i >= len(party)]
    withdraw = max(wanted, key=level) - len(party) if wanted and (len(party) < 6 or deposit is not None) else None
    names = ", ".join(owned[i].get("species", "?") for i in sorted(keep))
    return {"keep": sorted(i for i in keep if i < len(party)), "deposit": deposit, "withdraw": withdraw,
            "reason": f"Keep {names}: strongest battlers, useful coverage, and field moves. Store surplus; never release."}


def catch_reason(state, enemy, effectiveness=None):
    """One useful addition at a time. Type potential is not a claim about learned attacks."""
    owned = [*(state.get("party") or []), *(state.get("box") or [])]
    if not owned or not enemy or any(p.get("species") == enemy.get("species") for p in owned):
        return None
    if len(state.get("party") or []) >= 6 and len(state.get("box") or []) >= 20:
        return None
    move = state.get("field_move_needed")
    hm = FIELD_MOVES.get(move, (None,))[0]
    if hm and _can_learn(enemy, hm) and not any(_can_learn(p, hm) for p in owned):
        return f"Can learn {hm} ({move}), which the objective needs and no owned Pokémon can learn."
    if len(owned) < 2:
        return "Add a backup so the starter is not the only battler."
    types = set(enemy.get("types") or [])
    new_types = types - {t for p in owned for t in p.get("types") or []}
    if not new_types or enemy.get("level", 0) < max(p.get("level", 0) for p in owned) - 12:
        return None
    gym = next_gym(state)
    if gym and effectiveness and any(effectiveness(t, gym["types"]) >= 2 for t in new_types):
        return f"Adds potential coverage for {gym['leader']}; check its learned moves before using it."
    if len(owned) < 3:
        return "Adds a different type to the small battle team."
    return None


FIELD_MOVES = {"CUT": ("HM01", 0x02, "Cascade Badge"), "SURF": ("HM03", 0x10, "Soul Badge")}


def _species_hms(record):
    machines = (record or {}).get("machines") or []
    return [f"HM{index + 1:02}" for index in range(5)
            if (50 + index) // 8 < len(machines) and machines[(50 + index) // 8] & (1 << ((50 + index) % 8))]


def _by_name(species, name):
    return next((record for record in (species or {}).values() if record.get("name") == name), None)


def _can_learn(record, hm):
    if not record:
        return False
    return hm in (record.get("learnable_hms") or _species_hms(record))


def _evolution_route(record, hm, species, items, bag, level, depth=0):
    """How this species reaches one that can learn `hm`, using a stone only when it is in the bag."""
    if depth > 2 or not record:
        return None
    if _can_learn(record, hm):
        return []
    for evolution in record.get("evolutions") or []:
        if evolution.get("method") == "trade":
            continue
        nxt = (species or {}).get(evolution.get("into"))
        rest = _evolution_route(nxt, hm, species, items, bag, level, depth + 1)
        if rest is None:
            continue
        if evolution.get("method") == "item":
            stone = (items or {}).get(evolution.get("item"), "item")
            if stone not in bag:
                continue
            how = f"with a {stone} (one is in the bag)"
        else:
            at = evolution.get("level")
            how = f"at Lv{at}" if at and (level or 0) < at else f"on its next level-up (it's past Lv{at})"
        into = (nxt or {}).get("name", "?")
        followed = "".join(f", then {step}" if not step.startswith("then ") else f", {step}" for step in rest)
        return [f"evolves into {into} {how}{followed}"]
    return None


def field_move_report(state, species=None, items=None):
    """Who can learn the missing Cut or Surf, and whether that Pokémon is in the box."""
    move = state.get("field_move_needed")
    if move not in FIELD_MOVES:
        return None
    hm, badge_bit, badge_name = FIELD_MOVES[move]
    bag = {item["name"] for item in state.get("bag") or [] if item.get("qty", 1)}
    owned = "it is in the bag" if hm in bag else "not in the bag"
    badge = "you have it" if state.get("badges", 0) & badge_bit else "you don't have it yet"
    party_now, box_now, later = [], [], []

    def consider(mon, where):
        record = _by_name(species, mon.get("species")) or {}
        if mon.get("species_id") in (species or {}):
            record = species[mon["species_id"]]
        learned = mon.get("learnable_hms")
        if learned is None:
            learned = _species_hms(record)
        label = f"{mon.get('nickname') or mon.get('species')} ({mon.get('species')} Lv{mon.get('level')})"
        if hm in learned or _can_learn(record, hm):
            (box_now if where == "box" else party_now).append(label)
            return
        route = _evolution_route(record, hm, species, items, bag, mon.get("level"))
        if route:
            later.append(f"in the {'PC box' if where == 'box' else 'party'}: {label} {', '.join(route)}")

    for mon in state.get("party") or []:
        consider(mon, "party")
    for mon in state.get("box") or []:
        consider(mon, "box")
    text = (f"The objective can't be reached from here without {move}, and no party Pokémon knows {move}. "
            f"{hm} teaches {move} ({owned}); using {move} outside battle needs the {badge_name} ({badge}). "
            f"Party Pokémon that can learn {hm}: {', '.join(party_now) or 'none'}. "
            f"Pokémon in the PC box that can learn it: {', '.join(box_now) or 'none'}.")
    if later:
        text += " Able to learn it only after evolving: " + "; ".join(later) + "."
    withdraw = bool(box_now or any(line.startswith("in the PC box") for line in later)) and not party_now and not any(
        line.startswith("in the party") for line in later)
    if withdraw:
        text += " First step: withdraw a Pokémon that can learn it at a Pokémon Center PC, then teach it."
    return {"text": text, "withdraw": withdraw}


def wild_field_move_learners(state, maps, species, nearby):
    """Wild species on nearby maps that can learn the missing Cut or Surf, by map. Empty when someone owned can."""
    hm = FIELD_MOVES.get(state.get("field_move_needed"), (None,))[0]
    owned = [*(state.get("party") or []), *(state.get("box") or [])]
    if not hm or any(_can_learn(p, hm) for p in owned):
        return {}
    found = {}
    for record in (maps or {}).values():
        if record.get("name") in nearby:
            for wild in record.get("wild") or []:
                sp = (species or {}).get(wild["species_id"]) or {}
                if _can_learn(sp, hm):
                    found.setdefault(sp["name"], set()).add(record["name"])
    return {name: sorted(where) for name, where in sorted(found.items())}


def describe_situation(state, species=None, items=None, effectiveness=None):
    """The short facts the original sends with every choice. Raw party stats stay beside them."""
    party = state.get("party") or []
    hp = sum(mon.get("hp", 0) for mon in party)
    total = sum(mon.get("max_hp", 0) for mon in party)
    fainted = sum(mon.get("hp", 0) == 0 for mon in party)
    health = f"{round(100 * hp / total)}% total HP, {fainted} fainted" if total else "no Pokémon"
    balls = sum(item.get("qty", 0) for item in state.get("bag") or [] if str(item.get("name", "")).endswith("BALL"))
    milestone = state.get("milestone") or {}
    level = milestone.get("level")
    weak = [mon for mon in party if level and mon.get("level", 0) <= level - 10]
    gap = None
    if weak:
        names = ", ".join(f"{mon.get('nickname') or mon.get('species')} Lv{mon.get('level')}" for mon in weak)
        gap = (f"{len(weak)} of your {len(party)} Pokémon are 10+ levels below the typical opponent level "
               f"of the objective (Lv{level}): {names}.")
    fight_lines = None
    fight = next_fight(state)
    if fight and effectiveness and party:
        types = fight["types"]
        label = "/".join(types)
        dual = bool(fight.get("dual_type"))
        fight_lines = []
        for mon in party:
            moves = [move for move in mon.get("moves") or [] if move.get("power") and move.get("pp", 1)]
            if moves:
                best = max(_move_score(move.get("type"), types, effectiveness, dual) for move in moves)
                attack = f"best move vs {label} is {_effect_word(best)}"
            else:
                attack = "has no damaging moves"
            threat = max((effectiveness(typing, mon.get("types") or []) for typing in types), default=1)
            fight_lines.append(f"{mon.get('species')} Lv{mon.get('level')}: {attack}; {label} moves are {_effect_word(threat)} against it")
    losses = []
    for place, loss in (state.get("losses") or {}).items():
        lineup = loss.get("lineup") or []
        team = ", ".join(lineup) if isinstance(lineup, list) else str(lineup)
        losses.append(f"All Pokémon fainted at {place} {loss.get('count', 1)} time(s); team at the last one: {team}.")
    blackout = None
    if state.get("blackout"):
        blackout = (f"Pokémon Center in {state['blackout']}. A wipe sends you there and halves your money.")
    report = field_move_report(state, species, items)
    facts = {"party_health": health, "poke_balls": balls, "strongest_level": max((mon.get("level", 0) for mon in party), default=0),
             "team_size": f"{len(party)}/6"}
    gyms = {}
    for gym in story():
        if event := gym.get("reward_event"):
            badge = complete(gym["success"], {"badges": state.get("badges", 0)})
            received = event in (state.get("events") or [])
            if badge or received or state.get("map") in gym["maps"]:
                # Receipt flags survive using a TM; a full bag can delay the gift after a win.
                gyms[gym["maps"][0]] = {"badge_earned": badge, "tm_received": received, "reward_event": event}
    if gyms:
        facts["gym_checkpoints"] = gyms
    if gap:
        facts["level_gap"] = gap
    if fight_lines:
        facts["team_vs_fight"] = fight_lines
    if losses:
        facts["losses"] = losses
    if blackout:
        facts["blackout"] = blackout
    dialog = [line for line in (state.get("recent_dialog") or []) if line][-6:]
    if dialog:
        facts["recent_dialog"] = dialog
    if report:
        facts["field_move"] = report["text"]
        if report["withdraw"]:
            facts["withdraw_field_move"] = True
    return facts


def current_milestone(state):
    m = next((m for m in story() if not complete(m["success"], state)), None)
    if m is None:
        return None
    bag = {i["name"] for i in state["bag"] if i["qty"] > 0}
    need = next((n for n in m.get("needs", []) if not bag.intersection(n["items"])
                 and not set(n.get("events", [])).intersection(state.get("events") or [])
                 and not state.get(n.get("done_flag"))), None)
    return {**m, "missing_need": need}


def intent_key(state):
    """Situation signature. Small HP changes do not count; a faint of half the team does."""
    party = state.get("party") or []
    hp = sum(mon.get("hp", 0) for mon in party)
    total = max(1, sum(mon.get("max_hp", 0) for mon in party))
    fainted = sum(mon.get("hp", 0) == 0 for mon in party)
    band = "many" if party and fainted >= -(-len(party) // 2) else "few"
    species = ",".join(mon.get("species", "") for mon in party)
    levels = sum(mon.get("level", 0) for mon in party) // 5
    bag = ",".join(item["name"] for item in state.get("bag") or [])
    milestone = (state.get("milestone") or {}).get("id") or ""
    field = state.get("field_move_needed") or ""
    health = "low" if hp / total < 0.25 else "ok"
    return f"{health}|{band}|{species}:{levels}|{state.get('badges')}|{milestone}|{bag}|{field}"


def intent_options(state, shop_money=None, objective_hops=None):
    """Focuses Jev may pick. Impossible ones are left out, the same way an unusable item is."""
    party = state.get("party") or []
    bag = state.get("bag") or []
    balls = sum(item.get("qty", 0) for item in bag if str(item.get("name", "")).endswith("BALL"))
    money = state.get("money") or 0
    box = state.get("box") or []
    healing = [item for item in bag if re.search(r"POTION|FRESH WATER|SODA POP|LEMONADE|FULL RESTORE|REVIVE", item["name"])]
    carried = ", ".join(f"{item['name']} x{item['qty']}" for item in healing) or "none"
    heal_note = f" Healing items in the bag: {carried} (they heal without a walk to a Pokémon Center)."
    options = dict(INTENTS)
    options["catch"] = (f"{INTENTS['catch']} Team size {len(party)}/6. Poké Balls in bag: {balls}."
                        + (" Catching needs a Poké Ball: with none in the bag, wild Pokémon can only be fought." if balls == 0 else "")
                        + heal_note)
    options["shop"] = (f"{INTENTS['shop']} Money: ¥{money}. Prices: Poké Ball ¥200, Potion ¥300 (heals 20 HP), "
                       f"Super Potion ¥700 (heals 50 HP), Antidote ¥100.{heal_note}")
    options["heal"] = f"{INTENTS['heal']} Healing at a Pokémon Center is free.{heal_note}"
    options["train"] = f"{INTENTS['train']} Beating trainers also earns money."
    gap = (state.get("situation") or {}).get("level_gap")
    if gap:
        options["train"] += " " + gap
        options["catch"] = options.get("catch", INTENTS["catch"]) + " " + gap
    if objective_hops is not None:
        options["progress"] = f"{INTENTS['progress']} From here the objective is {objective_hops} area(s) away."
    if balls == 0 and money < 200:
        options.pop("catch", None)
    if money < 100 or (shop_money is not None and money < shop_money + 200):
        options.pop("shop", None)
    if box or len(party) > 2:
        shown = ", ".join(
            f"{mon.get('species', '?')} Lv{mon.get('level', '?')}"
            + (f" ({'/'.join(mon['types'])})" if mon.get("types") else "")
            for mon in box[:6])
        options["team"] = f"{INTENTS['team']} In the box: {shown}."
    else:
        options.pop("team", None)
    blocked = None
    if state.get("recovery"):
        blocked = (" Moving on toward the objective isn't offered right now: this team lost here repeatedly. "
                   "It comes back after a different lineup, a new move, or five levels gained across the party.")
    elif state.get("field_move_needed") and objective_hops is None:
        blocked = (f" Moving on toward the objective isn't offered right now: it can't be reached without "
                   f"{state['field_move_needed']}, which nobody on the team knows yet.")
    if blocked:
        options.pop("progress", None)
        for name in options:
            options[name] += blocked
    if not options:
        options["explore"] = INTENTS["explore"]
    return options


def focus_note(focus, text):
    """Mark an action that serves the focus Jev just chose. Other actions stay available."""
    pattern = _FOCUS_MARK.get(focus or "")
    if not pattern or "Matches your current focus." in text or not pattern.search(text):
        return text
    return text + " Matches your current focus."


def fallback_goal(state):
    """A temporary story task when the planner is unavailable; never claims completion."""
    m = state.get("milestone")
    if m:
        if need := m.get("missing_need"):
            return Goal(need["what"], "progress", need["map"],
                        {"kind": "any", "value": [{"kind": "item", "value": name} for name in need["items"]]
                         + [{"kind": "event", "value": name} for name in need.get("events", [])]}, 20)
        condition = m["success"]
        # The fallback is internal and can use compound story checks.
        return Goal(m["goal"], "progress", m["maps"][0], condition, 10)
    return Goal("Explore this area", "explore", state["map"],
                {"kind": "interaction", "value": "uncompleted-fallback"}, 10)

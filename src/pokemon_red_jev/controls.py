"""Menu mechanics and battle options. The model selects; code presses buttons."""

import re

from .navigation import Action

PHYSICAL = {"NORMAL", "FIGHTING", "FLYING", "POISON", "GROUND", "ROCK", "BUG", "GHOST"}
HEAL = {"POTION": 20, "SUPER POTION": 50, "HYPER POTION": 200, "MAX POTION": 999, "FULL RESTORE": 999,
        "FRESH WATER": 50, "SODA POP": 60, "LEMONADE": 80}
# Gen 1 stat-stage ratios, stage 1..13. Stage 7 is unchanged.
STAGE = [(25, 100), (28, 100), (33, 100), (40, 100), (50, 100), (66, 100), (1, 1),
         (15, 10), (2, 1), (25, 10), (3, 1), (35, 10), (4, 1)]
SHOP_FACTS = [(re.compile(r"BALL$"), "Catches wild Pokémon."),
              (re.compile(r"^POTION$"), "Heals 20 HP."), (re.compile(r"^SUPER POTION$"), "Heals 50 HP."),
              (re.compile(r"^HYPER POTION$"), "Heals 200 HP."), (re.compile(r"^ANTIDOTE$"), "Cures poison."),
              (re.compile(r"^PARLYZ HEAL$"), "Cures paralysis."), (re.compile(r"^BURN HEAL$"), "Cures a burn."),
              (re.compile(r"^AWAKENING$"), "Wakes a sleeping Pokémon."), (re.compile(r"^ICE HEAL$"), "Cures freezing."),
              (re.compile(r"^FULL HEAL$"), "Cures any status problem."), (re.compile(r"^REVIVE$"), "Revives a fainted Pokémon to half HP."),
              (re.compile(r"^ESCAPE ROPE$"), "Escapes a cave or dungeon."), (re.compile(r"REPEL$"), "Keeps weak wild Pokémon away for a while.")]


def catch_chance(ball, catch_rate, hp, max_hp, status):
    """Rough Gen 1 chance that one thrown ball catches. Information for the choice, not a simulator."""
    if "MASTER" in ball:
        return 1
    roll_max = 201 if "GREAT" in ball else 151 if "ULTRA" in ball or "SAFARI" in ball else 256
    status_bonus = 25 if status in {"SLEEP", "FREEZE"} else 0 if status == "OK" else 12
    ball_factor = 8 if "GREAT" in ball else 12
    f = min(255, ((max_hp * 255) // ball_factor) // max(1, hp // 4))
    p_status = status_bonus / roll_max
    p_rate = max(0, min(1, (min(catch_rate, roll_max - 1 - status_bonus) + 1) / roll_max))
    return min(1, p_status + (1 - p_status) * p_rate * ((f + 1) / 256))


def hit_chance(accuracy, accuracy_stage, evasion_stage):
    """Current hit percent after accuracy and evasion stages. None when the printed accuracy still applies."""
    if not accuracy or not 1 <= accuracy_stage <= 13 or not 1 <= evasion_stage <= 13 or (accuracy_stage == 7 and evasion_stage == 7):
        return None
    attack_n, attack_d = STAGE[accuracy_stage - 1]
    evade_n, evade_d = STAGE[13 - evasion_stage]
    return max(1, min(100, round(accuracy * (attack_n / attack_d) * (evade_n / evade_d))))


def shop_note(rows, label, bag):
    """Price printed on a shop screen, plus what the item is for."""
    screen = " / ".join(re.sub(r"[┌─┐│└┘]", " ", row).strip() for row in rows if str(row).strip())
    if not re.search(r"BUY|MONEY", screen):
        return ""
    note = ""
    fact = next((text for pattern, text in SHOP_FACTS if pattern.search(label)), "")
    if fact:
        owned = next((item["qty"] for item in bag if item["name"] == label), 0)
        note = f"{fact} You have {owned}."
    if label in {"BUY", "SELL", "QUIT"} or label not in screen or "BUY" not in screen:
        return note
    match = re.search(re.escape(label) + r"(?:(?!<ED>|¥).){0,40}(?:<ED>|¥)(\d+)", screen)
    if not match:
        return note
    full = len(bag) >= 20 and not any(item["name"] == label for item in bag)
    price = f"Costs ¥{match[1]}."
    if full:
        price += " The bag is full (20 of 20 item slots): a new kind of item can't be bought until a slot is freed."
    return f"{note} {price}".strip()


FIELD_MOVES = ("CUT", "SURF", "STRENGTH", "FLY", "FLASH")


def menu_note(rows, label, state, pc_mode=None):
    """What a menu label does: teaching, tossing, a mansion switch, or a PC deposit."""
    screen = " / ".join(re.sub(r"[┌─┐│└┘]", " ", row).strip() for row in rows if str(row).strip())
    flat = re.sub(r"\s*/\s*", " ", screen)
    notes = []
    if label == "FLASH":
        notes.append("Lights up a dark cave.")
    elif label == "FLY":
        notes.append("Fly to a town already visited.")
    teach = re.search(r"Teach ([A-Z0-9 .'-]+?) to a POK", flat)
    if teach and label == "YES":
        notes.append(f"Pick a Pokémon to learn {teach[1]}.")
    elif teach and label == "NO":
        notes.append(f"Don't teach {teach[1]} now; the TM goes back into the bag.")
    learn = re.search(r"room for ([A-Z0-9 .'-]+?)\?", flat) or re.search(r"Abandon learning ([A-Z0-9 .'-]+?)\?", flat)
    if learn and "move to make room" in flat:
        if label == "YES":
            notes.append(f"Pick one of the current moves to forget; {learn[1]} takes its place.")
        elif label == "NO":
            notes.append(f"Don't forget a move; the game then asks whether to give up learning {learn[1]}.")
    elif learn and "Abandon learning" in flat:
        if label == "YES":
            notes.append(f"Give up on {learn[1]} and keep the current four moves.")
        elif label == "NO":
            notes.append(f"Don't give up; the game goes back to asking whether to forget a move to make room for {learn[1]}.")
    if "Press it?" in flat:
        if label == "YES":
            notes.append("Presses the switch: all gates in this building flip.")
        elif label == "NO":
            notes.append("Leaves the switch and the gates as they are.")
    if re.search(r"OK to toss", flat, re.I):
        slots = len(state.get("bag") or [])
        if label == "YES":
            notes.append(f"Throws the item away for good and frees a bag slot (the bag holds {slots} of 20).")
        elif label == "NO":
            notes.append(f"Keeps the item; the bag stays at {slots} of 20 slots.")
    if pc_mode == "DEPOSIT":
        party = state.get("party") or []
        mon = next((pokemon for pokemon in party if pokemon.get("nickname") == label), None)
        if mon:
            known = [move["name"] for move in mon.get("moves") or [] if move.get("name") in FIELD_MOVES]
            details = []
            for move in known:
                others = [other.get("nickname") or other.get("species") for other in party if other is not mon
                          and any(item.get("name") == move for item in other.get("moves") or [])]
                details.append(f"{move} ({', '.join(others)} also knows it)" if others else f"{move} (no other team member knows it)")
            sentence = "Picking it DEPOSITS it: it leaves the team and goes into the PC box."
            notes.append(sentence if not details else sentence + " Knows field moves: " + "; ".join(details) + ".")
    return " ".join(notes)


def move_list_open(rows):
    """The battle move list is open; the main Fight menu is underneath it."""
    return any("TYPE/" in row for row in rows)


def party_unusable(rows, party_size):
    """Every party row is marked NOT ABLE, so choosing anyone does nothing."""
    if not party_size:
        return False
    marks = [rows[i * 2 + 1] if i * 2 + 1 < len(rows) else "" for i in range(party_size)]
    return any("ABLE" in row for row in marks) and all("NOT ABLE" in row for row in marks)


def note_menu(seen, screen, progress, limit=5):
    """Count identical menus that change nothing. The fifth one should be closed."""
    if seen and seen[0] == progress and seen[1] == screen:
        count = seen[2] + 1
    else:
        count = 1
    return (progress, screen, 0 if count >= limit else count), count >= limit


def pc_summary(state):
    """Who is on the team and in the current box. Does not choose either."""
    party, box = state.get("party") or [], state.get("box") or []
    shown = [f"{mon.get('species', '?')} Lv{mon.get('level', '?')}" for mon in box[:6]]
    if len(box) > 6:
        shown.append(f"+{len(box) - 6} more")
    text = f"Party {len(party)}/6. Box: {', '.join(shown) if shown else 'empty'}."
    if len(party) >= 6 and box:
        text += " Team is full, so deposit someone before withdrawing."
    elif len(party) == 1:
        text += " The last team member has to stay out."
    return text


def damage(player, enemy, move, effectiveness):
    if not move["power"] or not effectiveness:
        return 0
    physical = move["type"] in PHYSICAL
    attack = player["attack"] if physical else player["special"]
    defense = enemy["defense"] if physical else enemy["special"]
    base = ((2 * player["level"] // 5 + 2) * move["power"] * attack // max(1, defense)) // 50 + 2
    return int(base * (1.5 if move["type"] in player["types"] else 1) * effectiveness)


class Controls:
    def __init__(self, game):
        self.game = game

    def find(self, label):
        screen = self.game.screen()
        hits = []
        for y, cells in enumerate(screen["cells"]):
            row = "".join(cells).upper()
            index = row.find(label.upper())
            if index >= 0:
                columns = [x for x, cell in enumerate(cells) for _ in cell]
                hits.append((columns[index], y))
        cur = screen["cursor"] or (0, 17)
        return min(hits, key=lambda p: abs(p[1] - cur[1]) * 4 + abs(p[0] - cur[0])) if hits else None

    def select(self, label, index=None):
        g = self.game
        for _ in range(32):
            g.settle_screen()
            cur = g.screen()["cursor"]
            pos = self.find(label)
            if not cur or not g.menu_ready():
                g.tick(5)
                continue
            if index is not None:
                now = g.u8("wCurrentMenuItem")
                if now == index:
                    return self.confirm()
                g.press("up" if now > index else "down", settle=6)
            elif pos:
                x, y = pos
                if cur[1] == y and abs(cur[0] - (x - 1)) <= 1:
                    return self.confirm()
                direction = "up" if cur[1] > y else "down" if cur[1] < y else "left" if cur[0] > x - 1 else "right"
                g.press(direction, settle=6)
            else:
                g.press("down", settle=6)
        return False

    def confirm(self):
        g = self.game
        for _ in range(3):
            before = g.screen()["rows"]
            g.press("a", 4, 40)
            if g.screen()["rows"] != before:
                g.settle_screen()
                return True
        return False

    def leave_menu(self):
        """Press B until the menu or text box is gone."""
        g = self.game
        for _ in range(8):
            screen = g.screen()
            if g.mode() == "overworld" or not (screen["cursor"] or screen["box"]):
                return
            g.press("b", settle=12)

    def _item_box_open(self):
        return any("TOSS" in row for row in self.game.screen()["rows"])

    def _confirm_item_box(self):
        """Wait until USE / TOSS is drawn. The bag ignores A for a moment after it opens."""
        g = self.game
        g.tick(20)
        for _ in range(2):
            if self._item_box_open():
                return True
            before = g.screen()["rows"]
            g.press("a", 4, 5)
            for _ in range(18):
                g.tick(5)
                if self._item_box_open():
                    return True
                if g.screen()["rows"] != before:
                    break
        return self._item_box_open()

    def options(self):
        g = self.game
        s = g.screen()
        if not s["cursor"]:
            return []
        party = g.party()
        if party and s["cursor"][0] == 0 and s["rows"][0][3:].startswith(party[0]["nickname"]):
            return [(p["nickname"], i) for i, p in enumerate(party)]
        y, x, last = g.u8("wTopMenuItemY"), g.u8("wTopMenuItemX"), g.u8("wMaxMenuItem")
        def clean(text):
            return re.split(r"\s{2,}", re.sub(r"[┌─┐│└┘▶▷▼]", " ", text).strip())[0]
        if s["cursor"][0] == x and last < 12:
            for step in [2, 1]:
                options = [(clean("".join(s["cells"][y + i * step][x + 1:])), i)
                           for i in range(last + 1) if y + i * step < 18]
                if len(options) == last + 1 and all(re.search(r"[A-Za-z]{2}|^-|^B?\d+F$", t) for t, _ in options) and len({t for t, _ in options}) == len(options):
                    return options
        col, cy = s["cursor"]
        top, bottom = cy, cy
        def in_box(row):
            return s["cells"][row][col] in {"│", " ", "▶", "▷"}
        while top > 0 and in_box(top - 1):
            top -= 1
        while bottom < 17 and in_box(bottom + 1):
            bottom += 1
        return [(text, None) for row in s["cells"][top:bottom + 1]
                if re.search(r"[A-Za-z]{2}|^B?\d+F$", text := clean("".join(row[col + 1:])))]

    def actions(self, state):
        g = self.game
        g.settle_screen()
        screen = g.screen()
        state["screen"] = screen["rows"]
        rows = " ".join(screen["rows"])
        if any("A B C D E F G H I" in r.replace("▶", " ") for r in screen["rows"]):
            typed = screen["rows"][2][10:].strip(" _")
            limit = 10 if "NICKNAME" in rows else 7
            choices = []
            for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if len(typed) < limit else "":
                pos = next(((x, y) for y in range(4, 16) for x, cell in enumerate(screen["cells"][y]) if cell == ch), None)
                if pos:
                    choices.append(Action(f"letter:{ch}", f"Append {ch} to the name {typed!r}. Use a short made-up nickname.", "letter", target={"x": pos[0] - 1, "y": pos[1]}))
            if typed:
                choices.append(Action("name:done", f"Finish the name {typed!r}.", "button", target={"button": "start"}))
            return choices
        if screen["cursor"] and not screen["waiting"] and not g.menu_ready():
            return [Action("wait", "Wait for the menu to accept input.", "wait")]
        if state["mode"] == "battle" and screen["cursor"] and self.find("RUN") and (self.find("FIGHT") or state["battle"].get("safari")):
            return self.battle_actions(state)
        if screen["waiting"] or not screen["cursor"]:
            return [Action("advance", "Advance dialogue or wait for the animation.", "button", target={"button": "a"})]
        actions = []
        for i, (label, index) in enumerate(self.options()):
            facts = label
            pokemon = next((p for p in state["party"] if p["nickname"] == label), None)
            if pokemon:
                if state["mode"] == "battle" and pokemon["hp"] == 0:
                    continue
                if screen["cursor"][0] == 0 and "NOT ABLE" in screen["rows"][pokemon["slot"] * 2 + 1]:
                    continue
                facts += f". {pokemon['species']} Lv{pokemon['level']} HP {pokemon['hp']}/{pokemon['max_hp']}; moves {[m['name'] for m in pokemon['moves']]}"
            if "nickname" in rows.lower() and label == "YES":
                facts += ". Gives this Pokémon a nickname."
            if label == "RELEASE":
                facts += ". Permanently gives away this Pokémon."
            if label == "DEPOSIT":
                facts += ". Moves a team member into the box. " + pc_summary(state)
            if label == "WITHDRAW":
                facts += ". Moves a boxed Pokémon onto the team. " + pc_summary(state)
            if "BILL" in label and "PC" in label:
                facts += ". Pokémon storage. " + pc_summary(state)
            if label == "BUY":
                facts += f". Money {state['money']}; bag slots {len(state['bag'])}/20. Buy only needed supplies."
            machine = next((item for item, name in g.rom.items.items() if name == label and item >= 0xc4), None)
            if machine is not None:
                machine_index, move = g.rom.machine(machine)
                if move:
                    compatible = {sp["name"] for sp in g.rom.species.values()
                                  if sp["machines"][machine_index // 8] & (1 << (machine_index % 8))}
                    learners = [p["nickname"] for p in state["party"] if p["species"] in compatible]
                    facts += f". Teaches {move['name']}. Compatible team: {', '.join(learners) or 'none'}."
            if label in HEAL:
                facts += f". Restores up to {HEAL[label]} HP; owned quantity " + str(next((i["qty"] for i in state["bag"] if i["name"] == label), 0))
            if label == "TOSS":
                facts += ". Discards the item permanently."
            nav = getattr(self, "navigation", None)
            pc_mode = getattr(getattr(nav, "memory", None), "pc_mode", None) if nav else None
            extra = shop_note(screen["rows"], label, state["bag"])
            context = menu_note(screen["rows"], label, state, pc_mode)
            if context:
                extra = f"{extra} {context}".strip()
            if nav and re.fullmatch(r"B?\d{1,2}F", label):
                extra = f"{extra} {nav.floor_choice(state, label)}".strip()
            if extra:
                facts += ". " + extra
            actions.append(Action(f"menu:{i}", facts, "menu", target={"label": label, "index": index}))
        forced_party = state["mode"] == "battle" and state["battle"]["player"]["hp"] == 0
        if not forced_party:
            actions.append(Action("cancel", "Press B to close or back out of this menu.", "button", target={"button": "b"}))
        if any("▼" in row or "<CONT>" in row for row in screen["rows"][:12]):
            actions.append(Action("scroll", "Scroll down to more list entries.", "button", target={"button": "down"}))
        return actions

    def battle_actions(self, state):
        b = state["battle"]
        options = []
        if b.get("safari"):
            enemy = b.get("enemy") or {}
            rate = enemy.get("catch_rate", 0)
            if enemy and hasattr(self.game, "u8"):
                rate = self.game.u8("wEnemyMonActualCatchRate")
            ball = f"Throw a Safari Ball; {b['safari_balls']} remaining."
            if enemy:
                chance = round(100 * catch_chance("SAFARI BALL", rate, enemy.get("hp", 1), enemy.get("max_hp", 1), enemy.get("status", "OK")))
                ball += f" Estimated catch chance ~{chance}% at catch rate {rate}/255."
            return [Action(f"safari:{label}", description, "menu", target={"label": label}) for label, description in
                    [("BALL", ball),
                     ("BAIT", "Throw bait: reduces fleeing and makes catching harder."),
                     ("THROW ROCK", "Throw a rock: improves catching and increases fleeing."),
                     ("RUN", "Leave this Safari encounter.")]]
        accuracy_stage = evasion_stage = 7
        if hasattr(self.game, "u8"):
            accuracy_stage = self.game.u8("wPlayerMonAccuracyMod")
            evasion_stage = self.game.u8("wEnemyMonEvasionMod")
        for move in b["moves"]:
            if not move["pp"]:
                continue
            eff = self.game.rom.effectiveness(move["type"], b["enemy"]["types"])
            high = damage(b["player"], b["enemy"], move, eff)
            current_hit = hit_chance(move["accuracy"], accuracy_stage, evasion_stage)
            hit_note = f" Hit chance right now about {current_hit}%." if current_hit not in (None, move["accuracy"]) else ""
            desc = f"Use {move['name']}: {move['type']}, power {move['power']}, PP {move['pp']}, accuracy {move['accuracy']}%, type multiplier {eff}.{hit_note}"
            # Special effects don't follow the ordinary power formula.
            special = {"SEISMIC TOSS": f"Level-based damage: {b['player']['level']} HP.",
                       "NIGHT SHADE": f"Level-based damage: {b['player']['level']} HP.",
                       "SONIC BOOM": "Fixed damage: 20 HP.", "DRAGON RAGE": "Fixed damage: 40 HP.",
                       "SUPER FANG": "Removes half the enemy's remaining HP.",
                       "PSYWAVE": "Random damage up to 1.5 times your level.",
                       "BIDE": "Waits 2–3 turns, then returns twice the damage taken.",
                       "COUNTER": "Returns twice the NORMAL/FIGHTING damage taken this turn; otherwise fails.",
                       **{m: "One-hit KO; fails against faster enemies. Low accuracy." for m in ("FISSURE", "HORN DRILL", "GUILLOTINE")}}
            if move["name"] in special:
                desc += " " + special[move["name"]] + " Ordinary damage estimate does not apply."
            elif move["power"]:
                low = high * 217 // 255
                hp = b["enemy"]["hp"]
                effect = " Likely KO." if low >= hp else " High damage." if hp and round(high / hp * 100) > 50 else ""
                desc += f" Rough damage {low}-{high}.{effect} Ignores critical hits and special effects. Enemy HP {hp}."
            else:
                desc += " Status move (no direct damage)."
            options.append(Action(f"move:{move['slot']}", desc, "battle_move", target=move))
        for p in state["party"]:
            if p["hp"] and p["slot"] != b["active_slot"]:
                options.append(Action(f"switch:{p['slot']}", f"Switch to {p['nickname']} ({p['species']}, Lv{p['level']}, HP {p['hp']}/{p['max_hp']}). Uses a turn.", "switch", target=p))
        for item in state["bag"]:
            if item["name"] in HEAL and b["player"]["hp"] < b["player"]["max_hp"]:
                options.append(Action(f"item:{item['name']}", f"Use {item['name']} to heal up to {HEAL[item['name']]} HP. Quantity {item['qty']}. Uses a turn.", "battle_item", target={"name": item["name"], "slot": b["active_slot"]}))
            if item["name"].endswith("BALL") and b["kind"] == "wild":
                options.append(Action(f"ball:{item['name']}", self._catch_text(state, b, item), "battle_item", target={"name": item["name"]}))
            cures = {"ANTIDOTE": {"POISON"}, "BURN HEAL": {"BURN"}, "ICE HEAL": {"FREEZE"},
                     "AWAKENING": {"SLEEP"}, "PARLYZ HEAL": {"PARALYZED"},
                     "FULL HEAL": {"POISON", "BURN", "FREEZE", "SLEEP", "PARALYZED"}}
            if b["player"]["status"] in cures.get(item["name"], set()):
                options.append(Action(f"cure:{item['name']}", f"Cure {b['player']['status']} with {item['name']}. Uses a turn.", "battle_item", target={"name": item["name"], "slot": b["active_slot"]}))
            if item["name"] in {"REVIVE", "MAX REVIVE"}:
                for p in state["party"]:
                    if not p["hp"]:
                        options.append(Action(f"revive:{p['slot']}", f"Use {item['name']} on fainted {p['nickname']}. Uses a turn.", "battle_item", target={"name": item["name"], "slot": p["slot"]}))
        if b["kind"] == "wild":
            options.append(Action("run", "Attempt to escape this wild battle.", "menu", target={"label": "RUN"}))
            if state["map"].startswith("POKEMON_TOWER_") and "SILPH SCOPE" not in {i["name"] for i in state["bag"]}:
                return [Action("run", "Flee the unidentified ghost. Without the Silph Scope the party cannot fight it and it dodges balls.", "menu", target={"label": "RUN"})]
        if not options:
            options.append(Action("struggle", "Fight with no PP remaining; the game uses STRUGGLE.", "menu", target={"label": "FIGHT"}))
        return options

    def _catch_text(self, state, battle, item):
        enemy, party = battle["enemy"], state["party"]
        chance = round(100 * catch_chance(item["name"], enemy.get("catch_rate", 0), enemy["hp"], enemy["max_hp"], enemy["status"]))
        dex = enemy.get("dex") or 0
        owned = self.game.owned(dex) if dex and hasattr(self.game, "owned") else False
        species_note = "You already own this species." if owned else "NEW species you don't own yet."
        team_types = {typing for mon in party for typing in mon.get("types", [])}
        missing = [typing for typing in enemy.get("types", []) if typing not in team_types]
        type_note = f"Its type(s) {'/'.join(missing)} are not on your team yet." if missing else "Your team already has its type(s)."
        levels = [mon.get("level", 0) for mon in party]
        weaker = sum(level < enemy.get("level", 0) for level in levels)
        weakest = min(party, key=lambda mon: mon.get("level", 0)) if party else None
        level_note = f"Its level {enemy.get('level', '?')} is higher than {weaker} of your {len(party)} team members."
        if weakest and enemy.get("level", 0) > weakest.get("level", 0):
            level_note += f" Higher level than your weakest, {weakest.get('nickname', weakest.get('species', '?'))} Lv{weakest.get('level', '?')}."
        full = " Your team is full: a caught Pokémon goes to the PC box." if len(party) >= 6 else ""
        return (f"Throw {item['name']} to try catching the wild {enemy['species']}. Estimated catch chance ~{chance}%. "
                f"{species_note} {type_note} {level_note}{full} Quantity {item['qty']}.")

    def execute(self, action):
        g, t = self.game, action.target
        if action.kind == "button":
            g.press(t["button"], 4, 45)
        elif action.kind == "wait":
            g.tick(12)
        elif action.kind == "menu":
            return self.select(t["label"], t.get("index"))
        elif action.kind == "letter":
            for _ in range(24):
                cur = g.screen()["cursor"]
                if cur is None:
                    return False
                if cur == (t["x"], t["y"]):
                    g.press("a", settle=6)
                    return True
                direction = "up" if cur[1] > t["y"] else "down" if cur[1] < t["y"] else "left" if cur[0] > t["x"] else "right"
                g.press(direction, settle=6)
            return False
        elif action.kind == "battle_move":
            return self.select("FIGHT") and self.select(t["name"])
        elif action.kind == "switch":
            return self.select("PKMN") and self.select(t["nickname"], t["slot"]) and self.select("SWITCH")
        elif action.kind == "battle_item":
            if self.select("ITEM") and self.select(t["name"]):
                return "slot" not in t or self.select(g.party()[t["slot"]]["nickname"], t["slot"])
            return False
        elif action.kind == "item":
            g.press("start", settle=20)
            if not (self.select("ITEM") and self.select(t["name"])):
                return False
            if self._confirm_item_box():
                return self.select("USE")
            return True
        elif action.kind == "toss":
            g.press("start", settle=20)
            if not self.select("ITEM"):
                return False
            name = t.get("name") or ""
            if name and name != "BAG":
                if not self.select(name) or not self._confirm_item_box() or not self.select("TOSS"):
                    return False
                g.press("a", settle=20)
            return True
        elif action.kind in {"bag", "party", "field"}:
            if action.kind == "field" and "direction" in t:
                g.face(t["direction"])
            g.press("start", settle=20)
            if action.kind == "bag":
                return self.select("ITEM")
            if not self.select("POKéMON"):
                return False
            if action.kind == "field":
                p = next(p for p in g.party() if any(m["name"] == t["move"] for m in p["moves"]))
                return self.select(p["nickname"], p["slot"]) and self.select(t["move"])
        return True

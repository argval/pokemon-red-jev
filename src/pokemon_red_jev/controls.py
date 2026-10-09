"""Menu mechanics and battle options. The model selects; code presses buttons."""

import re

from .battle import FIXED, CONDITIONAL, damage, damage_range, damaging, move_failure, move_name, switch_stats
from .goals import next_gym
from .healing import HEAL, field_heal_allowed
from .navigation import FLY_TOWNS, Action

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


def escape_chance(player_speed, enemy_speed, attempts=0):
    """Chance the next Run succeeds. `attempts` is how many tries have already failed.

    The game escapes for certain when the player's speed is at least the enemy's.
    Otherwise it scores (player speed * 32) / (enemy speed / 4), then adds 30 for each
    earlier failed try. The first try does not get that bonus. A roll of 0–255 succeeds
    when it is less than or equal to that score.
    """
    if player_speed >= enemy_speed:
        return 1
    divisor = (enemy_speed // 4) & 0xFF
    if divisor == 0:
        return 1
    score = (player_speed * 32) // divisor
    if score > 255:
        return 1
    score += 30 * attempts
    if score > 255:
        return 1
    return (score + 1) / 256


# Move effect ids from pret/pokered constants/move_effect_constants.asm.
EFFECTS = {
    0x02: "May poison.", 0x03: "Drains some of the damage as HP.", 0x04: "May burn.",
    0x05: "May freeze.", 0x06: "May paralyze.", 0x07: "The user faints.",
    0x08: "Hits only a sleeping target and drains HP.", 0x09: "Copies the last move used on the user.",
    0x0A: "Raises the user's Attack.", 0x0B: "Raises the user's Defense.", 0x0C: "Raises the user's Speed.",
    0x0D: "Raises the user's Special.", 0x0E: "Raises the user's accuracy.", 0x0F: "Raises the user's evasion.",
    0x10: "Scatters coins.", 0x11: "Never misses.", 0x12: "Lowers the target's Attack.",
    0x13: "Lowers the target's Defense.", 0x14: "Lowers the target's Speed.", 0x15: "Lowers the target's Special.",
    0x16: "Lowers the target's accuracy.", 0x17: "Lowers the target's evasion.",
    0x18: "User becomes the type of one of its moves.", 0x19: "Resets every stat change.",
    0x1A: "Waits 2–3 turns, then returns twice the damage taken.",
    0x1B: "Attacks for 2–3 turns, then confuses the user.",
    0x1C: "Can end a wild battle. Fails against a trainer.",
    0x1D: "Hits 2–5 times.", 0x1F: "May cause flinching.",
    0x20: "Puts the target to sleep.", 0x21: "May poison.", 0x22: "May burn.", 0x24: "May paralyze.",
    0x25: "May cause flinching.", 0x26: "One-hit KO. Fails against a faster target. Low accuracy.",
    0x27: "Charges on the first turn, then hits.", 0x28: "Removes half the target's remaining HP.",
    0x29: "Deals fixed or level-based damage.", 0x2A: "Traps the target for 2–5 turns so it cannot run or switch.",
    0x2B: "Vanishes for a turn, then hits.", 0x2C: "Hits twice.",
    0x2D: "If it misses, the user is hurt.", 0x2E: "Blocks stat drops while it lasts.",
    0x2F: "Lowers the user's critical-hit rate in this game.", 0x30: "The user takes recoil.",
    0x31: "Confuses the target.", 0x32: "Sharply raises the user's Attack.", 0x33: "Sharply raises the user's Defense.",
    0x34: "Sharply raises the user's Speed.", 0x35: "Sharply raises the user's Special.",
    0x36: "Sharply raises the user's accuracy.", 0x37: "Sharply raises the user's evasion.",
    0x38: "Restores HP.", 0x39: "Copies the target's species, stats, and moves.",
    0x3A: "Sharply lowers the target's Attack.", 0x3B: "Sharply lowers the target's Defense.",
    0x3C: "Sharply lowers the target's Speed.", 0x3D: "Sharply lowers the target's Special.",
    0x3E: "Sharply lowers the target's accuracy.", 0x3F: "Sharply lowers the target's evasion.",
    0x40: "Halves special damage against the user for a while.",
    0x41: "Halves physical damage against the user for a while.",
    0x42: "Poisons the target.", 0x43: "Paralyzes the target.",
    0x44: "May lower the target's Attack.", 0x45: "May lower the target's Defense.",
    0x46: "May lower the target's Speed.", 0x47: "May lower the target's Special.",
    0x4C: "May confuse.", 0x4D: "Hits twice and may poison.",
    0x4F: "Spends a quarter of max HP to put up a substitute.",
    0x50: "The user recharges next turn if it hits.",
    0x51: "Attack rises when hit, and the user keeps using it.",
    0x52: "Copies one of the target's moves until switched out.", 0x53: "Uses a random move.",
    0x54: "Saps HP from the target each turn.", 0x55: "Does nothing.",
    0x56: "Disables one of the target's moves for a while.",
}
NAMED_EFFECTS = {
    "SEISMIC TOSS": "Damage equals the user's level.", "NIGHT SHADE": "Damage equals the user's level.",
    "SONIC BOOM": "Deals 20 HP.", "DRAGON RAGE": "Deals 40 HP.",
    "SUPER FANG": "Removes half the target's remaining HP.",
    "PSYWAVE": "Random damage, up to 1.5 times the user's level.",
    "REST": "Fully heals, then falls asleep.", "RECOVER": "Restores half of max HP.",
    "SOFTBOILED": "Restores half of max HP.",
    "QUICK ATTACK": "Usually strikes before a normal move.",
    "COUNTER": "Usually strikes last. Returns twice the NORMAL or FIGHTING damage taken this turn.",
}


STATS = ("Attack", "Defense", "Speed", "Special", "Accuracy", "Evasion")


def stage_note(effect, u8):
    """Stat stages run 1..13 (7 is neutral). A stat move at the limit does nothing; say so."""
    for first, side, limit in ((0x12, "Enemy", 1), (0x3A, "Enemy", 1), (0x0A, "Player", 13), (0x32, "Player", 13)):
        if first <= effect < first + 6:
            stat = STATS[effect - first]
            if u8(f"w{side}Mon{stat}Mod") == limit:
                return f" {'Its' if side == 'Enemy' else 'Your'} {stat} can't go any further: using it again does nothing."
    return ""


def describe_effect(move):
    """What a move does beyond its printed power, in words a battle choice can use."""
    named = NAMED_EFFECTS.get(move.get("name"))
    if named:
        return named
    return EFFECTS.get(move.get("effect"), "")


STATUS_NOTE = {
    "BURN": "burned: it loses HP every turn, and its physical attacks are weaker.",
    "POISON": "poisoned: it loses HP every turn, and also while you walk.",
    "PARALYZED": "paralyzed: it is slower and may be unable to move.",
    "SLEEP": "asleep: it cannot move until it wakes.",
    "FREEZE": "frozen: it cannot move until it thaws.",
}


def status_note(name):
    """What a non-OK status is doing. Empty when the Pokémon is fine."""
    return STATUS_NOTE.get(name or "OK", "")


def attack_types(party):
    """Types the team can attack with, from species and from moves."""
    found = []
    for mon in party:
        found.extend(mon.get("types") or [])
        found.extend(move.get("type") for move in mon.get("moves") or [] if move.get("type"))
    return found


def gym_match(attack_types, defense_types, effectiveness):
    """Which of these attack types the type chart calls strong or resisted."""
    strong, resisted = [], []
    for typing in dict.fromkeys(attack_types):
        score = effectiveness(typing, defense_types)
        if score > 1:
            strong.append(typing)
        elif score < 1:
            resisted.append(typing)
    return strong, resisted


def gym_sentence(state, attack_types, effectiveness, subject):
    """How these types line up with the next gym. Empty before the story has a gym."""
    gym = next_gym(state)
    if not gym:
        return ""
    leader, defense = gym["leader"], gym["types"]
    shown = "/".join(defense)
    if not attack_types or effectiveness is None:
        return f"Next gym is {leader} ({shown})."
    strong, resisted = gym_match(attack_types, defense, effectiveness)
    if strong:
        return f"{subject} is strong into the next gym, {leader} ({shown}), via {', '.join(strong)}."
    if resisted:
        return f"{subject} is resisted by the next gym, {leader} ({shown})."
    return f"{subject} is neutral into the next gym, {leader} ({shown})."


def leave_shop_fact(money):
    """QUIT on a mart counter. Say so when another Poké Ball is unaffordable."""
    text = "Leave the shop and return to the map."
    if isinstance(money, int) and money < 200:
        text += f" You have ¥{money}. A Poké Ball costs ¥200, so another ball is unaffordable."
    return text


def quantity_actions(state, text):
    """The mart's how-many box. Up and down change the count; A buys; B returns to the list."""
    count = re.search(r"(?:x|×)\s*(\d+)", text, re.I)
    price = re.search(r"(?:¥|<ED>)\s*(\d+)", text[count.end():]) if count else None
    qty = count.group(1) if count else "1"
    cost = price.group(1) if price else "?"
    money = state.get("money")
    afford = ""
    if isinstance(money, int) and cost.isdigit():
        afford = f" You have ¥{money}, which is not enough." if money < int(cost) else f" You have ¥{money}."
    return [
        Action("buy:yes", f"Buy {qty} of the selected item for ¥{cost}.{afford}", "button", target={"button": "a"}),
        Action("buy:more", "Increase how many you buy.", "button", target={"button": "up"}),
        Action("buy:less", "Decrease how many you buy.", "button", target={"button": "down"}),
        Action("buy:no", "Do not buy it. Returns to the item list.", "button", target={"button": "b"}),
    ]


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
    if label == "RELEASE":
        notes.append("Permanently lets this Pokémon go. It is gone for good.")
    elif label == "CHANGE BOX":
        notes.append("Switch to another PC box.")
    elif label in {"WITHDRAW ITEM", "DEPOSIT ITEM"}:
        notes.append("Item storage, not Pokémon.")
    elif label == "TOSS ITEM":
        notes.append("Throw away a stored item. It is gone for good.")
    elif "OAK" in label and label.endswith("PC"):
        notes.append("Rates your Pokédex progress.")
    elif label.endswith("PC") and "BILL" not in label and "SOMEONE" not in label:
        notes.append("Item storage: store and take out items. No Pokémon here.")
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
    """Count returning menus, including cycles through submenus and cursor movement."""
    counts = seen[1] if seen and seen[0] == progress else {}
    screen = tuple(" ".join(row.replace("▶", " ").replace("▷", " ").split()) for row in screen)
    count = counts.get(screen, 0) + 1
    counts[screen] = 0 if count >= limit else count
    if len(counts) > 200:
        del counts[next(iter(counts))]
    return (progress, counts), count >= limit


def party_item_note(mon, item, move_name, unable):
    """What using the open bag item would do to this party Pokémon."""
    notes = []
    if unable:
        notes.append("NOT ABLE to use this item (choosing it does nothing).")
    level = mon.get("level")
    if item == "RARE CANDY" and isinstance(level, int):
        notes.append(f"Rare Candy would raise it from Lv{level} to Lv{level + 1}.")
    if move_name and any(move.get("name") == move_name for move in mon.get("moves") or []):
        notes.append(f"Already knows {move_name}: choosing it does nothing.")
    return " ".join(notes)


def pc_summary(state):
    """Who is on the team and in the current box. Does not choose either."""
    party, box = state.get("party") or [], state.get("box") or []
    shown = [f"{mon.get('species', '?')} Lv{mon.get('level', '?')}"
             + (f" ({'/'.join(mon['types'])})" if mon.get("types") else "") for mon in box[:6]]
    if len(box) > 6:
        shown.append(f"+{len(box) - 6} more")
    text = f"Party {len(party)}/6. Box: {', '.join(shown) if shown else 'empty'}."
    if len(party) >= 6 and box:
        text += " Team is full, so deposit someone before withdrawing."
    elif len(party) == 1:
        text += " The last team member has to stay out."
    return text


def race(hp, enemy_hp, mine, theirs, first):
    """Who faints first if both sides repeat their best hit. mine/theirs are (low, high) damage.

    ponytail: average damage only; ignores accuracy, crits, stat stages, and enemies picking weaker moves.
    """
    hits = lambda target, low, high: -(-target * 2 // (low + high)) if high else None
    need, survive = hits(enemy_hp, *mine), hits(hp, *theirs)
    if need is None:
        return ""
    if survive is None:
        return f" Knocks it out in about {need} hit(s); the enemy has no known direct damage."
    wins = need < survive or (need == survive and first)
    return (f" Knocks it out in about {need} hit(s); its best attack knocks you out in about {survive}, "
            f"and {'you move' if first else 'it moves'} first: "
            f"{'you likely win this exchange' if wins else 'you likely faint first'}.")


class Controls:
    def __init__(self, game):
        self.game = game
        self._menu_wait = None

    def find(self, label):
        screen = self.game.screen()
        hits = []
        want = label.upper()
        for y, cells in enumerate(screen["cells"]):
            row = "".join(cells).upper()
            index = self._item_at(row, want)
            if index >= 0:
                columns = [x for x, cell in enumerate(cells) for _ in cell]
                hits.append((columns[index], y))
        cur = screen["cursor"] or (0, 17)
        return min(hits, key=lambda p: abs(p[1] - cur[1]) * 4 + abs(p[0] - cur[0])) if hits else None

    @staticmethod
    def _item_at(row, label):
        """Match the whole item. REVIVE is not the REVIVE inside MAX REVIVE."""
        longer = {"MAX", "SUPER", "HYPER", "FULL", "GREAT", "ULTRA"}
        start = 0
        while True:
            index = row.find(label, start)
            if index < 0:
                return -1
            if index > 1 and row[index - 1] == " " and row[index - 2] != " ":
                end = index - 1
                begin = end
                while begin > 0 and row[begin - 1].isalpha():
                    begin -= 1
                if row[begin:end] in longer:
                    start = index + 1
                    continue
            return index

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
            label = re.split(r"\s{2,}", re.sub(r"[┌─┐│└┘▶▷▼]", " ", text).strip())[0]
            return re.sub(r"^(WITHDRAW|DEPOSIT|RELEASE) PKMN$", r"\1", label)
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
        if state["mode"] == "battle" and callable(getattr(g, "snapshot", None)):
            catchable = (state.get("battle") or {}).get("catchable", False)
            state.update(g.snapshot())
            if state.get("battle"):
                state["battle"]["catchable"] = catchable
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
        # Options has its own joypad loop, outside HandleMenuInput, so menu_ready never becomes true there.
        if state["mode"] != "battle" and self._options_open(rows):
            self._menu_wait = None
            return [Action("options", "Set text speed to FAST, battle animations OFF, then close Options.", "options")]
        if "TAKE YOUR TIME" in rows.upper() and re.search(r"(?:x|×)\s*\d+", rows, re.I) and not screen["waiting"]:
            return quantity_actions(state, rows)
        if screen["cursor"] and not screen["waiting"] and not g.menu_ready():
            plain = tuple(row.replace("▶", " ").replace("▷", " ") for row in screen["rows"])
            self._menu_wait, stuck = note_menu(self._menu_wait, plain, None, limit=40)
            if stuck:
                return [Action("cancel", "Back out of a menu that is no longer accepting input.", "button", target={"button": "b"})]
            return [Action("wait", "Wait for the menu to accept input.", "wait")]
        self._menu_wait = None
        if "NEW GAME" in rows and "OPTION" in rows and g.u8("wOptions") & 0x8f != 0x81:
            return [Action("options:open", "Open Options to set fast text and turn battle animations off.", "menu", target={"label": "OPTION"})]
        if state["mode"] == "battle" and screen["cursor"] and self.find("RUN") and (self.find("FIGHT") or state["battle"].get("safari")):
            return self.battle_actions(state)
        if screen["waiting"] or not screen["cursor"]:
            return [Action("advance", "Advance dialogue or wait for the animation.", "button", target={"button": "a"})]
        actions = []
        labels = self.options()
        fly_open = sum(label in FLY_TOWNS for label, _ in labels) >= 2
        for i, (label, index) in enumerate(labels):
            if label == "OPTION" and "NEW GAME" in rows:
                continue
            if label in FIELD_MOVES and "be forgotten?" in rows:
                continue  # the game refuses to forget an HM and reopens this menu, so picking one loops forever
            facts = label
            if label == "NEW GAME":
                facts = "Start a new game."
            elif label == "CONTINUE":
                facts = "Continue the saved game."
            pokemon = next((p for p in state["party"] if p["nickname"] == label), None)
            if pokemon:
                if state["mode"] == "battle" and pokemon["hp"] == 0:
                    continue
                if (state["mode"] != "battle" and state.get("using_item") in HEAL
                        and not field_heal_allowed(state, pokemon)):
                    continue
                unable = screen["cursor"][0] == 0 and pokemon["slot"] * 2 + 1 < len(screen["rows"]) and "NOT ABLE" in screen["rows"][pokemon["slot"] * 2 + 1]
                facts += f". {pokemon['species']} Lv{pokemon['level']} HP {pokemon['hp']}/{pokemon['max_hp']}; moves {[m['name'] for m in pokemon['moves']]}"
                move_name = None
                using = state.get("using_item")
                if using and hasattr(g, "rom"):
                    machine = next((item for item, name in g.rom.items.items() if name == using and item >= 0xc4), None)
                    taught = g.rom.machine(machine)[1] if machine is not None else None
                    move_name = taught["name"] if taught else None
                item_note = party_item_note(pokemon, using, move_name, unable)
                if item_note:
                    facts += ". " + item_note
            if "nickname" in rows.lower() and label == "YES":
                facts += ". Gives this Pokémon a nickname."
            if label == "DEPOSIT":
                facts += ". Moves a team member into the box. " + pc_summary(state)
            if label == "WITHDRAW":
                facts += ". Moves a boxed Pokémon onto the team. " + pc_summary(state)
            if ("BILL" in label or "SOMEONE" in label) and "PC" in label:
                facts += ". Pokémon storage. " + pc_summary(state)
            if label == "BUY":
                facts += f". Money {state['money']}; bag slots {len(state['bag'])}/20. Buy only needed supplies."
            if label == "QUIT" and "BUY" in rows and "SELL" in rows:
                facts = leave_shop_fact(state.get("money"))
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
            if nav and fly_open:
                extra = f"{extra} {nav.fly_choice(state, label)}".strip()
            if extra:
                facts += ". " + extra
            target = {"label": label, "index": index}
            roster = state.get("party") if pc_mode == "DEPOSIT" else state.get("box") if pc_mode == "WITHDRAW" else []
            if roster and index is not None:
                # Both PC lists scroll: the cursor reaches only the top three entries.
                slot = index + g.u8("wListScrollOffset")
                if slot < len(roster) and roster[slot].get("nickname") == label:
                    target.update(pc_mode=pc_mode, pc_slot=slot, pc_count=len(roster))
            actions.append(Action(f"menu:{i}", facts, "menu", target=target))
        forced_party = state["mode"] == "battle" and state["battle"]["player"]["hp"] == 0
        # B on the title menu returns to the intro and resets text speed and battle animations.
        # A mart counter already has QUIT. B there only redraws the same menu.
        shop = "BUY" in rows and "SELL" in rows and "QUIT" in rows
        if not forced_party and "NEW GAME" not in rows and "NEW NAME" not in rows and not shop:
            actions.append(Action("cancel", "Press B to close or back out of this menu.", "button", target={"button": "b"}))
        if shop and not any((a.target or {}).get("label") == "QUIT" for a in actions):
            actions.append(Action("shop:quit", leave_shop_fact(state.get("money")), "menu", target={"label": "QUIT"}))
        slots = [(a.target["pc_slot"], a.target["pc_count"]) for a in actions if "pc_slot" in a.target]
        # The ▼ blinks. A PC list with entries past the last labeled slot can always scroll.
        if any("▼" in row or "<CONT>" in row for row in screen["rows"][:12]) or slots and max(slots)[0] + 1 < slots[0][1]:
            actions.append(Action("scroll", "Scroll down to more list entries.", "button", target={"button": "down"}))
        return actions

    def battle_actions(self, state):
        b = state["battle"]
        options = []
        blocked = []
        b["move_facts"], b["unavailable_moves"] = [], []
        b.pop("fallback_move", None)
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
        screens = 0
        disabled = -1
        if hasattr(self.game, "u8"):
            accuracy_stage = self.game.u8("wPlayerMonAccuracyMod")
            evasion_stage = self.game.u8("wEnemyMonEvasionMod")
            screens = self.game.u8("wPlayerBattleStatus3")
            # High nibble is the disabled slot, 1-4. Picking it reopens the move menu without spending a turn,
            # so the Disable never wears off and the same pick repeats forever.
            disabled = (self.game.u8("wPlayerDisabledMove") >> 4) - 1
        incoming = self._threat(b["enemy"], b["player"])
        uncertain = self._uncertain_threat(b["enemy"], b["player"])
        theirs = incoming[1::-1] if incoming else (0, 0)
        first = (b["player"].get("speed") or 0) > (b["enemy"].get("speed") or 0)
        usable = [move for move in b["moves"] if move["pp"] and move["slot"] != disabled]
        for move in usable:
            eff = self.game.rom.effectiveness(move["type"], b["enemy"]["types"])
            estimate = self._estimate(b["player"], b["enemy"], move)
            reason = move_failure(move, b["player"], b["enemy"], b["kind"])
            if not reason and damaging(move) and estimate == (0, 0):
                reason = "This attack cannot damage the target with its current types and stats."
            current_hit = hit_chance(move["accuracy"], accuracy_stage, evasion_stage)
            if ((b["player"].get("volatile") or {}).get("x_accuracy") or move.get("effect") == 0x11
                    or move.get("effect") == 0x20 and (b["enemy"].get("volatile") or {}).get("recharge")):
                current_hit = 100
            hit_note = f" Hit chance right now about {current_hit}%." if current_hit not in (None, move["accuracy"]) else ""
            desc = f"Use {move['name']}: {move['type']}, power {move['power']}, PP {move['pp']}, accuracy {move['accuracy']}%.{hit_note}"
            if damaging(move):
                if move_name(move) not in FIXED | CONDITIONAL:
                    desc += f" Damage type multiplier {eff}."
                else:
                    desc += " Red's special damage rules apply instead of the ordinary type multiplier."
            if damaging(move) and estimate is not None:
                low, high = estimate
                hp = b["enemy"]["hp"]
                spoiled = " Knocks it out, so it can no longer be caught." if low >= hp and b["kind"] == "wild" and b.get("catchable") else ""
                effect = " Likely KO." if low >= hp else " High damage." if hp and round(high / hp * 100) > 50 else ""
                desc += f" Rough damage {low}-{high} on a hit.{effect}{spoiled} Ignores critical hits. Enemy HP {hp}."
                if uncertain:
                    desc += " Enemy conditional damage makes the exchange uncertain."
                elif move.get("effect") not in {0x26, 0x27, 0x2B, 0x50} and move_name(move) != "SUPERFANG":
                    moves_first = True if move_name(move) == "QUICKATTACK" else first
                    desc += race(b["player"]["hp"], hp, estimate, theirs, moves_first)
            elif damaging(move):
                desc += " Damage depends on the moves and damage taken during the turn."
            else:
                desc += " Status move (no direct damage)."
                if screens & {"REFLECT": 4, "LIGHT SCREEN": 2}.get(move["name"], 0):
                    desc += " Already in effect: using it again does nothing."
                elif hasattr(self.game, "u8"):
                    desc += stage_note(move.get("effect") or 0, self.game.u8)
            does = move.get("does") or describe_effect(move)
            if does:
                desc += " " + does
            if reason:
                desc += f" Cannot work now: {reason}"
                b["unavailable_moves"].append({"slot": move["slot"], "name": move["name"], "reason": reason})
            hit = current_hit or move["accuracy"]
            b["move_facts"].append({"slot": move["slot"], "name": move["name"], "damage_range": estimate,
                                    "hit_percent": hit, "blocked_reason": reason})
            action = Action(f"move:{move['slot']}", desc, "battle_move",
                            target={**move, "damage_range": estimate, "hit_percent": hit, "blocked_reason": reason})
            (blocked if reason and not state.get("manual_control") else options).append(action)
        for p in state["party"]:
            if p["hp"] and p["slot"] != b["active_slot"]:
                p = switch_stats(p)
                attacks = [m for m in p["moves"] if damaging(m) and m["pp"]]
                best = max((self.game.rom.effectiveness(m["type"], b["enemy"]["types"]) for m in attacks), default=None)
                threat = max((self.game.rom.effectiveness(t, p["types"]) for t in b["enemy"]["types"]), default=1)
                listed = self._bench_moves(p, b["enemy"])
                offense = listed or (f"Best usable damaging move type multiplier {best} against the enemy." if best is not None else "No damaging moves with PP remaining.")
                speed = ""
                if isinstance(p.get("speed"), int) and isinstance(b["enemy"].get("speed"), int):
                    speed = f" Speed {p['speed']} vs the enemy's {b['enemy']['speed']}."
                if status_note(p.get("status")):
                    speed = f" It is {status_note(p['status'])}{speed}"
                options.append(Action(f"switch:{p['slot']}", f"Switch to {p['nickname']} ({p['species']}, Lv{p['level']}, {'/'.join(p['types'])}, HP {p['hp']}/{p['max_hp']}). {offense}{speed} Enemy type attacks have a maximum type multiplier of {threat} against it. Uses a turn.{self._switch_cost(p, b['enemy'])}", "switch", target=p))
        for item in state["bag"]:
            if item["name"] in HEAL and b["player"]["hp"] < b["player"]["max_hp"]:
                options.append(Action(f"item:{item['name']}", f"Use {item['name']} to heal up to {HEAL[item['name']]} HP. Quantity {item['qty']}. Uses a turn.", "battle_item", target={"name": item["name"], "slot": b["active_slot"]}))
            if item["name"].endswith("BALL") and b["kind"] == "wild":
                options.append(Action(f"ball:{item['name']}", self._catch_text(state, b, item), "battle_item", target={"name": item["name"]}))
            cures = {"ANTIDOTE": {"POISON"}, "BURN HEAL": {"BURN"}, "ICE HEAL": {"FREEZE"},
                     "AWAKENING": {"SLEEP"}, "PARLYZ HEAL": {"PARALYZED"},
                     "FULL HEAL": {"POISON", "BURN", "FREEZE", "SLEEP", "PARALYZED"}}
            if b["player"]["status"] in cures.get(item["name"], set()):
                cure = status_note(b["player"]["status"])
                options.append(Action(f"cure:{item['name']}", f"Cure {b['player']['status']} with {item['name']}. {cure} Uses a turn.".replace("  ", " "), "battle_item", target={"name": item["name"], "slot": b["active_slot"]}))
            if item["name"] in {"REVIVE", "MAX REVIVE"}:
                for p in state["party"]:
                    if not p["hp"]:
                        options.append(Action(f"revive:{item['name']}:{p['slot']}", f"Use {item['name']} on fainted {p['nickname']}, restoring {'full' if item['name'] == 'MAX REVIVE' else 'half'} HP. Uses a turn.", "battle_item", target={"name": item["name"], "slot": p["slot"]}))
        if b["kind"] == "wild":
            options.append(Action("run", "Attempt to escape this wild battle.", "menu", target={"label": "RUN"}))
            if state["map"].startswith("POKEMON_TOWER_") and "SILPH SCOPE" not in {i["name"] for i in state["bag"]}:
                return [Action("run", "Flee the unidentified ghost. Without the Silph Scope the party cannot fight it and it dodges balls.", "menu", target={"label": "RUN"})]
        # Struggle depends on remaining PP outside the disabled move. Switching, items, or running do not replace it.
        if not usable:
            options.append(Action("struggle", "Fight with no usable PP remaining; the game uses STRUGGLE.", "menu", target={"label": "FIGHT"}))
        if blocked:
            b["fallback_move"] = {"key": blocked[0].key, "description": blocked[0].description,
                                  "target": blocked[0].target}
        if not options:
            options.extend(self.battle_fallback(state))
        outlook = self._outlook(state, b)
        if outlook:
            b["outlook"] = outlook
            for option in options:
                option.description += " " + outlook
        return options

    def battle_fallback(self, state):
        """Keep a legal last resort if later automatic filters remove every alternative."""
        fallback = (state.get("battle") or {}).get("fallback_move")
        if not fallback:
            return []
        text = fallback["description"] + " No useful alternative remains. Spend this PP until the game permits Struggle."
        return [Action(fallback["key"], text, "battle_move", target=fallback["target"])]

    def _estimate(self, attacker, defender, move):
        """Apply the ROM's type entries in order, preserving Red's integer rounding."""
        rom = self.game.rom
        chart = getattr(rom, "type_chart", None)
        multipliers = ([value for (attack, defense), value in chart.items()
                        if attack == move.get("type") and defense in defender.get("types", [])]
                       if isinstance(chart, dict) else None)
        return damage_range(attacker, defender, move,
                            rom.effectiveness(move.get("type"), defender.get("types") or []), multipliers=multipliers)

    def _byte(self, name):
        if not hasattr(self.game, "u8"):
            return None
        try:
            value = self.game.u8(name)
        except (KeyError, TypeError, AttributeError):
            return None
        return value if isinstance(value, int) else None

    def _bench_moves(self, mon, enemy):
        lines = []
        for move in mon.get("moves") or []:
            if not move.get("pp"):
                lines.append(f"{move['name']} has no PP.")
                continue
            lines.append(self._move_detail(mon, enemy, move))
        return " ".join(lines)

    def _move_detail(self, attacker, defender, move):
        does = move.get("does") or describe_effect(move)
        reason = move_failure(move, attacker, defender)
        if reason:
            return f"{move['name']}: cannot work now. {reason}"
        if not damaging(move):
            text = f"{move['name']}: {move.get('type', '?')}, PP {move.get('pp')}, no direct damage."
            return f"{text} {does}".strip()
        eff = self.game.rom.effectiveness(move["type"], defender.get("types") or [])
        text = (f"{move['name']}: {move.get('type', '?')}, power {move['power']}, PP {move.get('pp')}, "
                f"accuracy {move.get('accuracy')}%.")
        text += (" Red's special damage rules ignore ordinary type multipliers."
                 if move_name(move) in FIXED | CONDITIONAL else f" Damage type multiplier {eff}.")
        estimate = self._estimate(attacker, defender, move)
        if estimate is not None:
            low, high = estimate
            hp = defender.get("hp")
            effect = " Likely KO." if hp and low >= hp else " High damage." if hp and round(high / hp * 100) > 50 else ""
            text += f" Rough damage {low}-{high}.{effect}"
        if does:
            text += " " + does
        return text

    def _known(self, moves):
        bits = []
        for move in moves:
            does = move.get("does") or describe_effect(move)
            text = f"{move['name']} ({move.get('type', '?')}"
            if move.get("power"):
                text += f" power {move['power']}"
            text += f", PP {move.get('pp', '?')})"
            if does:
                text += f" {does}"
            bits.append(text)
        return "; ".join(bits)

    def _threat(self, enemy, defender):
        """The enemy's hardest known hit on defender, as (high, low, move name), or None."""
        best = None
        for move in enemy.get("moves") or []:
            if not damaging(move) or not move.get("pp", 1):
                continue
            estimate = self._estimate(enemy, defender, move)
            if estimate is None:
                continue
            low, high = estimate
            if best is None or high > best[0]:
                best = (high, low, move["name"])
        return best

    def _uncertain_threat(self, enemy, defender):
        """Unknown Counter/Bide damage cannot be treated as a harmless enemy turn."""
        return any(damaging(move) and move.get("pp", 1) and self._estimate(enemy, defender, move) is None
                   for move in enemy.get("moves") or [])

    def _switch_cost(self, mon, enemy):
        """Switching hands the enemy a free hit on the incoming Pokémon. Then its best move races the enemy."""
        hit = self._threat(enemy, mon)
        uncertain = self._uncertain_threat(enemy, mon)
        if not hit:
            return " Enemy conditional damage makes the switch-in risk uncertain." if uncertain else ""
        text = f" The enemy gets a free hit as it comes in: its {hit[2]} does about {hit[1]}-{hit[0]} of its {mon['hp']} HP"
        if hit[1] >= mon["hp"]:
            return text + ", enough to knock it out before it acts."
        if uncertain:
            return text + ". Counter or Bide may change this damage; the exchange is uncertain."
        best = None
        if mon.get("attack") and mon.get("special"):
            for move in mon.get("moves") or []:
                if damaging(move) and move.get("pp"):
                    estimate = self._estimate(mon, enemy, move)
                    if estimate is not None and (best is None or estimate[1] > best[0]):
                        best = (estimate[1], move["name"], estimate)
        if not best:
            return text + "."
        left = max(1, mon["hp"] - (hit[0] + hit[1]) // 2)
        first = (mon.get("speed") or 0) > (enemy.get("speed") or 0)
        return text + f". Then with {best[1]}:" + race(left, enemy.get("hp", 0), best[2], hit[1::-1], first)

    def _incoming(self, battle):
        player = battle["player"]
        best = self._threat(battle["enemy"], player)
        if not best:
            return ""
        note = f"Its {best[2]} is about {best[1]}-{best[0]} damage"
        hp = player.get("hp")
        if hp:
            note += f" against your {hp} HP"
            if best[1] >= hp:
                note += ", enough to knock you out"
        return note + "."

    def _outlook(self, state, battle):
        player, enemy = battle["player"], battle["enemy"]
        parts = [f"Enemy {enemy.get('species', '?')} Lv{enemy.get('level', '?')}, {'/'.join(enemy.get('types') or [])}, "
                 f"HP {enemy.get('hp', '?')}/{enemy.get('max_hp', '?')}, status {enemy.get('status', 'OK')}. "
                 f"Attack {enemy.get('attack', '?')}, Defense {enemy.get('defense', '?')}, "
                 f"Special {enemy.get('special', '?')}, Speed {enemy.get('speed', '?')}."]
        p_speed, e_speed = player.get("speed"), enemy.get("speed")
        if isinstance(p_speed, int) and isinstance(e_speed, int):
            if p_speed > e_speed:
                parts.append(f"You act first (speed {p_speed} vs {e_speed}).")
            elif e_speed > p_speed:
                parts.append(f"The enemy acts first (speed {e_speed} vs {p_speed}).")
            else:
                parts.append(f"Speed is tied at {p_speed}; either side may move first.")
            parts.append("Priority moves can change this order.")
            if battle.get("kind") == "wild":
                attempts = self._byte("wNumRunAttempts") or 0
                chance = escape_chance(p_speed, e_speed, attempts)
                if chance >= 1:
                    parts.append(f"Escape succeeds (your speed {p_speed}, enemy speed {e_speed}).")
                else:
                    parts.append(f"Escape chance about {round(100 * chance)}% "
                                 f"(your speed {p_speed}, enemy speed {e_speed}, failed tries so far {attempts}). "
                                 "A failed escape spends the turn.")
        known = self._known(enemy.get("moves") or [])
        if known:
            incoming = self._incoming(battle)
            parts.append(f"Enemy knows {known.rstrip('.')}." + (f" {incoming}" if incoming else ""))
        if battle.get("kind") == "trainer":
            parts.append("Trainer battle: running is impossible.")
        else:
            errand = (state.get("active_goal") or {}).get("goal")
            if errand:
                parts.append(f"Current errand: {errand}.")
        yours, theirs = status_note(player.get("status")), status_note(enemy.get("status"))
        if yours:
            parts.append(f"Your active Pokémon is {yours}")
        if theirs:
            ball = ""
            if battle.get("kind") == "wild":
                ball = " A ball is much more likely to work." if enemy.get("status") in {"SLEEP", "FREEZE"} else " A ball is more likely to work."
            parts.append(f"The enemy is {theirs}{ball}")
        effectiveness = getattr(getattr(self.game, "rom", None), "effectiveness", None)
        gym = next_gym(state)
        if gym and effectiveness:
            subject = enemy.get("species") or "The enemy"
            line = gym_sentence(state, enemy.get("types") or [], effectiveness, subject)
            if line:
                parts.append(line)
            strong, _ = gym_match(attack_types(state.get("party") or []), gym["types"], effectiveness)
            covered = ", ".join(strong) if strong else ""
            parts.append(f"The team already has {covered} into {gym['leader']}." if covered
                         else f"The team has nothing strong into {gym['leader']} ({'/'.join(gym['types'])}).")
        return " ".join(parts)

    def _apply_options(self):
        """Set FAST text and battle animations OFF, then leave.

        The options menu ignores a new button for 30 frames after each press, so a
        quicker sequence changes one setting and never registers B.
        """
        g = self.game
        for _ in range(8):
            if not self._options_open(" ".join(g.screen()["rows"])):
                return
            y, options = g.u8("wTopMenuItemY"), g.u8("wOptions")
            fast, anim_off = (options & 0x0f) == 1, bool(options & 0x80)
            if not fast:
                g.press("left" if y == 3 else "up", 4, 32)
            elif not anim_off:
                g.press("right" if y == 8 else "down" if y < 8 else "up", 4, 32)
            else:
                g.press("b", 4, 32)

    def _options_open(self, rows):
        if "TEXT SPEED" in rows and "BATTLE" in rows:
            return True
        probe = getattr(self.game, "in_routine", None)
        try:
            return bool(probe and probe("DisplayOptionMenu", "TextSpeedOptionData"))
        except (KeyError, AttributeError):
            return False

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
        evolved = self._evo_note(enemy.get("species"))
        evolved = f" {evolved}" if evolved else ""
        return (f"Throw {item['name']} to try catching the wild {enemy['species']}. Estimated catch chance ~{chance}%. "
                f"{species_note} {type_note} {level_note}{full}{evolved} Quantity {item['qty']}.")

    def _evo_note(self, name):
        species = getattr(getattr(self.game, "rom", None), "species", None)
        if not isinstance(species, dict):
            return ""
        record = next((sp for sp in species.values() if isinstance(sp, dict) and sp.get("name") == name), None)
        if not record:
            return ""
        bits = []
        for evo in record.get("evolutions") or []:
            if evo.get("method") == "level":
                bits.append(f"level {evo.get('level')}")
            elif evo.get("method") == "item":
                bits.append("a stone")
            elif evo.get("method") == "trade":
                bits.append("a trade")
        return f"Evolves by {' or '.join(bits)}." if bits else ""

    def execute(self, action):
        g, t = self.game, action.target
        if action.kind == "button":
            g.press(t["button"], 4, 45)
        elif action.kind == "options":
            self._apply_options()
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
            if t.get("direction"):
                g.face(t["direction"])
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

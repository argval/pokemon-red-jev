"""ROM tables and read-only WRAM observations, ported from jev-pokemon."""

import hashlib
from pathlib import Path
import time

from .controls import describe_effect
from .data import Data, RED_SHA1
from .goals import current_milestone

TYPES = dict(zip([0, 1, 2, 3, 4, 5, 7, 8, 20, 21, 22, 23, 24, 25, 26],
                 ["NORMAL", "FIGHTING", "FLYING", "POISON", "GROUND", "ROCK", "BUG", "GHOST",
                  "FIRE", "WATER", "GRASS", "ELECTRIC", "PSYCHIC", "ICE", "DRAGON"]))


class Rom:
    def __init__(self, path: Path, data: Data):
        self.b = path.read_bytes()
        if hashlib.sha1(self.b).hexdigest() != RED_SHA1:
            raise ValueError("ROM must be Pokémon Red US/EU, SHA-1 " + RED_SHA1)
        self.data = data
        sym = data.sym
        move_names = self.strings(sym("MoveNames"), 165)
        self.moves = {}
        for i, name in enumerate(move_names):
            a = sym("Moves") + i * 6
            self.moves[i + 1] = dict(id=i + 1, name=name, effect=self.b[a + 1], power=self.b[a + 2],
                                     type=TYPES.get(self.b[a + 3], "?"), accuracy=round(self.b[a + 4] / 255 * 100), pp=self.b[a + 5])
        self.items = {i + 1: name for i, name in enumerate(self.strings(sym("ItemNames"), 97))}
        self.items.update({0xc3 + i: f"HM{i:02}" for i in range(1, 6)})
        self.items.update({0xc8 + i: f"TM{i:02}" for i in range(1, 51)})
        self.species = {}
        for i in range(1, 191):
            dex = self.b[sym("PokedexOrder") + i - 1]
            if not dex:
                continue
            a = sym("MewBaseStats") if dex == 151 else sym("BaseStats") + (dex - 1) * 28
            self.species[i] = dict(id=i, dex=dex, name=data.decode(self.b[sym("MonsterNames") + (i - 1) * 10:][:10]),
                                  types=list(dict.fromkeys(TYPES.get(t, "?") for t in self.b[a + 6:a + 8])),
                                  catch_rate=self.b[a + 8], machines=list(self.b[a + 20:a + 27]),
                                  evolutions=self._evolutions(i))
        for species in self.species.values():
            species["stones"] = [e["item"] for e in species["evolutions"] if e["method"] == "item"]
        self.type_chart = {}
        for a in range(sym("TypeEffects"), len(self.b) - 2, 3):
            if self.b[a] == 0xff:
                break
            self.type_chart[TYPES.get(self.b[a]), TYPES.get(self.b[a + 1])] = self.b[a + 2] / 10
        self.maps = {}
        trainers = self.strings(sym("TrainerNames"), 47)
        for mid, meta in data.maps.items():
            if meta["name"].startswith("UNUSED"):
                continue
            bank = self.b[sym("MapHeaderBanks") + mid]
            h = self.flat(bank, self.u16(sym("MapHeaderPointers") + mid * 2))
            tileset, height, width = self.b[h:h + 3]
            blocks, flags = self.u16(h + 3), self.b[h + 9]
            h += 10
            connections = []
            for bit, direction in [(3, "up"), (2, "down"), (1, "left"), (0, "right")]:
                if flags & (1 << bit):
                    connections.append(dict(direction=direction, map=self.b[h],
                                            y_align=int.from_bytes(self.b[h + 7:h + 8], signed=True),
                                            x_align=int.from_bytes(self.b[h + 8:h + 9], signed=True)))
                    h += 11
            o = self.flat(bank, self.u16(h)) + 1
            warps, signs, objects = [], [], []
            n, o = self.b[o], o + 1
            for _ in range(n):
                warps.append(dict(y=self.b[o], x=self.b[o + 1], warp=self.b[o + 2], map=self.b[o + 3]))
                o += 4
            n, o = self.b[o], o + 1
            for _ in range(n):
                signs.append(dict(y=self.b[o], x=self.b[o + 1], text_id=self.b[o + 2]))
                o += 3
            n, o = self.b[o], o + 1
            for i in range(n):
                flag = self.b[o + 5]
                obj = dict(index=i + 1, picture=self.b[o], y=self.b[o + 1] - 4, x=self.b[o + 2] - 4,
                           movement=self.b[o + 3], trainer=bool(flag & 0x40), item=None)
                if flag & 0x40:
                    cls = self.b[o + 6] - 201
                    obj["trainer_class"] = trainers[cls] if 0 <= cls < len(trainers) else "TRAINER"
                    o += 8
                elif flag & 0x80:
                    obj["item"] = self.items.get(self.b[o + 6], "item")
                    o += 7
                else:
                    o += 6
                objects.append(obj)
            self.maps[mid] = dict(id=mid, name=meta["name"], bank=bank, width=width, height=height,
                                  tileset=tileset, blocks=blocks, connections=connections, warps=warps,
                                  signs=signs, objects=objects)
            wild = sym("WildDataPointers")
            encounters = self.flat(wild // 0x4000, self.u16(wild + mid * 2))
            self.maps[mid]["grass_rate"] = self.b[encounters]
            self.maps[mid]["wild"] = [{"level": self.b[a], "species_id": self.b[a + 1]}
                                      for a in range(encounters + 1, encounters + 21, 2)] if self.b[encounters] else []
        self.machines = list(self.b[sym("TechnicalMachines"):sym("TechnicalMachines") + 55])
        self.hidden, self.spinners = {}, {}
        names = {a: n for n, a in data.symbols.items() if a >= 0x4000 and "." not in n}
        maps, pointers = sym("HiddenEventMaps"), sym("HiddenEventPointers")
        i = 0
        while self.b[maps + i] != 0xff:
            entries = []
            a = self.flat(maps // 0x4000, self.u16(pointers + i * 2))
            while self.b[a] != 0xff:
                entries.append(dict(y=self.b[a], x=self.b[a + 1], arg=self.b[a + 2],
                                    fn=names.get(self.flat(self.b[a + 3], self.u16(a + 4)), "Unknown")))
                a += 6
            self.hidden[self.b[maps + i]] = entries
            i += 1
        for symbol, map_name in [("RocketHideout2ArrowTilePlayerMovement", "ROCKET_HIDEOUT_B2F"),
                                 ("RocketHideout3ArrowTilePlayerMovement", "ROCKET_HIDEOUT_B3F"),
                                 ("ViridianGymArrowTilePlayerMovement", "VIRIDIAN_GYM")]:
            a = sym(symbol)
            mid = next(mid for mid, md in self.maps.items() if md["name"] == map_name)
            self.spinners[mid] = {}
            while self.b[a] != 0xff:
                y, x = self.b[a:a + 2]
                p = self.flat(a // 0x4000, self.u16(a + 2))
                tx, ty = x, y
                while self.b[p] != 0xff:
                    dx, dy = {0x10: (1, 0), 0x20: (-1, 0), 0x40: (0, -1), 0x80: (0, 1)}.get(self.b[p], (0, 0))
                    tx, ty = tx + dx * self.b[p + 1], ty + dy * self.b[p + 1]
                    p += 2
                self.spinners[mid][x, y] = (tx, ty)
                a += 4

    def _evolutions(self, species_id):
        """Evolution requirements and destination species from EvosMovesPointerTable."""
        try:
            table = self.data.sym("EvosMovesPointerTable")
        except KeyError:
            return []
        addr = self.flat(table // 0x4000, self.u16(table + (species_id - 1) * 2))
        evolutions = []
        for _ in range(8):
            if addr >= len(self.b) or self.b[addr] == 0:
                break
            method = self.b[addr]
            if method == 1:
                evolutions.append(dict(method="level", level=self.b[addr + 1], into=self.b[addr + 2]))
                addr += 3
            elif method == 2:
                evolutions.append(dict(method="item", item=self.b[addr + 1], into=self.b[addr + 3]))
                addr += 4
            elif method == 3:
                evolutions.append(dict(method="trade", into=self.b[addr + 2]))
                addr += 3
            else:
                break
        return evolutions

    def machine(self, item):
        index = item - 0xc9 if item >= 0xc9 else 50 + item - 0xc4 if item >= 0xc4 else -1
        return (index, self.moves[self.machines[index]]) if 0 <= index < 55 else (None, None)

    def is_key_item(self, item_id):
        """HMs and KeyItemFlags entries cannot be tossed. TMs can."""
        if 0xc4 <= item_id <= 0xc8:
            return True
        if item_id >= 0xc9:
            return False
        bit = item_id - 1
        flags = self.data.sym("KeyItemFlags")
        return bool(self.b[flags + (bit >> 3)] & (1 << (bit & 7)))

    def is_key_item_name(self, name):
        item_id = next((i for i, item_name in self.items.items() if item_name == name), None)
        return item_id is not None and self.is_key_item(item_id)

    def step_tiles(self, tileset):
        """Tiles that warp as soon as they are stepped on. Edge mats that are not in this set can be stood on to push."""
        cached = getattr(self, "_step_tiles", None)
        if cached is None:
            cached = self._step_tiles = {}
        if tileset not in cached:
            tiles = set()
            table = self.data.sym("WarpTileIDPointers")
            addr = self.flat(table // 0x4000, self.u16(table + tileset * 2))
            while addr < len(self.b) and self.b[addr] != 0xff:
                tiles.add(self.b[addr])
                addr += 1
            table = self.data.sym("DoorTileIDPointers")
            bank, addr = table // 0x4000, table
            while addr + 2 < len(self.b) and self.b[addr] != 0xff:
                if self.b[addr] == tileset:
                    cursor = self.flat(bank, self.u16(addr + 1))
                    while cursor < len(self.b) and self.b[cursor] not in (0, 0xff):
                        tiles.add(self.b[cursor])
                        cursor += 1
                addr += 3
            cached[tileset] = tiles
        return cached[tileset]

    @staticmethod
    def flat(bank, ptr):
        return ptr if ptr < 0x4000 else bank * 0x4000 + ptr - 0x4000

    def u16(self, addr):
        return self.b[addr] | self.b[addr + 1] << 8

    def strings(self, addr, count):
        result = []
        for _ in range(count):
            end = self.b.index(0x50, addr)
            result.append(self.data.decode(self.b[addr:end]))
            addr = end + 1
        return result

    def effectiveness(self, attack, defense):
        result = 1
        for t in defense:
            result *= self.type_chart.get((attack, t), 1)
        return result


def quiet_rows(rows):
    """Menu text with the blinking cursor treated as blank."""
    return tuple(row.replace("▶", " ").replace("▷", " ") for row in rows)


def hm_names(record):
    """HMs this species can be taught, from the ROM compatibility bytes."""
    machines = (record or {}).get("machines") or []
    names = []
    for index in range(5):
        bit = 50 + index
        if bit // 8 < len(machines) and machines[bit // 8] & (1 << (bit % 8)):
            names.append(f"HM{index + 1:02}")
    return names


def status(value):
    if value & 7:
        return "SLEEP"
    return next((name for bit, name in [(8, "POISON"), (16, "BURN"), (32, "FREEZE"), (64, "PARALYZED")] if value & bit), "OK")


class Game:
    def __init__(self, path, data, *, headless=False, speed=1):
        # Import lazily: --demo and the control-loop checks need neither SDL nor a ROM.
        from pyboy import PyBoy
        self.data = data
        self.rom = Rom(path, data)
        # The visible window is ours. PyBoy stays headless so it does not open a second screen.
        self.pyboy = PyBoy(str(path), window="null", sound_emulated=False, no_input=True)
        self.pyboy.set_emulation_speed(speed)
        self.speed = speed
        self.memory = self.pyboy.memory
        self.frames = 0
        self.visited = set()
        self.interactions = set()
        self.last_map = None
        self.outside_map = None
        self.stage = None
        if not headless:
            from .stage import Stage
            try:
                self.stage = Stage(self.rom.b, self.data.sym("FontGraphics"), self.data.charmap)
                self.stage.present(self.pyboy.screen, force=True)
            except BaseException:
                self.close()
                raise

    def tick(self, frames=1):
        if not self.pyboy.tick(frames, True):
            raise KeyboardInterrupt
        self.frames += frames
        if self.stage:
            self.stage.present(self.pyboy.screen, fast=self.speed <= 0)

    def show_overlay(self, state, controller):
        if self.stage:
            self.stage.show(state, controller, self.pyboy.screen)

    def pause(self, seconds):
        """Keep Escape/window close responsive without advancing the emulator during a retry."""
        until = time.monotonic() + seconds
        while (left := until - time.monotonic()) > 0:
            if self.stage:
                self.stage.present(self.pyboy.screen)
            time.sleep(min(.1, left))

    def press(self, button, hold=6, settle=12):
        self.pyboy.button_press(button)
        try:
            self.tick(hold)
        finally:
            self.pyboy.button_release(button)
        self.tick(settle)

    def face(self, direction):
        expected = {"down": 0, "up": 4, "left": 8, "right": 12}[direction]
        for _ in range(4):
            if self.u8("wSpritePlayerStateData1FacingDirection") == expected:
                return
            self.press(direction, 3, 8)

    def settle_screen(self):
        """Wait until the text stops changing. A blinking menu cursor is not a change."""
        previous, stable = None, 0
        for _ in range(120):
            current = quiet_rows(self.screen()["rows"])
            stable = stable + 1 if current == previous else 0
            if stable >= 8:
                return
            previous = current
            self.tick()

    def menu_ready(self):
        lo, hi = self.data.sym("HandleMenuInput"), self.data.sym("PlaceMenuCursor")
        for _ in range(4):
            if self._return_in(lo, hi):
                return True
            self.tick()
        return False

    def in_routine(self, start, end):
        """True when a return address on the stack is inside this routine. Does not advance frames."""
        lo, hi = self.data.sym(start), self.data.sym(end)
        if lo >= 0x4000:
            bank = lo // 0x4000
            if self.u8("hLoadedROMBank") != bank:
                return False
            lo, hi = lo - (bank - 1) * 0x4000, hi - (bank - 1) * 0x4000
        return self._return_in(lo, hi)

    def _return_in(self, lo, hi):
        sp = self.pyboy.register_file.SP
        for a in range(sp, min(sp + 24, 0xffff), 2):
            ret = self.memory[a] | self.memory[a + 1] << 8
            if lo <= ret < hi:
                return True
        return False

    def settle_map(self):
        """Map IDs change before the destination's collision grid and sprites load."""
        self.tick(45)
        previous, stable = None, 0
        for _ in range(360):
            key = tuple(self.u8(name) for name in ("wCurMap", "wCurMapWidth", "wCurMapHeight", "wXCoord", "wYCoord", "wCurMapTileset"))
            stable = stable + 1 if key == previous and not self.u8("wWalkCounter") else 0
            if stable >= 24:
                return
            previous = key
            self.tick()

    def u8(self, name, offset=0):
        return self.memory[self.data.sym(name) + offset]

    def _optional_u8(self, name, default=0):
        """Read added battle facts without requiring a newer generated symbol set."""
        try:
            value = self.u8(name)
        except (KeyError, AttributeError, TypeError, IndexError):
            return default
        return value if isinstance(value, int) and 0 <= value <= 255 else default

    def be16(self, addr):
        return self.memory[addr] << 8 | self.memory[addr + 1]

    def owned(self, dex):
        """True when the Pokédex already records this species."""
        if not dex:
            return False
        bit = dex - 1
        return bool(self.memory[self.data.sym("wPokedexOwned") + (bit >> 3)] & (1 << (bit & 7)))

    def moves_at(self, addr, pp):
        result = []
        for i in range(4):
            mid = self.memory[addr + i]
            if mid in self.rom.moves:
                move = self.rom.moves[mid]
                value = self.memory[pp + i]
                entry = {**move, "slot": i, "max_pp": move["pp"] + move["pp"] // 5 * (value >> 6), "pp": value & 63}
                does = describe_effect(entry)
                if does:
                    entry["does"] = does
                result.append(entry)
        return result

    def party(self):
        result = []
        for i in range(min(self.u8("wPartyCount"), 6)):
            a = self.data.sym("wPartyMons") + i * 44
            sp = self.rom.species.get(self.memory[a], {})
            nick = self.data.sym("wPartyMonNicks") + i * 11
            result.append(dict(slot=i, species=sp.get("name", "?"), nickname=self.data.decode(self.memory[nick:nick + 11]),
                               level=self.memory[a + 33], hp=self.be16(a + 1), max_hp=self.be16(a + 34),
                               status=status(self.memory[a + 4]), types=sp.get("types", []), moves=self.moves_at(a + 8, a + 29),
                               attack=self.be16(a + 36), defense=self.be16(a + 38), speed=self.be16(a + 40), special=self.be16(a + 42),
                               experience=(self.memory[a + 14] << 16) | (self.memory[a + 15] << 8) | self.memory[a + 16],
                               learnable_hms=hm_names(sp)))
        return result

    def bag(self):
        a = self.data.sym("wBagItems")
        return [dict(name=self.rom.items.get(self.memory[a + 2 * i], "?"), qty=self.memory[a + 2 * i + 1])
                for i in range(min(20, self.u8("wNumBagItems")))]

    def box(self):
        result = []
        for i in range(min(20, self.u8("wBoxCount"))):
            a = self.data.sym("wBoxMons") + i * 33
            n = self.data.sym("wBoxMonNicks") + i * 11
            sp = self.rom.species.get(self.memory[a], {})
            result.append(dict(slot=i, species=sp.get("name", "?"), species_id=self.memory[a],
                               nickname=self.data.decode(self.memory[n:n + 11]), level=self.memory[a + 3],
                               types=list(sp.get("types") or []), learnable_hms=hm_names(sp),
                               moves=self.moves_at(a + 8, a + 29)))
        return result

    def screen(self):
        base = self.data.sym("wTileMap")
        tiles = list(self.memory[base:base + 360])
        cells = [[self.data.decode([t], row=True) for t in tiles[y * 20:y * 20 + 20]] for y in range(18)]
        rows = ["".join(row) for row in cells]
        cursor = next(((i % 20, i // 20) for i, t in enumerate(tiles) if t == 0xed), None)
        box = any(0x79 <= t <= 0x7e for t in tiles)
        return dict(rows=rows, cells=cells, cursor=cursor, box=box,
                    waiting=any(t == 0xee for t in tiles[240:]),
                    ui_tiles=sum(t >= 0x60 for t in tiles),
                    dialog=" ".join(rows[y].strip(" ┌─┐│└┘") for y in [14, 16]) if box else "")

    def mode(self):
        screen = self.screen()
        if self.u8("wIsInBattle"):
            return "battle"
        if screen["box"] or screen["cursor"]:
            return "dialog"
        if self.u8("wJoyIgnore") & 0xf0 or self.u8("wStatusFlags5") & 0x80 or self.u8("wWalkCounter"):
            return "busy"
        if self.memory[0xc100] and self.u8("wCurMapWidth") and not screen["ui_tiles"]:
            return "overworld"
        return "dialog" if screen["ui_tiles"] else "boot"

    def sprites(self):
        hidden = set()
        base = self.data.sym("wToggleableObjectList")
        for a in range(base, base + 34, 2):
            if self.memory[a] == 0xff:
                break
            flag = self.memory[a + 1]
            if self.u8("wToggleableObjectFlags", flag >> 3) & (1 << (flag & 7)):
                hidden.add(self.memory[a])
        return [dict(index=i, picture=self.u8("wSpriteStateData1", i * 16),
                     x=self.u8("wSpriteStateData2", i * 16 + 5) - 4,
                     y=self.u8("wSpriteStateData2", i * 16 + 4) - 4)
                for i in range(1, min(self.u8("wNumSprites"), 15) + 1)
                if i not in hidden and self.u8("wSpriteStateData1", i * 16)]

    def battle(self):
        """Observe current stats, types, temporary effects, and enemy identity.
        Recharge is explicit because Red's sleep effect can replace a status during recharge.
        """
        def mon(name, side):
            """Read this combatant's battle struct and current effects from Red WRAM."""
            a = self.data.sym(name)
            sp = self.rom.species.get(self.memory[a], {})
            flags = [self._optional_u8(f"w{side}BattleStatus{i}") for i in range(1, 4)]
            volatile: dict[str, bool | int] = {key: bool(flags[byte - 1] & (1 << bit)) for key, byte, bit in (
                ("charging", 1, 4), ("invulnerable", 1, 6), ("confused", 1, 7),
                ("x_accuracy", 2, 0), ("mist", 2, 1), ("focus_energy", 2, 2),
                ("substitute", 2, 4), ("recharge", 2, 5), ("seeded", 2, 7),
                ("badly_poisoned", 3, 0), ("light_screen", 3, 1), ("reflect", 3, 2), ("transformed", 3, 3))}
            volatile["substitute_hp"] = self._optional_u8(f"w{side}SubstituteHP") if volatile["substitute"] else 0
            stages = {}
            for stat in ("Attack", "Defense", "Speed", "Special", "Accuracy", "Evasion"):
                value = self._optional_u8(f"w{side}Mon{stat}Mod", 7)
                stages[stat.lower()] = value if 1 <= value <= 13 else 7
            types = list(dict.fromkeys(TYPES.get(self.memory[a + offset], "?") for offset in (5, 6)))
            return dict(species=sp.get("name", "?"), species_id=self.memory[a], types=types, level=self.memory[a + 14],
                        hp=self.be16(a + 1), max_hp=self.be16(a + 15), status=status(self.memory[a + 4]),
                        attack=self.be16(a + 17), defense=self.be16(a + 19), speed=self.be16(a + 21), special=self.be16(a + 23),
                        catch_rate=sp.get("catch_rate", 0), dex=sp.get("dex", 0), learnable_hms=hm_names(sp),
                        moves=self.moves_at(a + 8, a + 25), volatile=volatile, stat_stages=stages)
        player, enemy = mon("wBattleMon", "Player"), mon("wEnemyMon", "Enemy")
        kind = "wild" if self.u8("wIsInBattle") == 1 else "trainer"
        slot = self._optional_u8("wEnemyMonPartyPos", self.memory[self.data.sym("wEnemyMon") + 3])
        enemy["party_slot"] = slot if kind == "trainer" and slot < 6 else None
        original_species = self._optional_u8("wEnemyMonSpecies2", enemy["species_id"])
        if original_species not in self.rom.species:
            original_species = enemy["species_id"]
        enemy_id = ([kind, enemy["party_slot"]] if enemy["party_slot"] is not None else
                    [kind, None, original_species, enemy["level"], enemy["max_hp"]])
        return dict(kind=kind, player=player, enemy=enemy, enemy_id=enemy_id,
                    active_slot=self.u8("wPlayerMonNumber"), moves=player["moves"],
                    safari=self.u8("wBattleType") == 2, safari_balls=self.u8("wNumSafariBalls"))

    def snapshot(self):
        mid, mode = self.u8("wCurMap"), self.mode()
        if mode == "overworld" and mid != self.last_map:
            self.settle_map()
            mid, mode = self.u8("wCurMap"), self.mode()
        name = self.data.maps.get(mid, {}).get("name", f"MAP_{mid}")
        if mode == "overworld":
            self.visited.add(name)
            if mid != self.last_map:
                if self.last_map is not None and self.last_map < 0x25:
                    self.outside_map = self.last_map
                self.last_map = mid
        flags = self.data.sym("wEventFlags")
        events = [name for name, bit in self.data.events.items() if self.memory[flags + (bit >> 3)] & (1 << (bit & 7))]
        money = 0
        for i in range(3):
            b = self.u8("wPlayerMoney", i)
            money = money * 100 + (b >> 4) * 10 + (b & 15)
        blackout = None
        try:
            blackout = self.data.maps.get(self.u8("wLastBlackoutMap"), {}).get("name")
        except (KeyError, AttributeError):
            blackout = None
        state = dict(map=name, map_id=mid, x=self.u8("wXCoord"), y=self.u8("wYCoord"), mode=mode,
                     badges=self.u8("wObtainedBadges"), money=money, party=self.party(), bag=self.bag(), box=self.box(),
                     blackout=blackout, saffron_open=bool(self.u8("wStatusFlags1") & 0x40),
                     events=events, visited=sorted(self.visited), interactions=sorted(self.interactions),
                     screen=self.screen()["rows"], battle=self.battle() if mode == "battle" else None)
        state["milestone"] = current_milestone(state)
        return state

    def close(self):
        if self.stage:
            self.stage.close()
            self.stage = None
        self.pyboy.stop(save=False)

"""Reachable local actions and map-distance facts. Jev chooses among them."""

from collections import deque
from dataclasses import dataclass, field
import heapq
import re

DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
FACING = {0: "down", 4: "up", 8: "left", 12: "right"}


@dataclass
class Action:
    key: str
    description: str
    kind: str
    path: list = field(default_factory=list)
    target: dict = field(default_factory=dict)


class Grid:
    def __init__(self, game, map_id=None, *, cut=False, surf=False, overrides=None):
        self.game = game
        m, r, sym = game.memory, game.rom, game.data.sym
        if map_id is None:
            self.w, self.h = game.u8("wCurMapWidth") * 2, game.u8("wCurMapHeight") * 2
            self.tileset = game.u8("wCurMapTileset")
            stride = self.w // 2 + 6
            base = sym("wOverworldMap")
            self.layout = bytes(m[base + (y + 3) * stride + x + 3]
                                for y in range(self.h // 2) for x in range(self.w // 2))
            surf |= game.u8("wWalkBikeSurfState") == 2
        else:
            md = r.maps[map_id]
            self.w, self.h, self.tileset = md["width"] * 2, md["height"] * 2, md["tileset"]
            a = r.flat(md["bank"], md["blocks"])
            self.layout = r.b[a:a + md["width"] * md["height"]]
        if overrides:
            self.layout = bytes(overrides.get(i, b) for i, b in enumerate(self.layout))
        self.cut, self.surf = cut, surf
        header = sym("Tilesets") + self.tileset * 12
        bank = r.b[header]
        self.blocks = r.flat(bank, r.u16(header + 1))
        collision = r.flat(bank, r.u16(header + 5))
        self.passable = set()
        for a in range(collision, min(collision + 256, len(r.b))):
            if r.b[a] == 0xff:
                break
            self.passable.add(r.b[a])
        self.counters = set(r.b[header + 7:header + 10]) - {0xff}
        self.ledges, self.pairs = [], set()
        if self.tileset == 0:
            for a in range(sym("LedgeTiles"), len(r.b) - 3, 4):
                if r.b[a] == 0xff:
                    break
                self.ledges.append((FACING.get(r.b[a]), r.b[a + 1], r.b[a + 2]))
        for a in range(sym("TilePairCollisionsLand"), len(r.b) - 2, 3):
            if r.b[a] == 0xff:
                break
            if r.b[a] == self.tileset:
                self.pairs.add((r.b[a + 1], r.b[a + 2]))
                self.pairs.add((r.b[a + 2], r.b[a + 1]))

    def tile(self, x, y):
        if not 0 <= x < self.w or not 0 <= y < self.h:
            return -1
        block = self.layout[(y >> 1) * (self.w // 2) + (x >> 1)]
        return self.game.rom.b[self.blocks + block * 16 + ((y & 1) * 2 + 1) * 4 + (x & 1) * 2]

    def walkable(self, x, y):
        return self.tile(x, y) in self.passable or self.surf and self.water(x, y) or self.cut and self.tree(x, y)

    def water(self, x, y):
        return self.tileset in {0, 3, 5, 7, 13, 14, 17, 22, 23} and self.tile(x, y) == 0x14

    def tree(self, x, y):
        return (self.tileset, self.tile(x, y)) in {(0, 0x3d), (7, 0x50)}


def find_path(grid, start, goal, blocked=frozenset(), exit_ok=None, grass=None, grass_cost=0):
    """Shortest walk. Tall grass costs extra so a route to a door prefers open ground."""
    if goal(*start):
        return []
    blocked = set(blocked)
    best, previous = {start: 0}, {start: None}
    order, seen = 0, 0
    heap = [(0, 0, start)]
    while heap and seen < 20000:
        cost, _, (x, y) = heapq.heappop(heap)
        if cost != best.get((x, y)):
            continue
        seen += 1
        if (x, y) != start and goal(x, y):
            path, point = [], (x, y)
            while previous[point] is not None:
                point, step = previous[point]
                path.append(step)
            return list(reversed(path))
        if not (0 <= x < grid.w and 0 <= y < grid.h):
            continue
        for direction, (dx, dy) in DIRS.items():
            nx, ny = x + dx, y + dy
            outside = not (0 <= nx < grid.w and 0 <= ny < grid.h)
            if outside:
                if exit_ok is None or not exit_ok(nx, ny):
                    continue
            elif (direction, grid.tile(x, y), grid.tile(nx, ny)) in grid.ledges:
                nx, ny = nx + dx, ny + dy
                if not grid.walkable(nx, ny):
                    continue
            elif not grid.walkable(nx, ny) or (grid.tile(x, y), grid.tile(nx, ny)) in grid.pairs:
                continue
            point = nx, ny
            if point in blocked:
                continue
            step = 1 + (grass_cost if grass_cost and grass and not outside and grass(nx, ny) else 0)
            nxt = cost + step
            if nxt >= best.get(point, 1e18):
                continue
            order += 1
            best[point] = nxt
            previous[point] = ((x, y), (direction, nx, ny))
            heapq.heappush(heap, (nxt, order, point))
    return None


# Victory Road floor switches and the hole a boulder can drop through. Coords are from the map scripts.
FLOOR_FEATURES = {
    "VICTORY_ROAD_1F": [{"x": 17, "y": 13, "kind": "switch", "event": "EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH"}],
    "VICTORY_ROAD_2F": [{"x": 1, "y": 16, "kind": "switch", "event": "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH1"},
                        {"x": 9, "y": 16, "kind": "switch", "event": "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH2"}],
    "VICTORY_ROAD_3F": [{"x": 3, "y": 5, "kind": "switch", "event": "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH1"},
                        {"x": 23, "y": 15, "kind": "hole", "event": "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH2"}],
}
STRENGTH_BADGE = 0x08
HEAL_ITEM = re.compile(
    r"POTION|FRESH WATER|SODA POP|LEMONADE|FULL RESTORE|REVIVE|ANTIDOTE|PARLYZ HEAL|AWAKENING|BURN HEAL|ICE HEAL|FULL HEAL")


class RouteMemory:
    """Exits that failed, and bag items opened without effect. Reset when badges, story, or items change."""

    def __init__(self):
        self.blocked_exits = {}
        self.blocked_edges = {}
        self.item_unused = {}
        self.block_sig = None
        self.switch_pressed = None
        self.switch_failures = {}
        self.said = {}
        self.pending = None
        self.lines = []
        self.trash_map = None
        self.traps = {}
        self.trap_sig = None
        self.pc_mode = None

    def sync(self, state):
        bag = ",".join(sorted(i["name"] for i in state["bag"]))
        sig = f"{state['badges']}|{(state.get('milestone') or {}).get('id')}|{bag}"
        if self.block_sig is not None and sig != self.block_sig:
            self.blocked_exits.clear()
            self.blocked_edges.clear()
        self.block_sig = sig
        trap_sig = f"{state['badges']}|{(state.get('milestone') or {}).get('id')}"
        if self.trap_sig is not None and trap_sig != self.trap_sig:
            self.traps.clear()
        self.trap_sig = trap_sig

    def skip(self):
        return frozenset(edge for edge, count in self.blocked_edges.items() if count >= 2)

    def hidden(self, map_name, key):
        return self.blocked_exits.get(f"{map_name}:{key}", 0) >= 5

    def failure_note(self, map_name, key):
        count = self.blocked_exits.get(f"{map_name}:{key}", 0)
        return f" Tried {count} time(s) before and did not get through." if count else ""

    def note_exit(self, map_name, key, edges, cleared):
        token = f"{map_name}:{key}"
        if cleared:
            self.blocked_exits.pop(token, None)
            for edge in edges:
                self.blocked_edges.pop(edge, None)
            return
        self.blocked_exits[token] = self.blocked_exits.get(token, 0) + 1
        for edge in edges:
            self.blocked_edges[edge] = self.blocked_edges.get(edge, 0) + 1

    def note_supply(self, name, changed):
        if changed:
            self.item_unused.pop(name, None)
        else:
            self.item_unused[name] = self.item_unused.get(name, 0) + 1

    def begin(self, key):
        """Start attributing on-screen text to this person, sign, or blocked exit."""
        self.pending = key
        self.lines = []

    def close(self):
        self.pending = None
        self.lines = []
        self.pc_mode = None

    def hear(self, line):
        line = " ".join(str(line or "").split())
        if not self.pending or not line:
            return
        if self.lines and line.startswith(self.lines[-1]):
            if line != self.lines[-1]:
                self.lines[-1] = line
        else:
            self.lines.append(line)
        self.said[self.pending] = " ".join(self.lines)[-1500:]
        if "were reset" in line and ":hidden" in self.pending:
            prefix = self.pending[:self.pending.index(":hidden") + 7]
            for key in list(self.said):
                if key != self.pending and key.startswith(prefix):
                    del self.said[key]

    def forget_trash(self, map_name, indexes):
        """Gym trash cans are rewritten on every entry, so the last reading is stale."""
        if self.trash_map == map_name:
            return
        self.trash_map = map_name
        for index in indexes:
            self.said.pop(f"{map_name}:hidden:{index}", None)

    def talk_fact(self, map_name, key, kind):
        text = self.said.get(f"{map_name}:{key}", "")[:300]
        if kind == "npc":
            return f' Last time they said: "{text}".' if text else " Not yet talked to."
        if kind == "sign":
            return f' It says: "{text}".' if text else ""
        return f' Examined before: "{text}".' if text else " Not examined yet."

    def stopped_note(self, map_name, key):
        text = self.said.get(f"{map_name}:{key}:blocked", "")[:300]
        return f' What was said when you were stopped: "{text}".' if text else ""

    def trap_points(self, map_name):
        points = set()
        for token in self.traps.get(map_name, []):
            x, y = token.split(",")
            points.add((int(x), int(y)))
        return points

    def remember_pushback(self, map_name, path, x, y, goal=None):
        """Remember the next square a guard's speech pushed the player off, and walk around it."""
        nxt = None
        for index, step in enumerate(path):
            if (step[1], step[2]) == (x, y) and index + 1 < len(path):
                nxt = (path[index + 1][1], path[index + 1][2])
                break
        if nxt is None:
            for step in path:
                if abs(step[1] - x) + abs(step[2] - y) == 1:
                    nxt = (step[1], step[2])
        if nxt is None or nxt == goal:
            return
        token = f"{nxt[0]},{nxt[1]}"
        squares = self.traps.setdefault(map_name, [])
        if token not in squares:
            squares.append(token)

    def to_dict(self):
        return {"blocked_exits": self.blocked_exits, "blocked_edges": self.blocked_edges,
                "item_unused": self.item_unused, "block_sig": self.block_sig,
                "switch_pressed": self.switch_pressed, "switch_failures": self.switch_failures,
                "said": self.said, "pending": self.pending, "lines": self.lines, "trash_map": self.trash_map,
                "traps": self.traps, "trap_sig": self.trap_sig, "pc_mode": self.pc_mode}

    def load(self, data):
        self.__init__()
        if not data:
            return
        self.blocked_exits = dict(data.get("blocked_exits", {}))
        self.blocked_edges = dict(data.get("blocked_edges", {}))
        self.item_unused = dict(data.get("item_unused", {}))
        self.block_sig = data.get("block_sig")
        self.switch_pressed = data.get("switch_pressed")
        self.switch_failures = dict(data.get("switch_failures", {}))
        self.said = dict(data.get("said", {}))
        self.pending = data.get("pending")
        self.lines = list(data.get("lines") or [])
        self.trash_map = data.get("trash_map")
        self.traps = {map_name: list(squares) for map_name, squares in (data.get("traps") or {}).items()}
        self.trap_sig = data.get("trap_sig")
        self.pc_mode = data.get("pc_mode")


def floor_phrase(map_name, hops, here):
    """What choosing an elevator floor does relative to the active goal."""
    if hops is None:
        return f"Sets the elevator doors to lead to {map_name}."
    relation = ""
    if here is not None and hops < here:
        relation = " Leads toward the objective."
    elif here is not None and hops > here:
        relation = " Leads away from the objective."
    return f"Sets the elevator doors to lead to {map_name}. That floor is {hops} area(s) from the objective.{relation}"


def supply_actions(state, *, snorlax, surfing, machine_text, is_key, unused, stone_text=None):
    """Field item choices a player could make: teach an HM, heal, wake Snorlax, or free a full bag."""
    actions = []
    party = state["party"]
    injured = any(p["hp"] < p["max_hp"] for p in party)
    ailment = any(p.get("status", "OK") != "OK" for p in party)
    for item in state["bag"]:
        name, qty = item["name"], item["qty"]
        if unused.get(name, 0) >= 3 or qty <= 0:
            continue
        taught = machine_text(name, party)
        if taught is not None:
            desc = taught
        elif HEAL_ITEM.search(name):
            desc = f"Use on a Pokémon to heal or cure. {qty} left." if injured or ailment or "REVIVE" in name else ""
        elif name.endswith("STONE"):
            desc = stone_text(name, party) if stone_text else ""
        elif "REPEL" in name:
            desc = "Keeps weak wild Pokémon away for a while."
        elif "FLUTE" in name and name.startswith("POK"):
            desc = "Wakes a sleeping Pokémon blocking the road, such as Snorlax." if snorlax else ""
        elif name == "BICYCLE":
            desc = "" if surfing else "Ride the bicycle for faster travel."
        elif name == "ESCAPE ROPE":
            desc = "Leave this cave or dungeon for the last Pokémon Center."
        elif "RARE CANDY" in name and party:
            low = min(party, key=lambda p: p["level"])
            desc = f"Raises one Pokémon by one level. {qty} left. For example {low['nickname']} Lv{low['level']}."
        else:
            desc = ""
        if not desc:
            continue
        if unused.get(name):
            desc += f" Opened {unused[name]} time(s) before and closed without using it."
        actions.append((f"item:{name}", desc, {"name": name}))
    if len([i for i in state["bag"] if i["qty"] > 0]) >= 20:
        tossable = [i["name"] for i in state["bag"] if i["qty"] > 0 and not is_key(i["name"])]
        if tossable and unused.get("BAG", 0) < 3:
            note = f" Opened {unused['BAG']} time(s) before and closed without tossing." if unused.get("BAG") else ""
            actions.append(("toss", "The bag is full (20 of 20), so items on the ground cannot be picked up. "
                            f"Toss one of: {', '.join(tossable)}.{note}", {"name": "BAG"}))
    return actions


def _pushes_to(grid, base, bx0, by0, px, py, targets, limit=4000):
    """Fewest pushes that put this boulder on a switch or hole. None if impossible, 'unknown' if the search is too big."""
    if not targets:
        return None

    def free(x, y):
        return grid.walkable(x, y) and (x, y) not in base

    def reach(bx, by, sx, sy):
        seen, stack = {(sx, sy)}, [(sx, sy)]
        while stack:
            x, y = stack.pop()
            for dx, dy in DIRS.values():
                point = x + dx, y + dy
                if point in seen or point == (bx, by) or not free(*point):
                    continue
                seen.add(point)
                stack.append(point)
        return seen

    visited, queue = set(), deque([(bx0, by0, px, py, 0)])
    while queue and len(visited) < limit:
        bx, by, sx, sy, n = queue.popleft()
        if any(bx == t["x"] and by == t["y"] for t in targets):
            return n
        area = reach(bx, by, sx, sy)
        sig = bx, by, min(area) if area else None
        if sig in visited:
            continue
        visited.add(sig)
        for dx, dy in DIRS.values():
            side, dest = (bx - dx, by - dy), (bx + dx, by + dy)
            if side not in area or not free(*dest):
                continue
            if grid.tile(*dest) == 0x15 or (grid.tile(*side), grid.tile(*dest)) in grid.pairs:
                continue
            queue.append((dest[0], dest[1], bx, by, n + 1))
    return "unknown" if len(visited) >= limit else None


def boulder_actions(grid, start, boulders, blocked, warps, targets, *, known, badges, strength_on, edge_mats=frozenset()):
    """Strength pushes that can still reach a floor switch or hole. Requires the Rainbow Badge."""
    if not boulders or "STRENGTH" not in known or not (badges & STRENGTH_BADGE):
        return []
    if not strength_on:
        note = " A boulder on a floor switch, or dropped through a hole, opens the way." if targets else ""
        return [("field:STRENGTH", "Activate STRENGTH to push boulders on this map." + note, [], {"move": "STRENGTH"})]

    def can_go(sx, sy, tx, ty):
        return grid.walkable(tx, ty) and grid.tile(tx, ty) != 0x15 and (grid.tile(sx, sy), grid.tile(tx, ty)) not in grid.pairs

    def doors(blockset):
        seen, stack = {start}, [start]
        while stack:
            x, y = stack.pop()
            for dx, dy in DIRS.values():
                point = x + dx, y + dy
                if point in seen or point in blockset or not grid.walkable(*point):
                    continue
                seen.add(point)
                stack.append(point)
        return {(w["x"], w["y"]) for w in warps
                if any((w["x"] + dx, w["y"] + dy) in seen for dx, dy in DIRS.values())}

    floor = set(blocked) - set(edge_mats)
    doors_now = doors(floor)
    offered = []
    for boulder in boulders:
        x, y = boulder["x"], boulder["y"]
        base = set(floor)
        base.discard((x, y))
        now = _pushes_to(grid, base, x, y, *start, targets)
        can_now = isinstance(now, int)
        mine = []
        for direction, (dx, dy) in DIRS.items():
            stand, dest = (x - dx, y - dy), (x + dx, y + dy)
            if dest in floor or not grid.walkable(*stand) or not can_go(*stand, *dest):
                continue
            if stand in blocked and stand not in edge_mats:
                continue
            walk_blocked = blocked - {stand} if stand in edge_mats else blocked
            path = [] if stand == start else find_path(grid, start, lambda px, py, stand=stand: (px, py) == stand, walk_blocked)
            if path is None:
                continue
            feature = next((t for t in targets if (t["x"], t["y"]) == dest), None)
            after = _pushes_to(grid, base, dest[0], dest[1], x, y, targets)
            lost = bool(targets) and feature is None and after is None and can_now
            others = set(floor)
            others.discard((x, y))
            pushable = [d2 for d2, (ex, ey) in DIRS.items()
                        if grid.walkable(dest[0] - ex, dest[1] - ey) and can_go(dest[0] - ex, dest[1] - ey, dest[0] + ex, dest[1] + ey)
                        and (dest[0] - ex, dest[1] - ey) not in others and (dest[0] + ex, dest[1] + ey) not in others]
            moved = set(floor)
            moved.discard((x, y))
            moved.add(dest)
            opens = any(door not in doors_now for door in doors(moved))
            if targets and feature is None and (not pushable or lost) and not opens:
                continue
            closer = isinstance(after, int) and isinstance(now, int) and after < now
            leads = bool(targets) and (feature is not None or closer and pushable and not lost)
            kind = "hole in the floor" if feature and feature["kind"] == "hole" else "floor switch"
            facts = [f"Moves the boulder one square {direction} to {dest}."]
            if feature:
                facts.append(f"That square is a {kind}.")
            if leads:
                facts.append("Leads toward the objective: " + ("puts the boulder on it." if feature else "brings the boulder closer to a floor switch or hole."))
            elif targets and feature is None and not pushable:
                facts.append("After this push the boulder cannot be pushed from any side.")
            elif lost:
                facts.append("After this push no sequence of pushes can bring this boulder onto a floor switch or into a hole.")
            mine.append((leads, opens, f"push:{boulder['index']}:{direction}", " ".join(facts), path + [(direction, x, y)],
                         {"x": x, "y": y, "direction": direction}))
        if any(lead for lead, *_ in mine):
            mine = [item for item in mine if item[0] or item[1]]
        offered.extend(item[2:] for item in mine)
    return offered


class Navigation:
    def __init__(self, game):
        self.game = game
        self.seen = set()
        from .regions import Regions
        self.regions = Regions(game)
        self.state = None
        self.memory = RouteMemory()

    def update(self, state):
        self.state = state
        self.regions.update(state)

    def hops(self, origin, target):
        if self.state is None:
            self.update(self.game.snapshot())
        self.memory.sync(self.state)
        if origin == self.state["map_id"]:
            regions = self.regions.landing(self.regions.at(origin, self.state["x"], self.state["y"]))
        else:
            regions = set(self.regions.cells.get(origin, {}).values())
        return self._areas(regions, self.objective(target))

    def _areas(self, regions, target):
        """Region hops to the goal. Inside the Mansion, a blocked route includes one switch press."""
        skip = self.memory.skip()
        plain = self.regions.distance(regions, target, skip)
        if plain is not None or self.regions.flip is None or not self.state["map"].startswith("POKEMON_MANSION_"):
            return plain
        reached = self.regions.reach_through_switch(regions, skip)
        if reached is None:
            return None
        layer_a, layer_b = reached
        found = [layer_a[r] for r in self.regions._targets(target) if r in layer_a]
        found += [layer_b[r] for r in self.regions.flip._targets(target) if r in layer_b]
        return min(found) if found else None

    def field_move_needed(self, state):
        milestone = state.get("milestone") or {}
        need = milestone.get("missing_need") or {}
        target = need.get("map") or (milestone.get("maps") or [None])[0]
        if not target:
            return None
        if self.state is None:
            self.update(state)
        return self.regions.field_move_needed(state["map_id"], state["x"], state["y"], self.objective(target))

    def objective(self, target):
        """Route to the story's room on this map, even when the active task only says to arrive."""
        state = self.state or {}
        milestone = state.get("milestone") or {}
        need = milestone.get("missing_need")
        active = state.get("active_goal") or {}
        if active.get("target_map") != target:
            active = {}
        if need:
            if target != need.get("map"):
                return target
            at = need.get("at")
            if at and at.get("map") == target:
                return target, at["x"], at["y"]
            return self._item_spot(target, milestone, active) or target
        if target not in (milestone.get("maps") or []):
            return target
        at = milestone.get("at")
        if at and at.get("map") == target:
            return target, at["x"], at["y"]
        md = next((md for md in self.game.rom.maps.values() if md["name"] == target), None)
        if md and target.endswith("_GYM"):
            leader = next((obj for obj in md["objects"]
                           if obj.get("trainer") and re.fullmatch(
                               r"BROCK|MISTY|LT\.?SURGE|ERIKA|KOGA|SABRINA|BLAINE|GIOVANNI",
                               obj.get("trainer_class") or "")), None)
            if leader:
                return target, leader["x"], leader["y"] + 1
        return self._item_spot(target, milestone, active) or target

    def _item_spot(self, target, milestone, active):
        """The item ball named by the story or by the active task, on this map."""
        md = next((md for md in self.game.rom.maps.values() if md["name"] == target), None)
        if md is None:
            return None

        def leaves(check):
            if not isinstance(check, dict):
                return []
            if check.get("kind") in {"all", "any"}:
                return [leaf for child in check.get("value") or [] for leaf in leaves(child)]
            return [check]

        named = {check.get("value") for check in leaves((active or {}).get("success") or milestone.get("success") or {})
                 if check.get("kind") == "item"}
        letters = re.sub(r"[^A-Z]", "", f"{milestone.get('goal') or ''} {(active or {}).get('goal') or ''}".upper())
        for obj in md["objects"]:
            item = obj.get("item")
            token = re.sub(r"[^A-Z]", "", str(item or "").upper())
            if item and (item in named or len(token) >= 5 and token in letters):
                return target, obj["x"], obj["y"]
        return None

    def floor_hops(self, floor_id, elevator_id, target):
        """Areas from the floor's elevator landing to the goal. Whole-floor rooms if the landing is unknown."""
        maps = getattr(self.game.rom, "maps", {})
        floor = maps.get(floor_id)
        if floor is None or self.state is None:
            return None
        regions = set()
        for warp in floor["warps"]:
            if warp["map"] == elevator_id:
                region = self.regions.at(floor_id, warp["x"], warp["y"])
                if region is not None:
                    regions.add(region)
        if not regions:
            regions = set(self.regions.cells.get(floor_id, {}).values())
        return self._areas(regions, self.objective(target))

    def floor_choice(self, state, label):
        if self.state is None or "ELEVATOR" not in state.get("map", ""):
            return ""
        elevator = state["map_id"]
        match = next((md for md in self.game.rom.maps.values()
                      if md["name"].endswith(f"_{label}") and any(w["map"] == elevator for w in md["warps"])), None)
        if match is None:
            return ""
        target = (state.get("active_goal") or {}).get("target_map") or state["map"]
        return floor_phrase(match["name"], self.floor_hops(match["id"], elevator, target), self.hops(elevator, target))

    def floor_labels(self, elevator_id, target):
        labels = []
        here = self.hops(elevator_id, target) if self.state and self.state.get("map_id") == elevator_id else None
        for md in self.game.rom.maps.values():
            if not any(w["map"] == elevator_id for w in md["warps"]):
                continue
            short = md["name"].rsplit("_", 1)[-1]
            hops = self.floor_hops(md["id"], elevator_id, target)
            if hops is None:
                labels.append(short)
                continue
            way = ", toward the objective" if here is not None and hops < here else ", away from the objective" if here is not None and hops > here else ""
            labels.append(f"{short} ({hops} areas{way})")
        return labels

    def _machine_text(self, name, party):
        rom = self.game.rom
        item_id = next((i for i, item_name in getattr(rom, "items", {}).items() if item_name == name), None)
        if item_id is None or item_id < 0xc4 or not hasattr(rom, "machine"):
            return None
        index, move = rom.machine(item_id)
        if not move:
            return None
        learners = []
        for pokemon in party:
            if any(m["name"] == move["name"] for m in pokemon.get("moves", [])):
                continue
            species = next((s for s in rom.species.values() if s["name"] == pokemon["species"]), None)
            if species and species["machines"][index // 8] & (1 << (index % 8)):
                learners.append(pokemon["species"])
        if not learners:
            return ""
        field = " Field move: use it from the party once its badge is earned." if name.startswith("HM") else ""
        return f"Teaches {move['name']} ({move['type']}, power {move['power']}). Can be learned by: {', '.join(learners)}.{field}"

    def _is_key(self, name):
        rom = self.game.rom
        if hasattr(rom, "is_key_item_name"):
            return rom.is_key_item_name(name)
        return name.startswith("HM")

    def _stone_text(self, name, party):
        rom = self.game.rom
        item_id = next((i for i, item_name in getattr(rom, "items", {}).items() if item_name == name), None)
        if item_id is None:
            return ""
        evolvers = []
        for pokemon in party:
            species = next((s for s in rom.species.values() if s["name"] == pokemon["species"]), None)
            if species and item_id in species.get("stones", ()):
                evolvers.append(pokemon["species"])
        return f"Evolves {', '.join(evolvers)}." if evolvers else ""

    def actions(self, state, goal):
        from .controls import pc_summary
        from .regions import switch_fact
        g = self.game
        state["active_goal"] = goal.to_dict()
        self.update(state)
        self.memory.sync(state)
        grid, mid = Grid(g), state["map_id"]
        self.memory.forget_trash(state["map"], [i for i, hidden in enumerate(g.rom.hidden.get(mid, [])) if "GymTrash" in hidden["fn"]])
        md = g.rom.maps[mid]
        start = state["x"], state["y"]
        self.seen.add((mid, *start))
        sprites = g.sprites()
        occupied = {(s["x"], s["y"]) for s in sprites}
        doors = {(w["x"], w["y"]) for w in md["warps"]}
        spins = g.rom.spinners.get(mid, {})
        holes = self.regions.special(mid) - doors
        blocked = occupied | doors | set(spins) | holes | self.memory.trap_points(state["map"])
        grass_tile = g.u8("wGrassTile")

        def grassy(x, y):
            return grass_tile != 0xff and grid.tile(x, y) == grass_tile

        def travel(goal, block, exit_ok=None):
            return find_path(grid, start, goal, block, exit_ok, grass=grassy, grass_cost=1)
        objective = self.objective(goal.target_map)
        player = self.regions.landing(self.regions.at(mid, *start))
        here = self.regions.at(mid, *start)
        here_hops = self._areas(player, objective)
        plain = self.regions.distance(player, objective, self.memory.skip())
        gated = plain is None and here_hops is not None and md["name"].startswith("POKEMON_MANSION_")
        if self.memory.switch_pressed == state["map"]:
            count = self.memory.switch_failures.get(state["map"], 0)
            self.memory.switch_failures[state["map"]] = count + 1 if gated else 0
            self.memory.switch_pressed = None
        elif not gated:
            self.memory.switch_failures.pop(state["map"], None)
        needed = self.regions.field_move_needed(mid, *start, objective)
        if needed:
            state["field_move_needed"] = needed
        layers = self.regions.mansion_distances(objective, self.memory.skip()) if md["name"].startswith("POKEMON_MANSION_") else None
        result = []

        def edges_of(regions):
            if here is None:
                return []
            return [f"{here[0]}:{here[1]}>{region[0]}:{region[1]}" for region in regions if region is not None]

        def add(key, description, kind, path, edges=(), **target):
            if path is None or self.memory.hidden(state["map"], key):
                return
            if edges:
                target["edges"] = list(edges)
            result.append(Action(key, description + self.memory.failure_note(state["map"], key) + self.memory.stopped_note(state["map"], key), kind, path, target))

        def route(destinations):
            regions = [r for r in destinations if r is not None]
            names = sorted({g.rom.maps[r[0]]["name"] for r in regions})
            landing = set().union(*(self.regions.landing(r) for r in regions)) if regions else set()
            distance = self._areas(landing, objective) if landing else None
            text = f"Destination {', '.join(names) or 'unknown'}. Areas to goal: {distance if distance is not None else 'no known route'}."
            if distance is not None and here_hops is not None and distance < here_hops:
                text += " Leads toward the objective."
            elif distance is None and needed:
                text += f" No walking route until {needed} is available."
            else:
                text += " Unseen scripted gates may block it."
            return text

        for i, warp in enumerate(md["warps"]):
            pos = warp["x"], warp["y"]
            if pos in occupied:
                continue
            path = travel(lambda x, y: (x, y) == pos, blocked - {pos})
            if path == [] and start[1] < grid.h - 1:
                # Arriving on stairs/pads does not trigger them again until you step off.
                away = travel(lambda x, y: abs(x - start[0]) + abs(y - start[1]) == 1, blocked)
                back = find_path(grid, tuple(away[-1][1:]), lambda x, y: (x, y) == pos, blocked - {pos},
                                 grass=grassy, grass_cost=1) if away else None
                path = away + back if away and back else None
            targets = self.regions.warp_targets(mid, i)
            if "ELEVATOR" in md["name"]:
                # The floor panel edits the live door destinations.
                base = g.data.sym("wWarpEntries") + i * 4
                live_warp = dict(map=g.memory[base + 3], warp=g.memory[base + 2])
                destination = g.rom.maps.get(live_warp["map"], {}).get("warps", [])
                if live_warp["warp"] < len(destination):
                    point = destination[live_warp["warp"]]
                    region = self.regions.at(live_warp["map"], point["x"], point["y"])
                    targets = {region} if region is not None else set()
            add(f"door:{i}", f"Use door/stairs at {pos}. {route(targets)}", "door", path, edges_of(targets), **warp)
        for con in md["connections"]:
            direction = con["direction"]
            edge = {"up": lambda x, y: y < 0, "down": lambda x, y: y >= grid.h,
                    "left": lambda x, y: x < 0, "right": lambda x, y: x >= grid.w}[direction]
            def landing_at(x, y):
                if not edge(x, y):
                    return None
                point = self.regions.connection_point(con, x, y)
                if self.regions.grids[con["map"]].water(*point) and g.u8("wWalkBikeSurfState") != 2:
                    return None
                return self.regions.at(con["map"], *point)
            border = ([(x, -1 if direction == "up" else grid.h) for x in range(grid.w)]
                      if direction in {"up", "down"} else
                      [(-1 if direction == "left" else grid.w, y) for y in range(grid.h)])
            destinations = {r for x, y in border if (r := landing_at(x, y)) is not None}
            candidates = []
            for region in destinations:
                valid = lambda x, y: landing_at(x, y) == region
                path = travel(valid, blocked, valid)
                if path:
                    distance = self._areas({region}, objective)
                    candidates.append((distance if distance is not None else 9999, len(path), region, path))
            for i, (_, _, region, path) in enumerate(sorted(candidates)):
                key = f"exit:{direction}" + (f":{region[1]}" if i else "")
                add(key, f"Leave {direction}. {route({region})}", "walk", path, edges_of({region}))
        for (x, y), landing in spins.items():
            seen = {(x, y)}
            while landing in spins and landing not in seen:
                seen.add(landing)
                landing = spins[landing]
            region = self.regions.at(mid, *landing)
            add(f"spin:{x},{y}", f"Step onto arrow at ({x},{y}), landing at {landing}. {route({region} if region else set())}",
                "walk", travel(lambda px, py: (px, py) == (x, y), blocked - {(x, y)}))
        for x, y in holes:
            add(f"drop:{x},{y}", f"Drop through the floor hole at ({x},{y}).",
                "walk", travel(lambda px, py: (px, py) == (x, y), blocked - {(x, y)}))
        for kind, targets in [("npc", sprites), ("sign", md["signs"])]:
            for i, target in enumerate(targets):
                x, y = target["x"], target["y"]
                if target.get("picture") == 63:
                    continue
                def adjacent(px, py):
                    return abs(px - x) + abs(py - y) == 1 or any(
                        px + 2 * dx == x and py + 2 * dy == y and grid.tile(px + dx, py + dy) in grid.counters
                        for dx, dy in DIRS.values())
                name = g.data.sprites.get(target.get("picture"), "sign")
                obj = next((o for o in md["objects"] if o["index"] == target.get("index")), {})
                key = f"{kind}:{target.get('index', i)}"
                said = self.memory.talk_fact(state["map"], key, kind)
                if kind == "sign" and state["map"] == "CELADON_MART_ROOF" and y <= 2 and 10 <= x <= 12:
                    facts = f"A drink vending machine (FRESH WATER ¥200, SODA POP ¥300, LEMONADE ¥350).{said}"
                elif kind == "sign" and "ELEVATOR" in state["map"]:
                    floors = ", ".join(self.floor_labels(mid, goal.target_map)) or "unknown"
                    facts = f"The elevator's floor-select panel. Floors: {floors}.{said}"
                elif kind == "sign":
                    facts = f"A sign.{said}" if said else "A sign, not yet read."
                else:
                    facts = obj.get("item") or obj.get("trainer_class") or name
                    facts += {"NURSE": " (heals the entire party for free)", "CLERK": " (shop clerk; buys/sells supplies)",
                              "POKE_BALL": " (Pokémon or collectible)"}.get(name, "")
                    facts += said
                add(key, f"Interact with {facts} at ({x},{y}).", "interact",
                    travel(adjacent, blocked), x=x, y=y, sprite=target.get("index"))
        grass = g.u8("wGrassTile")
        if grass != 0xff:
            path = find_path(grid, start, lambda x, y: (x, y) != start and grid.tile(x, y) == grass, blocked)
            add("grass", "Walk through grass to encounter wild Pokémon for training or catching.", "walk", path)
        explore = travel(lambda x, y: (mid, x, y) not in self.seen and abs(x - start[0]) + abs(y - start[1]) >= 4, blocked)
        add("explore", "Walk to a part of this map not yet explored.", "walk", explore)
        surfing = g.u8("wWalkBikeSurfState") == 2
        snorlax = any(g.data.sprites.get(s["picture"]) == "SNORLAX" for s in sprites)
        for key, description, item_target in supply_actions(
                state, snorlax=snorlax, surfing=surfing, machine_text=self._machine_text,
                is_key=self._is_key, unused=self.memory.item_unused, stone_text=self._stone_text):
            add(key, description, "toss" if key == "toss" else "item", [], **item_target)
        if state["party"]:
            add("party", "Open the party menu to inspect the team or use a known field move.", "party", [])
        for i, hidden in enumerate(g.rom.hidden.get(mid, [])):
            fn, x, y = hidden["fn"], hidden["x"], hidden["y"]
            if any(name in fn for name in ("HiddenItems", "HiddenCoins", "StartSlotMachine", "CableClub", "Unknown")):
                continue
            direction = "up" if any(name in fn for name in ("Quiz", "GymStatues", "Binoculars", "PokemonCenterPC", "BillsHousePC", "OakLabEmail", "IndigoPlateauHQ")) else None
            if not direction and any(name in fn for name in ("PC", "Switches")):
                direction = FACING.get(hidden["arg"])
            def position(px, py):
                if direction:
                    dx, dy = DIRS[direction]
                    return (px + dx, py + dy) == (x, y)
                return abs(px - x) + abs(py - y) == 1
            fact = ""
            if any(name in fn for name in ("PokemonCenterPC", "RedsPC", "BillsHousePC")):
                fact = " Pokémon storage. " + pc_summary(state)
            if "Switches" in fn and layers and self.regions.flip is not None:
                layer_a, layer_b = layers
                stand_a = self.regions.at(mid, x, y + 1)
                stand_b = self.regions.flip.at(mid, x, y + 1)
                fact = switch_fact(layer_a.get(stand_a) if stand_a else None, layer_b.get(stand_b) if stand_b else None)
                fact += " Switches in this building open some gates and close others."
                fails = self.memory.switch_failures.get(state["map"], 0)
                if gated and fails:
                    fact += f" The way is still closed after pressing a switch here {fails} time(s)."
                elif gated:
                    fact += " The way to the objective is closed by a gate right now; this switch changes which gates are closed."
            talk = self.memory.talk_fact(state["map"], f"hidden:{i}", "hidden")
            add(f"hidden:{i}", f"Interact with {fn} at ({x},{y}).{fact}{talk}", "interact",
                travel(position, blocked), x=x, y=y, switch="Switches" in fn)
        known = {m["name"] for p in state["party"] for m in p["moves"]}
        for move, allowed, obstacle in [("CUT", state["badges"] & 2, grid.tree),
                                         ("SURF", state["badges"] & 16 and g.u8("wWalkBikeSurfState") != 2, grid.water)]:
            if not allowed or move not in known:
                continue
            for direction, (dx, dy) in DIRS.items():
                path = travel(lambda x, y: obstacle(x + dx, y + dy), blocked)
                add(f"field:{move}:{direction}", f"Walk to a reachable obstacle and use {move} facing {direction}.",
                    "field", path, move=move, direction=direction)
        if "FLASH" in known:
            add("field:FLASH", "Use FLASH. Lights up a dark cave so the path is visible.", "field", [], move="FLASH")
        if "FLY" in known and state["badges"] & 4:
            add("field:FLY", "Use FLY. Fly to a town already visited.", "field", [], move="FLY")
        boulders = [s for s in sprites if s["picture"] == 63]
        open_targets = [dict(feature) for feature in FLOOR_FEATURES.get(md["name"], [])
                        if feature["event"] not in state["events"]
                        and not any(s["x"] == feature["x"] and s["y"] == feature["y"] for s in boulders)]
        edge_mats = set()
        if (boulders and "STRENGTH" in known and state["badges"] & STRENGTH_BADGE
                and md["tileset"] not in {0, 13, 14, 23}
                and md["name"] not in {"ROCKET_HIDEOUT_B1F", "ROCKET_HIDEOUT_B2F", "ROCKET_HIDEOUT_B4F", "ROCK_TUNNEL_1F"}):
            stepping = g.rom.step_tiles(md["tileset"]) if hasattr(g.rom, "step_tiles") else set()
            edge_mats = {(w["x"], w["y"]) for w in md["warps"]
                         if (w["x"] in (0, grid.w - 1) or w["y"] in (0, grid.h - 1)) and grid.tile(w["x"], w["y"]) not in stepping}
        for key, description, path, extra in boulder_actions(
                grid, start, boulders, blocked, md["warps"], open_targets, known=known,
                badges=state["badges"], strength_on=bool(g.u8("wStatusFlags1") & 1), edge_mats=edge_mats):
            add(key, description, "field" if key.startswith("field:") else "walk", path, **extra)
        if "CARD KEY" in {i["name"] for i in state["bag"]} and md["name"].startswith("SILPH_CO_"):
            for y in range(grid.h):
                for x in range(grid.w):
                    if grid.tile(x, y) in {0x18, 0x24} or md["name"] == "SILPH_CO_11F" and grid.tile(x, y) == 0x5e:
                        add(f"unlock:{x},{y}", f"Unlock the CARD KEY door at ({x},{y}).", "interact",
                            travel(lambda px, py: abs(px - x) + abs(py - y) == 1, blocked), x=x, y=y)
        if any(a.key.startswith("push:") and "Leads toward the objective" in a.description for a in result):
            return [a for a in result if a.key != "explore" and not (
                a.key.startswith(("exit:", "door:")) and "Leads toward the objective" not in a.description)]
        return result

    def execute(self, action):
        g = self.game
        origin = g.u8("wCurMap")
        for direction, tx, ty in action.path:
            if g.mode() != "overworld" or g.u8("wCurMap") != origin:
                return False
            before = g.u8("wXCoord"), g.u8("wYCoord")
            # Hold only until a tile transition begins, then allow it to settle.
            g.pyboy.button_press(direction)
            try:
                for _ in range(90):
                    g.tick()
                    if g.u8("wCurMap") != origin or (g.u8("wXCoord"), g.u8("wYCoord")) != before or g.mode() in {"battle", "dialog"}:
                        break
            finally:
                g.pyboy.button_release(direction)
            g.tick(12)
            for _ in range(1200):
                if g.mode() != "busy":
                    break
                g.tick()
            self.seen.add((g.u8("wCurMap"), g.u8("wXCoord"), g.u8("wYCoord")))
            if g.u8("wCurMap") != origin or (g.u8("wXCoord"), g.u8("wYCoord")) != (tx, ty):
                return False
        if g.mode() != "overworld" or g.u8("wCurMap") != origin:
            return False
        if action.kind == "interact":
            target = next((s for s in g.sprites() if s["index"] == action.target.get("sprite")), action.target)
            dx, dy = target["x"] - g.u8("wXCoord"), target["y"] - g.u8("wYCoord")
            if not 0 < abs(dx) + abs(dy) <= 2 or dx and dy:
                return False
            direction = "right" if dx > 0 else "left" if dx < 0 else "down" if dy > 0 else "up"
            self.memory.begin(f"{g.data.maps[origin]['name']}:{action.key}")
            g.face(direction)
            g.press("a")
            if g.mode() != "overworld":
                g.interactions.add(f"{g.data.maps[origin]['name']}:{action.key}")
        elif action.kind == "door":
            # Some exit mats warp only when walking through their edge.
            y = g.u8("wYCoord")
            if y >= g.u8("wCurMapHeight") * 2 - 1:
                g.press("down", 8, 30)
        return True

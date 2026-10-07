"""Directed room graph: doors never join the floor on both sides of a warp."""

from collections import deque

from .navigation import DIRS, Grid
from .snorlax import SNORLAX

HOLES = [("POKEMON_MANSION_3F", 16, 14, "POKEMON_MANSION_1F", 16, 14),
         ("POKEMON_MANSION_3F", 17, 14, "POKEMON_MANSION_1F", 16, 14),
         ("POKEMON_MANSION_3F", 19, 14, "POKEMON_MANSION_2F", 18, 14)]
# Map script block replacements from pret/pokered; (block y, x, switch off, on).
MANSION = {
    "POKEMON_MANSION_1F": [(6, 12, 14, 45), (3, 8, 45, 14), (8, 10, 45, 14), (13, 13, 45, 14)],
    "POKEMON_MANSION_2F": [(2, 4, 14, 95), (4, 9, 84, 14), (11, 3, 95, 14)],
    "POKEMON_MANSION_3F": [(2, 7, 14, 95), (5, 7, 95, 14)],
    "POKEMON_MANSION_B1F": [(8, 13, 14, 45), (11, 6, 14, 95), (3, 4, 95, 14), (8, 8, 84, 14)],
}


def rid(region):
    return f"{region[0]}:{region[1]}"


def switch_distances(out_a, out_b, links, targets_a, targets_b, skip=frozenset()):
    """Hop counts across both mansion switch layouts. One press moves between layouts."""
    reverse = {}

    def edge(src, dst):
        reverse.setdefault(dst, []).append(src)

    for src, dests in out_a.items():
        for dst in dests:
            if f"{rid(src)}>{rid(dst)}" not in skip:
                edge(("A", src), ("A", dst))
    for src, dests in out_b.items():
        for dst in dests:
            edge(("B", src), ("B", dst))
    for left, right in links:
        if left is not None and right is not None:
            edge(("A", left), ("B", right))
            edge(("B", right), ("A", left))
    dist = {("A", t): 0 for t in targets_a}
    dist.update({("B", t): 0 for t in targets_b})
    queue = deque(dist)
    while queue:
        node = queue.popleft()
        for prev in reverse.get(node, ()):
            if prev not in dist:
                dist[prev] = dist[node] + 1
                queue.append(prev)
    return ({k: v for (layer, k), v in dist.items() if layer == "A"},
            {k: v for (layer, k), v in dist.items() if layer == "B"})


def switch_reach(out_a, out_b, links, origins, skip=frozenset()):
    """One search from the player's rooms through both switch layouts."""
    adjacent = {}

    def add(src, dst):
        adjacent.setdefault(src, []).append(dst)

    for src, dests in out_a.items():
        for dst in dests:
            if f"{rid(src)}>{rid(dst)}" not in skip:
                add(("A", src), ("A", dst))
    for src, dests in out_b.items():
        for dst in dests:
            add(("B", src), ("B", dst))
    for left, right in links:
        if left is not None and right is not None:
            add(("A", left), ("B", right))
            add(("B", right), ("A", left))
    dist = {("A", origin): 0 for origin in origins if origin is not None}
    queue = deque(dist)
    while queue:
        node = queue.popleft()
        for nxt in adjacent.get(node, ()):
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                queue.append(nxt)
    return ({k: v for (layer, k), v in dist.items() if layer == "A"},
            {k: v for (layer, k), v in dist.items() if layer == "B"})


def switch_fact(without, after):
    """How a mansion switch press changes the distance to the current goal."""
    if after is None:
        return " After pressing it, the objective would not be reachable from here."
    now = f"{without} without pressing" if without is not None else "not reachable without pressing"
    if without is None or after < without:
        return (f" Pressing it leads toward the objective: all gates flip, and the objective is then "
                f"{after} area(s) away ({now}).")
    return (f" Pressing it flips all gates; the objective is then {after} area(s) away ({now}), "
            "so pressing does not bring it closer.")


class Regions:
    def __init__(self, game):
        self.game = game
        self.maps = game.rom.maps
        self.names = {md["name"]: mid for mid, md in self.maps.items()}
        self.grids, self.cells, self.out, self.cache = {}, {}, {}, {}
        self.signature = None
        self.live_key = None
        self.warp_sides = {}
        self.blocked = {}
        self.flip = None
        self._static_cache = {}
        self._mansion_cache = {}
        self.snorlax_cleared = frozenset()

    def fixed_blocks(self, md, *, layout=False):
        site = SNORLAX.get(md["name"])
        sleeping = site and site["event"] not in self.snorlax_cleared
        return {(o["x"], o["y"]) for o in md["objects"]
                if (o["picture"] == 63 and not layout) or (o["picture"] == 67 and sleeping)}

    def update(self, state):
        known = {m["name"] for p in state["party"] for m in p["moves"]}
        cut, surf = "CUT" in known and bool(state["badges"] & 2), "SURF" in known and bool(state["badges"] & 16)
        switch = "EVENT_MANSION_SWITCH_ON" in state["events"]
        signature = cut, surf, switch
        cleared = frozenset(site["event"] for site in SNORLAX.values() if site["event"] in state["events"])
        rebuild = signature != self.signature or cleared != self.snorlax_cleared
        if cleared != self.snorlax_cleared:
            self.snorlax_cleared = cleared
            self._static_cache.clear()
            self._mansion_cache.clear()
        if rebuild:
            self.signature, self.live_key = signature, None
            self.grids = {mid: Grid(self.game, mid, cut=cut, surf=surf,
                                   overrides={y * md["width"] + x: on if switch else off
                                              for y, x, off, on in MANSION.get(md["name"], [])})
                          for mid, md in self.maps.items()}
            self.blocked = {mid: self.fixed_blocks(md)
                            for mid, md in self.maps.items()}
            self.flip = self.static(cut, surf, not switch)
        if state["mode"] == "overworld":
            mid = state["map_id"]
            live = Grid(self.game, cut=cut, surf=surf)
            fixed = {o["index"] for o in self.maps[mid]["objects"] if o["movement"] == 0xff}
            occupied = {(s["x"], s["y"]) for s in self.game.sprites() if s["index"] in fixed or s["picture"] == 63}
            key = mid, live.layout, frozenset(occupied)
            if key != self.live_key:
                old = self.live_key[0] if self.live_key else None
                if old is not None and old != mid:
                    self.blocked[old] = self.fixed_blocks(self.maps[old])
                    self.label(old)
                self.grids[mid], self.blocked[mid], self.live_key = live, occupied, key
                if not rebuild:
                    self.label(mid)
                rebuild_links = True
            else:
                rebuild_links = False
        else:
            rebuild_links = False
        if rebuild:
            for mid in self.maps:
                self.label(mid)
        if rebuild or rebuild_links:
            self.link()

    def at(self, mid, x, y):
        return self.cells.get(mid, {}).get((x, y))

    def add(self, a, b):
        if a is not None and b is not None and a != b:
            self.out.setdefault(a, set()).add(b)

    def special(self, mid):
        md = self.maps[mid]
        return {(w["x"], w["y"]) for w in md["warps"]} | {(x, y) for name, x, y, *_ in HOLES if name == md["name"]}

    def label(self, mid):
        g = self.grids[mid]
        cells = self.cells[mid] = {}
        special = self.special(mid)
        excluded = special | self.blocked[mid] | set(self.game.rom.spinners.get(mid, {}))
        next_id = 0
        for y in range(g.h):
            for x in range(g.w):
                if (x, y) in cells or (x, y) in excluded or not g.walkable(x, y):
                    continue
                region = mid, next_id
                next_id += 1
                cells[x, y] = region
                queue = [(x, y)]
                while queue:
                    px, py = queue.pop()
                    for dx, dy in DIRS.values():
                        point = nx, ny = px + dx, py + dy
                        if point in cells or point in excluded or not g.walkable(nx, ny):
                            continue
                        if (g.tile(px, py), g.tile(nx, ny)) in g.pairs:
                            continue
                        cells[point] = region
                        queue.append(point)
        sides = self.warp_sides[mid] = {}
        for x, y in special:
            if not 0 <= x < g.w or not 0 <= y < g.h or (x, y) in self.blocked[mid]:
                continue
            own = mid, next_id
            next_id += 1
            sides[own] = {cells[x + dx, y + dy] for dx, dy in DIRS.values()
                          if (x + dx, y + dy) in cells and (x + dx, y + dy) not in special}
            cells[x, y] = own

    def landing(self, region):
        if region is None:
            return set()
        return self.warp_sides.get(region[0], {}).get(region) or {region}

    def warp_targets(self, mid, index):
        md = self.maps[mid]
        w = md["warps"][index]
        targets = set()

        def land(destination, i):
            warps = self.maps.get(destination, {}).get("warps", [])
            if i < len(warps):
                p = warps[i]
                region = self.at(destination, p["x"], p["y"])
                if region is not None:
                    targets.add(region)

        if "ELEVATOR" in md["name"]:
            for other, src in self.maps.items():
                for i, sw in enumerate(src["warps"]):
                    if sw["map"] == mid:
                        land(other, i)
        elif w["map"] != 0xff:
            land(w["map"], w["warp"])
        else:
            for other, src in self.maps.items():
                if any(sw["map"] == mid and sw["warp"] == index for sw in src["warps"]):
                    land(other, w["warp"])
            if not targets:
                for other, src in self.maps.items():
                    if any(sw["map"] == mid for sw in src["warps"]) and not any(sw["map"] == other for sw in md["warps"]):
                        land(other, w["warp"])
        return targets

    @staticmethod
    def connection_point(con, x, y):
        return (x + con["x_align"], con["y_align"]) if con["direction"] in {"up", "down"} else (con["x_align"], y + con["y_align"])

    def link(self):
        self.out.clear()
        self.cache.clear()
        self._mansion_cache = {}
        for mid, md in self.maps.items():
            g = self.grids[mid]
            for own, sides in self.warp_sides[mid].items():
                for side in sides:
                    self.add(own, side)
            for i, w in enumerate(md["warps"]):
                own = self.at(mid, w["x"], w["y"])
                if own is not None:
                    for a in {own} | self.warp_sides[mid].get(own, set()):
                        for b in self.warp_targets(mid, i):
                            self.add(a, b)
            for name, x, y, dest, tx, ty in HOLES:
                if name == md["name"]:
                    own = self.at(mid, x, y)
                    for a in {own} | self.warp_sides[mid].get(own, set()):
                        self.add(a, self.at(self.names[dest], tx, ty))
            for con in md["connections"]:
                direction = con["direction"]
                points = ([(x, 0 if direction == "up" else g.h - 1) for x in range(g.w)]
                          if direction in {"up", "down"} else
                          [(0 if direction == "left" else g.w - 1, y) for y in range(g.h)])
                for x, y in points:
                    self.add(self.at(mid, x, y), self.at(con["map"], *self.connection_point(con, x, y)))
            for (x, y), a in self.cells[mid].items():
                for direction, (dx, dy) in DIRS.items():
                    if (direction, g.tile(x, y), g.tile(x + dx, y + dy)) in g.ledges:
                        self.add(a, self.at(mid, x + 2 * dx, y + 2 * dy))
            spins = self.game.rom.spinners.get(mid, {})
            for (x, y), land in spins.items():
                seen = {(x, y)}
                while land in spins and land not in seen:
                    seen.add(land)
                    land = spins[land]
                for dx, dy in DIRS.values():
                    self.add(self.at(mid, x + dx, y + dy), self.at(mid, *land))

    def distances(self, target, skip=frozenset()):
        key = (target, skip)
        if key not in self.cache:
            targets = self._targets(target)
            distances = {r: 0 for r in targets}
            reverse = {}
            for a, bs in self.out.items():
                for b in bs:
                    if f"{rid(a)}>{rid(b)}" in skip:
                        continue
                    reverse.setdefault(b, set()).add(a)
            queue = deque(distances)
            while queue:
                b = queue.popleft()
                for a in reverse.get(b, ()):
                    if a not in distances:
                        distances[a] = distances[b] + 1
                        queue.append(a)
            self.cache[key] = distances
        return self.cache[key]

    def _targets(self, target):
        if isinstance(target, tuple):
            name, x, y = target
            mid = self.names.get(name)
            region = self.at(mid, x, y) if mid is not None else None
            if region is not None:
                return {region}
            return {r for dx, dy in DIRS.values() if (r := self.at(mid, x + dx, y + dy)) is not None}
        mid = self.names.get(target)
        return set(self.cells.get(mid, {}).values())

    def distance(self, regions, target, skip=frozenset()):
        d = self.distances(target, skip)
        return min((d[r] for r in regions if r in d), default=None)

    def static(self, cut, surf, switch, *, layout=False):
        """ROM layout for one HM/switch combination, without live sprites."""
        key = (cut, surf, switch, layout)
        if key not in self._static_cache:
            other = Regions(self.game)
            other.snorlax_cleared = self.snorlax_cleared
            other.grids = {mid: Grid(self.game, mid, cut=cut, surf=surf,
                                    overrides={y * md["width"] + x: on if switch else off
                                               for y, x, off, on in MANSION.get(md["name"], [])})
                           for mid, md in self.maps.items()}
            other.blocked = {mid: other.fixed_blocks(md, layout=layout)
                             for mid, md in self.maps.items()}
            for mid in other.maps:
                other.label(mid)
            other.link()
            self._static_cache[key] = other
        return self._static_cache[key]

    def _switch_links(self):
        links = []
        for name in MANSION:
            mid = self.names.get(name)
            if mid is None:
                continue
            for hidden in self.game.rom.hidden.get(mid, []):
                if "Switches" not in hidden.get("fn", ""):
                    continue
                x, y = hidden["x"], hidden["y"] + 1
                links.append((self.at(mid, x, y), self.flip.at(mid, x, y)))
        return links

    def mansion_distances(self, target, skip=frozenset()):
        """Distances that count a switch press when the current gates block the goal."""
        if self.flip is None:
            return None
        key = (target, skip, self.live_key, self.signature)
        if key not in self._mansion_cache:
            links = self._switch_links()
            self._mansion_cache[key] = switch_distances(
                self.out, self.flip.out, links, self._targets(target), self.flip._targets(target), skip)
        return self._mansion_cache[key]

    def reach_through_switch(self, origins, skip=frozenset()):
        """Distances from these rooms, including one switch press. Reused for every destination."""
        if self.flip is None:
            return None
        key = ("reach", frozenset(origins), skip, self.live_key, self.signature)
        if key not in self._mansion_cache:
            self._mansion_cache[key] = switch_reach(self.out, self.flip.out, self._switch_links(), origins, skip)
        return self._mansion_cache[key]

    def field_move_needed(self, map_id, x, y, target):
        """CUT or SURF when that move, and not the current one, connects this tile to the goal."""
        if not self.signature:
            return None
        origin = self.at(map_id, x, y)
        if origin is not None and self.distance({origin}, target) is not None:
            return None
        if self.flip is not None and origin is not None:
            via = self.mansion_distances(target)
            if via is not None and origin in via[0]:
                return None
        cut, surf, switch = self.signature
        baseline = self.static(cut, surf, switch)
        start = baseline.at(map_id, x, y)
        # Compare like-for-like layouts: removing an NPC is not evidence that CUT is needed.
        if start is not None and baseline.distance({start}, target) is not None:
            return None
        for move, caps in (("CUT", (True, surf, switch)), ("SURF", (cut, True, switch))):
            if caps[:2] == (cut, surf):
                continue
            graph = self.static(*caps)
            start = graph.at(map_id, x, y)
            if start is not None and graph.distance({start}, target) is not None:
                return move
        return None

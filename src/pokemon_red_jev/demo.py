"""Scripted simulation of the real control loop. This is not Pokémon emulation."""

from copy import deepcopy
from types import SimpleNamespace

from .agent import Agent
from .goals import Goal
from .navigation import Action

MAPS = ["ROUTE_1", "VIRIDIAN_CITY", "VIRIDIAN_POKECENTER", "VIRIDIAN_MART"]
EDGES = {0: [1], 1: [0, 2, 3], 2: [1], 3: [1]}


class DemoGame:
    def __init__(self):
        self.state = dict(map=MAPS[0], map_id=0, mode="overworld", x=0, y=0, badges=0, money=300,
                          party=[dict(nickname="JEV", species="BULBASAUR", hp=8, max_hp=20, status="OK", level=5, moves=[])],
                          bag=[], events=[], visited=[MAPS[0]], interactions=[], screen=[], battle=None)
        self.data = SimpleNamespace(events={"EVENT_GOT_OAKS_PARCEL": 0})
        self.rom = SimpleNamespace(items={1: "OAK'S PARCEL"}, maps={i: dict(name=name, objects=[], signs=[]) for i, name in enumerate(MAPS)})
        self.rom.hidden = {}
        self.block_once = True

    def snapshot(self):
        state = deepcopy(self.state)
        state["milestone"] = None if "EVENT_GOT_OAKS_PARCEL" in state["events"] else dict(
            id="parcel", goal="Receive Oak's Parcel from the Viridian Poké Mart.", maps=["VIRIDIAN_MART"],
            success={"kind": "event", "value": "EVENT_GOT_OAKS_PARCEL"})
        return state


class DemoNavigation:
    def __init__(self, game):
        self.game = game

    def update(self, state):
        pass

    def hops(self, origin, target):
        from collections import deque
        queue, seen = deque([(origin, 0)]), {origin}
        while queue:
            mid, n = queue.popleft()
            if MAPS[mid] == target:
                return n
            for dest in EDGES[mid]:
                if dest not in seen:
                    queue.append((dest, n + 1))
                    seen.add(dest)
        return None

    def actions(self, state, goal):
        actions = [Action(f"go:{MAPS[i]}", f"Enter {MAPS[i]}", "walk", target={"map": i}) for i in EDGES[state["map_id"]]]
        if state["map"] == "VIRIDIAN_POKECENTER":
            actions.append(Action("heal", "Ask the nurse to heal the party", "interact"))
        if state["map"] == "VIRIDIAN_MART":
            actions.append(Action("parcel", "Talk to the clerk to receive Oak's Parcel", "interact"))
        return actions

    def execute(self, action):
        if self.game.block_once:
            self.game.block_once = False
            return
        state = self.game.state
        if action.key == "heal":
            state["party"][0]["hp"] = state["party"][0]["max_hp"]
        elif action.key == "parcel":
            state["events"].append("EVENT_GOT_OAKS_PARCEL")
            state["bag"].append({"name": "OAK'S PARCEL", "qty": 1})
        else:
            state["map_id"] = action.target["map"]
            state["map"] = MAPS[state["map_id"]]
            if state["map"] not in state["visited"]:
                state["visited"].append(state["map"])


class DemoPlanner:
    def plan(self, state, catalog, previous):
        hurt = state["party"][0]["hp"] < state["party"][0]["max_hp"]
        obj = dict(goal="Heal the party" if hurt else "Receive Oak's Parcel", focus="heal" if hurt else "progress",
                   target_map="VIRIDIAN_POKECENTER" if hurt else "VIRIDIAN_MART",
                   success={"kind": "healed", "value": True} if hurt else {"kind": "event", "value": "EVENT_GOT_OAKS_PARCEL"},
                   max_decisions=20)
        return Goal.parse(obj, catalog)


class DemoJev:
    def __init__(self, nav):
        self.nav = nav

    def choose(self, state, options):
        target = state["active_goal"]["target_map"]
        if state["map"] == target:
            return "heal" if "heal" in options else "parcel"
        return min((key for key in options if key.startswith("go:")),
                   key=lambda key: self.nav.hops(MAPS.index(key[3:]), target))


def run_demo(log):
    game = DemoGame()
    nav = DemoNavigation(game)
    agent = Agent(game, DemoPlanner(), DemoJev(nav), nav, None, log)
    for _ in range(20):
        agent.step()
        if game.snapshot()["milestone"] is None:
            if agent.goal and agent.goal.done(game.snapshot()):
                agent.finish_goal("complete")
            break
    assert game.snapshot()["milestone"] is None, "Demo did not reach the parcel event"
    assert agent.completed_goals == 2, "Demo must verify healing and parcel goals"
    assert any(not h["changed"] for h in agent.history), "Demo must include the failed movement"
    return agent

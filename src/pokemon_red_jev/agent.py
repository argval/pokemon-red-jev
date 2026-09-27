"""One active planner goal; Jev selects the next available game action."""

import io
import json
import os
from pathlib import Path
import random
import tempfile
import zipfile

from .controls import move_list_open, note_menu, party_unusable
from .goals import fallback_goal
from .models import ModelError


def alternate(choice, options, failures, location, probabilities):
    """If Jev's top pick already failed here three times, draw another option from its probabilities."""
    if failures.get(f"{location}:{choice}", 0) < 3 or not isinstance(probabilities, dict):
        return choice
    alternatives = []
    for key, value in probabilities.items():
        if key == choice or key not in options:
            continue
        try:
            alternatives.append((key, float(value)))
        except (TypeError, ValueError):
            continue
    if not alternatives:
        return choice
    roll = random.random() * sum(prob + 0.05 for _, prob in alternatives)
    for key, prob in alternatives:
        roll -= prob + 0.05
        if roll <= 0:
            return key
    return alternatives[-1][0]


class Agent:
    def __init__(self, game, planner, jev, navigation, controls, log):
        self.game, self.planner, self.jev = game, planner, jev
        self.navigation, self.controls, self.log = navigation, controls, log
        if controls is not None:
            controls.navigation = navigation
        self.goal = None
        self.goal_decisions = 0
        self.no_progress = 0
        self.previous = None
        self.milestone_id = None
        self.history = []
        self.failures = {}
        self.best_distance = None
        self.completed_goals = 0
        self.plans = 0
        self.replan_reason = "startup"
        self.started_healthy = False
        self.menu_seen = None

    def catalog(self, state):
        maps = [m["name"] for m in self.game.rom.maps.values()]
        destinations = {name: self.navigation.hops(state["map_id"], name) for name in maps}
        state["map_distances"] = {k: v for k, v in destinations.items() if v is not None and v <= 4}
        report = getattr(self.navigation, "field_move_needed", None)
        if report and (needed := report(state)):
            state["field_move_needed"] = needed
        return {"maps": maps, "events": sorted(self.game.data.events),
                "items": sorted(set(self.game.rom.items.values())),
                "interactions": [f"{state['map']}:npc:{o['index']}" for o in self.game.rom.maps[state["map_id"]]["objects"]] +
                                [f"{state['map']}:sign:{i}" for i, _ in enumerate(self.game.rom.maps[state["map_id"]]["signs"])] +
                                [f"{state['map']}:hidden:{i}" for i, _ in enumerate(self.game.rom.hidden.get(state["map_id"], []))]}

    def finish_goal(self, reason):
        self.previous = {"goal": self.goal.to_dict(), "outcome": reason, "decisions": self.goal_decisions}
        self.log("goal_end", **self.previous)
        self.completed_goals += reason == "complete"
        self.goal = None
        self.replan_reason = reason

    def _show(self, state):
        show = getattr(self.game, "show_overlay", None)
        if show:
            show(state, self.jev)

    def _remember_dialogue(self, state):
        memory = getattr(self.navigation, "memory", None)
        if not memory:
            return
        if state["mode"] == "overworld":
            memory.close()
            return
        if state["mode"] != "dialog":
            return
        screen = getattr(self.game, "screen", None)
        if screen:
            memory.hear(screen().get("dialog", ""))

    def _guard_menu(self, state, actions):
        """Close a battle move list, a useless party screen, or a menu that keeps returning."""
        if self.controls is None or state["mode"] == "overworld":
            self.menu_seen = None
            return False
        rows = state.get("screen") or []
        if move_list_open(rows):
            self.log("guard", reason="battle move list")
            self.controls.leave_menu()
            return True
        if party_unusable(rows, len(state.get("party") or [])):
            self.log("guard", reason="no pokemon can use this item")
            self.controls.leave_menu()
            return True
        if not any(getattr(action, "kind", None) == "menu" for action in actions):
            return False
        progress = (state.get("map"), tuple(state.get("events") or ()),
                    json.dumps(state.get("bag"), sort_keys=True), json.dumps(state.get("party"), sort_keys=True),
                    json.dumps(state.get("box"), sort_keys=True))
        self.menu_seen, leave = note_menu(self.menu_seen, tuple(rows), progress)
        if leave:
            self.log("guard", reason="menu repeated without a change")
            self.controls.leave_menu()
            return True
        return False

    def step(self):
        state = self.game.snapshot()
        self._remember_dialogue(state)
        if self.goal:
            state["active_goal"] = self.goal.to_dict()
        self._show(state)
        if state["mode"] == "overworld":
            self.navigation.update(state)
        milestone_id = (state.get("milestone") or {}).get("id")
        hp_fraction = sum(p["hp"] for p in state["party"]) / max(1, sum(p["max_hp"] for p in state["party"]))
        if self.goal:
            if self.goal.done(state):
                self.finish_goal("complete")
            elif milestone_id != self.milestone_id:
                self.finish_goal("story changed")
            elif state["mode"] == "overworld" and self.started_healthy and hp_fraction < .25:
                self.finish_goal("party health changed")
            elif self.goal_decisions >= self.goal.max_decisions:
                self.finish_goal("decision budget exhausted")
            elif self.no_progress >= 8:
                self.finish_goal("stalled")
        if state["mode"] == "busy":
            self.game.tick(8)
            return
        if state["mode"] == "boot":
            self.game.press("a", settle=30)
            return
        state["recent_actions"] = self.history[-8:]
        if self.goal is None and state["mode"] == "overworld":
            state.pop("active_goal", None)
            self._show(state)
            if self.planner is not None:
                self.plans += 1
                try:
                    self.goal = self.planner.plan(state, self.catalog(state), self.previous)
                except ModelError as exc:
                    self.log("planner_error", error=str(exc), fallback="temporary story goal")
                    self.goal = fallback_goal(state)
            else:
                self.goal = fallback_goal(state)
            self.goal_decisions = self.no_progress = 0
            self.best_distance = self.navigation.hops(state["map_id"], self.goal.target_map)
            self.milestone_id = milestone_id
            self.started_healthy = hp_fraction >= .25
            self.log("goal", reason=self.replan_reason, goal=self.goal.to_dict())
        active = self.goal or fallback_goal(state)
        state["active_goal"] = active.to_dict()
        self._show(state)
        actions = self.navigation.actions(state, active) if state["mode"] == "overworld" else self.controls.actions(state)
        if self._guard_menu(state, actions):
            return
        if not actions:
            self.log("no_actions", map=state["map"], mode=state["mode"])
            self.no_progress += 1
            self.game.tick(12)
            return
        location = f"{state['map']}:{state['x']},{state['y']}:{state['mode']}"
        options = {a.key: a.description + (f" Failed without state change here {count} times."
                                           if (count := self.failures.get(location + ':' + a.key, 0)) else "")
                   for a in actions}
        choice = self.jev.choose(state, options)
        if choice not in options:
            raise ModelError("Action selector returned an unavailable action")
        probabilities = (getattr(self.jev, "last", None) or {}).get("probabilities")
        picked = choice
        choice = alternate(choice, options, self.failures, location, probabilities)
        if choice != picked:
            self.log("retry", picked=picked, choice=choice, reason="top pick already failed here 3 times")
        last = getattr(self.jev, "last", None)
        if last:
            self.jev.last = {**last, "purpose": state["mode"], "picked": choice}
            self._show(state)
        action = next(a for a in actions if a.key == choice)
        self.log("action", map=state["map"], choice=choice, description=action.description)
        if action.kind == "field":
            if self.navigation.execute(action):
                self.controls.execute(action)
        elif action.kind in {"walk", "door", "interact"}:
            self.navigation.execute(action)
        else:
            self.controls.execute(action)
        after = self.game.snapshot()
        if after["mode"] == "overworld":
            self.navigation.update(after)
        changed = self.change_key(state) != self.change_key(after)
        key = location + ":" + action.key
        if not changed:
            self.failures[key] = self.failures.get(key, 0) + 1
        else:
            self.failures.pop(key, None)
        memory = getattr(self.navigation, "memory", None)
        if memory and state["mode"] == "overworld":
            if action.key.startswith(("door:", "exit:")) and after["mode"] == "overworld":
                memory.note_exit(state["map"], action.key, action.target.get("edges") or [], after["map"] != state["map"])
            if after["mode"] == "dialog" and action.kind in {"walk", "door"} and not memory.pending:
                memory.begin(f"{state['map']}:{action.key}:blocked")
            if after["mode"] == "dialog" and action.path and after.get("map") == state["map"]:
                goal_square = (action.target.get("x"), action.target.get("y")) if action.target.get("x") is not None else None
                memory.remember_pushback(state["map"], action.path, after["x"], after["y"], goal_square)
            if action.kind in {"item", "toss"}:
                memory.note_supply(action.target.get("name", "BAG"), changed)
            if action.target.get("switch") and changed:
                memory.switch_pressed = state["map"]
        if memory:
            label = (action.target or {}).get("label")
            if label in {"DEPOSIT", "WITHDRAW", "RELEASE"}:
                memory.pc_mode = label
            elif label in {"SEE YA", "LOG OFF"}:
                memory.pc_mode = None
        if after["mode"] == "dialog":
            self._remember_dialogue(after)
        self.history.append({"map": state["map"], "action": choice, "changed": changed,
                             "after": {"map": after["map"], "x": after["x"], "y": after["y"], "mode": after["mode"]}})
        self.history = self.history[-12:]
        if len(self.failures) > 256:
            del self.failures[next(iter(self.failures))]
        if self.goal and state["mode"] == "overworld":
            self.goal_decisions += 1
            distance = self.navigation.hops(after["map_id"], self.goal.target_map)
            closer = distance is not None and (self.best_distance is None or distance < self.best_distance)
            if closer:
                self.best_distance = distance
            useful = closer or state["events"] != after["events"] or state["bag"] != after["bag"] or self.goal.done(after)
            if self.goal.focus in {"heal", "train", "catch", "team"}:
                useful |= state["party"] != after["party"]
            self.no_progress = 0 if useful else self.no_progress + 1

    @staticmethod
    def change_key(state):
        return json.dumps({k: state.get(k) for k in ("map", "x", "y", "mode", "events", "bag", "party", "screen", "badges", "money")}, sort_keys=True)

    def save(self, path: Path):
        """Atomically commit emulator state and matching agent memory in one archive."""
        memory = {"version": 1, "visited": sorted(self.game.visited), "interactions": sorted(self.game.interactions),
                  "outside_map": self.game.outside_map, "last_map": self.game.last_map, "frames": self.game.frames,
                  "history": self.history, "failures": self.failures,
                  "previous": self.previous, "completed_goals": self.completed_goals,
                  "active_goal": self.goal.to_dict() if self.goal else None,
                  "route_memory": self.navigation.memory.to_dict() if hasattr(self.navigation, "memory") else None}
        state = io.BytesIO()
        self.game.pyboy.save_state(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".checkpoint-", delete=False) as f:
                temp = Path(f.name)
                with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("emulator.state", state.getvalue())
                    archive.writestr("agent.json", json.dumps(memory))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if temp and temp.exists():
                temp.unlink()
        self.log("save", path=str(path))

    def load(self, path: Path):
        with zipfile.ZipFile(path) as archive:
            memory = json.loads(archive.read("agent.json"))
            state = archive.read("emulator.state")
        if memory["version"] != 1:
            raise ValueError("Unsupported checkpoint version")
        self.game.pyboy.load_state(io.BytesIO(state))
        self.game.visited = set(memory["visited"])
        self.game.interactions = set(memory["interactions"])
        self.game.outside_map, self.game.last_map = memory["outside_map"], memory["last_map"]
        self.game.frames = memory["frames"]
        self.history, self.failures = memory["history"], memory["failures"]
        if hasattr(self.navigation, "memory"):
            self.navigation.memory.load(memory.get("route_memory"))
        self.completed_goals = memory["completed_goals"]
        self.previous = {"goal": memory["active_goal"], "outcome": "checkpoint restored"}
        self.goal = None  # Replan from the loaded observation; don't apply an old in-flight plan.
        self.replan_reason = "checkpoint restored"
        self.log("load", path=str(path))

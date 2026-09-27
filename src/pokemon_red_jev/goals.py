"""Small tasks proposed by the planner and verified against observed game state."""

from dataclasses import asdict, dataclass
from functools import cache
from importlib.resources import files
import json

FOCUSES = {"progress", "heal", "train", "catch", "shop", "explore", "team"}
KINDS = {"map", "event", "item", "healed", "level", "badge", "interaction"}


@dataclass
class Goal:
    goal: str
    focus: str
    target_map: str
    success: dict
    max_decisions: int = 20

    @classmethod
    def parse(cls, obj, catalog):
        if not isinstance(obj, dict) or set(obj) != {"goal", "focus", "target_map", "success", "max_decisions"}:
            raise ValueError("Goal must contain goal, focus, target_map, success, max_decisions only")
        if not isinstance(obj["goal"], str) or not 1 <= len(obj["goal"].strip()) <= 400:
            raise ValueError("Goal text must be 1..400 characters")
        if not isinstance(obj["focus"], str) or obj["focus"] not in FOCUSES:
            raise ValueError("Unknown goal focus")
        if not isinstance(obj["target_map"], str) or obj["target_map"] not in catalog["maps"]:
            raise ValueError("Unknown target map")
        if type(obj["max_decisions"]) is not int or not 1 <= obj["max_decisions"] <= 100:
            raise ValueError("Goal budget must be 1..100 decisions")
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
        return value in state["events"]
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
        return value in state["interactions"]
    raise ValueError(f"Unknown completion kind: {kind}")


@cache
def story():
    return json.loads(files(__package__).joinpath("story.json").read_text())


def current_milestone(state):
    m = next((m for m in story() if not complete(m["success"], state)), None)
    if m is None:
        return None
    bag = {i["name"] for i in state["bag"] if i["qty"] > 0}
    need = next((n for n in m.get("needs", []) if not bag.intersection(n["items"]) and not state.get(n.get("done_flag"))), None)
    return {**m, "missing_need": need}


def fallback_goal(state):
    """A temporary story task when the planner is unavailable; never claims completion."""
    m = state.get("milestone")
    if m:
        if need := m.get("missing_need"):
            return Goal(need["what"], "progress", need["map"],
                        {"kind": "any", "value": [{"kind": "item", "value": name} for name in need["items"]]}, 10)
        condition = m["success"]
        # The fallback is internal and can use compound story checks.
        return Goal(m["goal"], "progress", m["maps"][0], condition, 10)
    return Goal("Explore this area", "explore", state["map"],
                {"kind": "interaction", "value": "uncompleted-fallback"}, 10)

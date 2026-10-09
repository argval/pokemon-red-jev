"""One active planner goal; Jev selects the next available game action."""

from collections import deque
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import tempfile
import time
import zipfile

from .controls import move_list_open, note_menu, party_unusable
from .battle import damaging
from .battle_memory import BattleMemory
from .goals import (FOCUS_TTL, Goal, catch_reason, describe_situation, fallback_goal, focus_note,
                    intent_key, intent_options, team_plan, wild_field_move_learners)
from .llm.context_builder import formatPartyForPrompt
from .healing import field_healing
from .models import ModelError
from .navigation import Action
from .snorlax import snorlax_context
from .state.party import getPartySnapshot


def alternate(choice, options, failures, location, probabilities):
    """If Jev's top pick already failed here three times, draw another option from its probabilities."""
    if failures.get(f"{location}:{choice}", 0) < 3 or not isinstance(probabilities, dict):
        return choice
    alternatives = []
    for key, value in probabilities.items():
        if key == choice or key not in options:
            continue
        text = options.get(key) or ""
        if key.startswith("toss") or re.search(r"\b(RELEASE|TOSS)\b", text):
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


# The story fallback keeps the player walking. Ask the planner only when that walk is not enough.
# Choices (menus and dialog included, battles and forced single options excluded) without progress
# before the agent closes any menu and replans. The overworld-only stall check never sees menu loops.
# ponytail: fixed limit; lower it if loops still run long, raise it if long errands get cut off.
IDLE_LIMIT = 60

PLAN_REASONS = {"startup", "stalled", "decision budget exhausted", "party health changed",
                "repeated team losses", "checkpoint restored", "focus unavailable"}


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
        # States seen since the last progress. Landing on one again is a loop: it counts as a failure.
        # ponytail: fixed window (~32 decisions); widen it if long no-progress cycles slip through.
        self.recent = deque(maxlen=64)
        self.idle = 0
        # Party slots switched out against the current enemy Pokémon. Switching back only feeds it free hits.
        self.benched = {"foe": None, "slots": set()}
        self.battle_memory = BattleMemory()
        self.best_distance = None
        self.completed_goals = 0
        self.plans = 0
        self.replan_reason = "startup"
        self.started_healthy = False
        self.menu_seen = None
        self.observed = None
        self.pending_item = None
        self.item_fresh = False
        self.losses = {}
        self.wiped_now = False
        self.intent = None
        self.shop_money = None
        self.shop_at = 0
        self.pc_session = None
        self.pc_done = False
        self.team_sig = None
        self.team_at = 0
        self.overworld_map = None
        self.resume_walk = None
        self.pending_walk = None
        self.tried = {}
        self.best_routes = {}
        self.team_attempts = {}
        self.catch_throws = {"key": None, "count": 0}
        self.field_need = None
        self.team_errand = False

    @staticmethod
    def team_key(state):
        party = state.get("party") or []
        return {"lineup": sorted(p["species"] for p in party),
                "levels": sum(p["level"] for p in party),
                "moves": sorted(m["name"] for p in party for m in p.get("moves", []))}

    def _observe(self, state):
        """Observe changes even when a menu guard or a busy frame performed the action."""
        self._observe_battle(state)
        if state["mode"] == "overworld":
            self.navigation.update(state)
        state["boulder_gains"] = getattr(getattr(self.navigation, "memory", None), "boulder_gains", 0)
        self._settle_walk(state)
        party = state.get("party") or []
        wiped = bool(state.get("in_battle", state["mode"] == "battle") and party and
                     all(p["hp"] == 0 for p in party))
        if wiped and not self.wiped_now:
            previous = self.losses.get(state["map"], {})
            self.losses[state["map"]] = {"count": previous.get("count", 0) + 1, **self.team_key(state)}
        self.wiped_now = wiped
        # Opening the menu is not a use. Judge the session only after it has closed.
        if self.pending_item and state["mode"] == "overworld" and not self.item_fresh:
            self.navigation.memory.note_supply(self.pending_item["name"],
                                               self.pending_item["before"] != self.supply_key(state))
            self.pending_item = None
        useful = self.observed is not None and self.progress_key(state) != self.progress_key(self.observed)
        focus = (self.intent or {}).get("value") or (self.goal.focus if self.goal else None)
        if self.observed and focus == "heal":
            useful |= any(new["hp"] > old["hp"] or new.get("status") == "OK" != old.get("status")
                          for old, new in zip(self.observed.get("party") or [], party))
        if useful:
            self.tried.clear()
        if self.goal and (useful or self.goal.done(state)):
            self.no_progress = 0
        self.observed = state
        return useful

    def _observe_battle(self, state):
        """Keep battle messages and effects until the next turn can be judged."""
        screen = getattr(self.game, "screen", None)
        dialog = screen().get("dialog", "") if state["mode"] == "battle" and callable(screen) else ""
        outcome = self.battle_memory.observe(state, dialog)
        if outcome:
            self.log("battle_turn", **outcome)

    def _battle_recovery(self, state, actions):
        """After three fruitless turns, choose a legal attack or a viable retreat.
        Missing an attack stays retryable; PP expenditure alone cannot clear the guard.
        """
        battle = state.get("battle")
        context = self.battle_memory.context()
        if not battle or context["stalled_turns"] < 3 or getattr(self.jev, "manual", False):
            return None
        balls = [a for a in actions if a.key.startswith("ball:")]
        if battle.get("catchable") and balls:
            return balls[0]
        attacks = []
        for action in actions:
            estimate = action.target.get("damage_range")
            if action.kind != "battle_move" or action.target.get("blocked_reason") or not estimate or not estimate[1]:
                continue
            if battle.get("catchable") and estimate[0] >= battle["enemy"].get("hp", 0):
                continue
            score = sum(estimate) * action.target.get("hit_percent", 100) / 200
            score /= 1 + context["move_failures"].get(action.key, 0)
            attacks.append((score, action))
        if attacks:
            return max(attacks, key=lambda item: item[0])[1]
        switches = []
        estimate_move = getattr(self.controls, "_estimate", None)
        if callable(estimate_move):
            for action in actions:
                if action.kind != "switch":
                    continue
                mon = action.target
                incoming = self.controls._threat(battle["enemy"], mon)
                if incoming and incoming[0] >= mon.get("hp", 0):
                    continue
                estimates = [estimate_move(mon, battle["enemy"], m) for m in mon.get("moves") or []
                             if damaging(m) and m.get("pp")]
                best = max((e[1] for e in estimates if e is not None), default=0)
                if best:
                    switches.append((best, action))
        if switches:
            return max(switches, key=lambda item: item[0])[1]
        return next((a for a in actions if a.key == "run"), None)

    def progress_key(self, state):
        result = {k: state.get(k) for k in ("events", "bag", "badges", "box", "visited")}
        result["boulder_gains"] = state.get("boulder_gains", 0)
        result["team"] = self.team_key(state)
        focus = (self.intent or {}).get("value") or (self.goal.focus if self.goal else None)
        # Losing HP or spending PP is not progress. Count experience only when training/catching.
        if focus in {"train", "catch"}:
            result["training"] = sum(p.get("experience", 0) for p in state.get("party") or [])
        return json.dumps(result, sort_keys=True)

    def _settle_walk(self, state):
        """A speech can heal, start a battle, or send us back. Wait for its outcome."""
        pending = self.pending_walk
        if not pending or state["mode"] not in {"overworld", "battle"}:
            return
        self.pending_walk = None
        before, action = pending["before"], Action(**pending["action"])
        if state["mode"] == "battle":
            key = self.trial_key(before, action)
            self.tried[key] = max(0, self.tried.get(key, 0) - 1)
            if state["map"] == before["map"]:
                self.resume_walk = {"map": before["map"], "key": action.key, "tries": pending["tries"]}
            return
        memory = self.navigation.memory
        self.navigation.update(state)
        cleared = self._exit_cleared(before, state, action)
        tx, ty = action.path[-1][1:]
        advanced = abs(state["x"] - tx) + abs(state["y"] - ty) <= abs(before["x"] - tx) + abs(before["y"] - ty) - 2
        said = bool(memory.lines)
        if action.key.startswith(("door:", "exit:")) and (cleared or said and not advanced):
            memory.note_exit(before["map"], action.key, action.target.get("edges", []), cleared)
        if not cleared and said and not advanced:
            goal = (action.target.get("x"), action.target.get("y"))
            memory.remember_pushback(before["map"], action.path, state["x"], state["y"], goal)
            self.log("guard", reason="walk returned after dialogue", map=before["map"], choice=action.key)

    @staticmethod
    def supply_key(state):
        """Bag quantities and party vitals. Position and the open menu are not a use."""
        bag = ",".join(f"{item['name']}{item['qty']}" for item in state.get("bag") or [])
        party = ",".join(
            f"{mon.get('level')}{'+'.join(move.get('name', '') for move in mon.get('moves') or [])}"
            f"{mon.get('hp')}{mon.get('status')}"
            for mon in state.get("party") or [])
        return f"{bag}|{party}"

    def _recovery(self, state):
        state["losses"] = self.losses
        team = self.team_key(state)
        for place, loss in self.losses.items():
            if (loss["count"] >= 2 and team["lineup"] == loss["lineup"] and
                    team["levels"] - loss["levels"] < 5 and team["moves"] == loss["moves"]):
                state["recovery"] = {"map": place, "losses": loss["count"],
                                     "allowed_focuses": ["heal", "train", "catch", "shop", "explore", "team"],
                                     "reason": "The unchanged team lost here repeatedly. Change the lineup, learn a move, "
                                               "or gain five levels across the party before resuming progress."}
                return True
        return False

    def _recovery_goal(self, state):
        if hasattr(self.navigation, "training_target") and (target := self.navigation.training_target()):
            level = max((p["level"] for p in state["party"]), default=1)
            return Goal("Train the party before retrying the fight that defeated it", "train", target[0],
                        {"kind": "level", "value": min(100, level + 5)}, 20,
                        f"Re-level the wiped team: gain NORMAL coverage at Lv{min(100, level + 5)} before retrying.")
        routes = [(self.navigation.hops(state["map_id"], m["name"]), m["name"])
                  for m in self.game.rom.maps.values() if m["name"].startswith("ROUTE_")]
        reachable = [(distance, name) for distance, name in routes if distance is not None]
        target = min(reachable)[1] if reachable else state["map"]
        level = max((p["level"] for p in state["party"]), default=1)
        return Goal("Train the party before retrying the fight that defeated it", "train", target,
                    {"kind": "level", "value": min(100, level + 5)}, 20,
                    f"Re-level the wiped team: gain NORMAL coverage at Lv{min(100, level + 5)} before retrying.")

    def _attach_situation(self, state):
        """Derived facts for this choice: levels, matchups, field moves, wipes, and the last lines of text."""
        if state.get("mode") == "overworld" and hasattr(self.navigation, "update"):
            self.navigation.update(state)
        report = getattr(self.navigation, "field_move_needed", None)
        if report and state.get("mode") == "overworld":
            self.field_need = report(state)
        # Battles keep the last overworld answer: the Cut learner is caught in battle.
        state["field_move_needed"] = self.field_need
        memory = getattr(self.navigation, "memory", None)
        state["recent_dialog"] = list(getattr(memory, "dialog", []) or [])[-6:]
        rom = getattr(self.game, "rom", None)
        state["situation"] = describe_situation(
            state, getattr(rom, "species", None), getattr(rom, "items", None), getattr(rom, "effectiveness", None))
        if state["mode"] not in {"boot", "battle"} and hasattr(self.game, "sprites"):
            blocker = snorlax_context(state, self.game.sprites())
            if blocker:
                state["situation"]["route_blocker"] = blocker
        state["field_move_in_box"] = bool(state["situation"].get("withdraw_field_move"))
        state["team_plan"] = team_plan(state, getattr(rom, "effectiveness", None))
        state["situation"]["team_plan"] = state["team_plan"]
        context = self._team_context(state)
        if self.catch_throws["key"] != context:
            self.catch_throws = {"key": context, "count": 0}

    def _team_context(self, state):
        return json.dumps([(state.get("milestone") or {}).get("id"), self.owned_team(state),
                           state.get("field_move_needed")])

    def _team_goal(self, state):
        """Short errands near the current route; retry after a story/roster change or 15 minutes."""
        if (getattr(self.jev, "manual", False) or "EVENT_GOT_POKEDEX" not in state.get("events", [])
                or not state.get("party") or (state.get("milestone") or {}).get("missing_need")
                or sum(p["hp"] for p in state["party"]) < sum(p["max_hp"] for p in state["party"]) / 2
                or not hasattr(self.navigation, "service_target")):
            return None
        context, now = self._team_context(state), time.time()
        def available(focus):
            last = self.team_attempts.get(focus) or {}
            return last.get("key") != context or now - last.get("at", 0) >= 900
        plan, goal = state["team_plan"], None
        if available("team") and (plan["deposit"] is not None or plan["withdraw"] is not None):
            if center := self.navigation.service_target("POKECENTER", 2):
                _lvl = max((p.get("level", 0) for p in state.get("party") or []), default=1)
                goal = Goal(plan["reason"], "team", center, {"kind": "team_ready", "value": True}, 12,
                            f"{plan['reason']} Adds missing type coverage at Lv{_lvl}.")
        if goal is None and available("catch"):
            rom = self.game.rom
            maps = {md["name"] for md in rom.maps.values() if any(
                catch_reason(state, {**rom.species.get(w["species_id"], {}), "species":
                             rom.species.get(w["species_id"], {}).get("name"), "level": w["level"]}, rom.effectiveness)
                for w in md.get("wild") or [])}
            target = self.navigation.training_target(maps=maps) if maps else None
            distance = self.navigation.hops(state["map_id"], target[0]) if target else None
            balls = sum(i["qty"] for i in state["bag"] if i["name"].endswith("BALL") and i["name"] != "MASTER BALL")
            if target and distance is not None and distance <= 1 and balls and self.catch_throws["count"] < 5:
                _lvl = max((p.get("level", 0) for p in state.get("party") or []), default=1)
                goal = Goal("Catch one useful backup or coverage Pokémon; then resume the story", "catch", target[0],
                            {"kind": "owned_count", "value": len(self.owned_team(state)) + 1}, 12,
                            f"Adds a new type for coverage at Lv{_lvl}; check learned moves before using it.")
            elif maps and not balls and state.get("money", 0) >= 600 and len(state["bag"]) < 20 and available("shop"):
                if mart := self.navigation.service_target("_MART", 2):
                    goal = Goal("Buy up to five Poké Balls for a useful team addition", "shop", mart,
                                {"kind": "item", "value": "POKé BALL"}, 12)
        if goal:
            self.team_attempts[goal.focus] = {"key": context, "at": now}
            self.team_errand = True
            self.pc_done = False
        return goal

    def _team_action(self, state, actions):
        """Execute the concrete team errand through the same menu/button drivers as other actions."""
        labels = {a.target.get("label"): a for a in actions}
        keys = {a.key: a for a in actions}
        plan = state.get("team_plan") or {}
        if "POKECENTER" in state["map"] and state["mode"] != "battle":
            deposit, withdraw = plan.get("deposit"), plan.get("withdraw")
            if state["mode"] == "overworld" and self.team_errand and self.goal.focus == "team":
                return next((a for a in actions if "OpenPokemonCenterPC" in a.description), None)
            if "DEPOSIT" in labels and "WITHDRAW" in labels:
                return labels.get("DEPOSIT" if deposit is not None else "WITHDRAW" if withdraw is not None else "SEE YA!") or keys.get("cancel")
            if any("PC" in str(label) for label in labels) and "LOG OFF" in labels:
                return (next((a for label, a in labels.items() if label and ("BILL" in label or "SOMEONE" in label)), None)
                        if deposit is not None or withdraw is not None else labels["LOG OFF"])
            mode = getattr(getattr(self.navigation, "memory", None), "pc_mode", None)
            slot = deposit if mode == "DEPOSIT" else withdraw
            if mode in {"DEPOSIT", "WITHDRAW"} and any(a.target.get("pc_slot") is not None for a in actions):
                if slot is None:
                    return keys.get("cancel")
                return next((a for a in actions if a.target.get("pc_slot") == slot), keys.get("scroll") or keys.get("cancel"))
        if self.team_errand and self.goal and self.goal.focus == "shop":
            if self.goal.done(state):
                return labels.get("QUIT") or keys.get("cancel")
            if state["mode"] == "overworld":
                return next((a for a in actions if a.kind == "interact" and "CLERK" in a.description), None)
            if "buy:yes" in keys:
                count = re.search(r"Buy (\d+)", keys["buy:yes"].description)
                if count and int(count[1]) < min(5, max(1, (state["money"] - 200) // 200)):
                    return keys.get("buy:more")
                return keys["buy:yes"]
            return labels.get("POKé BALL") or labels.get("POKE BALL") or labels.get("BUY")
        if (state.get("battle") or {}).get("kind") == "wild":
            enemy = state["battle"].get("enemy")
            if self.catch_throws["count"] < 5 and catch_reason(state, enemy, self.game.rom.effectiveness):
                ball = next((a for a in actions if a.key.startswith("ball:") and a.target.get("name") != "MASTER BALL"), None)
                if ball:
                    ball.description += " Team plan: " + catch_reason(state, enemy, self.game.rom.effectiveness)
                    return ball
        if self.team_errand and self.goal and self.goal.focus == "catch" and state["mode"] == "overworld":
            if state["map"] == self.goal.target_map:
                return keys.get("grass")
        if "nickname" in " ".join(state.get("screen") or []).lower():
            return labels.get("NO")
        return None

    def _take_resume(self, state, actions):
        """Continue a walk a wild battle interrupted, up to three times, if it is still available."""
        walk = self.resume_walk
        if not walk or state.get("mode") != "overworld" or walk.get("map") != state.get("map") or walk.get("tries", 0) >= 3:
            return None
        action = next((candidate for candidate in actions if candidate.key == walk.get("key")), None)
        if action is None:
            return None
        walk["tries"] += 1
        return action

    def _objective_hops(self, state):
        milestone = state.get("milestone") or {}
        target = (self.goal.target_map if self.goal else (milestone.get("missing_need") or {}).get("map"))
        maps = [target] if target else milestone.get("maps") or []
        if not maps or not hasattr(self.navigation, "hops"):
            return None
        found = []
        for name in maps:
            try:
                distance = self.navigation.hops(state["map_id"], name)
            except (KeyError, TypeError):
                continue
            if isinstance(distance, int):
                found.append(distance)
        return min(found) if found else None

    def _intent_expired(self, state, value):
        if value == "team" and self.pc_done:
            return True
        party = state.get("party") or []
        if value == "heal" and party and all(mon.get("hp") == mon.get("max_hp") and mon.get("status", "OK") == "OK" for mon in party):
            return True
        previous = self.overworld_map
        if value == "shop" and previous and "MART" in previous and "MART" not in state.get("map", ""):
            self._finish_shop(state)
            return True
        return False

    @staticmethod
    def owned_team(state):
        return sorted(p["species"] for p in [*(state.get("party") or []), *(state.get("box") or [])])

    def _finish_pc(self, state):
        self.pc_done = True
        self.pc_session = None
        self.team_sig = self.owned_team(state)
        self.team_at = time.time()
        self.navigation.memory.pc_mode = None

    def _finish_shop(self, state):
        self.shop_money = state.get("money") or 0
        self.shop_at = time.time()
        if self.intent and self.intent.get("value") == "shop":
            self.intent["age"] = FOCUS_TTL

    def _pick_intent(self, state, options):
        if not state.get("party") and "progress" in options:
            return "progress"
        if (self.planner is not None or self.team_errand) and self.goal and self.goal.focus in options:
            return self.goal.focus
        choose = getattr(self.jev, "focus", None)
        if choose is not None and len(options) > 1:
            value = choose(state, options)
            if value not in options:
                raise ModelError("Focus selector returned an unavailable focus")
            return value
        if self.goal and self.goal.focus in options:
            return self.goal.focus
        if state.get("recovery") and "train" in options:
            return "train"
        return next(iter(options))

    def _choose_intent(self, state):
        """Use the active planner goal's focus, or let Jev hold a bounded focus without a planner."""
        held = self.intent
        expired = bool(held) and self._intent_expired(state, held["value"])
        now = time.time()
        options = intent_options(state, shop_money=self.shop_money if now - self.shop_at < 900 else None,
                                 objective_hops=self._objective_hops(state))
        if (not self.team_errand and self.team_sig == self.owned_team(state)
                and not (state.get("field_move_in_box") and now - self.team_at >= 900)):
            options.pop("team", None)
        if hasattr(self.navigation, "_nearest"):
            regions = self.navigation.regions
            origin = regions.landing(regions.at(state["map_id"], state["x"], state["y"]))
            for token, focuses in (("POKECENTER", ("heal", "team")), ("_MART", ("shop",))):
                if self.navigation._nearest(origin, token) is None:
                    for focus in focuses:
                        options.pop(focus, None)
        if self.overworld_map != state["map"]:
            for key in list(self.tried):
                if key.startswith(f"{state['map']}:push:"):
                    del self.tried[key]
        key = intent_key(state)
        planned = self.goal.focus if (self.planner is not None or self.team_errand) and self.goal else None
        if planned and planned not in options and self.goal == fallback_goal(state):
            # The story fallback is blocked (say, progress needs Cut): let Jev pick an available focus within the
            # fallback's budget. Ending it here asked the planner again at once, over and over.
            planned = None
        if planned and (planned not in options or expired and held["value"] == planned):
            self.finish_goal("focus unavailable")
            self.intent = None
            return None
        if (held and not expired and held["value"] in options and held["key"] == key
                and (planned == held["value"] or planned is None and held["age"] < FOCUS_TTL)):
            held["age"] += 1
            self.overworld_map = state.get("map")
            return held["value"]
        value = self._pick_intent(state, options)
        if held and held["value"] != value:
            self.resume_walk = None
        self.intent = {"value": value, "key": key, "age": 0}
        self.pc_done = False
        self.overworld_map = state.get("map")
        self.log("intent", focus=value)
        return value

    @staticmethod
    def trial_key(state, action):
        key = f"{state['map']}:{action.key}"
        if action.key.startswith("push:"):
            key += f":{action.target.get('x')},{action.target.get('y')}"
        return key

    def _routine_action(self, state, actions):
        """Finish setup and take an unambiguous verified route without asking a model."""
        if len(actions) == 1:
            return actions[0]
        if getattr(self.jev, "manual", False):
            return None
        blocker = (state.get("situation") or {}).get("route_blocker")
        if (state["mode"] == "overworld" and blocker and not blocker["has_flute"]
                and abs(state["x"] - blocker["x"]) + abs(state["y"] - blocker["y"]) <= 2):
            exits = [a for a in actions if a.key.startswith(("exit:", "door:"))]
            if exits:
                return min(exits, key=lambda a: ("Leads toward the objective" not in a.description, len(a.path)))
        action = self._team_action(state, actions)
        if action and self.failures.get(f"{state['map']}:{state['x']},{state['y']}:{state['mode']}:{action.key}", 0) < 3:
            return action
        if state["mode"] != "overworld":
            if not state.get("party"):
                labels = {a.target.get("label"): a for a in actions}
                if "NEW NAME" in labels:
                    return labels.get("RED") or labels.get("BLUE")
                if "NEW GAME" in labels and "CONTINUE" not in labels:
                    return labels["NEW GAME"]
                text = " ".join(state.get("screen") or [])
                if "YOUR NAME?" in text or "RIVAL's NAME?" in text:
                    keys = {a.key: a for a in actions}
                    return keys.get("name:done") or keys.get("letter:J")
            return None
        if state.get("current_focus") == "progress" or self.team_errand:
            toward = [a for a in actions if a.kind in {"walk", "door", "interact"}
                      and "Leads toward the objective" in a.description and "by the map layout" not in a.description]
            # Adjacent doorway tiles can lead to the same room. Prefer the shorter walk.
            routes = {tuple(sorted(a.target.get("edges") or [a.key])) for a in toward}
            if len(routes) == 1:
                for action in sorted(toward, key=lambda a: len(a.path)):
                    location = f"{state['map']}:{state['x']},{state['y']}:{state['mode']}:{action.key}"
                    if self.tried.get(self.trial_key(state, action), 0) < 2 and self.failures.get(location, 0) < 2:
                        return action
        return None

    def _exit_cleared(self, state, after, action):
        if after["map"] != state["map"]:
            return True
        target = action.target or {}
        regions = getattr(self.navigation, "regions", None)
        # A teleport pad stays on this map. It worked when we left the starting area.
        if action.key.startswith("door:") and target.get("map") == state.get("map_id") and regions is not None:
            if (after["x"], after["y"]) == (state["x"], state["y"]):
                return False
            origin = regions.at(state["map_id"], state["x"], state["y"])
            arrived = regions.at(after["map_id"], after["x"], after["y"])
            pad = target.get("x"), target.get("y")
            far = None not in pad and abs(after["x"] - pad[0]) + abs(after["y"] - pad[1]) > 1
            return bool(far and arrived is not None and arrived != origin)
        if not regions or (after["x"], after["y"]) == (state["x"], state["y"]):
            return False
        arrived = regions.at(after["map_id"], after["x"], after["y"])
        source = regions.landing(regions.at(state["map_id"], state["x"], state["y"]))
        for edge in action.target.get("edges", []):
            destination = tuple(map(int, edge.split(">")[1].split(":")))
            landing = regions.landing(destination)
            if arrived == destination or arrived in landing - source:
                return True
        return False

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

    def planner_brief(self, state):
        """The facts a short-term goal needs. The screen, the full event list, and move ids are not among them."""
        party = getPartySnapshot(state)
        milestone = state.get("milestone") or {}
        kept = {key: milestone.get(key) for key in ("id", "goal", "maps", "success", "missing_need") if key in milestone}
        brief = {"map": state.get("map"), "x": state.get("x"), "y": state.get("y"), "badges": state.get("badges"),
                 "money": state.get("money"), "party": party, "party_text": formatPartyForPrompt(party),
                 "bag": state.get("bag"), "milestone": kept,
                 "nearby_maps": state.get("map_distances") or {}, "visited": state.get("visited"),
                 "events": state.get("events") or [], "interactions": state.get("interactions") or [],
                 "recent_actions": state.get("recent_actions"), "owned_count": len(self.owned_team(state))}
        if state.get("field_move_needed"):
            brief["field_move_needed"] = state["field_move_needed"]
            rom = getattr(self.game, "rom", None)
            learners = wild_field_move_learners(state, getattr(rom, "maps", None), getattr(rom, "species", None),
                                                {state.get("map"), *(state.get("map_distances") or {})})
            if learners:
                brief["wild_field_move_learners"] = learners
        if state.get("recovery"):
            brief["recovery"] = state["recovery"]
        if state.get("situation"):
            brief["situation"] = state["situation"]
        if state.get("healing"):
            brief["healing"] = state["healing"]
        return brief

    def _attach_healing(self, state):
        if state["mode"] in {"battle", "boot", "busy"} or getattr(self.jev, "manual", False):
            return
        center, hops = None, None
        if hasattr(self.navigation, "service_target"):
            if getattr(self.navigation, "state", None) is None:
                self.navigation.update(state)
            center = self.navigation.service_target("POKECENTER")
            if center:
                hops = self.navigation.hops(state["map_id"], center)
        state["healing"] = field_healing(state, center, hops)

    def planner_catalog(self, state):
        """Identifiers the planner may use: nearby maps, the story step's maps, and its success event."""
        full = self.catalog(state)
        milestone = state.get("milestone") or {}
        maps = set(state.get("map_distances") or {})
        maps.update(milestone.get("maps") or [])
        need = milestone.get("missing_need") or {}
        if need.get("map"):
            maps.add(need["map"])
        maps.add(state["map"])

        def events_in(condition, found):
            if not isinstance(condition, dict):
                return
            if condition.get("kind") in {"all", "any"}:
                for child in condition.get("value") or []:
                    events_in(child, found)
            elif condition.get("kind") == "event" and condition.get("value") in full["events"]:
                found.add(condition["value"])

        events = set()
        events_in(milestone.get("success"), events)
        events.update(name for name in need.get("events", []) if name in full["events"])
        items = {item["name"] for item in state.get("bag") or []}
        items.update(need.get("items") or [])
        return {"maps": sorted(maps), "events": sorted(events),
                "items": sorted(item for item in items if item in full["items"]),
                "interactions": full["interactions"]}

    def finish_goal(self, reason):
        self.previous = {"goal": self.goal.to_dict(), "outcome": reason, "decisions": self.goal_decisions}
        self.log("goal_end", **self.previous)
        self.completed_goals += reason == "complete"
        self.goal = None
        self.team_errand = False
        self.resume_walk = None
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
            self.pc_session = None
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
        if state["mode"] != "battle" and ("POKECENTER" in state["map"] or self.pc_session
                                          or any("LOG OFF" in row for row in rows)):
            sig = json.dumps([[(p["species"], p["level"]) for p in state.get("party") or []],
                              [p["species"] for p in state.get("box") or []], state.get("bag")], sort_keys=True)
            if not self.pc_session or self.pc_session["sig"] != sig:
                self.pc_session = {"sig": sig, "steps": 0}
            if self.pc_session["steps"] >= 15:
                self.log("guard", reason="PC session exceeded 15 choices without progress")
                self.controls.leave_menu()
                self._finish_pc(self.game.snapshot())
                return True
            self.pc_session["steps"] += 1
        progress = (state.get("map"), tuple(state.get("events") or ()),
                    json.dumps(state.get("bag"), sort_keys=True), json.dumps(state.get("party"), sort_keys=True),
                    json.dumps(state.get("box"), sort_keys=True))
        self.menu_seen, leave = note_menu(self.menu_seen, tuple(rows), progress)
        if leave:
            self.log("guard", reason="menu repeated without a change")
            self.controls.leave_menu()
            if self.pc_session:
                self._finish_pc(self.game.snapshot())
            return True
        return False

    def step(self):
        state = self.game.snapshot()
        self._observe(state)
        recovering = self._recovery(state)
        self._remember_dialogue(state)
        self._attach_situation(state)
        state["active_goal"] = (self.goal or fallback_goal(state)).to_dict()
        self._show(state)
        if state["mode"] == "overworld":
            self.navigation.update(state)
        self._attach_healing(state)
        milestone_id = (state.get("milestone") or {}).get("id")
        hp_fraction = (sum(p["hp"] for p in state["party"]) / max(1, sum(p["max_hp"] for p in state["party"]))
                       if state["party"] else 1)
        if self.goal:
            if self.goal.done(state) and (not self.team_errand or state["mode"] == "overworld"):
                self.finish_goal("complete")
            elif milestone_id != self.milestone_id:
                self.finish_goal("story changed")
            elif state["mode"] == "overworld" and self.started_healthy and hp_fraction < .25:
                self.finish_goal("party health changed")
            elif state["mode"] == "overworld" and recovering and self.goal.focus == "progress":
                self.finish_goal("repeated team losses")
            elif state["mode"] == "overworld" and self.goal_decisions >= self.goal.max_decisions:
                self.finish_goal("decision budget exhausted")
            elif state["mode"] == "overworld" and self.team_errand and self.goal.focus == "catch" and self.catch_throws["count"] >= 5:
                self.finish_goal("catch attempt limit reached")
            elif state["mode"] == "overworld" and self.no_progress >= 8:
                self.finish_goal("stalled")
        if state["mode"] == "busy":
            self.game.tick(8)
            return
        if state["mode"] == "boot":
            self.game.press("a", settle=30)
            return
        state["recent_actions"] = self.history[-8:]
        if self.goal is None and state["mode"] == "overworld":
            walking = fallback_goal(state)
            state["active_goal"] = walking.to_dict()
            if hp_fraction < .25:
                self.replan_reason = "party health changed"
            self.goal = self._team_goal(state)
            if self.goal is not None:
                pass
            elif state["party"] and self.planner is not None and self.replan_reason in PLAN_REASONS:
                self.plans += 1
                state["agent_status"] = "Planning next step; story task shown"
                self._show(state)
                try:
                    catalog = self.planner_catalog(state)
                    self.goal = self.planner.plan(self.planner_brief(state), catalog, self.previous)
                except ModelError as exc:
                    self.log("planner_error", error=str(exc), fallback="temporary story goal")
                    self.goal = walking
                finally:
                    state.pop("agent_status", None)
            else:
                self.goal = walking
            if recovering and self.goal.focus == "progress":
                self.goal = self._recovery_goal(state)
            self.goal_decisions = self.no_progress = 0
            self.best_distance = self.navigation.hops(state["map_id"], self.goal.target_map)
            self.milestone_id = milestone_id
            self.started_healthy = hp_fraction >= .25
            self.log("goal", reason=self.replan_reason, goal=self.goal.to_dict())
        active = self.goal or fallback_goal(state)
        state["active_goal"] = active.to_dict()
        state["current_focus"] = (self.intent or {}).get("value") or active.focus
        if state["mode"] == "overworld":
            state["current_focus"] = self._choose_intent(state)
            if state["current_focus"] is None:
                return
        if self.pending_item:
            state["using_item"] = self.pending_item["name"]
        self._show(state)
        wanted = (catch_reason(state, (state.get("battle") or {}).get("enemy"),
                               getattr(getattr(self.game, "rom", None), "effectiveness", None))
                  and self.catch_throws["count"] < 5)
        if state.get("battle"):
            # Move facts and battle instructions warn against a knockout only when a ball will actually be offered.
            # Otherwise "don't KO it" with no ball leaves only status moves, and Jev stalls with them while losing HP.
            state["battle"]["catchable"] = bool(wanted) and any(
                i["name"].endswith("BALL") and i["name"] != "MASTER BALL" and i.get("qty") for i in state.get("bag") or [])
        state["manual_control"] = bool(getattr(self.jev, "manual", False))
        actions = self.navigation.actions(state, active) if state["mode"] == "overworld" else self.controls.actions(state)
        if state.get("battle"):
            self._observe_battle(state)
            if any(a.kind in {"battle_move", "switch", "battle_item"} or a.key in {"run", "struggle"} for a in actions):
                outcome = self.battle_memory.finish(state)
                if outcome:
                    self.log("battle_turn", **outcome)
            state["battle"]["turn_memory"] = self.battle_memory.context()
        if not getattr(self.jev, "manual", False):
            # Never release, deposit a protected battler/HM user, or spend balls on unwanted duplicates.
            actions = [a for a in actions if a.target.get("label") != "RELEASE"
                       and not (a.target.get("pc_mode") == "DEPOSIT" and a.target.get("pc_slot") in state["team_plan"]["keep"])
                       and not (a.key.startswith("ball:") and (not wanted or a.target.get("name") == "MASTER BALL"))]
            enemy = (state.get("battle") or {}).get("enemy") or {}
            foe = ((state.get("battle") or {}).get("enemy_id") or
                   [enemy.get("species"), enemy.get("level"), enemy.get("max_hp")])
            if state["mode"] == "overworld" or state["mode"] == "battle" and foe != self.benched["foe"]:
                self.benched = {"foe": foe, "slots": set()}
            actions = [a for a in actions if not (a.key.startswith("switch:") and a.target.get("slot") in self.benched["slots"])]
            if state.get("battle") and not actions:
                fallback = getattr(self.controls, "battle_fallback", None)
                if callable(fallback):
                    actions = fallback(state)
        if state["mode"] == "overworld":
            for action in actions:
                action.description = focus_note(state["current_focus"], action.description)
        if self._guard_menu(state, actions):
            return
        if not actions:
            self.log("no_actions", map=state["map"], mode=state["mode"])
            self.no_progress += state["mode"] == "overworld"
            self.game.tick(12)
            return
        location = f"{state['map']}:{state['x']},{state['y']}:{state['mode']}"
        attempts = dict(self.failures)
        if state["mode"] == "overworld":
            pool = [action for action in actions if self.tried.get(self.trial_key(state, action), 0) < 5
                    or action.key == "field:STRENGTH" or "Leads toward the objective" in action.description]
            if pool and any(action.kind != "toss" for action in pool):
                actions = pool
            for action in actions:
                count = self.tried.get(self.trial_key(state, action), 0)
                if count:
                    action.description += f" Already tried {count} time(s) without progress. Choose a different action."
                key = location + ":" + action.key
                attempts[key] = max(attempts.get(key, 0), count)
        options = {a.key: a.description + (f" Tried here {count} times without progress (nothing changed, or it looped back to a recent state)."
                                           if (count := self.failures.get(location + ':' + a.key, 0)) else "")
                   for a in actions}
        resumed = self._take_resume(state, actions)
        recovery = self._battle_recovery(state, actions)
        routine = None if resumed else recovery or self._routine_action(state, actions)
        if recovery:
            self.log("guard", reason="three battle turns without useful effect", choice=recovery.key)
        if resumed:
            choice = resumed.key
            self.log("resume", map=state["map"], choice=choice, tries=self.resume_walk["tries"])
        else:
            if state["mode"] == "overworld":
                self.resume_walk = None
            if routine:
                choice = routine.key
                if self.jev is not None:
                    self.jev.last = {"purpose": "routine", "picked": choice, "probabilities": None}
            else:
                choice = self.jev.choose(state, options)
        if choice not in options:
            raise ModelError("Action selector returned an unavailable action")
        probabilities = None if resumed or routine else (getattr(self.jev, "last", None) or {}).get("probabilities")
        picked = choice
        choice = alternate(choice, options, attempts, location, probabilities)
        if choice != picked:
            self.log("retry", picked=picked, choice=choice, reason="top pick already tried 3 times without progress")
        last = getattr(self.jev, "last", None)
        if last:
            self.jev.last = {**last, "purpose": state["mode"], "picked": choice}
            self._show(state)
        action = next(a for a in actions if a.key == choice)
        if state.get("battle"):
            self.battle_memory.start(action, state["battle"])
        if choice.startswith("ball:"):
            self.catch_throws["count"] += 1
        if choice.startswith("switch:"):
            self.benched["slots"].add(state["battle"]["active_slot"])
        tried_key = self.trial_key(state, action)
        if state["mode"] == "overworld" and action.key != "field:STRENGTH" and not resumed:
            self.tried[tried_key] = self.tried.get(tried_key, 0) + 1
        self.log("action", map=state["map"], choice=choice, description=action.description,
                 source="resume" if resumed else "routine" if routine else "controller")
        if state["mode"] == "overworld" and action.kind in {"item", "toss"} and hasattr(self.navigation, "memory"):
            self.pending_item = {"name": action.target.get("name", "BAG"), "before": self.supply_key(state)}
            self.item_fresh = True
        if action.kind == "field" or action.kind == "item" and action.path:
            if self.navigation.execute(action):
                self.controls.execute(action)
        elif action.kind in {"walk", "door", "interact"}:
            self.navigation.execute(action)
        else:
            self.controls.execute(action)
        after = self.game.snapshot()
        useful = self._observe(after)
        self.item_fresh = False
        if after["mode"] == "overworld":
            self.navigation.update(after)
        before_key, after_key = (hashlib.sha1(self.change_key(snap).encode()).hexdigest()[:16] for snap in (state, after))
        changed = before_key != after_key
        key = location + ":" + action.key
        memory = getattr(self.navigation, "memory", None)
        # Re-talking someone already in said opens dialog (a "change") but earns nothing.
        retalk = (action.kind == "interact" and memory is not None and
                  bool(memory.said.get(f"{state['map']}:{action.key}")))
        if memory and state["mode"] == "overworld":
            if action.key.startswith(("door:", "exit:")) and after["mode"] == "overworld":
                memory.note_exit(state["map"], action.key, action.target.get("edges") or [], self._exit_cleared(state, after, action))
            if action.kind in {"walk", "door"} and action.path and after["mode"] in {"dialog", "busy"}:
                memory.begin(f"{state['map']}:{action.key}:blocked")
                self.pending_walk = {"before": {k: state[k] for k in ("map", "map_id", "x", "y")},
                                     "action": asdict(action), "tries": (self.resume_walk or {}).get("tries", 0)}
            if action.target.get("switch") and changed:
                memory.switch_pressed = state["map"]
        label = (action.target or {}).get("label")
        if memory:
            if label in {"DEPOSIT", "WITHDRAW", "RELEASE"}:
                memory.pc_mode = label
        if (re.match(r"^(SEE YA|LOG OFF)", label or "") or
                action.key == "cancel" and any("LOG OFF" in row for row in state.get("screen") or [])):
            self._finish_pc(after)
        if label == "QUIT" and any("BUY" in row for row in state.get("screen") or []):
            self._finish_shop(after)
        interrupted = (state["mode"] == "overworld" and action.kind in {"walk", "door"} and after["mode"] == "battle"
                       and after.get("map") == state.get("map"))
        if interrupted:
            self.tried[tried_key] = max(0, self.tried.get(tried_key, 0) - 1)
            prior = self.resume_walk["tries"] if self.resume_walk and self.resume_walk.get("key") == action.key else 0
            self.resume_walk = {"map": state["map"], "key": action.key, "tries": prior}
        elif state["mode"] == "overworld":
            self.resume_walk = None
        if after["mode"] == "dialog":
            self._remember_dialogue(after)
        if self.goal and state["mode"] == "overworld":
            self.goal_decisions += 1
            distance = self.navigation.hops(after["map_id"], self.goal.target_map)
            closer = distance is not None and (self.best_distance is None or distance < self.best_distance)
            if closer:
                self.best_distance = distance
            route_key = f"{milestone_id}:{self.goal.target_map}"
            previous_best = self.best_routes.get(route_key)
            if distance is not None:
                if previous_best is not None and distance < previous_best:
                    self.tried.clear()
                self.best_routes[route_key] = min(distance, previous_best) if previous_best is not None else distance
            useful |= closer
        # Reward: progress (an event, item, team change, or a map closer to the goal) clears the window.
        # A change that lands on a state already seen since then is a loop and is penalized like no change.
        if useful:
            self.recent.clear()
        looped = (changed and not useful and action.key != "grass" and "battle" not in (state["mode"], after["mode"])
                  and after_key in self.recent)
        self.recent.extend((before_key, after_key))
        if not changed or retalk or looped:
            self.failures[key] = self.failures.get(key, 0) + 1
        else:
            self.failures.pop(key, None)
        if len(self.failures) > 256:
            del self.failures[next(iter(self.failures))]
        if looped:
            self.log("loop", map=state["map"], choice=choice, count=self.failures[key])
        self.history.append({"map": state["map"], "action": choice, "changed": changed, "looped": looped,
                             "after": {"map": after["map"], "x": after["x"], "y": after["y"], "mode": after["mode"]}})
        self.history = self.history[-12:]
        if self.goal and (useful or self.goal.done(after)):
            self.no_progress = 0
        elif self.goal and state["mode"] == "overworld":
            self.no_progress += 1
        if useful:
            self.idle = 0
        elif len(actions) > 1 and "battle" not in (state["mode"], after["mode"]):
            self.idle += 1
        if self.idle >= IDLE_LIMIT:
            self.idle = 0
            self.log("guard", reason=f"no progress in {IDLE_LIMIT} choices", map=after["map"], mode=after["mode"])
            if after["mode"] != "overworld" and self.controls is not None:
                self.controls.leave_menu()
            if self.goal:
                self.finish_goal("stalled")

    @staticmethod
    def change_key(state):
        return json.dumps({k: state.get(k) for k in ("map", "x", "y", "mode", "events", "bag", "party", "screen", "badges", "money")}, sort_keys=True)

    def save(self, path: Path):
        """Atomically commit emulator state and matching agent memory in one archive."""
        memory = {"version": 1, "visited": sorted(self.game.visited), "interactions": sorted(self.game.interactions),
                  "outside_map": self.game.outside_map, "last_map": self.game.last_map, "frames": self.game.frames,
                  "history": self.history, "failures": self.failures,
                  "battle_memory": self.battle_memory.to_dict(),
                  "losses": self.losses, "wiped_now": self.wiped_now, "pending_item": self.pending_item,
                  "intent": self.intent, "shop_money": self.shop_money, "shop_at": self.shop_at,
                  "pc_session": self.pc_session, "pc_done": self.pc_done,
                  "team_sig": self.team_sig, "team_at": self.team_at, "overworld_map": self.overworld_map,
                  "team_attempts": self.team_attempts, "catch_throws": self.catch_throws,
                  "resume_walk": self.resume_walk, "pending_walk": self.pending_walk,
                  "tried": self.tried, "best_routes": self.best_routes,
                  "recent": list(self.recent), "idle": self.idle,
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
        self.battle_memory.load(memory.get("battle_memory"))
        self.losses = memory.get("losses", {})
        self.wiped_now = memory.get("wiped_now", False)
        self.intent = memory.get("intent")
        self.shop_money = memory.get("shop_money")
        self.shop_at = memory.get("shop_at", 0)
        self.pc_session = memory.get("pc_session")
        self.pc_done = memory.get("pc_done", False)
        self.team_sig = memory.get("team_sig")
        self.team_attempts = memory.get("team_attempts", {})
        self.catch_throws = memory.get("catch_throws", {"key": None, "count": 0})
        self.team_errand = False
        self.team_at = memory.get("team_at", 0)
        self.overworld_map = memory.get("overworld_map")
        self.resume_walk = memory.get("resume_walk")
        self.pending_walk = memory.get("pending_walk")
        self.tried = memory.get("tried", {})
        self.best_routes = memory.get("best_routes", {})
        self.recent = deque(memory.get("recent", []), maxlen=64)
        self.idle = memory.get("idle", 0)
        self.pending_item = memory.get("pending_item")
        self.item_fresh = False
        self.observed = None
        if hasattr(self.navigation, "memory"):
            self.navigation.memory.load(memory.get("route_memory"))
        self.completed_goals = memory["completed_goals"]
        self.previous = {"goal": memory["active_goal"], "outcome": "checkpoint restored"}
        self.goal = None  # Replan from the loaded observation; don't apply an old in-flight plan.
        self.replan_reason = "checkpoint restored"
        self.log("load", path=str(path))

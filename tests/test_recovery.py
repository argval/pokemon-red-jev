"""Session and puzzle regressions: uv run python -m unittest discover -s tests."""

from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pokemon_red_jev.navigation import Action, Navigation, RouteMemory, boulder_actions, route_fact
from pokemon_red_jev.controls import quantity_actions
from test_core import new_agent


class Floor:
    w, h, pairs, ledges = 30, 20, set(), []

    def tile(self, x, y):
        return 0 if 0 <= x < self.w and 0 <= y < self.h else -1

    def walkable(self, x, y):
        return self.tile(x, y) == 0


class RecoveryChecks(unittest.TestCase):
    def test_shop_quantity_price_is_not_the_wallet_balance(self):
        actions = quantity_actions({"money": 2575}, "MONEY <ED>2575 POKé BALL <ED>200 ×02 <ED>400 TAKE YOUR TIME")
        self.assertIn("for ¥400.", actions[0].description)
        self.assertIn("You have ¥2575.", actions[0].description)

    def test_pc_limit_counts_different_screens_and_resets_on_real_changes(self):
        agent, records = new_agent()
        agent.navigation.memory = RouteMemory()
        closed = []
        agent.controls = SimpleNamespace(leave_menu=lambda: closed.append(True))
        state = agent.game.state
        state.update(map="VIRIDIAN_POKECENTER", mode="dialog", box=[{"species": "PIDGEY"}])
        menu = [Action("menu:0", "Choose", "menu")]
        for i in range(15):
            state["screen"] = [f"PC screen {i}"]
            self.assertFalse(agent._guard_menu(state, menu))
        state["party"][0]["level"] += 1
        self.assertFalse(agent._guard_menu(state, menu))
        self.assertEqual(agent.pc_session["steps"], 1)
        for i in range(14):
            state["screen"] = [f"PC changed {i}"]
            self.assertFalse(agent._guard_menu(state, menu))
        self.assertTrue(agent._guard_menu(state, menu))
        self.assertEqual(len(closed), 1)
        self.assertTrue(agent.pc_done)
        self.assertEqual(agent.team_sig, agent.owned_team(state))
        self.assertIn("15 choices", records[-1]["reason"])
        state["mode"] = "overworld"
        self.assertFalse(agent._guard_menu(state, []))
        self.assertIsNone(agent.pc_session)

    def test_completed_pc_visit_blocks_swapping_loops_but_allows_new_need(self):
        agent, _ = new_agent()
        agent.navigation.memory = RouteMemory()
        state = agent.game.state
        state["box"] = [deepcopy(state["party"][0])]
        available = []
        agent._pick_intent = lambda state, options: available.append(options) or next(iter(options))
        with patch("pokemon_red_jev.agent.time.time", return_value=1000):
            agent._finish_pc(state)
            agent._choose_intent(state)
        self.assertNotIn("team", available[-1])
        state["party"][0]["level"] += 1
        state["party"], state["box"] = state["box"], state["party"]
        state["field_move_in_box"] = True
        for now, allowed in [(1899, False), (1900, True)]:
            agent.intent = None
            with patch("pokemon_red_jev.agent.time.time", return_value=now):
                agent._choose_intent(state)
            self.assertEqual("team" in available[-1], allowed)
        state["field_move_in_box"] = False
        state["box"].append({"species": "RATTATA"})
        agent.intent = None
        agent._choose_intent(state)
        self.assertIn("team", available[-1])

    def test_shop_cooldown_expires_with_money_or_time_and_survives_save(self):
        agent, _ = new_agent()
        game = agent.game
        game.state["money"] = 1000
        available = []
        agent._pick_intent = lambda state, options: available.append(options) or next(iter(options))
        with patch("pokemon_red_jev.agent.time.time", return_value=1000):
            agent._finish_shop(game.state)
        game.pyboy = SimpleNamespace(save_state=lambda f: f.write(b"state"), load_state=lambda f: None)
        game.visited, game.interactions, game.outside_map, game.last_map, game.frames = set(), set(), 0, 0, 0
        agent.pc_session, agent.pc_done = {"sig": "unchanged", "steps": 14}, True
        agent.team_sig, agent.team_at = ["BULBASAUR"], 999
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.zip"
            agent.save(path)
            restored, _ = new_agent()
            restored.game = game
            restored.load(path)
            self.assertEqual((restored.shop_money, restored.shop_at), (1000, 1000))
            self.assertEqual(restored.pc_session["steps"], 14)
            self.assertEqual((restored.team_sig, restored.team_at, restored.pc_done), (["BULBASAUR"], 999, True))
        for now, money, allowed in [(1899, 1199, False), (1899, 1200, True), (1900, 1000, True)]:
            agent.intent = None
            game.state["money"] = money
            with patch("pokemon_red_jev.agent.time.time", return_value=now):
                agent._choose_intent(game.state)
            self.assertEqual("shop" in available[-1], allowed)

    def test_long_boulder_progress_survives_replan_and_checkpoint_without_counting_loops(self):
        agent, _ = new_agent()
        memory = agent.navigation.memory = RouteMemory()
        state = agent.game.state
        state.update(map="VICTORY_ROAD_1F", events=[])
        boulder = {"index": 5, "x": 2, "y": 13}
        agent.navigation.update = lambda s: memory.observe_boulders(s, Floor(), [boulder])
        agent._observe(deepcopy(state))
        self.assertEqual(memory.boulder_gains, 0)
        for x in range(3, 15):
            boulder["x"] = x
            self.assertTrue(agent._observe(deepcopy(state)))
            agent.no_progress = 7
        self.assertEqual(memory.boulder_gains, 12)
        agent.tried["repeat"] = 4
        for x in [13, 14, 13, 14]:
            boulder["x"] = x
            self.assertFalse(agent._observe(deepcopy(state)))
        self.assertEqual(agent.tried["repeat"], 4)
        key = memory.push_key(state["map"], dict(x=14, y=13, direction="up"))
        memory.stuck_pushes[key] = 2
        restored = RouteMemory()
        restored.load(memory.to_dict())
        self.assertEqual(restored.boulder_gains, 12)
        self.assertEqual(restored.stuck_pushes[key], 2)
        restored.observe_boulders(dict(state, map="VICTORY_ROAD_2F"), Floor(), [])
        boulder["x"] = 2
        restored.observe_boulders(state, Floor(), [boulder])
        self.assertEqual(restored.boulder_gains, 12)
        self.assertEqual(restored.stuck_pushes[key], 2)

    def test_push_can_enter_a_hole_that_walking_must_avoid(self):
        actions = boulder_actions(Floor(), (15, 6), [{"index": 1, "x": 16, "y": 6}],
                                  {(16, 6), (17, 6)}, [], [{"x": 17, "y": 6, "kind": "hole"}],
                                  known={"STRENGTH"}, badges=8, strength_on=True)
        right = next(a for a in actions if a[0] == "push:1:right")
        self.assertIn("hole in the floor", right[1])
        self.assertFalse(right[3]["strands"])

    def test_only_executed_stranding_pushes_are_remembered(self):
        nav = Navigation.__new__(Navigation)
        nav.memory, nav.seen = RouteMemory(), set()
        position = {"wCurMap": 1, "wXCoord": 0, "wYCoord": 0}
        boulder = {"index": 1, "x": 1, "y": 0}
        def push(_):
            position["wXCoord"] = 1
            boulder["x"] = 2
        nav.game = SimpleNamespace(u8=position.__getitem__, mode=lambda: "overworld", tick=lambda *args: None,
                                   sprites=lambda: [boulder], rom=SimpleNamespace(maps={1: {"name": "CAVE"}}),
                                   pyboy=SimpleNamespace(button_press=lambda _: None, button_release=lambda _: None))
        target = dict(x=1, y=0, direction="right", boulder=1, strands=True)
        action = Action("push:1:right", "Push", "walk", [("right", 1, 0)], target)
        self.assertFalse(nav.execute(action))
        self.assertEqual(nav.memory.stuck_pushes, {})
        nav.game.pyboy.button_press = push
        self.assertTrue(nav.execute(action))
        self.assertEqual(nav.memory.stuck_pushes, {"CAVE:1,0:right": 1})

    def test_relative_routes_qualify_gates_and_avoid_misleading_mansion_facts(self):
        self.assertIn("toward", route_fact(2, 3))
        self.assertIn("away", route_fact(4, 3))
        self.assertIn("Same distance", route_fact(3, 3))
        self.assertIn("dead end", route_fact(None, 3))
        self.assertIn("Leaves", route_fact(1, 0))
        self.assertIn("map layout", route_fact(2, 3, gated=True))
        self.assertEqual(route_fact(4, 3, gated=True, switch_building=True), "")
        self.assertEqual(route_fact(3, 3, gated=True, switch_building=True), "")

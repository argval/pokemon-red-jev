"""Offline checks: run `uv run python -m unittest discover -s tests -v`."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

from pokemon_red_jev.agent import Agent, alternate
from pokemon_red_jev.cli import Manual, load_env
from pokemon_red_jev.controls import (Controls, catch_chance, damage, describe_effect, escape_chance, hit_chance,
                                      menu_note, move_list_open, note_menu, party_unusable, pc_summary, shop_note,
                                      status_note)
from pokemon_red_jev.demo import DemoGame, DemoJev, DemoNavigation, DemoPlanner, run_demo
from pokemon_red_jev.goals import Goal, complete, current_milestone, next_gym, story
from pokemon_red_jev.game import Game, quiet_rows
from pokemon_red_jev.models import (CodexPlanner, CursorPlanner, Jev, ModelError, Planner, action_instructions,
                                    codex_failure, codex_json, cursor_failure, cursor_json, post_json)
from pokemon_red_jev.navigation import (Action, Navigation, RouteMemory, boulder_actions, find_path,
                                        floor_phrase, scripted_rival_exit, supply_actions)
from pokemon_red_jev.regions import Regions, switch_distances, switch_fact, switch_reach
from pokemon_red_jev.stage import LINE_H, PANEL_H, PanelRenderer, overlay_lines


def new_agent(planner=None):
    game = DemoGame()
    nav = DemoNavigation(game)
    records = []
    agent = Agent(game, planner or DemoPlanner(), DemoJev(nav), nav, None,
                  lambda kind, **fields: records.append({"kind": kind, **fields}))
    return agent, records


class CoreChecks(unittest.TestCase):
    def test_demo_uses_loop_and_verifies_two_goals(self):
        records = []
        agent = run_demo(lambda kind, **fields: records.append((kind, fields)))
        self.assertEqual(agent.completed_goals, 2)
        self.assertEqual(agent.plans, 1)
        self.assertIn("goal_end", [k for k, _ in records])

    def test_goal_validation_and_observed_completion(self):
        agent, _ = new_agent()
        state = agent.game.snapshot()
        catalog = agent.catalog(state)
        goal = DemoPlanner().plan(state, catalog, None)
        self.assertFalse(goal.done(state))
        healthy = deepcopy(state)
        healthy["party"][0]["hp"] = 20
        self.assertTrue(goal.done(healthy))
        healthy["party"] = []
        self.assertFalse(goal.done(healthy), "An empty party is not a healed party")
        for change in [{"target_map": "IMAGINARY"}, {"max_decisions": True}, {"max_decisions": 1000},
                       {"success": {"kind": "event", "value": "EVENT_INVENTED"}},
                       {"success": {"kind": "level", "value": False}}, {"focus": []},
                       {"success": {"kind": "map", "value": "ROUTE_1"}},
                       {"success": {"kind": "python", "value": "print(1)"}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Goal.parse({**goal.to_dict(), **change}, catalog)
        item = {"kind": "item", "value": "OAK'S PARCEL"}
        state["bag"] = [{"name": "OAK'S PARCEL", "qty": 0}]
        self.assertFalse(complete(item, state))
        state["bag"][0]["qty"] = 1
        self.assertTrue(complete(item, state))

    def test_story_has_33_milestones_and_specific_badge_checks(self):
        self.assertEqual(len(story()), 33)
        state = DemoGame().snapshot()
        self.assertEqual(current_milestone(state)["id"], "meet_oak")
        state["badges"] = 2
        self.assertFalse(complete({"kind": "badge", "value": 1}, state))
        self.assertTrue(complete({"kind": "badge", "value": 2}, state))
        for milestone in story():
            self.assertIsInstance(complete(milestone["success"], state), bool)

    def test_overlay_matches_stream_panel_sections_and_font(self):
        state = DemoGame().snapshot()
        state["milestone"] = current_milestone(state)
        state["badges"] = 0b101
        controller = SimpleNamespace(calls=1200, input_tokens=1_000_000, last={
            "purpose": "overworld", "picked": "talk", "probabilities": {"talk": 0.72, "walk": 0.2}})
        with patch.dict(os.environ, {"JEV_PRICE_PER_M": "0.042"}):
            lines = overlay_lines(state, controller, 36)
        self.assertEqual(lines[0], "# JEV PLAYS POKEMON RED")
        self.assertEqual(lines[1], "~ 2 badges | milestone 0/33")
        self.assertEqual(lines[2], "~ 1,200 jev calls | 1,000,000 tokens")
        self.assertEqual(lines[3], "~ total cost: $0.042 USD")
        self.assertLess(lines.index("# MILESTONE"), lines.index("# GOAL"))
        self.assertIn("~ none yet", lines)
        state["active_goal"] = {"goal": "Speak to Professor Oak"}
        with patch.dict(os.environ, {"JEV_PRICE_PER_M": "0.042"}):
            lines = overlay_lines(state, controller, 36)
        milestone_at, goal_at = lines.index("# MILESTONE"), lines.index("# GOAL")
        self.assertLess(milestone_at, goal_at)
        self.assertTrue(any(state["milestone"]["goal"].split()[0] in line for line in lines[milestone_at:goal_at]))
        self.assertEqual(lines[goal_at + 1], "Speak to Professor Oak")
        self.assertLessEqual(len(lines), PANEL_H // LINE_H)
        state["party"] = [dict(nickname=f"N{i}", species="PIDGEY", level=12, hp=30, max_hp=40) for i in range(6)]
        state["milestone"]["goal"] = "Defeat Brock, the Pewter City Gym Leader (Rock/Ground Pokémon, Lv12-14). Water, Grass and Fighting moves are strong against him."
        state["active_goal"] = {"goal": "Enter Pewter Gym and fight Brock after healing at the Pokemon Center so the party is ready."}
        controller.last = {"purpose": "overworld", "picked": "door:0", "probabilities": {"door:0": 0.5, "npc:1": 0.3, "explore": 0.2}}
        full = overlay_lines(state, controller, 36)
        self.assertLessEqual(len(full), PANEL_H // LINE_H)
        self.assertLess(full.index("# JEV DECISION (overworld)"), full.index("~  50% door:0"))
        self.assertIn("# WHERE", lines)
        self.assertIn("ROUTE 1", lines)
        team = next(line for line in lines if "BULBASAUR" in line)
        self.assertIn("BULBASAUR (JEV)", team)
        self.assertIn("Lv5", team)
        self.assertIn("8/20", team)
        self.assertLess(lines.index("# JEV DECISION (overworld)"), lines.index("~  72% talk"))
        self.assertLess(lines.index("~  72% talk"), lines.index("~  20% walk"))
        image = PanelRenderer(bytes([0xFF] * 8), 0, {0x80: "A"}).render(["# A", "~ A"])
        self.assertEqual(list(image[0:4]), [21, 21, 20, 255])
        self.assertEqual(list(image[(1 * 288) * 4:(1 * 288) * 4 + 3]), [124, 196, 155])
        self.assertEqual(list(image[(11 * 288) * 4:(11 * 288) * 4 + 3]), [154, 154, 146])

    def test_budget_replans_but_never_counts_timeout_as_success(self):
        agent, records = new_agent()
        agent.step()
        agent.goal.max_decisions = 1
        agent.step()
        ends = [r for r in records if r["kind"] == "goal_end"]
        self.assertEqual(ends[0]["outcome"], "decision budget exhausted")
        self.assertEqual(agent.completed_goals, 0)
        self.assertEqual(agent.plans, 2)

    def test_stall_replans_once_and_retains_failed_action_evidence(self):
        agent, records = new_agent()
        agent.navigation.execute = lambda action: None
        for _ in range(9):
            agent.step()
        self.assertEqual(agent.plans, 2)
        self.assertTrue(any(r.get("outcome") == "stalled" for r in records))
        self.assertTrue(agent.failures)
        self.assertEqual(agent.completed_goals, 0)

    def test_planner_failure_has_bounded_fallback(self):
        planner = SimpleNamespace(plan=lambda *args: (_ for _ in ()).throw(ModelError("timeout")))
        agent, records = new_agent(planner)
        agent.step()
        self.assertEqual(agent.goal.max_decisions, 10)
        self.assertEqual(agent.goal.target_map, "VIRIDIAN_MART")
        agent.step()
        self.assertEqual(agent.plans, 1)
        self.assertTrue(any(r["kind"] == "planner_error" for r in records))

    def test_a_completed_goal_walks_on_without_another_plan(self):
        calls = {"n": 0}

        def plan(state, catalog, previous):
            calls["n"] += 1
            self.assertIn("nearby_maps", state)
            self.assertNotIn("screen", state)
            self.assertLess(len(catalog["maps"]), 10)
            return Goal("Cross Route 1", "progress", "VIRIDIAN_CITY", {"kind": "map", "value": "VIRIDIAN_CITY"}, 8)

        agent, _ = new_agent(SimpleNamespace(plan=plan))
        agent.step()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(agent.goal.target_map, "VIRIDIAN_CITY")
        agent.finish_goal("complete")
        agent.step()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(agent.goal.target_map, "VIRIDIAN_MART")

    def test_models_emit_correct_payloads_and_reject_invalid_answers(self):
        logs = []
        log = lambda kind, **fields: logs.append((kind, fields))
        env = {"TYPESAFE_API_KEY": "test-key", "LLM_API_KEY": "test-key", "LLM_MODEL": "test/model", "JEV_MIN_INTERVAL_MS": "0"}
        agent, _ = new_agent()
        state = agent.game.snapshot()
        catalog = agent.catalog(state)
        expected = DemoPlanner().plan(state, catalog, None)
        with patch.dict(os.environ, env):
            planner, jev = Planner(log), Jev(log)
        with patch("pokemon_red_jev.models.post_json") as post:
            post.return_value = {"choices": [{"message": {"content": json.dumps(expected.to_dict())}}]}
            actual = planner.plan(state, catalog, None)
            self.assertEqual(actual, expected)
            payload = post.call_args.args[2]
            self.assertEqual(payload["model"], "test/model")
            self.assertEqual(len(payload["messages"]), 2)
            post.return_value = {"answers": {"action": {"type": "choice", "choice": "walk", "probabilities": {"walk": .9, "stay": .1}}},
                                  "usage": {"inputTokens": 42}}
            self.assertEqual(jev.choose(state, {"walk": "Move", "stay": "Wait"}), "walk")
            self.assertEqual(jev.calls, 1)
            self.assertEqual(jev.input_tokens, 42)
            self.assertEqual(jev.last["picked"], "walk")
            self.assertEqual(jev.choose(state, {"walk": "Move", "stay": "Wait"}), "walk")
            self.assertEqual(jev.calls, 1, "A cached choice is not another billed call")
            self.assertEqual(post.call_args.args[2]["questions"]["action"]["type"], "choice")
            post.return_value["answers"]["action"]["choice"] = "invented"
            with self.assertRaises(ModelError):
                jev.choose({**state, "changed": True}, {"walk": "Move", "stay": "Wait"})
            healthy = deepcopy(state)
            healthy["party"][0]["hp"] = 20
            post.return_value = {"choices": [{"message": {"content": json.dumps(expected.to_dict())}}]}
            with self.assertRaises(ModelError):
                planner.plan(healthy, catalog, None)
        self.assertNotIn("test-key", json.dumps(logs))

    def test_codex_planner_uses_local_cli_and_validates_goal(self):
        agent, _ = new_agent()
        state = agent.game.snapshot()
        catalog = agent.catalog(state)
        expected = DemoPlanner().plan(state, catalog, None)
        planner = CodexPlanner(lambda *args, **kwargs: None)
        def cli_run(args, **kwargs):
            self.assertIn("--ignore-user-config", args)
            self.assertIn("--ephemeral", args)
            self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
            self.assertEqual(args[-1], "-")
            self.assertIn("previous_goal", kwargs["input"])
            path = Path(args[args.index("--output-last-message") + 1])
            path.write_text(json.dumps(expected.to_dict()))
            return SimpleNamespace(returncode=0)
        with patch("pokemon_red_jev.models.shutil.which", return_value="/usr/bin/codex"), \
             patch("pokemon_red_jev.models.subprocess.run", side_effect=cli_run):
            self.assertEqual(planner.plan(state, catalog, None), expected)
        with patch("pokemon_red_jev.models.shutil.which", return_value=None), self.assertRaises(ValueError):
            codex_json("test", {}, 1)
        with patch("pokemon_red_jev.models.shutil.which", return_value="/usr/bin/codex"), \
             patch("pokemon_red_jev.models.subprocess.run", return_value=SimpleNamespace(returncode=7, stderr="", stdout="")), \
             self.assertRaisesRegex(ModelError, "exit 7"):
            planner.plan(state, catalog, None)
        limit = SimpleNamespace(returncode=1, stderr="ERROR: You’ve hit your usage limit. Try again at 7:15 PM.\n", stdout="")
        with patch("pokemon_red_jev.models.shutil.which", return_value="/usr/bin/codex"), \
             patch("pokemon_red_jev.models.subprocess.run", return_value=limit), \
             self.assertRaisesRegex(ModelError, "usage limit"):
            planner.plan(state, catalog, None)
        self.assertIn("usage limit", codex_failure(1, "ERROR: You’ve hit your usage limit. Upgrade to Pro.", ""))

    def test_cursor_planner_uses_local_cli_and_validates_goal(self):
        agent, _ = new_agent()
        state = agent.game.snapshot()
        catalog = agent.catalog(state)
        expected = DemoPlanner().plan(state, catalog, None)
        planner = CursorPlanner(lambda *args, **kwargs: None)

        def cli_run(args, **kwargs):
            self.assertEqual(args[1:7], ["-p", "--mode", "ask", "--output-format", "json", "--sandbox"])
            self.assertEqual(args[args.index("--sandbox") + 1], "enabled")
            self.assertIn("--trust", args)
            self.assertIn("--workspace", args)
            self.assertEqual(args[args.index("--model") + 1], "composer-2.5")
            self.assertIn("previous_goal", args[-1])
            envelope = {"type": "result", "subtype": "success", "is_error": False,
                        "result": json.dumps(expected.to_dict())}
            return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

        with patch("pokemon_red_jev.models.cursor_agent_binary", return_value="/usr/bin/agent"), \
             patch("pokemon_red_jev.models.subprocess.run", side_effect=cli_run):
            self.assertEqual(planner.plan(state, catalog, None), expected)
        with patch("pokemon_red_jev.models.cursor_agent_binary", return_value=None), self.assertRaises(ValueError):
            cursor_json("test", 1, "composer-2.5")
        with patch("pokemon_red_jev.models.cursor_agent_binary", return_value="/usr/bin/agent"), \
             patch("pokemon_red_jev.models.subprocess.run",
                   return_value=SimpleNamespace(returncode=7, stderr="", stdout="")), \
             self.assertRaisesRegex(ModelError, "exit 7"):
            planner.plan(state, catalog, None)
        limit = SimpleNamespace(returncode=1, stderr="", stdout=json.dumps({
            "type": "result", "is_error": True, "result": "You've hit your usage limit. Try again later."}))
        with patch("pokemon_red_jev.models.cursor_agent_binary", return_value="/usr/bin/agent"), \
             patch("pokemon_red_jev.models.subprocess.run", return_value=limit), \
             self.assertRaisesRegex(ModelError, "usage limit"):
            planner.plan(state, catalog, None)
        self.assertIn("usage limit", cursor_failure(1, "", json.dumps({
            "is_error": True, "result": "You've hit your usage limit. Upgrade to Pro."})))

    def test_http_json_auth_retries_and_error_redaction(self):
        class Response(io.BytesIO):
            pass
        with patch("pokemon_red_jev.models.urlopen", return_value=Response(b'{"ok":true}')) as request:
            self.assertEqual(post_json("https://example.test/api", "secret", {"a": 1}), {"ok": True})
            self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer secret")
            self.assertEqual(json.loads(request.call_args.args[0].data), {"a": 1})
        error = HTTPError("https://example.test", 503, "secret", {}, None)
        with patch("pokemon_red_jev.models.urlopen", side_effect=error) as request, patch("pokemon_red_jev.models.time.sleep"):
            with self.assertRaises(ModelError) as raised:
                post_json("https://example.test/api", "secret", {})
            self.assertEqual(request.call_count, 2)
            self.assertNotIn("secret", str(raised.exception))
        with self.assertRaises(ValueError):
            post_json("http://example.test/api", "secret", {})

    def test_pathfinding_obstacles_ledges_and_edge_exit(self):
        grid = SimpleNamespace(w=5, h=3, tile=lambda x, y: 0, walkable=lambda x, y: 0 <= x < 5 and 0 <= y < 3,
                               pairs=set(), ledges=[])
        path = find_path(grid, (0, 1), lambda x, y: (x, y) == (4, 1), {(2, 1)})
        self.assertEqual(path[-1][1:], (4, 1))
        self.assertNotIn((2, 1), [p[1:] for p in path])
        self.assertIsNone(find_path(grid, (0, 1), lambda x, y: x == 5))
        self.assertEqual(find_path(grid, (0, 1), lambda x, y: x == 5, exit_ok=lambda x, y: x == 5)[-1][1], 5)
        grid.ledges = [("right", 0, 0)]
        self.assertEqual(find_path(grid, (0, 1), lambda x, y: (x, y) == (2, 1)), [("right", 2, 1)])
        self.assertIsNone(find_path(grid, (0, 1), lambda x, y: (x, y) == (2, 1), {(2, 1)}))
        wide = SimpleNamespace(w=5, h=2, tile=lambda x, y: 1 if y == 1 and x in {1, 2, 3} else 0,
                               walkable=lambda x, y: 0 <= x < 5 and 0 <= y < 2, pairs=set(), ledges=[])
        around = find_path(wide, (0, 1), lambda x, y: (x, y) == (4, 1), grass=lambda x, y: wide.tile(x, y) == 1, grass_cost=1)
        self.assertNotIn((1, 1), [step[1:] for step in around])
        self.assertEqual(around[-1][1:], (4, 1))

    def test_damage_respects_immunity_and_same_type_bonus(self):
        mon = dict(level=10, attack=20, defense=20, special=20, types=["NORMAL"])
        move = dict(power=40, type="NORMAL")
        self.assertEqual(damage(mon, mon, move, 0), 0)
        self.assertGreater(damage(mon, mon, move, 1), damage({**mon, "types": []}, mon, move, 1))

    def test_wram_party_bag_event_and_screen_decoding(self):
        from pokemon_red_jev.data import Data
        data = object.__new__(Data)
        data.symbols = {"wPartyCount": 0xc000, "wPartyMons": 0xc100, "wPartyMonNicks": 0xc300,
                        "wNumBagItems": 0xc400, "wBagItems": 0xc410, "wTileMap": 0xc500}
        data.charmap = {0x80: "A", 0x81: "B", 0x7f: " ", 0xed: "▶", 0x79: "┌"}
        game = object.__new__(Game)
        game.data, game.memory = data, bytearray(65536)
        game.rom = SimpleNamespace(species={1: {"name": "BULBASAUR", "types": ["GRASS", "POISON"]}},
                                   moves={1: {"name": "TACKLE", "pp": 35}}, items={1: "POTION"})
        game.memory[0xc000] = 1
        game.memory[0xc100] = 1
        game.memory[0xc101:0xc103] = bytes([1, 2])
        game.memory[0xc122:0xc124] = bytes([1, 44])
        game.memory[0xc121] = 12
        game.memory[0xc104] = 8
        game.memory[0xc108] = 1
        game.memory[0xc11d] = 0x40 | 7
        game.memory[0xc300:0xc303] = bytes([0x80, 0x81, 0x50])
        game.memory[0xc400] = 1
        game.memory[0xc410:0xc412] = bytes([1, 3])
        game.memory[0xc124:0xc12c] = bytes([0, 49, 0, 40, 0, 30, 0, 35])
        p = game.party()[0]
        self.assertEqual((p["nickname"], p["hp"], p["max_hp"], p["level"], p["status"]), ("AB", 258, 300, 12, "POISON"))
        self.assertEqual((p["attack"], p["defense"], p["speed"], p["special"]), (49, 40, 30, 35))
        self.assertEqual((p["moves"][0]["pp"], p["moves"][0]["max_pp"]), (7, 42))
        self.assertEqual(game.bag(), [{"name": "POTION", "qty": 3}])
        game.memory[0xc500:0xc500 + 360] = bytes([0x7f] * 360)
        game.memory[0xc500] = 0x79
        game.memory[0xc500 + 2 * 20 + 3] = 0xed
        game.memory[0xc500 + 14 * 20 + 2] = 0x80
        self.assertEqual(game.screen()["cursor"], (3, 2))
        self.assertTrue(game.screen()["box"])
        self.assertEqual(game.screen()["dialog"].strip(), "A")

    def test_low_health_replans_at_an_overworld_boundary(self):
        agent, records = new_agent()
        agent.step()
        agent.game.state["party"][0]["hp"] = 1
        agent.step()
        self.assertEqual(agent.plans, 2)
        self.assertTrue(any(r.get("outcome") == "party health changed" for r in records))

    def test_checkpoint_roundtrip_and_atomic_failure(self):
        agent, _ = new_agent()
        game = agent.game
        game.visited, game.interactions = {"ROUTE_1"}, set()
        game.frames, game.outside_map, game.last_map = 42, 0, 0
        loaded = []
        game.pyboy = SimpleNamespace(save_state=lambda f: f.write(b"emulator bytes"), load_state=lambda f: loaded.append(f.read()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.zip"
            agent.step()
            agent.save(path)
            original = path.read_bytes()
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.read("emulator.state"), b"emulator bytes")
            game.visited.clear()
            agent.load(path)
            self.assertEqual(game.visited, {"ROUTE_1"})
            self.assertEqual(loaded, [b"emulator bytes"])
            self.assertIsNone(agent.goal)
            with patch("pokemon_red_jev.agent.os.replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
                agent.save(path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_env_does_not_execute_or_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ALREADY": "set"}):
            path = Path(directory) / ".env"
            path.write_text('ALREADY=changed\nNEW_TEST_ENV="$(whoami)" # comment\nEMPTY_TEST_ENV=\n')
            load_env(path)
            self.assertEqual(os.environ["ALREADY"], "set")
            self.assertEqual(os.environ["NEW_TEST_ENV"], "$(whoami)")
            self.assertEqual(os.environ["EMPTY_TEST_ENV"], "")

    def test_regions_keep_warps_separate_and_edges_directed(self):
        maps = {i: dict(id=i, name=f"MAP_{i}", objects=[], warps=[], connections=[]) for i in range(5)}
        maps[0]["warps"] = [dict(x=2, y=0, map=1, warp=0)]
        maps[1]["warps"] = [dict(x=0, y=0, map=99, warp=0)]
        game = SimpleNamespace(rom=SimpleNamespace(maps=maps, spinners={3: {(2, 0): (4, 0)}}))
        graph = Regions(game)
        for mid in maps:
            graph.grids[mid] = SimpleNamespace(w=5, h=1, tile=lambda x, y: 0,
                                              walkable=lambda x, y: 0 <= x < 5 and y == 0, pairs=set(), ledges=[])
            graph.blocked[mid] = set()
        graph.grids[2] = SimpleNamespace(w=3, h=1, tile=lambda x, y: 1 if x == 0 else 2 if x == 1 else 0,
                                        walkable=lambda x, y: x in {0, 2} and y == 0,
                                        pairs=set(), ledges=[("right", 1, 2)])
        graph.grids[4] = SimpleNamespace(w=2, h=1, tile=lambda x, y: x,
                                        walkable=lambda x, y: x in {0, 1} and y == 0,
                                        pairs={(0, 1), (1, 0)}, ledges=[])
        for mid in maps:
            graph.label(mid)
        graph.link()
        left, right = graph.at(0, 0, 0), graph.at(0, 4, 0)
        self.assertNotEqual(left, right)
        self.assertIsNone(graph.distance({left}, ("MAP_0", 4, 0)), "A warp is not a floor shortcut")
        self.assertIsNotNone(graph.distance({left}, "MAP_1"))
        for mid in [2, 3]:
            left, right = graph.at(mid, 0, 0), graph.at(mid, graph.grids[mid].w - 1, 0)
            self.assertIn(right, graph.out[left])
            self.assertNotIn(left, graph.out.get(right, set()))
        self.assertNotEqual(graph.at(4, 0, 0), graph.at(4, 1, 0))
        self.assertEqual(graph.connection_point(dict(direction="right", x_align=0, y_align=-10), 20, 13), (0, 3))

    def test_map_settles_after_late_destination_layout_change(self):
        game = object.__new__(Game)
        game.frames = 0
        game.tick = lambda n=1: setattr(game, "frames", game.frames + n)
        game.u8 = lambda name: (10 if game.frames < 60 else 20) if name == "wCurMapWidth" else 0
        game.settle_map()
        self.assertGreaterEqual(game.frames, 84)
        self.assertLess(game.frames, 400)

    def test_manual_choices_validate_input_and_quit_without_models(self):
        state = dict(screen=[], map="OAKS_LAB", x=5, y=3, active_goal=dict(goal="Choose a starter"))
        with patch("builtins.input", side_effect=["invalid", "2"]), patch("builtins.print"):
            self.assertEqual(Manual().choose(state, {"a": "First", "b": "Second"}), "b")
        with patch("builtins.input", return_value="q"), patch("builtins.print"), self.assertRaises(KeyboardInterrupt):
            Manual().choose(state, {"a": "First", "b": "Second"})

    def test_controls_wait_for_menu_input_and_expose_safari_choices(self):
        screen = dict(rows=[" " * 20] * 18, cursor=(2, 2), waiting=False)
        game = SimpleNamespace(settle_screen=lambda: None, screen=lambda: screen, menu_ready=lambda: False)
        self.assertEqual(Controls(game).actions({"mode": "dialog"})[0].kind, "wait")
        actions = Controls(game).battle_actions(dict(battle=dict(safari=True, safari_balls=12)))
        self.assertEqual({a.target["label"] for a in actions}, {"BALL", "BAIT", "THROW ROCK", "RUN"})

    def test_switch_press_reconnects_a_blocked_route(self):
        room, goal = (0, 0), (1, 0)
        current, flipped = {room: set()}, {room: {goal}, goal: set()}
        with_press, _ = switch_distances(current, flipped, [(room, room)], set(), {goal})
        without_press, _ = switch_distances(current, flipped, [], set(), {goal})
        self.assertEqual(with_press[room], 2)
        self.assertNotIn(room, without_press)
        self.assertIn("leads toward the objective", switch_fact(None, 2))
        self.assertIn("does not bring it closer", switch_fact(1, 4))
        maps = {0: dict(name="POKEMON_MANSION_1F", objects=[], warps=[], connections=[]),
                1: dict(name="GOAL", objects=[], warps=[], connections=[])}
        game = SimpleNamespace(rom=SimpleNamespace(maps=maps, spinners={}, hidden={
            0: [dict(fn="Mansion1FSwitches", x=1, y=1)]}))
        graph = Regions(game)
        graph.cells[0] = {(1, 2): room}
        graph.out[room] = set()
        graph.flip = Regions(game)
        graph.flip.cells[0] = {(1, 2): (0, 1)}
        graph.flip.cells[1] = {(0, 0): goal}
        graph.flip.out[(0, 1)] = {goal}
        graph.flip.out[goal] = set()
        graph.live_key, graph.signature = None, (False, False, False)
        distances, _ = graph.mansion_distances("GOAL")
        self.assertEqual(distances[room], 2)
        reached, other = switch_reach(current, flipped, [(room, room)], {room})
        self.assertEqual(other[goal], 2)
        self.assertNotIn(goal, switch_reach(current, flipped, [], {room})[1])
        self.assertEqual(reached[room], 0)

    def test_pc_summary_lists_the_box_without_choosing(self):
        full = pc_summary({"party": [{}] * 6, "box": [{"species": "PIDGEY", "level": 12}]})
        self.assertIn("Party 6/6", full)
        self.assertIn("PIDGEY Lv12", full)
        self.assertIn("deposit someone before withdrawing", full)
        self.assertIn("has to stay out", pc_summary({"party": [{"species": "ABRA", "level": 8}], "box": []}))
        self.assertNotIn("deposit", pc_summary({"party": [{}, {}], "box": [{"species": "RATTATA", "level": 4}]}).split("Box:")[0])

    def test_strength_plans_switch_pushes_and_requires_the_badge(self):
        class Floor:
            def __init__(self, w, h):
                self.w, self.h, self.pairs, self.ledges = w, h, set(), []

            def tile(self, x, y):
                return 0 if 0 <= x < self.w and 0 <= y < self.h else -1

            def walkable(self, x, y):
                return self.tile(x, y) == 0

        boulder = [{"index": 7, "x": 2, "y": 1}]
        switch = [{"x": 4, "y": 1, "kind": "switch"}]
        wide = Floor(5, 3)
        actions = boulder_actions(wide, (1, 1), boulder, {(2, 1)}, [], switch,
                                  known={"STRENGTH"}, badges=0x08, strength_on=True)
        self.assertEqual([item[0] for item in actions], ["push:7:right"])
        self.assertIn("Leads toward the objective", actions[0][1])
        self.assertEqual(boulder_actions(wide, (1, 1), boulder, {(2, 1)}, [], switch,
                                         known={"STRENGTH"}, badges=0x02, strength_on=True), [])
        hole = boulder_actions(Floor(4, 1), (0, 0), [{"index": 1, "x": 1, "y": 0}], {(1, 0)}, [],
                               [{"x": 2, "y": 0, "kind": "hole"}], known={"STRENGTH"}, badges=0x08, strength_on=True)
        self.assertIn("hole in the floor", hole[0][1])
        self.assertIn("puts the boulder on it", hole[0][1])
        inactive = boulder_actions(wide, (1, 1), boulder, {(2, 1)}, [], switch,
                                   known={"STRENGTH"}, badges=0x08, strength_on=False)
        self.assertEqual(inactive[0][0], "field:STRENGTH")
        self.assertIn("floor switch", inactive[0][1])
        line, on_mat = Floor(3, 1), [{"index": 1, "x": 1, "y": 0}]
        mat_target, mat_blocked = [{"x": 2, "y": 0, "kind": "switch"}], {(1, 0), (0, 0)}
        self.assertEqual(boulder_actions(line, (0, 0), on_mat, mat_blocked, [], mat_target,
                                         known={"STRENGTH"}, badges=0x08, strength_on=True), [])
        from_mat = boulder_actions(line, (0, 0), on_mat, mat_blocked, [], mat_target, edge_mats={(0, 0)},
                                   known={"STRENGTH"}, badges=0x08, strength_on=True)
        self.assertIn("push:1:right", [item[0] for item in from_mat])

    def test_field_supplies_and_blocked_exit_memory(self):
        party = [dict(nickname="JEV", species="BULBASAUR", hp=10, max_hp=20, status="OK", level=5, moves=[])]
        bag = [{"name": "POKé FLUTE", "qty": 1}, {"name": "HM01", "qty": 1}, {"name": "POTION", "qty": 1}]

        def machine(name, team):
            return "Teaches CUT." if name == "HM01" else None

        offered = {key for key, _, _ in supply_actions(
            dict(bag=bag, party=party), snorlax=True, surfing=False, machine_text=machine,
            is_key=lambda name: name.startswith("HM"), unused={})}
        self.assertEqual(offered, {"item:POKé FLUTE", "item:HM01", "item:POTION"})
        healthy = [dict(party[0], hp=20)]
        quiet = {key for key, _, _ in supply_actions(
            dict(bag=bag, party=healthy), snorlax=False, surfing=False, machine_text=machine,
            is_key=lambda name: name.startswith("HM"), unused={})}
        self.assertEqual(quiet, {"item:HM01"})
        full = [{"name": "HM01", "qty": 1}] + [{"name": f"ITEM{i}", "qty": 1} for i in range(19)]
        toss = next(text for key, text, _ in supply_actions(
            dict(bag=full, party=healthy), snorlax=False, surfing=False, machine_text=machine,
            is_key=lambda name: name.startswith("HM"), unused={}) if key == "toss")
        self.assertNotIn("HM01", toss)
        self.assertIn("ITEM0", toss)
        memory = RouteMemory()
        base = {"badges": 0, "milestone": {"id": "parcel"}, "bag": []}
        memory.sync(base)
        for _ in range(2):
            memory.note_exit("ROUTE_1", "exit:up", ["0:1>1:0"], False)
        self.assertIn("0:1>1:0", memory.skip())
        self.assertIn("did not get through", memory.failure_note("ROUTE_1", "exit:up"))
        memory.note_exit("ROUTE_1", "exit:up", ["0:1>1:0"], True)
        self.assertEqual(memory.blocked_exits, {})
        for _ in range(5):
            memory.note_exit("ROUTE_1", "door:0", [], False)
        self.assertTrue(memory.hidden("ROUTE_1", "door:0"))
        memory.sync({"badges": 1, "milestone": {"id": "parcel"}, "bag": []})
        self.assertEqual(memory.blocked_exits, {})
        offered = {key for key, _, _ in supply_actions(
            dict(bag=[{"name": "MOON STONE", "qty": 1}, {"name": "REPEL", "qty": 1}], party=party),
            snorlax=False, surfing=False, machine_text=machine, is_key=lambda name: False, unused={},
            stone_text=lambda name, team: "Evolves CLEFAIRY." if name == "MOON STONE" else "")}
        self.assertEqual(offered, {"item:MOON STONE", "item:REPEL"})
        self.assertIn("Evolves CLEFAIRY", next(text for key, text, _ in supply_actions(
            dict(bag=[{"name": "MOON STONE", "qty": 1}], party=party), snorlax=False, surfing=False,
            machine_text=machine, is_key=lambda name: False, unused={},
            stone_text=lambda name, team: "Evolves CLEFAIRY.") if key == "item:MOON STONE"))

    def test_failed_exit_is_recorded_for_the_next_choice(self):
        from pokemon_red_jev.navigation import Action
        agent, _ = new_agent()
        agent.navigation.memory = RouteMemory()
        agent.navigation.actions = lambda state, goal: [Action("exit:up", "Leave north", "walk", target={"edges": ["0:1>1:0"]})]
        agent.navigation.execute = lambda action: None
        agent.jev.choose = lambda state, options: "exit:up"
        agent.step()
        self.assertEqual(agent.navigation.memory.blocked_exits["ROUTE_1:exit:up"], 1)
        self.assertEqual(agent.navigation.memory.blocked_edges["0:1>1:0"], 1)

    def test_dialogue_is_kept_and_shown_on_the_same_interaction(self):
        memory = RouteMemory()
        memory.begin("VIRIDIAN_MART:npc:1")
        memory.hear("Hello")
        memory.hear("Hello there")
        memory.hear("Hello there")
        memory.hear("Come again")
        memory.close()
        self.assertEqual(memory.said["VIRIDIAN_MART:npc:1"], "Hello there Come again")
        self.assertIn('Last time they said: "Hello there Come again".', memory.talk_fact("VIRIDIAN_MART", "npc:1", "npc"))
        memory.said["VERMILION_GYM:hidden:1"] = "This can is empty"
        memory.begin("VERMILION_GYM:hidden:2")
        memory.hear("The electric locks were reset")
        self.assertNotIn("VERMILION_GYM:hidden:1", memory.said)
        memory.forget_trash("VERMILION_GYM", [2])
        memory.begin("VERMILION_GYM:hidden:2")
        memory.hear("A switch under this can")
        memory.forget_trash("VERMILION_GYM", [2])
        self.assertIn("A switch under this can", memory.said["VERMILION_GYM:hidden:2"])
        memory.forget_trash("ROUTE_6", [])
        memory.forget_trash("VERMILION_GYM", [2])
        self.assertNotIn("VERMILION_GYM:hidden:2", memory.said)
        memory.said["ROUTE_1:exit:up:blocked"] = "You need a drink"
        self.assertIn("You need a drink", memory.stopped_note("ROUTE_1", "exit:up"))
        memory.sync({"badges": 0, "milestone": {"id": "parcel"}, "bag": []})
        memory.remember_pushback("ROUTE_1", [("up", 1, 1), ("up", 1, 2)], 1, 1, (9, 9))
        self.assertEqual(memory.trap_points("ROUTE_1"), {(1, 2)})
        memory.remember_pushback("ROUTE_1", [("up", 1, 1), ("up", 1, 2)], 1, 1, (9, 9))
        self.assertEqual(len(memory.traps["ROUTE_1"]), 1)
        memory.sync({"badges": 2, "milestone": {"id": "brock"}, "bag": []})
        self.assertEqual(memory.traps, {})
        party = [dict(nickname="JEV", species="ODDISH", moves=[dict(name="CUT")]),
                 dict(nickname="BLUE", species="PIDGEY", moves=[])]
        self.assertIn("no other team member knows it", menu_note(["Deposit"], "JEV", {"party": party}, "DEPOSIT"))
        self.assertIn("all gates in this building flip", menu_note(["Press it?"], "YES", {}))
        self.assertIn("frees a bag slot", menu_note(["Is it OK to toss POTION?"], "YES", {"bag": [{}] * 20}))
        self.assertIn("Pick one of the current moves to forget", menu_note(["move to make room for GUST?"], "YES", {}))
        self.assertIn("Lights up a dark cave", menu_note(["FLASH"], "FLASH", {}))

    def test_menu_guards_leave_a_dead_end_screen(self):
        self.assertTrue(move_list_open(["TACKLE", "TYPE/NORMAL"]))
        self.assertFalse(move_list_open(["FIGHT  PKMN  ITEM  RUN"]))
        rows = ["JEV", "NOT ABLE", "BLUE", "NOT ABLE"]
        self.assertTrue(party_unusable(rows, 2))
        self.assertFalse(party_unusable(["JEV", "ABLE", "BLUE", "NOT ABLE"], 2))
        seen, progress, screen = None, ("MART",), ("YES", "NO")
        for _ in range(4):
            seen, leave = note_menu(seen, screen, progress)
            self.assertFalse(leave)
        seen, leave = note_menu(seen, screen, progress)
        self.assertTrue(leave)
        seen, leave = note_menu(seen, screen, ("MART", "potion"))
        self.assertFalse(leave)

    def test_dialog_step_records_the_line_for_the_open_conversation(self):
        agent, _ = new_agent()
        agent.navigation.memory = RouteMemory()
        agent.navigation.memory.begin("ROUTE_1:npc:1")
        agent.game.screen = lambda: {"dialog": "OAK: Hello there"}
        agent.game.state["mode"] = "dialog"
        agent._remember_dialogue(agent.game.snapshot())
        self.assertEqual(agent.navigation.memory.said["ROUTE_1:npc:1"], "OAK: Hello there")
        agent.game.state["mode"] = "overworld"
        agent._remember_dialogue(agent.game.snapshot())
        self.assertIsNone(agent.navigation.memory.pending)
        self.assertIn("Hello there", agent.navigation.memory.said["ROUTE_1:npc:1"])

    def test_shop_price_and_elevator_floor_are_facts(self):
        rows = ["POKé MART", "POTION <ED>300", "BUY  SELL  QUIT"]
        bag = [{"name": "POTION", "qty": 2}]
        note = shop_note(rows, "POTION", bag)
        self.assertIn("Costs ¥300", note)
        self.assertIn("Heals 20 HP", note)
        self.assertIn("You have 2", note)
        self.assertEqual(shop_note(["HELLO"], "YES", []), "")
        self.assertIn("Leads toward the objective", floor_phrase("CELADON_MART_3F", 1, 4))
        self.assertIn("Leads away from the objective", floor_phrase("CELADON_MART_1F", 6, 2))
        self.assertNotIn("area", floor_phrase("CELADON_MART_5F", None, 2))

    def test_battle_facts_cover_catch_chance_hit_stages_and_a_likely_ko(self):
        self.assertEqual(catch_chance("MASTER BALL", 1, 50, 50, "OK"), 1)
        asleep = catch_chance("POKé BALL", 255, 1, 40, "SLEEP")
        healthy = catch_chance("POKé BALL", 255, 40, 40, "OK")
        self.assertGreater(asleep, healthy)
        self.assertIsNone(hit_chance(100, 7, 7))
        self.assertEqual(hit_chance(100, 13, 7), 100)
        self.assertLess(hit_chance(100, 7, 13), 100)
        player = dict(level=20, attack=40, defense=20, special=20, types=["WATER"])
        enemy = dict(level=5, attack=10, defense=10, special=10, types=["FIRE"], hp=4, max_hp=20,
                     status="OK", species="CHARMANDER", catch_rate=45, dex=4)
        move = dict(name="BUBBLE", type="WATER", power=20, pp=30, accuracy=100, slot=0)
        game = SimpleNamespace(rom=SimpleNamespace(effectiveness=lambda attack, defense: 2),
                               u8=lambda name: 1 if name == "wPlayerMonAccuracyMod" else 7,
                               owned=lambda dex: False)
        controls = Controls(game)
        state = dict(map="ROUTE_1", mode="battle", party=[dict(nickname="JEV", species="SQUIRTLE", level=8, hp=20,
                     max_hp=20, types=["WATER"], slot=0)], bag=[{"name": "POKé BALL", "qty": 3}],
                     battle=dict(kind="wild", player=dict(player, hp=20, max_hp=30, status="OK", slot=0),
                                 enemy=enemy, active_slot=0, moves=[move], safari=False))
        text = next(a.description for a in controls.battle_actions(state) if a.key == "move:0")
        ball = next(a.description for a in controls.battle_actions(state) if a.key.startswith("ball:"))
        self.assertIn("Likely KO", text)
        self.assertIn("Hit chance right now", text)
        self.assertIn("Estimated catch chance", ball)
        self.assertIn("NEW species", ball)
        self.assertIn("not on your team", ball)

    def test_battle_snapshot_includes_the_enemy_moveset(self):
        from pokemon_red_jev.data import Data
        data = object.__new__(Data)
        data.symbols = {"wIsInBattle": 0xc000, "wBattleType": 0xc001, "wPlayerMonNumber": 0xc002,
                        "wNumSafariBalls": 0xc003, "wBattleMon": 0xc100, "wEnemyMon": 0xc200}
        game = object.__new__(Game)
        game.data, game.memory = data, bytearray(65536)
        game.rom = SimpleNamespace(species={
            1: {"name": "RATTATA", "types": ["NORMAL"], "catch_rate": 255, "dex": 19},
            2: {"name": "PIDGEY", "types": ["NORMAL", "FLYING"], "catch_rate": 255, "dex": 16}},
            moves={1: {"name": "TACKLE", "type": "NORMAL", "power": 35, "accuracy": 95, "pp": 35, "effect": 0},
                   2: {"name": "GUST", "type": "FLYING", "power": 40, "accuracy": 100, "pp": 35, "effect": 0}})
        game.memory[0xc000] = 1
        game.memory[0xc100] = 1
        game.memory[0xc108] = 1
        game.memory[0xc200] = 2
        game.memory[0xc208] = 2
        game.memory[0xc200 + 25] = 10
        battle = game.battle()
        self.assertEqual(battle["kind"], "wild")
        self.assertEqual(battle["moves"][0]["name"], "TACKLE")
        self.assertEqual(battle["enemy"]["species"], "PIDGEY")
        self.assertEqual((battle["enemy"]["moves"][0]["name"], battle["enemy"]["moves"][0]["pp"]), ("GUST", 10))

    def test_wild_battle_shows_escape_enemy_moves_and_the_bench(self):
        self.assertEqual(escape_chance(40, 18), 1)
        self.assertEqual(escape_chance(79, 165), 62 / 256)
        self.assertEqual(escape_chance(79, 165, 1), 92 / 256)
        self.assertEqual(describe_effect({"name": "SLEEP POWDER", "effect": 0x20}), "Puts the target to sleep.")
        wild_rules = action_instructions({"mode": "battle", "battle": {"kind": "wild"}})
        self.assertIn("Judge this turn", wild_rules)
        self.assertIn("Escape when the escape is likely", wild_rules)
        self.assertIn("Running is impossible", action_instructions({"mode": "battle", "battle": {"kind": "trainer"}}))
        self.assertIn("prefer that step", action_instructions({"mode": "overworld"}))
        player = dict(level=12, attack=30, defense=20, special=30, speed=20, types=["WATER"],
                      hp=30, max_hp=30, status="OK", slot=0)
        enemy = dict(level=8, attack=28, defense=16, special=16, speed=80, types=["NORMAL"], hp=22, max_hp=22,
                     status="OK", species="RATTATA", catch_rate=255, dex=19,
                     moves=[dict(name="TACKLE", type="NORMAL", power=35, pp=30, accuracy=95, effect=0)])
        bench = dict(nickname="BLUE", species="PIDGEY", level=11, hp=24, max_hp=24, types=["NORMAL", "FLYING"],
                     slot=1, attack=24, defense=18, speed=26, special=18,
                     moves=[dict(name="GUST", type="FLYING", power=40, pp=15, accuracy=100, effect=0),
                            dict(name="SAND-ATTACK", type="NORMAL", power=0, pp=10, accuracy=100, effect=0x16)])
        game = SimpleNamespace(rom=SimpleNamespace(effectiveness=lambda attack, defense: 1),
                               u8=lambda name: 0 if name == "wNumRunAttempts" else 7, owned=lambda dex: True)
        state = dict(map="ROUTE_1", mode="battle", active_goal={"focus": "progress", "goal": "Reach the mart"},
                     party=[dict(nickname="JEV", species="SQUIRTLE", level=12, hp=30, max_hp=30, types=["WATER"], slot=0, moves=[]),
                            bench],
                     bag=[], battle=dict(kind="wild", player=player, enemy=enemy, active_slot=0, safari=False,
                                         moves=[dict(name="BUBBLE", type="WATER", power=20, pp=30, accuracy=100, slot=0,
                                                     effect=0x46)]))
        actions = {a.key: a for a in Controls(game).battle_actions(state)}
        move, switch = actions["move:0"].description, actions["switch:1"].description
        self.assertIn("May lower the target's Speed", move)
        self.assertIn("Enemy knows TACKLE", move)
        self.assertIn("The enemy acts first", move)
        self.assertIn("Escape chance about", move)
        self.assertIn("failed tries so far 0", move)
        self.assertIn("Current errand: Reach the mart.", move)
        self.assertIn("GUST:", switch)
        self.assertIn("type multiplier 1", switch)
        self.assertIn("SAND-ATTACK", switch)
        self.assertIn("Lowers the target's accuracy", switch)
        self.assertIn("Speed 26 vs the enemy's 80", switch)
        self.assertNotIn("run", {a.key: a for a in Controls(game).battle_actions(
            dict(state, battle=dict(state["battle"], kind="trainer")))})
        trainer = Controls(game).battle_actions(dict(state, battle=dict(state["battle"], kind="trainer")))
        self.assertIn("running is impossible", trainer[0].description)

    def test_status_and_the_next_gym_are_part_of_the_battle_choice(self):
        self.assertIn("loses HP every turn", status_note("POISON"))
        self.assertIn("cannot move", status_note("SLEEP"))
        self.assertEqual(status_note("OK"), "")
        self.assertEqual(next_gym({"milestone": {"id": "parcel"}})["leader"], "Brock")
        self.assertEqual(next_gym({}) , None)
        chart = {("GRASS", "ROCK"): 2, ("GRASS", "GROUND"): 2, ("NORMAL", "ROCK"): 0.5, ("WATER", "ROCK"): 2, ("WATER", "GROUND"): 2}

        def effectiveness(attack, defense):
            score = 1
            for typing in defense:
                score *= chart.get((attack, typing), 1)
            return score

        player = dict(level=12, attack=30, defense=20, special=30, speed=20, types=["NORMAL"],
                      hp=18, max_hp=30, status="POISON", slot=0, species="RATTATA")
        enemy = dict(level=10, attack=20, defense=16, special=20, speed=18, types=["GRASS"], hp=24, max_hp=28,
                     status="SLEEP", species="ODDISH", catch_rate=255, dex=43,
                     moves=[dict(name="ABSORB", type="GRASS", power=20, pp=20, accuracy=100, effect=3)])
        bench = dict(nickname="BLUE", species="SQUIRTLE", level=12, hp=30, max_hp=30, types=["WATER"], slot=1,
                     status="OK", attack=24, defense=24, speed=18, special=24,
                     moves=[dict(name="BUBBLE", type="WATER", power=20, pp=30, accuracy=100)])
        game = SimpleNamespace(rom=SimpleNamespace(effectiveness=effectiveness, species={
            43: {"name": "ODDISH", "evolutions": [{"method": "level", "level": 21}]}}),
            u8=lambda name: 0 if name == "wNumRunAttempts" else 7, owned=lambda dex: False)
        state = dict(map="ROUTE_1", mode="battle", milestone={"id": "brock"},
                     active_goal={"focus": "catch", "goal": "Find a Pokémon for Brock"},
                     party=[dict(nickname="JEV", species="RATTATA", level=12, hp=18, max_hp=30, types=["NORMAL"],
                                 status="POISON", slot=0, moves=[dict(name="TACKLE", type="NORMAL", power=35, pp=30)]),
                            bench],
                     bag=[{"name": "ANTIDOTE", "qty": 1}, {"name": "POKé BALL", "qty": 2}],
                     battle=dict(kind="wild", player=player, enemy=enemy, active_slot=0, safari=False,
                                 moves=[dict(name="TACKLE", type="NORMAL", power=35, pp=30, accuracy=95, slot=0, effect=0)]))
        actions = {a.key: a for a in Controls(game).battle_actions(state)}
        move, ball, cure, switch = (actions["move:0"].description, actions["ball:POKé BALL"].description,
                                    actions["cure:ANTIDOTE"].description, actions["switch:1"].description)
        self.assertIn("Your active Pokémon is poisoned", move)
        self.assertIn("The enemy is asleep", move)
        self.assertIn("much more likely", move)
        self.assertIn("strong into the next gym, Brock", move)
        self.assertIn("The team already has WATER into Brock", move)
        self.assertIn("Evolves by level 21", ball)
        self.assertIn("loses HP every turn", cure)
        self.assertIn("BUBBLE:", switch)

    def test_three_failures_stay_available_and_another_option_is_tried(self):
        self.assertEqual(alternate("exit:up", {"exit:up": "North", "exit:down": "South"}, {}, "here",
                                   {"exit:up": 0.9, "exit:down": 0.1}), "exit:up")
        self.assertEqual(alternate("exit:up", {"exit:up": "North", "exit:down": "South"}, {"here:exit:up": 3}, "here",
                                   {"exit:up": 0.9, "exit:down": 0.1}), "exit:down")
        agent, records = new_agent()
        agent.navigation.memory = RouteMemory()
        agent.navigation.actions = lambda state, goal: [
            Action("exit:up", "Leave north", "walk", target={"edges": []}),
            Action("exit:down", "Leave south", "walk", target={"edges": []})]
        agent.navigation.execute = lambda action: None
        agent.failures["ROUTE_1:0,0:overworld:exit:up"] = 3

        def choose(state, options):
            self.assertIn("exit:up", options)
            self.assertIn("Failed without state change here 3 times", options["exit:up"])
            agent.jev.last = {"picked": "exit:up", "probabilities": {"exit:up": 0.9, "exit:down": 0.1}}
            return "exit:up"

        agent.jev.choose = choose
        agent.step()
        self.assertEqual(agent.history[-1]["action"], "exit:down")
        self.assertTrue(any(r["kind"] == "retry" for r in records))

    def test_options_screen_is_not_an_endless_wait(self):
        rows = ["TEXT SPEED", " FAST", "BATTLE ANIMATION", " ON"]
        screen = dict(rows=rows, cursor=(1, 1), waiting=False)
        game = SimpleNamespace(settle_screen=lambda: None, screen=lambda: screen, menu_ready=lambda: (_ for _ in ()).throw(AssertionError("options")))
        self.assertEqual(Controls(game).actions({"mode": "dialog"})[0].kind, "options")
        blank = [" " * 20] * 18
        waiting = dict(rows=blank, cursor=(2, 2), waiting=False)
        loop = SimpleNamespace(settle_screen=lambda: None, screen=lambda: waiting, menu_ready=lambda: False,
                               in_routine=lambda start, end: start == "DisplayOptionMenu")
        self.assertEqual(Controls(loop).actions({"mode": "dialog"})[0].kind, "options")
        frames = {"n": 0}

        def blinking():
            mark = "▶" if frames["n"] % 2 else "▷"
            frames["n"] += 1
            shown = [mark + "HELLO"] + [" " * 20] * 17
            return dict(rows=shown, cursor=(0, 0), waiting=False)

        controls = Controls(SimpleNamespace(settle_screen=lambda: None, screen=blinking, menu_ready=lambda: False))
        kinds = [controls.actions({"mode": "dialog"})[0].key for _ in range(40)]
        self.assertEqual(kinds[-1], "cancel")
        self.assertTrue(all(key == "wait" for key in kinds[:-1]))
        self.assertEqual(quiet_rows(["▶HELLO", "▷HELLO"]), (" HELLO", " HELLO"))

        class Blink:
            def __init__(self):
                self.n = self.ticks = 0

            def screen(self):
                mark = "▶" if self.n % 2 else "▷"
                self.n += 1
                return {"rows": [mark + "TEXT SPEED", "BATTLE ANIMATION"]}

            def tick(self, frames=1):
                self.ticks += frames

        blink = Blink()
        Game.settle_screen(blink)
        self.assertLess(blink.ticks, 30)

    def test_options_presses_clear_the_menu_lockout(self):
        box = {"y": 3, "options": 3, "open": True}
        presses = []

        def screen():
            rows = ["TEXT SPEED", "BATTLE ANIMATION", "BATTLE STYLE", "CANCEL"] if box["open"] else ["NEW GAME", "OPTION"]
            return {"rows": rows}

        def press(button, hold=6, settle=12):
            presses.append((button, settle))
            if button == "left" and box["y"] == 3:
                box["options"] = (box["options"] & 0xf0) | 1
            elif button == "down" and box["y"] == 3:
                box["y"] = 8
            elif button == "right" and box["y"] == 8:
                box["options"] |= 0x80
            elif button == "b" and box["options"] & 0x8f == 0x81:
                box["open"] = False

        game = SimpleNamespace(screen=screen, press=press, tick=lambda n=1: None,
                               u8=lambda name: box["y"] if name == "wTopMenuItemY" else box["options"])
        Controls(game).execute(Action("options", "Set options", "options"))
        self.assertFalse(box["open"])
        self.assertEqual(box["options"] & 0x8f, 0x81)
        self.assertEqual([button for button, _ in presses], ["left", "down", "right", "b"])
        self.assertTrue(all(settle >= 30 for _, settle in presses))

    def test_title_menu_starts_the_game_instead_of_backing_out(self):
        cells = [[" "] * 20 for _ in range(18)]
        for y, text in ((2, "│▶NEW GAME    │"), (4, "│ OPTION      │")):
            cells[y][:len(text)] = list(text)
        screen = {"rows": ["".join(row) for row in cells], "cells": cells, "cursor": (1, 2), "waiting": False}
        addrs = {"wTopMenuItemY": 2, "wTopMenuItemX": 1, "wMaxMenuItem": 1, "wOptions": 0x81}
        game = SimpleNamespace(settle_screen=lambda: None, screen=lambda: screen, menu_ready=lambda: True,
                               u8=lambda name: addrs[name], rom=SimpleNamespace(items={}), party=lambda: [])
        keys = [a.key for a in Controls(game).actions({"mode": "dialog", "party": [], "bag": [], "battle": None})]
        self.assertEqual(keys, ["menu:0"])
        self.assertEqual(Controls(game).actions({"mode": "dialog", "party": [], "bag": [], "battle": None})[0].description,
                         "Start a new game.")

    def test_oaks_lab_rival_battle_is_at_the_door(self):
        ready = dict(map="OAKS_LAB", party=[{"species": "BULBASAUR"}], events=[])
        self.assertTrue(scripted_rival_exit(ready))
        self.assertFalse(scripted_rival_exit(dict(ready, party=[])))
        self.assertFalse(scripted_rival_exit(dict(ready, events=["EVENT_BATTLED_RIVAL_IN_OAKS_LAB"])))
        self.assertFalse(scripted_rival_exit(dict(ready, map="PALLET_TOWN")))
        # Talking to RIVAL1/BLUE is omitted; the lab doors are the fight trigger.
        rival = {"index": 1, "trainer_class": "RIVAL1"}
        oak = {"index": 5, "trainer_class": None}
        self.assertTrue(ready["party"] and rival.get("trainer_class") == "RIVAL1")
        self.assertFalse(oak.get("trainer_class") == "RIVAL1")

    def test_story_room_survives_an_arrival_only_goal(self):
        nav = Navigation.__new__(Navigation)
        nav.game = SimpleNamespace(rom=SimpleNamespace(maps={
            1: {"name": "SAFFRON_GYM", "objects": [
                {"trainer": True, "trainer_class": "SABRINA", "x": 9, "y": 8, "item": None}]},
            2: {"name": "POKEMON_MANSION_B1F", "objects": [
                {"trainer": False, "item": "FULL RESTORE", "x": 1, "y": 22},
                {"trainer": False, "item": "SECRET KEY", "x": 5, "y": 13}]},
        }))
        nav.state = {"milestone": {"goal": "Defeat Sabrina at the Saffron City Gym.", "maps": ["SAFFRON_GYM"],
                                   "success": {"kind": "badge", "value": 6}, "missing_need": None},
                     "active_goal": {"goal": "Go to the gym", "target_map": "SAFFRON_GYM",
                                     "success": {"kind": "map", "value": "SAFFRON_GYM"}}}
        self.assertEqual(nav.objective("SAFFRON_GYM"), ("SAFFRON_GYM", 9, 9))
        self.assertEqual(nav.objective("VIRIDIAN_CITY"), "VIRIDIAN_CITY")
        nav.state = {"milestone": {"goal": "Explore the Pokémon Mansion to find the Secret Key.",
                                   "maps": ["POKEMON_MANSION_B1F"], "success": {"kind": "item", "value": "SECRET KEY"},
                                   "missing_need": None},
                     "active_goal": {"goal": "Enter the mansion", "target_map": "POKEMON_MANSION_B1F",
                                     "success": {"kind": "map", "value": "POKEMON_MANSION_B1F"}}}
        self.assertEqual(nav.objective("POKEMON_MANSION_B1F"), ("POKEMON_MANSION_B1F", 5, 13))

    def test_same_map_teleport_is_not_a_blocked_exit(self):
        class Regions:
            def at(self, mid, x, y):
                return (mid, 0) if (x, y) == (1, 1) else (mid, 4)

            def landing(self, region):
                return {region} if region else set()

        agent, _ = new_agent()
        agent.navigation.regions = Regions()
        action = Action("door:3", "Teleport pad", "door", target={"map": 7, "x": 1, "y": 3, "edges": ["7:0>99:99"]})
        state = {"map": "SAFFRON_GYM", "map_id": 7, "x": 1, "y": 1}
        landed = {"map": "SAFFRON_GYM", "map_id": 7, "x": 8, "y": 4}
        self.assertTrue(agent._exit_cleared(state, landed, action))
        self.assertFalse(agent._exit_cleared(state, dict(state), action))
        left = {"map": "SAFFRON_CITY", "map_id": 8, "x": 1, "y": 1}
        self.assertTrue(agent._exit_cleared(state, left, action))

    def test_exhausted_mon_can_struggle_beside_other_actions(self):
        player = dict(level=20, attack=40, defense=20, special=20, types=["NORMAL"], hp=10, max_hp=30, status="OK", slot=0)
        enemy = dict(level=5, attack=10, defense=10, special=10, types=["NORMAL"], hp=20, max_hp=20,
                     status="OK", species="RATTATA", catch_rate=255, dex=19)
        fainted = dict(nickname="SLEEPY", species="CATERPIE", level=4, hp=0, max_hp=20, types=["BUG"], slot=2, moves=[])
        bench = dict(nickname="BLUE", species="PIDGEY", level=12, hp=20, max_hp=20, types=["NORMAL"], slot=1,
                     moves=[dict(name="GUST", type="FLYING", power=40, pp=0, accuracy=100)])
        game = SimpleNamespace(rom=SimpleNamespace(effectiveness=lambda attack, defense: 1),
                               u8=lambda name: 7, owned=lambda dex: False)
        state = dict(map="ROUTE_1", mode="battle", party=[
            dict(nickname="JEV", species="RATTATA", level=20, hp=10, max_hp=30, types=["NORMAL"], slot=0, moves=[]),
            bench, fainted],
            bag=[{"name": "POTION", "qty": 1}, {"name": "REVIVE", "qty": 1}, {"name": "MAX REVIVE", "qty": 1},
                 {"name": "POKé BALL", "qty": 2}],
            battle=dict(kind="wild", player=player, enemy=enemy, active_slot=0, safari=False,
                        moves=[dict(name="TACKLE", type="NORMAL", power=35, pp=0, accuracy=95, slot=0)]))
        actions = {a.key: a for a in Controls(game).battle_actions(state)}
        self.assertIn("struggle", actions)
        self.assertIn("switch:1", actions)
        self.assertIn("item:POTION", actions)
        self.assertIn("run", actions)
        self.assertIn("revive:REVIVE:2", actions)
        self.assertIn("revive:MAX REVIVE:2", actions)
        self.assertEqual(actions["revive:MAX REVIVE:2"].target["name"], "MAX REVIVE")
        self.assertNotEqual(actions["revive:REVIVE:2"].key, actions["revive:MAX REVIVE:2"].key)

    def test_revive_search_does_not_land_on_max_revive(self):
        cells = [[" "] * 20 for _ in range(18)]
        for i, ch in enumerate("MAX REVIVE"):
            cells[2][i] = ch
        for i, ch in enumerate("REVIVE"):
            cells[4][i] = ch
        for i, ch in enumerate("YES NO"):
            cells[6][i] = ch
        controls = Controls(SimpleNamespace(screen=lambda: {"cells": cells, "cursor": (0, 0)}))
        self.assertEqual(controls.find("MAX REVIVE"), (0, 2))
        self.assertEqual(controls.find("REVIVE"), (0, 4))
        self.assertEqual(controls.find("NO")[1], 6)

    def test_canceled_item_does_not_count_as_used_when_the_menu_opens(self):
        agent, _ = new_agent()
        agent.navigation.memory = RouteMemory()
        agent.game.state["bag"] = [{"name": "POTION", "qty": 1}]
        before = agent.game.snapshot()
        agent.pending_item = {"name": "POTION", "before": Agent.supply_key(before)}
        agent.item_fresh = True
        agent.game.state["x"] = 3
        agent.game.state["mode"] = "dialog"
        agent.game.state["screen"] = ["ITEM", "POTION"]
        agent._observe(agent.game.snapshot())
        self.assertEqual(agent.navigation.memory.item_unused, {})
        self.assertIsNotNone(agent.pending_item)
        agent.item_fresh = False
        agent.game.state["mode"] = "overworld"
        agent._observe(agent.game.snapshot())
        self.assertEqual(agent.navigation.memory.item_unused.get("POTION"), 1)
        agent.pending_item = {"name": "POTION", "before": Agent.supply_key(agent.game.snapshot())}
        agent.game.state["bag"] = [{"name": "POTION", "qty": 0}]
        agent._observe(agent.game.snapshot())
        self.assertNotIn("POTION", agent.navigation.memory.item_unused)

    def test_battle_level_gain_clears_a_pending_stall(self):
        agent, records = new_agent()
        agent.goal = Goal("Grow stronger", "progress", "ROUTE_1", {"kind": "level", "value": 50}, 20)
        agent.milestone_id = "parcel"
        agent.observed = agent.game.snapshot()
        agent.no_progress = 8
        agent.game.state["mode"] = "battle"
        agent.game.state["party"][0]["level"] = 8
        agent.game.state["party"][0]["experience"] = 400

        class BattleControls:
            def actions(self, state):
                return [Action("wait", "Wait for the attack", "wait")]

            def execute(self, action):
                pass

        agent.controls = BattleControls()
        agent.jev.choose = lambda state, options: "wait"
        agent.step()
        self.assertEqual(agent.no_progress, 0)
        self.assertTrue(agent.goal)
        self.assertFalse(any(r["kind"] == "goal_end" and r.get("outcome") == "stalled" for r in records))


if __name__ == "__main__":
    unittest.main()

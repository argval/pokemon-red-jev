"""Opt-in ROM integration checks with isolated, seeded late-game entry states.

Setup edits party capabilities, encounter protection, and one doorway destination.
After entry, all puzzle/menu actions use the production button drivers. No models,
ROM patches, downloaded saves, or changes to the player's checkpoint are involved.
"""

import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_rom
from pokemon_red_jev.agent import Agent
from pokemon_red_jev.cli import load_env
from pokemon_red_jev.controls import Controls, damage
from pokemon_red_jev.data import Data
from pokemon_red_jev.game import Game
from pokemon_red_jev.goals import Goal, fallback_goal
from pokemon_red_jev.navigation import Navigation


@unittest.skipUnless(os.getenv("RUN_ROM_TESTS") == "1", "Set RUN_ROM_TESTS=1 with a local supported ROM")
class RealScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_env()
        cls.game = Game(Path(os.environ["ROM_PATH"]), Data(Path(os.getenv("GAME_DATA_PATH", "data/generated.json"))),
                        headless=True, speed=0)
        cls.addClassCleanup(cls.game.close)
        models = patch("pokemon_red_jev.models.post_json", side_effect=AssertionError("No models in ROM checks"))
        models.start()
        cls.addClassCleanup(models.stop)
        test_rom.RealOpening().reach_pokedex(cls.game)
        g = cls.game
        a = g.data.sym("wPartyMons")
        g.memory[a + 3] = g.memory[a + 33] = 100
        experience = 1059860  # Bulbasaur's medium-slow growth curve at level 100.
        g.memory[a + 14:a + 17] = [(experience >> shift) & 255 for shift in (16, 8, 0)]
        for offset in (1, 34, 36, 38, 40, 42):
            g.memory[a + offset:a + offset + 2] = [1, 144]
        for index, name in enumerate(("STRENGTH", "SURF")):
            g.memory[a + 8 + index] = next(i for i, m in g.rom.moves.items() if m["name"] == name)
            g.memory[a + 29 + index] = 30
        g.memory[g.data.sym("wObtainedBadges")] = 0xff
        g.memory[g.data.sym("wRepelRemainingSteps")] = 255
        buf = io.BytesIO()
        g.pyboy.save_state(buf)
        cls.opening = buf.getvalue()
        cls.opening_meta = (set(g.visited), set(g.interactions), g.outside_map, g.last_map, g.frames)

    def setUp(self):
        self.game.pyboy.load_state(io.BytesIO(self.opening))
        visited, interactions, self.game.outside_map, self.game.last_map, self.game.frames = self.opening_meta
        self.game.visited, self.game.interactions = set(visited), set(interactions)
        self.nav, self.controls = Navigation(self.game), Controls(self.game)
        self.records = []
        self.agent = Agent(self.game, None, None, self.nav, self.controls,
                           lambda kind, **fields: self.records.append(dict(kind=kind, **fields)))

    def enter(self, name, warp=0):
        """Seed an entry, letting the ROM load the target map and sprites normally."""
        g = self.game
        mid = next(i for i, md in g.rom.maps.items() if md["name"] == name)
        a = g.data.sym("wWarpEntries")
        g.memory[a + 2:a + 4] = [warp, mid]
        state = g.snapshot()
        action = next(a for a in self.nav.actions(state, fallback_goal(state)) if a.key == "door:0")
        self.nav.execute(action)
        g.tick(60)
        self.assertEqual(g.snapshot()["map"], name)
        self.assertEqual(g.mode(), "overworld")
        self.goal = Goal("ROM scenario", "progress", name, {"kind": "event", "value": "EVENT_GOT_MASTER_BALL"}, 80)

    def choices(self):
        state = self.game.snapshot()
        return self.nav.actions(state, self.goal) if state["mode"] == "overworld" else self.controls.actions(state)

    def perform(self, action):
        if action.kind in {"walk", "door", "interact", "field"}:
            if self.nav.execute(action) and action.kind == "field":
                self.controls.execute(action)
        else:
            self.controls.execute(action)
        self.game.tick(8)

    def finish_dialog(self):
        for _ in range(60):
            mode = self.game.snapshot()["mode"]
            if mode == "overworld":
                return
            if mode == "busy":
                self.game.tick(12)
                continue
            actions = self.choices()
            action = next((a for a in actions if a.target.get("label") == "YES"), actions[0])
            self.perform(action)
        self.fail("Dialogue did not finish: " + str(self.game.screen()["rows"]))

    def test_victory_road_long_push_sequence(self):
        self.enter("VICTORY_ROAD_1F")
        self.goal.target_map = "VICTORY_ROAD_2F"
        def choose(state, options):
            if state["mode"] != "overworld":
                return next(iter(options))
            return next(k for k, description in options.items() if k == "field:STRENGTH" or
                        k.startswith("push:") and "Leads toward" in description)
        self.menu_agent(choose)
        pushes = 0
        for _ in range(60):
            state = self.game.snapshot()
            if "EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH" in state["events"]:
                break
            self.agent.step()
        pushes = sum(r["kind"] == "action" and r.get("choice", "").startswith("push:") for r in self.records)
        self.assertIn("EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH", self.game.snapshot()["events"])
        self.assertGreater(pushes, 8)
        self.assertGreater(self.nav.memory.boulder_gains, 8)
        self.assertFalse(any(r.get("outcome") == "stalled" for r in self.records))
        self.assertEqual(self.agent.plans, 0)
        print(f"ROM Victory Road: {pushes} pushes, {self.nav.memory.boulder_gains} progress gains", flush=True)

    def test_seafoam_pushes_boulder_into_hole(self):
        self.enter("SEAFOAM_ISLANDS_1F")
        self.perform(next(a for a in self.choices() if a.key == "field:STRENGTH"))
        self.finish_dialog()
        for _ in range(30):
            state = self.game.snapshot()
            if "EVENT_SEAFOAM1_BOULDER1_DOWN_HOLE" in state["events"]:
                break
            actions = self.choices()
            toward = [a for a in actions if a.key.startswith("push:1:") and "Leads toward" in a.description]
            self.assertTrue(toward, str([(a.key, a.description) for a in actions]))
            self.perform(toward[0])
            self.finish_dialog()
        self.assertIn("EVENT_SEAFOAM1_BOULDER1_DOWN_HOLE", self.game.snapshot()["events"])
        self.assertNotIn(1, [s["index"] for s in self.game.sprites() if s["picture"] == 63])
        self.goal.target_map = "SEAFOAM_ISLANDS_B1F"
        for _ in range(10):
            if self.game.snapshot()["map"] == self.goal.target_map:
                break
            self.perform(next(a for a in self.choices() if a.key == "door:4"))
            self.finish_dialog()
        self.assertEqual(self.game.snapshot()["map"], "SEAFOAM_ISLANDS_B1F")
        self.assertIn(1, [s["index"] for s in self.game.sprites() if s["picture"] == 63])
        for _ in range(20):
            if "EVENT_SEAFOAM2_BOULDER1_DOWN_HOLE" in self.game.snapshot()["events"]:
                break
            action = next(a for a in self.choices() if a.key == "field:STRENGTH" or
                          a.key.startswith("push:1:") and "Leads toward" in a.description)
            self.perform(action)
            self.finish_dialog()
        self.assertIn("EVENT_SEAFOAM2_BOULDER1_DOWN_HOLE", self.game.snapshot()["events"])
        print("ROM Seafoam: followed the boulder to B1F and dropped it again", flush=True)

    def test_mansion_switch_and_checkpoint(self):
        self.enter("POKEMON_MANSION_1F")
        self.goal.target_map = "POKEMON_MANSION_B1F"
        before = "EVENT_MANSION_SWITCH_ON" in self.game.snapshot()["events"]
        action = next(a for a in self.choices() if a.target.get("switch"))
        self.perform(action)
        self.finish_dialog()
        self.assertNotEqual(before, "EVENT_MANSION_SWITCH_ON" in self.game.snapshot()["events"])
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "mansion.zip"
            self.agent.save(checkpoint)
            original = self.agent.change_key(self.game.snapshot())
            self.game.press("down")
            self.agent.load(checkpoint)
            self.assertEqual(original, self.agent.change_key(self.game.snapshot()))
        doors = [a for a in self.choices() if a.kind == "door"]
        self.assertTrue(doors)
        self.assertTrue(any("objective" in a.description for a in doors))
        route = []
        for _ in range(20):
            self.game.snapshot()  # Settle map loads before deciding whether dialogue remains.
            if self.game.mode() != "overworld":
                self.finish_dialog()
                continue
            if self.game.snapshot()["map"] == "POKEMON_MANSION_B1F":
                break
            actions = self.choices()
            action = next((a for a in actions if a.target.get("switch") and "Pressing it leads" in a.description), None)
            action = action or next((a for a in actions if "Leads toward" in a.description), None)
            self.assertIsNotNone(action, str([(a.key, a.description) for a in actions]))
            route.append(action.key)
            self.perform(action)
            self.finish_dialog()
        self.assertEqual(self.game.snapshot()["map"], "POKEMON_MANSION_B1F")
        self.assertTrue(any(key.startswith("drop:") for key in route))
        print(f"ROM Mansion: checkpoint resumed through {route} to basement", flush=True)

    def open_interaction(self, fragment):
        for _ in range(10):
            action = next(a for a in self.choices() if a.kind == "interact" and fragment in a.description)
            self.perform(action)
            if self.game.mode() != "overworld":
                return
        self.fail("Could not reach " + fragment)

    def menu_agent(self, choose):
        self.agent.goal = self.goal
        self.agent.milestone_id = self.game.snapshot()["milestone"]["id"]
        self.agent.jev = SimpleNamespace(choose=choose, last=None)

    def test_center_healing_preserves_potions(self):
        g = self.game
        self.enter("VIRIDIAN_POKECENTER")
        a = g.data.sym("wPartyMons")
        g.memory[a + 1:a + 3] = [0, 50]
        potion = next(i for i, name in g.rom.items.items() if name == "SUPER POTION")
        g.memory[g.data.sym("wNumBagItems")] = 1
        g.memory[g.data.sym("wBagItems"):g.data.sym("wBagItems") + 3] = [potion, 4, 255]
        self.goal = Goal("Heal the party", "heal", "VIRIDIAN_POKECENTER", {"kind": "healed", "value": True}, 20)

        def choose(state, options):
            self.assertNotIn("item:SUPER POTION", options)
            if state["mode"] == "overworld":
                self.assertEqual(state["healing"]["allowed_slots"], [])
                return next(k for k, desc in options.items() if "NURSE" in desc)
            return next((k for k, desc in options.items() if desc.startswith("YES")), next(iter(options)))

        self.menu_agent(choose)
        for _ in range(30):
            self.agent.step()
            state = g.snapshot()
            if self.goal.done(state) and state["mode"] == "overworld":
                break
        self.assertTrue(self.goal.done(state), str(self.records[-10:]))
        self.assertEqual(state["mode"], "overworld")
        self.assertEqual(g.bag(), [{"name": "SUPER POTION", "qty": 4}])
        self.assertFalse(any(r.get("choice") == "item:SUPER POTION" for r in self.records))

    def test_snorlax_flute_walks_to_valid_position_and_starts_battle(self):
        from pokemon_red_jev.snorlax import SNORLAX
        g = self.game
        self.enter("ROUTE_12", warp=2)
        for name, bit in g.data.events.items():
            if name.startswith("EVENT_BEAT_ROUTE_12_TRAINER_"):
                g.memory[g.data.sym("wEventFlags") + (bit >> 3)] |= 1 << (bit & 7)
        flute = next(i for i, name in g.rom.items.items() if name == "POKé FLUTE")
        g.memory[g.data.sym("wNumBagItems")] = 1
        g.memory[g.data.sym("wBagItems"):g.data.sym("wBagItems") + 3] = [flute, 1, 255]
        self.goal = Goal("Clear Snorlax", "progress", "ROUTE_12",
                         {"kind": "event", "value": "EVENT_BEAT_ROUTE12_SNORLAX"}, 80)
        def choose(state, options):
            if state["mode"] == "overworld":
                self.assertIn("item:POKé FLUTE", options, str((state["map"], state["x"], state["y"], options, self.records[-8:])))
                return "item:POKé FLUTE"
            return next(iter(options))
        self.menu_agent(choose)
        for _ in range(60):
            state = g.snapshot()
            if state.get("battle") and state["battle"]["enemy"]["species"] == "SNORLAX":
                break
            self.agent.step()
        self.assertEqual((state.get("battle") or {}).get("enemy", {}).get("species"), "SNORLAX", str(self.records[-10:]))
        self.assertIn((state["x"], state["y"]), SNORLAX["ROUTE_12"]["positions"])
        self.assertTrue(any(r.get("choice") == "item:POKé FLUTE" for r in self.records))

    def test_sleeping_snorlax_context_and_remote_collision(self):
        self.enter("ROUTE_12")
        state = self.game.snapshot()
        self.agent._attach_situation(state)
        options = self.nav.actions(state, self.goal)
        self.assertFalse(any(a.key == "npc:1" for a in options))
        self.assertFalse(state["situation"]["route_blocker"]["has_flute"])
        route16 = next(mid for mid, md in self.game.rom.maps.items() if md["name"] == "ROUTE_16")
        self.assertIn((26, 10), self.nav.regions.blocked[route16])
        state["events"].append("EVENT_BEAT_ROUTE16_SNORLAX")
        self.nav.update(state)
        self.assertNotIn((26, 10), self.nav.regions.blocked[route16])
        self.assertNotIn((26, 10), self.nav.regions.static(*self.nav.regions.signature).blocked[route16])

    def test_snorlax_retreat_rechecks_old_dialog_trap(self):
        g = self.game
        self.enter("ROUTE_12", warp=2)
        for name, bit in g.data.events.items():
            if name.startswith("EVENT_BEAT_ROUTE_12_TRAINER_"):
                g.memory[g.data.sym("wEventFlags") + (bit >> 3)] |= 1 << (bit & 7)
        flute = next(i for i, name in g.rom.items.items() if name == "POKé FLUTE")
        g.memory[g.data.sym("wNumBagItems")] = 1
        g.memory[g.data.sym("wBagItems"):g.data.sym("wBagItems") + 3] = [flute, 1, 255]
        approach = next(a for a in self.choices() if a.key == "item:POKé FLUTE")
        self.assertTrue(self.nav.execute(approach))  # Approach without playing the Flute.
        self.assertEqual((g.snapshot()["x"], g.snapshot()["y"]), (10, 61))
        g.memory[g.data.sym("wNumBagItems")] = 0
        g.memory[g.data.sym("wBagItems")] = 255
        state = g.snapshot()
        self.nav.memory.sync(state)
        self.nav.memory.traps["ROUTE_12"] = ["10,60"]
        self.goal = Goal("Return to Lavender", "progress", "LAVENDER_TOWN",
                         {"kind": "map", "value": "LAVENDER_TOWN"}, 20)
        self.menu_agent(lambda *_: self.fail("Retreat should be automatic"))
        self.agent.step()
        self.assertNotEqual(g.snapshot()["map"], "ROUTE_12", str(self.records[-8:]))
        self.assertTrue(any(r.get("source") == "routine" and r.get("choice", "").startswith(("door:", "exit:"))
                            for r in self.records))

    def test_pc_cycle_guard_closes_the_real_pc(self):
        self.enter("VIRIDIAN_POKECENTER")
        self.open_interaction("OpenPokemonCenterPC")
        self.menu_agent(lambda state, options: next(iter(options)))
        self.agent.jev.manual = True  # Deliberately bypass the automatic team policy to exercise the loop guard.
        for _ in range(120):
            self.agent.step()
            if self.agent.pc_done:
                break
        self.assertTrue(self.agent.pc_done, str(self.game.screen()["rows"]))
        self.assertEqual(self.game.mode(), "overworld")
        self.assertEqual(self.agent.team_sig, self.agent.owned_team(self.game.snapshot()))
        self.assertTrue(any(r["kind"] == "guard" for r in self.records))
        print("ROM PC: repeating deposit session closed; completed visit remembered", flush=True)

    def test_shop_cycle_guard_and_quit_cooldown(self):
        self.enter("VIRIDIAN_MART")
        self.open_interaction("CLERK")
        def cycle(state, options):
            return next((k for k, desc in options.items() if desc.startswith("BUY")),
                        "cancel" if "cancel" in options else next(iter(options)))
        self.menu_agent(cycle)
        for _ in range(120):
            self.agent.step()
            if any(r["kind"] == "guard" for r in self.records):
                break
        self.assertEqual(self.game.mode(), "overworld", str(self.game.screen()["rows"]))
        self.assertTrue(any(r["kind"] == "guard" for r in self.records))
        self.open_interaction("CLERK")
        self.agent.jev.choose = lambda state, options: next((k for k, desc in options.items() if desc.startswith("Leave the shop")), next(iter(options)))
        for _ in range(30):
            self.agent.step()
            if self.agent.shop_at:
                break
        self.assertGreater(self.agent.shop_at, 0, str(self.records[-15:]) + str(self.game.screen()["rows"]))
        self.assertEqual(self.agent.shop_money, self.game.snapshot()["money"])
        self.controls.leave_menu()
        self.assertEqual(self.game.mode(), "overworld")
        print("ROM shop: repeated browsing closed; QUIT recorded cooldown", flush=True)

    def test_training_routes_to_grass_and_starts_a_wild_battle(self):
        self.game.memory[self.game.data.sym("wRepelRemainingSteps")] = 0
        self.enter("PEWTER_CITY", warp=1)
        self.goal.focus, self.goal.target_map = "train", "ROUTE_2"
        route = next(a for a in self.choices() if a.key == "exit:down")
        self.assertIn("Route to tall grass", route.description)
        self.perform(route)
        self.finish_dialog()
        self.assertEqual(self.game.snapshot()["map"], "ROUTE_2")
        for _ in range(3):
            self.perform(next(a for a in self.choices() if a.key == "grass"))
            if self.game.mode() == "battle":
                break
        self.assertEqual(self.game.snapshot()["battle"]["kind"], "wild")
        print("ROM training: Pewter -> Route 2 grass -> wild battle", flush=True)

    def battle_or_route(self, state, options):
        if state.get('battle') and any(k.startswith('move:') for k in options):
            b = state['battle']
            move = max((m for m in b['moves'] if f"move:{m['slot']}" in options),
                       key=lambda m: damage(b['player'], b['enemy'], m, self.game.rom.effectiveness(m['type'], b['enemy']['types'])))
            return f"move:{move['slot']}"
        if state['mode'] != 'overworld':
            return next((k for k, desc in options.items() if desc.startswith('YES')), next(iter(options)))
        toward = [k for k, desc in options.items() if 'Leads toward the objective' in desc]
        self.assertTrue(toward, str((state['map'], state['x'], state['y'], options)))
        return toward[0]

    def test_mt_moon_nerd_fossil_and_exit(self):
        self.game.memory[self.game.data.sym('wObtainedBadges')] = 1
        self.enter('MT_MOON_B2F', warp=1)
        # The cave's LAST_MAP exit expects the outdoor entry map, Route 4.
        route4 = next(i for i, md in self.game.rom.maps.items() if md['name'] == 'ROUTE_4')
        self.game.memory[self.game.data.sym('wLastMap')] = route4
        self.game.outside_map = route4
        self.goal = fallback_goal(self.game.snapshot())
        self.menu_agent(self.battle_or_route)
        for _ in range(350):
            state = self.game.snapshot()
            if state['map'] == 'ROUTE_4':
                break
            self.assertIsNone(self.nav.field_move_needed(state))
            self.agent.step()
        state = self.game.snapshot()
        self.assertIn('EVENT_BEAT_MT_MOON_EXIT_SUPER_NERD', state['events'])
        self.assertTrue({'EVENT_GOT_DOME_FOSSIL', 'EVENT_GOT_HELIX_FOSSIL'}.intersection(state['events']))
        self.assertEqual(state['map'], 'ROUTE_4', str((state, self.records[-10:])))
        print('ROM Mt. Moon: defeated fossil guard, took fossil, exited to Route 4', flush=True)

    def test_team_catches_a_cave_backup(self):
        g = self.game
        g.memory[g.data.sym('wRepelRemainingSteps')] = 0
        ball = next(i for i, name in g.rom.items.items() if name == 'ULTRA BALL')
        g.memory[g.data.sym('wNumBagItems')] = 1
        g.memory[g.data.sym('wBagItems'):g.data.sym('wBagItems') + 3] = [ball, 5, 255]
        self.enter('MT_MOON_B2F', warp=1)
        self.goal = Goal('Catch a backup', 'catch', 'MT_MOON_B2F', {'kind': 'owned_count', 'value': 2}, 12)
        self.menu_agent(self.battle_or_route)
        self.agent.team_errand = True
        for _ in range(200):
            if len(g.party()) == 2 and g.mode() == 'overworld':
                break
            self.agent.step()
        self.assertEqual(len(g.party()), 2, str(self.records[-12:]))
        self.assertEqual(g.mode(), 'overworld')
        self.assertGreater(sum(r.get('choice', '').startswith('ball:') for r in self.records), 0)
        self.assertLessEqual(sum(r.get('choice', '').startswith('ball:') for r in self.records), 5)
        print('ROM team: paced cave floor and caught a backup through ordinary ball controls', flush=True)

    def test_team_deposits_surplus_and_withdraws_a_backup(self):
        g = self.game
        a, nick = g.data.sym('wPartyMons'), g.data.sym('wPartyMonNicks')
        record = list(g.memory[a:a + 44])
        code = {v: k for k, v in g.data.charmap.items()}
        g.memory[g.data.sym('wPartyCount')] = 4
        g.memory[g.data.sym('wPartySpecies'):g.data.sym('wPartySpecies') + 5] = [record[0]] * 4 + [255]
        for i in range(1, 4):
            g.memory[a + i * 44:a + (i + 1) * 44] = record
            g.memory[a + i * 44 + 3] = g.memory[a + i * 44 + 33] = 20 - i
            name = f'MON{chr(65 + i)}'
            g.memory[nick + i * 11:nick + (i + 1) * 11] = [code[c] for c in name] + [0x50] * (11 - len(name))
        self.enter('VIRIDIAN_POKECENTER')
        self.goal = Goal('Store surplus', 'team', 'VIRIDIAN_POKECENTER', {'kind': 'team_ready', 'value': True}, 12)
        self.menu_agent(self.battle_or_route)
        self.agent.team_errand = True
        for _ in range(120):
            self.agent.step()
            if len(g.party()) == 2 and g.mode() == 'overworld':
                break
        self.assertEqual(len(g.party()), 2, str((g.screen()['rows'], self.records[-15:])))
        self.assertEqual(len(g.box()), 2)
        self.assertEqual(g.party()[0]['level'], 100)
        # A separate entry condition: only the starter remains in the party, with a backup in storage.
        g.memory[g.data.sym('wPartyCount')] = 1
        g.memory[g.data.sym('wPartySpecies') + 1] = 255
        self.agent.goal = self.goal
        self.agent.team_errand, self.agent.pc_done, self.agent.intent = True, False, None
        for _ in range(120):
            self.agent.step()
            if len(g.party()) == 2 and g.mode() == 'overworld':
                break
        self.assertEqual(len(g.party()), 2, str((g.screen()['rows'], self.records[-15:])))
        self.assertEqual(len(g.box()), 1)
        self.assertFalse(any('RELEASE' in r.get('description', '') for r in self.records if r['kind'] == 'action'))
        print('ROM PC: stored surplus, preserved strongest, withdrew boxed backup, closed PC', flush=True)

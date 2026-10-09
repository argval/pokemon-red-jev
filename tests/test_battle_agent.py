"""Battle feedback and recovery through the real Agent decision loop."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from test_core import new_agent
from pokemon_red_jev.controls import Controls
from pokemon_red_jev.navigation import Action


class BattleAgentChecks(unittest.TestCase):
    def setUp(self):
        """Use observed battle facts with a selector that keeps choosing a wasted move."""
        self.agent, self.records = new_agent()
        self.game = self.agent.game
        self.game.state["party"][0].update(hp=20, max_hp=20)
        self.enemy = dict(species="BELLSPROUT", level=21, hp=55, max_hp=55, status="PARALYZED",
                          types=["GRASS", "POISON"], attack=30, defense=30, special=30, speed=6, moves=[])
        self.player = dict(species="ODDISH", level=17, hp=20, max_hp=20, status="OK", types=["GRASS", "POISON"],
                           attack=30, defense=30, special=30, speed=17, moves=[])
        self.game.state.update(mode="battle", battle=dict(kind="trainer", enemy_id=["trainer", 0],
                               player=self.player, enemy=self.enemy, active_slot=0, moves=[]))
        self.game.rom.effectiveness = lambda attack, defense: 1
        self.attack = Action("move:0", "Use ABSORB", "battle_move",
                             target=dict(id=71, name="ABSORB", power=20, pp=20, slot=0, damage_range=(1, 1), hit_percent=100))
        self.wasted = Action("move:1", "Use HAZE", "battle_move",
                             target=dict(id=114, name="HAZE", power=0, pp=30, slot=1, effect=0x19, damage_range=(0, 0)))
        self.agent.controls = SimpleNamespace(actions=lambda state: [self.attack, self.wasted],
                                              execute=self.execute, leave_menu=lambda: None)
        self.offered = []
        self.agent.jev.choose = self.choose

    def choose(self, state, options):
        """Record model context while deliberately repeating the unproductive choice."""
        self.offered.append(deepcopy(state["battle"]["turn_memory"]))
        return "move:1"

    def execute(self, action):
        """Spend PP and change the screen; only an attack changes enemy HP."""
        self.game.state["screen"] = [f"Turn {len(self.records)}"]
        self.game.state["party"][0]["moves"] = [{"name": "HAZE", "pp": 30 - len(self.offered)}]
        if action.key == "move:0":
            self.enemy["hp"] -= 1

    def test_pp_and_screen_changes_do_not_hide_three_wasted_turns(self):
        for _ in range(4):
            self.agent.step()
        choices = [row["choice"] for row in self.records if row["kind"] == "action"]
        self.assertEqual(choices, ["move:1", "move:1", "move:1", "move:0"])
        self.assertEqual(self.enemy["hp"], 54)
        self.assertEqual(len(self.offered), 3)
        outcomes = [row for row in self.records if row["kind"] == "battle_turn"]
        self.assertEqual([row["useful"] for row in outcomes], [False, False, False])
        self.assertTrue(any(row.get("choice") == "move:0" for row in self.records if row["kind"] == "guard"))

    def test_advance_keeps_result_text_until_the_next_battle_choice(self):
        self.game.screen = lambda: {"dialog": self.game.state.get("dialog", "")}
        self.agent.step()
        self.game.state["dialog"] = "It didn't affect BELLSPROUT!"
        self.agent.controls.actions = lambda state: [Action("advance", "Advance battle text", "button")]
        self.agent.step()
        self.assertTrue(self.agent.battle_memory.context()["pending"])
        self.assertEqual(self.agent.battle_memory.context()["recent_turns"], [])
        self.game.state["dialog"] = "FIGHT PKMN ITEM RUN"
        self.agent.controls.actions = lambda state: [self.attack, self.wasted]
        self.agent.step()
        turn = self.offered[-1]["recent_turns"][-1]
        self.assertEqual(turn["move"], "HAZE")
        self.assertEqual(turn["text"], ["It didn't affect BELLSPROUT!"])

    def test_new_identical_trainer_opponent_resets_stalls_and_switch_history(self):
        for _ in range(3):
            self.agent.step()
        self.agent.benched["slots"].add(1)
        self.game.state["battle"]["enemy_id"] = ["trainer", 1]
        self.agent.step()
        self.assertEqual(self.offered[-1]["stalled_turns"], 0)
        self.assertEqual(self.agent.benched["slots"], set())
        self.assertEqual(self.offered[-1]["recent_turns"], [])

    def test_manual_control_keeps_the_users_choice(self):
        self.agent.jev.manual = True
        for _ in range(5):
            self.agent.step()
        self.assertEqual([row["choice"] for row in self.records if row["kind"] == "action"], ["move:1"] * 5)

    def test_controls_refresh_stats_after_settling_battle_text(self):
        state = deepcopy(self.game.state)
        state["battle"]["catchable"] = True
        self.game.settle_screen = lambda: self.enemy.update(hp=12, status="SLEEP")
        self.game.screen = lambda: {"rows": ["Battle text"], "cells": [[]], "cursor": None, "waiting": True}
        actions = Controls(self.game).actions(state)
        self.assertEqual(actions[0].key, "advance")
        self.assertEqual(state["battle"]["enemy"]["hp"], 12)
        self.assertEqual(state["battle"]["enemy"]["status"], "SLEEP")
        self.assertTrue(state["battle"]["catchable"])

    def test_switch_filter_cannot_remove_the_last_legal_pp_spending_action(self):
        from test_battle import pokemon, move
        player = pokemon("ODDISH")
        player["slot"] = 0
        player["moves"] = [move("POISONPOWDER", move_type="POISON", effect=0x42)]
        bench = pokemon("DIGLETT")
        bench["slot"] = 1
        bench["moves"] = [move("SCRATCH", move_type="NORMAL", power=40)]
        self.game.state["party"] = [player, bench]
        self.game.state["battle"].update(player=player, moves=player["moves"])
        self.agent.benched = {"foe": ["trainer", 0], "slots": {1}}
        controls = Controls(self.game)
        controls.actions = controls.battle_actions
        controls.execute = lambda action: None
        self.agent.controls = controls
        self.agent.jev.choose = lambda state, options: next(iter(options))
        self.agent.step()
        action = next(row for row in self.records if row["kind"] == "action")
        self.assertEqual(action["choice"], "move:0")
        self.assertIn("Spend this PP", action["description"])
        self.assertFalse(any(row["kind"] == "no_actions" for row in self.records))

if __name__ == "__main__":
    unittest.main()

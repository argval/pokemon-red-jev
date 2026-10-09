"""Core battle move availability and damage regressions."""

from copy import deepcopy
from math import prod
from types import SimpleNamespace
import unittest

from pokemon_red_jev.battle import move_failure
from pokemon_red_jev.controls import Controls


def move(name, *, slot=0, move_type="GRASS", power=0, effect=0, pp=15, accuracy=75):
    """Build a move with the same fields as the ROM observation."""
    return dict(name=name, slot=slot, type=move_type, power=power, effect=effect, pp=pp,
                max_pp=pp, accuracy=accuracy)


def pokemon(species="ODDISH", *, types=None, moves=None):
    """Build complete, realistic combat stats without any emulator state."""
    return dict(species=species, nickname=species, slot=0, level=18, hp=55, max_hp=55,
                attack=26, defense=30, speed=24, special=40,
                types=types or ["GRASS", "POISON"], status="OK", moves=moves or [],
                volatile={}, stat_stages=dict(attack=7, defense=7, speed=7, special=7, accuracy=7, evasion=7))


class BattleRuleChecks(unittest.TestCase):
    def setUp(self):
        """Start with healthy Oddish and Bellsprout as in the reported encounter."""
        self.player, self.enemy = pokemon(), pokemon("BELLSPROUT")

    def test_damaging_attacks_remain_valid_when_their_secondary_status_cannot_work(self):
        self.enemy["status"] = "PARALYZED"
        self.enemy["volatile"]["substitute"] = True
        for selected in (move("POISON STING", move_type="POISON", power=15, effect=0x02),
                         move("THUNDERSHOCK", move_type="ELECTRIC", power=40, effect=0x06),
                         move("TWINEEDLE", move_type="BUG", power=25, effect=0x4D)):
            with self.subTest(move=selected["name"]):
                self.assertIsNone(move_failure(selected, self.player, self.enemy))

class BattleActionChecks(unittest.TestCase):
    def setUp(self):
        """Supply Gen I type entries and readable WRAM values to the real Controls."""
        chart = {
            ("GRASS", "GRASS"): 0.5, ("GRASS", "POISON"): 0.5,
            ("GROUND", "GRASS"): 0.5, ("GROUND", "POISON"): 2,
            ("NORMAL", "GHOST"): 0, ("GHOST", "NORMAL"): 0,
            ("POISON", "GRASS"): 2, ("POISON", "POISON"): 0.5,
            ("PSYCHIC", "POISON"): 2, ("FIGHTING", "GHOST"): 0,
        }
        rom = SimpleNamespace(type_chart=chart,
                              effectiveness=lambda attack, defense: prod(chart.get((attack, t), 1) for t in defense))
        self.wram = {"wPlayerMonAccuracyMod": 7, "wEnemyMonEvasionMod": 7}
        self.controls = Controls(SimpleNamespace(rom=rom, u8=lambda name: self.wram.get(name, 7 if name.endswith("Mod") else 0)))

    def state(self, moves, *, status="OK", kind="trainer", manual=False):
        """Build the same battle and party context Jev receives at the Fight menu."""
        player = pokemon(moves=moves)
        enemy = pokemon("BELLSPROUT", moves=[move("VINE WHIP", power=35, accuracy=100)])
        enemy["status"] = status
        return dict(map="ROUTE_24", mode="battle", party=[deepcopy(player)], bag=[], badges=0,
                    manual_control=manual, current_focus="train", active_goal={"goal": "Train the party"},
                    battle=dict(player=player, enemy=enemy, kind=kind, active_slot=0, moves=moves,
                                catchable=False, safari=False))

    def powders(self):
        """Return Oddish's actual status moves and one useful damaging move."""
        return [move("STUN SPORE", slot=0, effect=0x43),
                move("POISONPOWDER", slot=1, move_type="POISON", effect=0x42),
                move("ABSORB", slot=2, power=20, effect=0x03, accuracy=100)]

    def test_healthy_and_paralyzed_bellsprout_offer_only_status_moves_that_can_work(self):
        state = self.state(self.powders())
        actions = self.controls.battle_actions(state)
        self.assertEqual({a.key for a in actions}, {"move:0", "move:2"})
        self.assertEqual([m["name"] for m in state["battle"]["unavailable_moves"]], ["POISONPOWDER"])
        state["battle"]["enemy"]["status"] = "PARALYZED"
        actions = self.controls.battle_actions(state)
        self.assertEqual([a.key for a in actions], ["move:2"])
        self.assertEqual({m["name"] for m in state["battle"]["unavailable_moves"]}, {"STUN SPORE", "POISONPOWDER"})
        self.assertEqual(len(state["battle"]["move_facts"]), 3)

    def test_manual_control_retains_blocked_moves_with_reasons_and_enemy_summary(self):
        state = self.state(self.powders(), status="PARALYZED", manual=True)
        actions = self.controls.battle_actions(state)
        self.assertEqual({a.key for a in actions}, {"move:0", "move:1", "move:2"})
        for action in actions:
            self.assertIn("BELLSPROUT", action.description)
            self.assertIn("GRASS/POISON", action.description)
            self.assertIn("HP 55/55", action.description)
            self.assertIn("PARALYZED", action.description)
        blocked = [a for a in actions if a.key != "move:2"]
        self.assertTrue(all(a.target["blocked_reason"] for a in blocked))
        self.assertTrue(all("Cannot work now" in a.description for a in blocked))

    def test_pp_zero_and_disabled_moves_are_not_selectable(self):
        moves = self.powders()
        moves[1]["pp"] = 0
        self.wram["wPlayerDisabledMove"] = 0x13
        state = self.state(moves)
        self.assertEqual([a.key for a in self.controls.battle_actions(state)], ["move:2"])
        moves[2]["pp"] = 0
        self.assertEqual([a.key for a in self.controls.battle_actions(state)], ["struggle"])

    def test_all_useless_trainer_moves_spend_pp_without_claiming_struggle_is_legal(self):
        state = self.state(self.powders()[:2], status="PARALYZED")
        actions = self.controls.battle_actions(state)
        self.assertEqual(len(actions), 1)
        self.assertIn(actions[0].key, {"move:0", "move:1"})
        self.assertIn("Spend this PP", actions[0].description)
        self.assertTrue(actions[0].target["blocked_reason"])

    def test_night_shade_with_zero_printed_power_remains_an_attack_against_normal(self):
        state = self.state([move("NIGHTSHADE", move_type="GHOST", power=0, effect=0x29)])
        state["battle"]["enemy"].update(species="RATTATA", types=["NORMAL"])
        actions = self.controls.battle_actions(state)
        self.assertEqual([a.key for a in actions], ["move:0"])
        self.assertEqual(state["battle"]["move_facts"][0]["damage_range"], (18, 18))
        self.assertNotIn("Status move", actions[0].description)

    def test_rom_type_order_is_used_for_damage_facts(self):
        state = self.state([move("DIG", move_type="GROUND", power=40)])
        state["battle"]["player"].update(types=["GROUND"], level=10, attack=20)
        state["battle"]["enemy"]["defense"] = 20
        self.controls.battle_actions(state)
        self.assertEqual(state["battle"]["move_facts"][0]["damage_range"], (6, 8))


if __name__ == "__main__":
    unittest.main()

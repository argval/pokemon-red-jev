"""Battle observations from synthetic Red WRAM, without an emulator or save."""

import json
from types import SimpleNamespace
import unittest

from pokemon_red_jev.data import Data
from pokemon_red_jev.game import Game


class BattleStateChecks(unittest.TestCase):
    def setUp(self):
        """Seed both battle structs and only the symbols required before this change."""
        self.game = object.__new__(Game)
        self.game.memory = bytearray(65536)
        self.game.data = object.__new__(Data)
        self.game.data.symbols = {
            "wBattleMon": 0xc000, "wEnemyMon": 0xc100,
            "wIsInBattle": 0xc200, "wPlayerMonNumber": 0xc201,
            "wBattleType": 0xc202, "wNumSafariBalls": 0xc203,
        }
        self.game.rom = SimpleNamespace(
            species={1: {"name": "ODDISH", "types": ["GRASS", "POISON"]},
                     2: {"name": "BELLSPROUT", "types": ["GRASS", "POISON"]}},
            moves={1: {"id": 1, "name": "ABSORB", "power": 20, "type": "GRASS", "pp": 25}},
        )
        for base, species in ((0xc000, 1), (0xc100, 2)):
            self.game.memory[base] = species
            self.game.memory[base + 5:base + 7] = bytes((22, 3))
            self.game.memory[base + 14] = 18
            for offset, value in ((1, 35), (15, 55), (17, 26), (19, 30), (21, 24), (23, 40)):
                self.game.memory[base + offset:base + offset + 2] = value.to_bytes(2, "big")
            self.game.memory[base + 8] = 1
            self.game.memory[base + 25] = 9
        self.game.memory[0xc200] = 2

    def symbol(self, name, value):
        """Add a synthetic optional symbol and its byte value."""
        address = 0xc300 + len(self.game.data.symbols)
        self.game.data.symbols[name] = address
        self.game.memory[address] = value

    def test_current_types_replace_species_types_and_remove_duplicates(self):
        self.game.memory[0xc005:0xc007] = bytes((20, 20))
        self.game.memory[0xc105:0xc107] = bytes((21, 24))
        battle = self.game.battle()
        self.assertEqual(battle["player"]["types"], ["FIRE"])
        self.assertEqual(battle["enemy"]["types"], ["WATER", "PSYCHIC"])
        self.assertEqual(battle["enemy"]["species"], "BELLSPROUT")
        self.assertEqual(self.game.rom.species[2]["types"], ["GRASS", "POISON"])

    def test_live_vitals_stats_and_pp_keep_the_red_struct_offsets(self):
        self.game.memory[0xc104] = 64
        self.game.memory[0xc101:0xc103] = bytes((1, 2))
        self.game.memory[0xc119] = 4
        enemy = self.game.battle()["enemy"]
        self.assertEqual((enemy["hp"], enemy["max_hp"], enemy["status"]), (258, 55, "PARALYZED"))
        self.assertEqual((enemy["attack"], enemy["defense"], enemy["speed"], enemy["special"]), (26, 30, 24, 40))
        self.assertEqual(enemy["moves"][0]["pp"], 4)

    def test_optional_symbols_decode_separate_effects_and_stat_stages(self):
        self.symbol("wPlayerBattleStatus1", 0x90)
        self.symbol("wPlayerBattleStatus2", 0xb7)
        self.symbol("wPlayerBattleStatus3", 0x0f)
        self.symbol("wPlayerSubstituteHP", 14)
        self.symbol("wEnemyBattleStatus1", 0x40)
        self.symbol("wEnemyBattleStatus2", 0)
        self.symbol("wEnemyBattleStatus3", 0)
        self.symbol("wEnemySubstituteHP", 28)
        for stat, player, enemy in (
            ("Attack", 13, 1), ("Defense", 12, 2), ("Speed", 11, 3),
            ("Special", 10, 4), ("Accuracy", 9, 5), ("Evasion", 8, 6),
        ):
            self.symbol(f"wPlayerMon{stat}Mod", player)
            self.symbol(f"wEnemyMon{stat}Mod", enemy)
        battle = self.game.battle()
        self.assertEqual(battle["player"]["volatile"], {
            "charging": True, "invulnerable": False, "confused": True,
            "x_accuracy": True, "mist": True, "focus_energy": True,
            "substitute": True, "recharge": True, "seeded": True,
            "badly_poisoned": True, "light_screen": True, "reflect": True,
            "transformed": True, "substitute_hp": 14,
        })
        self.assertTrue(battle["enemy"]["volatile"]["invulnerable"])
        self.assertFalse(battle["enemy"]["volatile"]["substitute"])
        self.assertEqual(battle["enemy"]["volatile"]["substitute_hp"], 0)
        self.assertEqual(battle["player"]["stat_stages"], {
            "attack": 13, "defense": 12, "speed": 11, "special": 10, "accuracy": 9, "evasion": 8,
        })
        self.assertEqual(battle["enemy"]["stat_stages"], {
            "attack": 1, "defense": 2, "speed": 3, "special": 4, "accuracy": 5, "evasion": 6,
        })
        self.assertEqual(json.loads(json.dumps(battle)), battle)

    def test_trainer_identity_changes_for_identical_opponents_and_survives_transform(self):
        first = self.game.battle()["enemy_id"]
        self.game.memory[0xc103] = 1
        second = self.game.battle()
        self.assertEqual(second["enemy"]["party_slot"], 1)
        self.assertNotEqual(first, second["enemy_id"])
        self.game.memory[0xc100] = 1
        self.game.memory[0xc104] = 64
        self.game.memory[0xc101:0xc103] = bytes((0, 12))
        self.assertEqual(self.game.battle()["enemy_id"], second["enemy_id"])

if __name__ == "__main__":
    unittest.main()

"""Battle result checks that run without a ROM or model connection."""

from copy import deepcopy
import json
import unittest

from pokemon_red_jev.battle_memory import BattleMemory
from pokemon_red_jev.navigation import Action


def state(slot=0):
    """Build a trainer turn with two combatants and an explicit roster identity."""
    return {"mode": "battle", "in_battle": True, "battle": {
        "kind": "trainer", "active_slot": 0, "enemy_id": ["trainer", slot],
        "player": {"species": "ODDISH", "hp": 40, "status": "OK",
                   "stat_stages": {"special": 7}, "volatile": {}},
        "enemy": {"species": "BELLSPROUT", "level": 15, "hp": 55, "max_hp": 55,
                  "status": "PARALYZED", "stat_stages": {"defense": 7}, "volatile": {}}}}


def move(name="POISONPOWDER", slot=1):
    """Build a committed move action, separate from message advancement."""
    return Action(f"move:{slot}", name, "battle_move", target={"name": name, "slot": slot, "pp": 35})


class BattleMemoryChecks(unittest.TestCase):
    def test_hit_after_a_miss_clears_failure_counts(self):
        memory, before = BattleMemory(), state()
        action = move("ABSORB", 0)
        memory.start(action, before["battle"])
        memory.observe(before, "ODDISH's attack missed!")
        self.assertFalse(memory.finish(before)["useful"])
        self.assertEqual(memory.context()["move_failures"], {"move:0": 1})
        memory.start(action, before["battle"])
        after = deepcopy(before)
        after["battle"]["enemy"]["hp"] = 50
        result = memory.finish(after)
        self.assertEqual(result["damage"], 5)
        self.assertTrue(result["useful"])
        self.assertEqual(memory.context()["move_failures"], {})
        self.assertEqual(memory.context()["stalled_turns"], 0)

    def test_pending_turn_and_failure_counts_survive_checkpoint(self):
        memory, before = BattleMemory(), state()
        memory.start(move(), before["battle"])
        memory.finish(before)
        memory.start(move("ABSORB", 0), before["battle"])
        memory.observe(before, "ODDISH used ABSORB!")
        restored = BattleMemory()
        restored.load(json.loads(json.dumps(memory.to_dict())))
        self.assertEqual(restored.context(), memory.context())
        after = deepcopy(before)
        after["battle"]["enemy"]["hp"] = 51
        result = restored.finish(after)
        self.assertEqual(result["damage"], 4)
        self.assertEqual(result["text"], ["ODDISH used ABSORB!"])

    def test_effects_and_a_completed_switch_count_as_progress(self):
        cases = [
            ("enemy", "status", "SLEEP"),
            ("enemy", "stat_stages", {"defense": 6}),
            ("enemy", "volatile", {"seeded": True}),
            ("enemy", "volatile", {"confused": True}),
            ("player", "stat_stages", {"special": 8}),
            ("player", "volatile", {"reflect": True}),
            ("player", "hp", 45),
        ]
        for side, key, value in cases:
            with self.subTest(side=side, key=key, value=value):
                memory, before = BattleMemory(), state()
                memory.start(move(), before["battle"])
                after = deepcopy(before)
                after["battle"][side][key] = value
                self.assertTrue(memory.finish(after)["useful"])
        memory, before = BattleMemory(), state()
        memory.start(Action("switch:1", "Switch", "switch", target={"slot": 1}), before["battle"])
        after = deepcopy(before)
        after["battle"]["active_slot"] = 1
        after["battle"]["player"]["hp"] = 5
        self.assertTrue(memory.finish(after)["useful"])

if __name__ == "__main__":
    unittest.main()

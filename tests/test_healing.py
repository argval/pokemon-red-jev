"""Center routing and resource conservation, including item menu continuation."""

from types import SimpleNamespace
import unittest

from pokemon_red_jev.controls import Controls
from pokemon_red_jev.healing import field_healing
from pokemon_red_jev.navigation import Navigation, supply_actions
from test_core import new_agent


class HealingChecks(unittest.TestCase):
    def state(self, hp=30, status="OK", where="VERMILION_CITY"):
        return dict(map=where, map_id=1, mode="overworld", party=[
            dict(slot=0, nickname="JEV", species="WARTORTLE", hp=hp, max_hp=80, status=status,
                 level=25, moves=[])], bag=[{"name": "SUPER POTION", "qty": 4}])

    def offers(self, state, hops):
        center = "VERMILION_POKECENTER" if hops is not None else None
        state["healing"] = field_healing(state, center, hops)
        return {key for key, _, _ in supply_actions(state, snorlax=False, surfing=False,
                machine_text=lambda *_: None, is_key=lambda _: False, unused={})}

    def test_nearby_center_withholds_routine_hp_items(self):
        for hops in (0, 1, 2):
            with self.subTest(hops=hops):
                self.assertNotIn("item:SUPER POTION", self.offers(self.state(), hops))
        self.assertNotIn("item:SUPER POTION", self.offers(self.state(hp=1, where="VERMILION_POKECENTER"), 0))

    def test_nearby_emergency_stops_after_recovering_a_battler(self):
        state = self.state(hp=10)
        self.assertIn("item:SUPER POTION", self.offers(state, 1))
        state["party"][0]["hp"] = 60
        self.assertNotIn("item:SUPER POTION", self.offers(state, 1))
        state["party"].append(dict(state["party"][0], slot=1, nickname="BACKUP", hp=70))
        state["party"][0]["hp"] = 10
        self.assertNotIn("item:SUPER POTION", self.offers(state, 1))

    def test_poison_emergency_and_status_only_hp_waste(self):
        self.assertIn("item:SUPER POTION", self.offers(self.state(hp=4, status="POISON"), 1))
        self.assertNotIn("item:SUPER POTION", self.offers(self.state(hp=80, status="POISON"), None))
        self.assertNotIn("item:SUPER POTION", self.offers(self.state(hp=4, status="POISON", where="VERMILION_POKECENTER"), 0))

    def test_distant_or_unreachable_center_allows_recovery_without_topups(self):
        for hops in (3, None):
            with self.subTest(hops=hops):
                state = self.state(hp=30)
                self.assertIn("item:SUPER POTION", self.offers(state, hops))
                state["party"][0]["hp"] = 60
                self.assertNotIn("item:SUPER POTION", self.offers(state, hops))

    def test_heal_focus_routes_to_reachable_center_instead_of_planner_target(self):
        nav = Navigation.__new__(Navigation)
        nav.state = dict(current_focus="heal", healing={"center": "VERMILION_POKECENTER"})
        self.assertEqual(nav.walking_target("VERMILION_MART"), "VERMILION_POKECENTER")

    def test_planner_and_controller_receive_center_and_exceptions(self):
        agent, _ = new_agent()
        agent.navigation.state = self.state()
        agent.navigation.service_target = lambda _: "VERMILION_POKECENTER"
        agent.navigation.hops = lambda *_: 1
        state = self.state()
        agent._attach_healing(state)
        self.assertEqual(state["healing"]["center_hops"], 1)
        self.assertEqual(state["healing"]["allowed_slots"], [])
        self.assertEqual(agent.planner_brief(state)["healing"], state["healing"])

    def test_open_item_menu_cannot_spend_on_a_protected_target(self):
        state = self.state()
        state.update(mode="menu", using_item="SUPER POTION", money=1000)
        state["healing"] = field_healing(state, "VERMILION_POKECENTER", 1)
        screen = dict(rows=["JEV", "CANCEL"], cursor=(0, 0), waiting=False)
        game = SimpleNamespace(screen=lambda: screen, u8=lambda _: 0,
                               settle_screen=lambda: None, menu_ready=lambda: True,
                               rom=SimpleNamespace(items={}, species={}))
        controls = Controls(game)
        controls.options = lambda: [("JEV", 0)]
        options = {a.key for a in controls.actions(state)}
        self.assertEqual(options, {"cancel"})
        state["party"][0]["hp"] = 10
        state["healing"] = field_healing(state, "VERMILION_POKECENTER", 1)
        self.assertIn("menu:0", {a.key for a in controls.actions(state)})

    def test_battle_healing_remains_available(self):
        state = self.state(hp=30)
        state["healing"] = field_healing(state, "VERMILION_POKECENTER", 1)
        player = dict(state["party"][0], types=["WATER"], attack=40, defense=40, special=40, speed=40)
        move = dict(slot=0, name="TACKLE", type="NORMAL", power=35, pp=10, accuracy=95)
        enemy = dict(player, species="RATTATA", hp=30, max_hp=30, types=["NORMAL"], moves=[])
        state.update(mode="battle", battle=dict(kind="trainer", player=player, enemy=enemy,
                     moves=[move], active_slot=0, safari=False))
        game = SimpleNamespace(rom=SimpleNamespace(effectiveness=lambda *_: 1), u8=lambda _: 7)
        actions = {a.key for a in Controls(game).battle_actions(state)}
        self.assertIn("item:SUPER POTION", actions)

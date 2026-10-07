"""Sleeping road blockers must provide a useful choice, not repeat dialogue."""

import unittest

from pokemon_red_jev.navigation import Action
from pokemon_red_jev.regions import Regions
from pokemon_red_jev.snorlax import snorlax_context
from test_core import new_agent


class SnorlaxChecks(unittest.TestCase):
    def state(self, bag=(), events=()):
        return dict(map="ROUTE_12", mode="overworld", x=9, y=62, bag=list(bag), events=list(events))

    def context(self, state):
        return snorlax_context(state, [dict(picture=67, x=10, y=62)])

    def test_missing_flute_explains_blocker_and_prerequisites(self):
        context = self.context(self.state())
        self.assertFalse(context["has_flute"])
        self.assertIn("Silph Scope", context["next_step"])
        self.assertIn("backtrack", context["note"])
        self.assertIn("will not wake", context["note"])

    def test_prerequisites_follow_scope_and_fuji_progress(self):
        context = self.context(self.state(bag=[{"name": "SILPH SCOPE", "qty": 1}]))
        self.assertIn("rescue Mr. Fuji", context["next_step"])
        context = self.context(self.state(events=["EVENT_RESCUED_MR_FUJI"]))
        self.assertIn("MR_FUJIS_HOUSE", context["next_step"])
        context = self.context(self.state(bag=[{"name": "POKé FLUTE", "qty": 1}]))
        self.assertTrue(context["has_flute"])
        self.assertIn("Lv30 wild battle", context["next_step"])

    def test_cleared_or_hidden_snorlax_is_not_a_blocker(self):
        self.assertIsNone(self.context(self.state(events=["EVENT_BEAT_ROUTE12_SNORLAX"])))
        self.assertIsNone(snorlax_context(self.state(), []))

    def test_off_map_sleeping_snorlax_stays_blocked_until_its_event(self):
        regions = Regions.__new__(Regions)
        regions.snorlax_cleared = frozenset()
        md = dict(name="ROUTE_16", objects=[dict(picture=67, x=26, y=10)])
        self.assertEqual(regions.fixed_blocks(md), {(26, 10)})
        self.assertEqual(regions.fixed_blocks(md, layout=True), {(26, 10)})
        regions.snorlax_cleared = frozenset({"EVENT_BEAT_ROUTE16_SNORLAX"})
        self.assertEqual(regions.fixed_blocks(md), set())

    def test_missing_flute_backtracks_instead_of_reopening_party(self):
        agent, _ = new_agent()
        state = self.state()
        state["situation"] = {"route_blocker": self.context(state)}
        actions = [Action("party", "Inspect party", "party"),
                   Action("exit:left", "Leave toward the objective. Leads toward the objective.", "walk", [("left", 8, 62)]),
                   Action("exit:down", "Leave away from the objective", "walk", [("down", 9, 63)])]
        self.assertEqual(agent._routine_action(state, actions).key, "exit:left")

    def test_distant_snorlax_does_not_force_a_retreat(self):
        agent, _ = new_agent()
        state = agent.game.snapshot()
        state.update(map="ROUTE_12", x=10, y=21)
        state["situation"] = {"route_blocker": self.context(state)}
        exit_action = Action("exit:up", "Leave this area", "walk", [("up", 10, 20)])
        self.assertIsNone(agent._routine_action(state, [exit_action, Action("party", "Inspect party", "party")]))

    def test_blocker_context_reaches_the_planner(self):
        agent, _ = new_agent()
        agent.game.sprites = lambda: [dict(picture=67, x=10, y=62)]
        state = agent.game.snapshot()
        state.update(map="ROUTE_12")
        agent._attach_situation(state)
        self.assertEqual(agent.planner_brief(state)["situation"]["route_blocker"]["kind"], "snorlax")

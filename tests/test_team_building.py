"""Team errands and the routing/planning failures that prevented progress."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pokemon_red_jev.goals import Goal, catch_reason, complete, team_plan
from pokemon_red_jev.regions import Regions
from test_core import new_agent


class TeamBuildingChecks(unittest.TestCase):
    def mon(self, name, level, typing, moves=()):
        return dict(species=name, nickname=name, level=level, types=[typing], hp=30, max_hp=30,
                    status='OK', moves=[dict(name=n, type=t, power=40, pp=20) for n, t in moves])

    def test_team_keeps_battlers_and_sole_hm_user_and_boxes_surplus(self):
        party = [self.mon('IVYSAUR', 20, 'GRASS', [('VINE WHIP', 'GRASS')]),
                 self.mon('PIKACHU', 15, 'ELECTRIC', [('THUNDERSHOCK', 'ELECTRIC')]),
                 self.mon('ZUBAT', 8, 'POISON'), self.mon('RATTATA', 5, 'NORMAL'),
                 self.mon('PARAS', 7, 'BUG', [('CUT', 'NORMAL')])]
        state = dict(party=party, box=[], milestone={'id': 'misty'})
        plan = team_plan(state, lambda move, types: 2 if move in {'GRASS', 'ELECTRIC'} else 1)
        self.assertIn(0, plan['keep'])
        self.assertIn(1, plan['keep'])
        self.assertIn(4, plan['keep'])
        self.assertEqual(plan['deposit'], 3)
        state['box'] = [party.pop(3)]
        self.assertNotEqual(team_plan(state)['withdraw'], 0)

    def test_catching_uses_owned_roster_and_actual_receipt(self):
        ivy = self.mon('IVYSAUR', 20, 'GRASS', [('VINE WHIP', 'GRASS')])
        zubat = self.mon('ZUBAT', 10, 'FLYING')
        state = dict(party=[ivy], box=[], bag=[], milestone={'id': 'mt_moon'})
        self.assertTrue(catch_reason(state, zubat))
        goal = Goal('Catch a backup', 'catch', 'MT_MOON_B2F', {'kind': 'owned_count', 'value': 2})
        self.assertFalse(goal.done(state))
        state['box'] = [zubat]
        self.assertTrue(goal.done(state))
        self.assertIsNone(catch_reason(state, zubat))
        self.assertEqual(team_plan(state)['withdraw'], 0)

    def test_static_npc_block_is_not_misreported_as_cut(self):
        regions = Regions.__new__(Regions)
        regions.signature = (False, False, False)
        regions.flip = None
        regions.at = lambda *_: (1, 0)
        regions.distance = lambda *_: None
        regions.static = lambda *_: SimpleNamespace(at=lambda *_: (1, 0), distance=lambda *_: 4)
        self.assertIsNone(regions.field_move_needed(1, 21, 17, 'CERULEAN_CITY'))

    def test_planning_keeps_the_story_task_visible(self):
        agent, _ = new_agent()
        shown = []
        agent.game.show_overlay = lambda state, _: shown.append(deepcopy(state))
        plan = agent.planner.plan
        def planning(*args):
            self.assertTrue(shown[-1].get('active_goal'))
            self.assertIn('Planning', shown[-1].get('agent_status', ''))
            return plan(*args)
        agent.planner.plan = planning
        agent.step()

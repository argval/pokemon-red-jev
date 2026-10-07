"""Team errands and the routing/planning failures that prevented progress."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pokemon_red_jev.goals import Goal, catch_reason, complete, team_plan, wild_field_move_learners
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

    def test_cut_learner_is_wanted_even_when_far_below_the_team(self):
        party = [dict(self.mon('WARTORTLE', 30, 'WATER'), learnable_hms=['HM03', 'HM04']),
                 dict(self.mon('DIGLETT', 19, 'GROUND'), learnable_hms=[])]
        oddish = dict(self.mon('ODDISH', 13, 'GRASS'), learnable_hms=['HM01'])
        mankey = dict(self.mon('MANKEY', 10, 'FIGHTING'), learnable_hms=['HM04'])
        state = dict(party=party, box=[], milestone={'id': 'surge'})
        self.assertIsNone(catch_reason(state, oddish))
        state['field_move_needed'] = 'CUT'
        self.assertIn('HM01', catch_reason(state, oddish))
        self.assertIsNone(catch_reason(state, mankey))

    def test_planner_hears_which_nearby_wild_species_can_learn_cut(self):
        maps = {1: {'name': 'ROUTE_6', 'wild': [{'species_id': 10, 'level': 16}, {'species_id': 11, 'level': 16},
                                                {'species_id': 10, 'level': 13}]},
                2: {'name': 'ROUTE_12', 'wild': [{'species_id': 10, 'level': 26}]}}
        species = {10: {'name': 'ODDISH', 'learnable_hms': ['HM01']}, 11: {'name': 'MANKEY', 'learnable_hms': ['HM04']}}
        party = [dict(self.mon('WARTORTLE', 30, 'WATER'), learnable_hms=['HM03', 'HM04'])]
        state = dict(party=party, box=[], field_move_needed='CUT')
        nearby = {'ROUTE_6', 'VERMILION_CITY'}
        self.assertEqual(wild_field_move_learners(state, maps, species, nearby), {'ODDISH': ['ROUTE_6']})
        state['box'] = [dict(self.mon('PARAS', 8, 'BUG'), learnable_hms=['HM01'])]
        self.assertEqual(wild_field_move_learners(state, maps, species, nearby), {})  # withdraw it instead
        self.assertEqual(wild_field_move_learners(dict(party=party), maps, species, nearby), {})

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

"""Regressions for the gym/Route 2 loop and training that never starts."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pokemon_red_jev.goals import Goal, intent_key
from pokemon_red_jev.navigation import Action, Navigation, RouteMemory
from test_core import new_agent


class TrainingChecks(unittest.TestCase):
    def test_new_planner_goal_replaces_a_stale_training_focus_before_routing(self):
        agent, _ = new_agent()
        agent.game.state['party'][0]['hp'] = 20
        agent.goal = Goal('Complete the errand', 'progress', 'VIRIDIAN_MART',
                          {'kind': 'event', 'value': 'EVENT_GOT_OAKS_PARCEL'}, 20)
        agent.milestone_id = 'parcel'
        state = agent.game.snapshot()
        agent.intent = {'value': 'train', 'key': intent_key(state), 'age': 15}
        def actions(state, goal):
            self.assertEqual(state['current_focus'], goal.focus)
            return [Action('go', 'Walk to objective', 'walk')]
        agent.navigation.actions = actions
        agent.navigation.execute = lambda _: None
        agent.jev.choose = lambda state, options: next(iter(options))
        agent.step()

    def test_small_wild_battle_rewards_count_as_training_progress(self):
        agent, _ = new_agent()
        agent.goal = Goal('Train to level 12', 'train', 'ROUTE_1', {'kind': 'level', 'value': 12}, 20)
        agent.intent = {'value': 'train'}
        agent.game.state['party'][0]['experience'] = 749
        agent._observe(agent.game.snapshot())
        agent.no_progress = 7
        agent.game.state['party'][0]['experience'] += 15
        self.assertTrue(agent._observe(agent.game.snapshot()))
        self.assertEqual(agent.no_progress, 0)

    def test_finished_training_does_not_resume_the_old_grass_walk(self):
        agent, _ = new_agent()
        agent.goal = Goal('Train', 'train', 'ROUTE_1', {'kind': 'level', 'value': 6}, 20)
        agent.resume_walk = {'map': 'ROUTE_1', 'key': 'grass', 'tries': 1}
        agent.finish_goal('complete')
        self.assertIsNone(agent.resume_walk)

    def test_completing_a_goal_with_low_hp_still_asks_for_a_healing_plan(self):
        agent, records = new_agent()
        agent.game.state['party'][0]['hp'] = 1
        agent.replan_reason = 'complete'
        agent.step()
        self.assertEqual(agent.goal.focus, 'heal')
        self.assertEqual(agent.plans, 1)
        self.assertEqual(agent.intent['value'], 'heal')

    def test_grass_action_paces_until_encounter_instead_of_one_tile(self):
        class Grass:
            w, h, pairs, ledges = 2, 2, set(), []
            def tile(self, x, y):
                return 1 if 0 <= x < 2 and 0 <= y < 2 else -1
            def walkable(self, x, y):
                return self.tile(x, y) == 1
            encounter = walkable
        values = {'wCurMap': 1, 'wXCoord': 0, 'wYCoord': 0, 'wGrassTile': 1}
        moves = []
        def button(direction):
            dx, dy = {'right': (1, 0), 'left': (-1, 0), 'up': (0, -1), 'down': (0, 1)}[direction]
            values['wXCoord'] += dx
            values['wYCoord'] += dy
            self.assertEqual(Grass().tile(values['wXCoord'], values['wYCoord']), 1)
            moves.append(direction)
        game = SimpleNamespace(u8=values.__getitem__, tick=lambda *args: None, sprites=lambda: [],
                               mode=lambda: 'battle' if len(moves) >= 6 else 'overworld',
                               pyboy=SimpleNamespace(button_press=button, button_release=lambda _: None))
        nav = Navigation.__new__(Navigation)
        nav.game, nav.seen, nav.memory = game, set(), RouteMemory()
        nav.regions = SimpleNamespace(special=lambda _: set())
        with patch('pokemon_red_jev.navigation.Grid', return_value=Grass()):
            nav.execute(Action('grass', 'Seek a wild encounter', 'walk', [('right', 1, 0)]))
        self.assertEqual(len(moves), 6)
        self.assertEqual(game.mode(), 'battle')

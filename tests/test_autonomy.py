"""Routine controls must work without a model; temporary outages must preserve play."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pokemon_red_jev.agent import Agent
from pokemon_red_jev.cli import load_env, main
from pokemon_red_jev.controls import Controls
from pokemon_red_jev.data import Data
from pokemon_red_jev.game import Game
from pokemon_red_jev.goals import Goal
from pokemon_red_jev.models import Jev, ModelError
from pokemon_red_jev.navigation import Action, Navigation
from test_core import new_agent


class AutonomyChecks(unittest.TestCase):
    def test_clear_route_needs_no_model_but_failed_or_ambiguous_routes_do(self):
        agent, _ = new_agent()
        agent.goal = Goal('Go to the Mart', 'progress', 'VIRIDIAN_MART', {'kind': 'map', 'value': 'VIRIDIAN_MART'}, 20)
        agent.milestone_id = 'parcel'
        route = Action('door:0', 'Leads toward the objective (0 areas away).', 'door')
        party = Action('party', 'Inspect the party', 'party')
        actions = [route, party]
        agent.navigation.actions = lambda *_: actions
        agent.navigation.execute = Mock()
        agent.controls = Mock()
        agent.jev.choose = Mock(return_value='party')
        agent.step()
        agent.jev.choose.assert_not_called()
        agent.navigation.execute.assert_called_once_with(route)
        agent.tried['ROUTE_1:door:0'] = 3
        agent.step()
        agent.jev.choose.assert_called_once()
        agent.tried.clear()
        actions.append(Action('door:1', route.description, 'door'))
        agent.step()
        self.assertEqual(agent.jev.choose.call_count, 2)
        actions.pop()
        agent.jev.manual = True
        agent.step()
        self.assertEqual(agent.jev.choose.call_count, 3)

    def test_retry_pause_allows_window_close_without_advancing_the_game(self):
        game = Game.__new__(Game)
        game.pyboy = Mock()
        game.stage = Mock()
        game.stage.present.side_effect = KeyboardInterrupt
        with patch('pokemon_red_jev.game.time.sleep'), self.assertRaises(KeyboardInterrupt):
            game.pause(60)
        game.stage.present.assert_called_once()
        game.pyboy.tick.assert_not_called()

    def test_transient_service_failures_retry_then_continue_auth_errors_stop(self):
        for transient in (True, False):
            with self.subTest(transient=transient), tempfile.TemporaryDirectory() as directory:
                rom = Path(directory) / 'red.gb'
                rom.touch()
                error = ModelError('Model endpoint returned HTTP ' + ('503' if transient else '401'))
                error.retryable = transient
                agent = Mock()
                agent.step.side_effect = [error] * 4 + [None]
                game = Mock()
                game.snapshot.side_effect = lambda: {'milestone': None if agent.step.call_count == 5 else {'id': 'starter'}}
                argv = ['pokemon-red-jev', 'run', '--rom', str(rom), '--headless', '--planner', 'off',
                        '--save', str(Path(directory) / 'save.zip'), '--log', str(Path(directory) / 'run.jsonl')]
                with patch('sys.argv', argv), patch('pokemon_red_jev.cli.load_env'), \
                     patch('pokemon_red_jev.cli.Data'), patch('pokemon_red_jev.cli.Jev'), \
                     patch('pokemon_red_jev.game.Game', return_value=game), \
                     patch('pokemon_red_jev.navigation.Navigation'), \
                     patch('pokemon_red_jev.controls.Controls'), \
                     patch('pokemon_red_jev.cli.Agent', return_value=agent), \
                     patch('pokemon_red_jev.cli.time.sleep'):
                    if transient:
                        main()
                        self.assertEqual(agent.step.call_count, 5)
                        self.assertEqual(game.pause.call_count, 4)
                    else:
                        with self.assertRaises(SystemExit):
                            main()
                        self.assertEqual(agent.step.call_count, 1)
                    agent.save.assert_called()


@unittest.skipUnless(os.getenv('RUN_ROM_TESTS') == '1', 'Set RUN_ROM_TESTS=1 with a local supported ROM')
class AutomaticOpening(unittest.TestCase):
    def test_fresh_game_reaches_oak_without_planner_or_model_requests(self):
        load_env()
        g = Game(Path(os.environ['ROM_PATH']), Data(Path(os.getenv('GAME_DATA_PATH', 'data/generated.json'))),
                 headless=True, speed=0)
        self.addCleanup(g.close)
        planner = SimpleNamespace(plan=Mock(side_effect=AssertionError('The opening should not need a planner')))
        def unexpected_request(_url, _key, payload, *_args):
            self.fail(f"Routine opening requested a model at {payload['state']['map']}: {payload['questions']}")
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'offline-test'}), \
             patch('pokemon_red_jev.models.post_json', side_effect=unexpected_request) as request:
            agent = Agent(g, planner, Jev(lambda *_args, **_kwargs: None), Navigation(g), Controls(g), lambda *_args, **_kwargs: None)
            for _ in range(300):
                agent.step()
                state = g.snapshot()
                if 'EVENT_FOLLOWED_OAK_INTO_LAB' in state['events'] and state['mode'] == 'overworld':
                    break
            else:
                self.fail(f"Opening stuck at {state['map']}: {state['screen']}")
            self.assertIn('PALLET_TOWN', g.visited)
            self.assertEqual(state['map'], 'OAKS_LAB')
            planner.plan.assert_not_called()
            request.assert_not_called()
            print('ROM automatic opening: fresh boot -> both house floors -> Pallet -> Oak, zero model requests', flush=True)

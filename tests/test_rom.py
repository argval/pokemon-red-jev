"""Opt in: RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -v.

Uses a local ROM and ordinary button inputs; never patches RAM or calls models.
The fixed opening route is a regression driver, not an autonomous controller.
"""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pokemon_red_jev.agent import Agent
from pokemon_red_jev.cli import load_env
from pokemon_red_jev.controls import Controls
from pokemon_red_jev.data import Data
from pokemon_red_jev.game import Game
from pokemon_red_jev.goals import fallback_goal
from pokemon_red_jev.navigation import Navigation


@unittest.skipUnless(os.getenv("RUN_ROM_TESTS") == "1", "Set RUN_ROM_TESTS=1 with a local supported ROM")
class RealOpening(unittest.TestCase):
    def test_boot_starter_rival_parcel_and_checkpoint(self):
        load_env()
        game = Game(Path(os.environ["ROM_PATH"]), Data(Path(os.getenv("GAME_DATA_PATH", "data/generated.json"))),
                    headless=True, speed=0)
        nav, controls = Navigation(game), Controls(game)
        milestones = set()
        with patch("pokemon_red_jev.models.post_json", side_effect=AssertionError("ROM checks must not call models")):
            try:
                for step in range(2400):
                    state = game.snapshot()
                    events = set(state["events"])
                    for event in ["EVENT_FOLLOWED_OAK_INTO_LAB", "EVENT_GOT_STARTER", "EVENT_BATTLED_RIVAL_IN_OAKS_LAB",
                                  "EVENT_GOT_OAKS_PARCEL", "EVENT_GOT_POKEDEX"]:
                        if event in events and event not in milestones:
                            milestones.add(event)
                            print(f"ROM check [{step}]: {event}", flush=True)
                    if "EVENT_GOT_POKEDEX" in events and state["mode"] == "overworld":
                        break
                    if state["mode"] == "busy":
                        game.tick(12)
                        continue
                    if state["mode"] == "overworld":
                        actions = nav.actions(state, fallback_goal(state))
                        returning = "EVENT_GOT_OAKS_PARCEL" in events
                        where = state["map"]
                        if where in {"REDS_HOUSE_2F", "REDS_HOUSE_1F"}:
                            key = "door:0"
                        elif where == "PALLET_TOWN":
                            key = "door:2" if returning else "exit:up"
                        elif where == "OAKS_LAB":
                            key = "npc:4" if not state["party"] else "npc:5" if returning else "door:0"
                        elif where == "ROUTE_1":
                            key = "exit:down" if returning else "exit:up"
                        elif where == "VIRIDIAN_CITY":
                            mart = next(i for i, w in enumerate(game.rom.maps[state["map_id"]]["warps"])
                                        if game.rom.maps[w["map"]]["name"] == "VIRIDIAN_MART")
                            key = "exit:down" if returning else f"door:{mart}"
                        elif where == "VIRIDIAN_MART":
                            key = "door:0" if returning else "npc:1"
                        else:
                            self.fail(f"Unexpected map {where}")
                        action = next((a for a in actions if a.key == key), None)
                        self.assertIsNotNone(action, f"Missing {key} at {where} {state['x']},{state['y']}")
                        nav.execute(action)
                        continue
                    actions = controls.actions(state)
                    self.assertTrue(actions, "No dialog/battle actions")
                    choices = {a.key: a for a in actions}
                    text = " ".join(game.screen()["rows"])
                    labels = ["NEW GAME", "RED", "BLUE", "NO" if "nickname" in text.lower() else "YES"]
                    action = next((a for label in labels for a in actions if a.target.get("label") == label), None)
                    if "run" in choices:
                        action = choices["run"]
                    elif "move:0" in choices:
                        action = choices["move:0"]
                    elif "name:done" in choices:
                        action = choices["name:done"]
                    elif "letter:J" in choices:
                        action = choices["letter:J"]
                    controls.execute(action or actions[0])
                else:
                    self.fail(f"Opening timed out: {state['map']} {state['mode']}\n" + "\n".join(state["screen"]))
                self.assertEqual(len(milestones), 5)
                self.assertTrue(state["party"])
                self.assertNotIn("OAK'S PARCEL", [i["name"] for i in state["bag"]])
                self.assertIn("OAKS_LAB", game.visited)
                path = Path("saves/rom-check.zip")
                agent = Agent(game, None, None, nav, controls, lambda *a, **kw: None)
                agent.save(path)
                before = agent.change_key(game.snapshot())
                game.press("left")
                agent.load(path)
                self.assertEqual(before, agent.change_key(game.snapshot()))
                print(f"ROM check passed; checkpoint: {path}", flush=True)
            finally:
                game.close()

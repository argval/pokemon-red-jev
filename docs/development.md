# Development, architecture, and validation

This application separates a goal from the actions that achieve it. The planner returns a small validated task; Jev chooses among actions generated from observed state. Python executes button inputs, then checks RAM for progress. A model's claim of success never completes a task by itself.

## Runtime flow

1. `cli.py` loads `.env`, chooses the controller/planner, creates PyBoy and the dashboard, and optionally restores a checkpoint.
2. `game.py` decodes RAM using generated symbols. Observations include map/coordinates, screen text, party/box, bag, event flags, badges, and battle facts.
3. `agent.py` advances the goal lifecycle and attaches recent decisions, dialogue, failures, and situation/team context.
4. `navigation.py` and `regions.py` generate reachable overworld candidates. `controls.py` handles boot, dialogue, menus, and battles.
5. Routine single choices and clear story routes can bypass models. Otherwise, manual input or Jev selects an available action.
6. The executor sends ordinary button inputs, observes the result, tracks progress, and saves memory with emulator state.

The window is local SDL2 rendering in `stage.py`, with a 1280×720 dashboard and ROM-font text. PyBoy itself uses a null window to avoid a second display. There is no web server or streaming upload. Headless mode skips dashboard construction. Live model modes send game context to the selected provider; manual/off and the scripted demo need no model calls.

Codex and Cursor start a new CLI process for goal planning. This isolates the goal prompt from repository files/rules, but adds latency and relies on external authentication. Model inference pauses the emulator. A validated goal is narrower than the full story milestone, which allows recovery without claiming the whole quest succeeded.

Completion normally advances to the next story task. Low HP, expiry, stalled progress, or an unavailable focus can trigger planning. Battle/menu interruptions preserve the active task and may resume the pending walk. Route and action failure memory prevents replanning from erasing evidence of a loop. Training progress uses experience and team changes rather than HP/PP expenditure.

Automatic play reserves HP-restoring items for battles and necessary field recovery. A heal focus routes to the nearest reachable Pokémon Center. Within two region transitions of a Center, field HP items are offered only when all conscious party members have at most 25% HP, or an injured poisoned Pokémon has at most 4 HP. Inside a Center, ask the nurse instead. Farther away, or with no reachable Center, field recovery is allowed at 50% HP or below. The policy is checked again in the item target menu and after each use, so routine top-ups cannot consume the remaining supplies. Status cures and revives retain their existing behavior; manual control can still use HP items freely. Planner and controller context includes the Center, its distance, and eligible party slots. Distances describe region transitions, not a guarantee that the walk is safe.

Sleeping Snorlax on Routes 12 and 16 remains a collision obstacle in the route graph until its defeated/caught event is set, including when that map is not loaded. Planner and controller context identifies the blocker, Flute ownership, and the Silph Scope/Tower/Fuji prerequisites. Talking to Snorlax is omitted because it cannot clear the road. Without the Flute, automatic play near Snorlax takes a reachable exit; remembered dialog traps cannot suppress a physically open retreat there. With the Flute, the item action first walks to one of the ROM's permitted waking positions and then plays it to start the Lv30 encounter. A battle or map change during that approach interrupts item use.

## Source map

| File or directory | Responsibility |
| --- | --- |
| `src/pokemon_red_jev/cli.py` | Commands, environment loading, manual input, log and save loop, service recovery. |
| `src/pokemon_red_jev/data.py` | Supported ROM hash and symbol/constants generation. |
| `src/pokemon_red_jev/game.py` | ROM tables, PyBoy adapter, RAM observation, screen decoding. |
| `src/pokemon_red_jev/stage.py` | Local dashboard, ROM-font rendering, window events. |
| `src/pokemon_red_jev/agent.py` | Goals, focus, progress, loop recovery, checkpoint memory. |
| `src/pokemon_red_jev/models.py` | TypeSafe choice API, HTTP/Codex/Cursor planners, validation retry. |
| `src/pokemon_red_jev/goals.py`, `story.json` | Goal contract, observed completion, built-in story milestones, team reasoning. |
| `src/pokemon_red_jev/navigation.py`, `regions.py` | Directed room/terrain routing, obstacles, warps, field actions, route memory. |
| `src/pokemon_red_jev/controls.py` | Button drivers, menus, battle estimates and action facts. |
| `src/jev/` | Battle/team/training policy helpers. |
| `src/state/`, `src/llm/`, package `state/` and `llm/` | Party/Pokédex helpers and planner context construction. |
| `src/pokemon_red_jev/demo.py` | Offline scripted game and model stand-ins exercising the real Agent. |
| `tests/` | Logic tests and opt-in real-ROM checks. |
| `PORT_AUDIT.md` | Historical reproduction details, port corrections, and validation limits. |

Start with the CLI and trace an action through Agent, candidate generation, execution, and observation. For a goal change, read `Goal.parse`, `complete`, and the planner prompt together; the prompt and validator intentionally have different budget limits. See the [goal reference](configuration.md#planner-goal-contract).

## How to validate a change

Install dependencies using [setup](getting-started.md), then run offline checks:

```sh
uv run pokemon-red-jev demo
uv run python -m unittest discover -s tests -v
```

The default suite skips ROM-gated tests. It checks goal validation/completion, model request contracts with mocked transport, checkpoint round trips/atomic failures, menu readiness, routing, training, team building, service recovery, and battle switch-loop prevention. It does not call live models.

After configuring the supported ROM/data, run all integration checks:

```sh
RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -v
```

These checks are headless and make no model requests. The opening test writes `saves/rom-check.zip`, not `saves/latest.zip`. Keep that output name reserved for the regression test. Changes to movement, map transitions, menus, or button timing should pass the ROM checks, not just synthetic-state tests.

| Test file | Coverage |
| --- | --- |
| `test_core.py` | Goals, contexts, models, checkpoints, dashboard drawing, routing/menu/battle facts, and regressions. |
| `test_training.py`, `test_team_building.py` | Training progress/focus, grass pacing, catching and roster decisions. |
| `test_recovery.py` | PC/shop cycles, cooldowns, boulder progress, route facts, checkpoint memory. |
| `test_autonomy.py` | Routine vs model decisions, transient/permanent service failures, responsive retry pause; opt-in fresh opening through Oak without model calls. |
| `test_rom.py` | Fixed-route new game through starter/rival/parcel/Pokédex and checkpoint restoration. Rival completion does not require a win. |
| `test_rom_scenarios.py` | Seeded Mansion, Seafoam, Victory Road, PC/shop, training encounter, Mt. Moon fossil/exit, cave backup capture, and PC deposit/withdrawal scenarios. |

The scenario tests seed party capabilities and doorway destinations before exercising production button drivers. They establish control/puzzle behavior from those states, not natural autonomous arrival there. The opening regression follows a fixed route without RAM edits. Mocked planner tests establish request shape/validation, not live service availability or decision quality.

## How to reproduce and report a gameplay issue

1. Copy the input checkpoint and select separate `--save`/`--log` outputs.
2. Record the commit, controller, planner, flags, Python/PyBoy versions, and map/coordinates. Include configuration names and non-secret values; omit API keys.
3. Reproduce in manual mode to separate execution failures from model choices.
4. Capture the action key, goal, relevant `guard`/`loop`/error records, expected result, and observed result.
5. Add a regression at the layer responsible for the failure. Use an isolated ROM scenario when timing or live RAM behavior is involved.

Do not edit a player's original archive or use logs from different sessions as one continuous trace. [Usage](usage.md) explains log inspection and independent output paths.

## Implemented behavior and remaining validation

Implemented behavior includes directed routing across floors/ledges/warps, live obstacles, Cut/Surf/Strength requirements, hidden interactions and puzzle switches, HM/TM compatibility, PC/shop handling, healing/cures/revival, Safari choices, battle damage and switch-in facts, and menu/action loop recovery. Team/training helpers support catching useful backups, training progress, and roster changes.

Historical local evidence includes live planner + Jev connection tests and a Cursor + Jev checkpoint replay defeating Brock, as recorded in the existing project audit. That evidence is not a reproducible fresh-checkout fixture. Full sustained autonomous story completion remains unverified. Seafoam B2F–B4F and Victory Road 2F/3F still need coverage from naturally played checkpoints. Compare repeated runs before claiming one planner improves speed or completion reliability.

## Attribution and maintenance

The RAM layouts, pathfinding/menu approach, battle calculations, and story milestones derive from Christian Mathiesen's [jev-pokemon](https://github.com/christianmat/jev-pokemon), commit `9ff2da2`. The original Node runtime, website, and streaming infrastructure are not part of this Python application. This derivative retains [GPL-2.0-or-later](../LICENSE); PyBoy and pret/pokered are separate upstream projects.

When adding flags, environment variables, or planner fields, update [configuration](configuration.md). When changing workflows, update [usage](usage.md) and [setup](getting-started.md). Keep validation claims tied to tests or clearly dated live-run evidence.

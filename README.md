# Pokémon Red: LLM planner + Jev

A local Python application. An LLM proposes one short-term goal, Jev selects available actions, and code executes them through PyBoy. Completion comes from observed game state.

## Current phase

Phase 2 adds real-ROM validation, room routing, and more gameplay actions. The local ROM and matching symbols are configured. **The opening regression test reaches the Pokédex after delivering Oak's Parcel**, using the production navigation/menu code and ordinary button inputs. It also saves and restores a checkpoint.

The regression test follows a fixed route. Codex goal planning and Jev action selection have both been tested together from a real checkpoint. After updating the local TypeSafe key, a live run selected an action and saved successfully. The application uses Python and a local window, with no web server.

| Area | Status |
| --- | --- |
| Codex goal planner + Jev action loop | Live run from the real ROM selected a Codex goal and a Jev action, then saved |
| Goal validation, completion, expiry, replanning | Implemented and checked |
| Local window / headless PyBoy | Windowed runs show the game on the left and the Jev panel on the right, and do not upload. `--headless` stays headless |
| Boot, naming, starter, rival battle, parcel delivery | Real-ROM regression passes; battle completion does not require winning |
| Atomic checkpoints and manual selection | Save/resume/quit checked without model calls |
| Navigation | Directed room graph, disconnected floors, ledges, warps, aligned map exits, live obstacles |
| Field actions | Cut/Surf/Strength approaches check the move and the required badge; Victory Road pushes aim at open switches and holes |
| Hidden interactions / puzzles | PCs, switches, trash cans, quizzes, Card Key doors, Mansion gates across both switch positions, arrow landings, live elevator destinations |
| Menus / team / battle | Input readiness, scrolling, HM/TM compatibility, PC/shop facts, healing, cures, revival, Safari choices, special damage facts |
| Later-game behavior | Blocked exits, HM/Flute/full-bag choices, Mansion switch routing, and Victory Road boulder search are implemented. The PC lists the box and the six-Pokémon limit without choosing the team. Shop heuristics remain. None of this is played through on the real ROM past the opening |

## Try it now

From this directory:

```sh
uv sync
uv run pokemon-red-jev demo
uv run pokemon-red-jev doctor
uv run python -m unittest discover -s tests -v
```

`demo` uses the actual agent/goal lifecycle with scripted game and model stand-ins. It demonstrates recovery from a failed movement, healing, and receiving Oak's Parcel. It does not run Pokémon, call an LLM, or call Jev. The trace is in `logs/demo.jsonl`.

## Configure models later

The local `.env` points to the ROM in `ROM/` and `data/generated.json`. Set `TYPESAFE_API_KEY` to a working TypeSafe key for Jev. Codex uses your existing ChatGPT sign-in, so leave `LLM_API_KEY` and `LLM_MODEL` blank. On a fresh checkout, copy `.env.example` to `.env` and set the ROM path.

```dotenv
ROM_PATH=/absolute/path/to/red.gb
GAME_DATA_PATH=data/generated.json

TYPESAFE_API_KEY=
JEV_MODEL=jev-latest

LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=
LLM_MODEL=
```

Jev uses TypeSafe's direct HTTP API. `TYPESAFE_API_KEY` is a TypeSafe key; the original project's Vercel gateway key is not interchangeable. For `--planner codex`, run `codex login` once with your ChatGPT account, then check `codex login status`. The app starts a fresh, read-only `codex exec` in a temporary directory for each new short-term goal. It validates Codex's structured response against known game identifiers before Jev sees it. [OpenAI Docs](https://learn.chatgpt.com/docs/non-interactive-mode) confirms that `codex exec` reuses saved CLI authentication and supports a JSON output schema.

For `--planner cursor`, install the Cursor Agent CLI (`agent` or `cursor-agent`), run `agent login` once, then check `agent status`. The app starts a fresh ask-mode `agent -p` in an empty temporary workspace for each new short-term goal (so repo rules and skills are not loaded into the prompt). It does not use OpenRouter keys. Set `CURSOR_MODEL` (default `composer-2.5`) and `CURSOR_TIMEOUT_SECONDS` in `.env` if needed.

The previous OpenRouter-compatible planner remains available with `--planner llm`. It requires `LLM_API_KEY` and `LLM_MODEL`; Codex and Cursor modes do not use them.

Codex, Cursor, and HTTP request timeouts, plus Jev's rate limits, are configurable in `.env.example`. HTTP requests have one bounded retry. Jev and the HTTP planner require their respective credentials; manual control with Codex or Cursor planning needs only the matching CLI sign-in. An invalid or failed planner response installs a temporary story goal and retries planning after its budget expires. Repeated Jev failures stop the run and save progress.

### Generate game symbols

The RAM reader needs addresses and constants from a matching local [pret/pokered](https://github.com/pret/pokered) build. Follow that project's build instructions to install RGBDS and run `make red`. Then:

```sh
uv run pokemon-red-jev prepare-data --pokered /path/to/pokered
```

This reads `pokered.sym`, constants, and the character map. It verifies the local build matches the supported US/EU Red SHA-1, `ea9bcae617fdf159b045185467ae58b2e4a48b9a`, and writes `data/generated.json`. The game runner independently verifies your ROM's hash. Other releases and ROM hacks are unsupported.

ROMs, generated data, credentials, saves and logs are gitignored. No ROM is bundled or downloaded by the application.

### Run locally without keys

```sh
uv run pokemon-red-jev run --controller manual --speed 0
# Continue from the verified opening checkpoint:
uv run pokemon-red-jev run --controller manual --resume saves/rom-check.zip --speed 0
```

Choose an action number or key in the terminal. The local window shows the game on the left and a Jev panel on the right (story milestone, active goal, location, team, and the latest decision), in the same arrangement as the jev-pokemon stream. It does not upload video or start a web server. Dialogue and animation waits with a single available action advance automatically. `q` at an action prompt, Escape, or closing the window saves and exits. These are the same action candidates and executors used by Jev. Manual mode defaults to `--planner off`; it uses story milestones as context.

The current checkout includes ignored, generated data built from `pret/pokered` commit `d2704a63c26f9ba046ade877445216b3de0519a4` with RGBDS 1.0.4. To regenerate it:

```sh
uv run pokemon-red-jev prepare-data --pokered vendor/pokered
```

### Run Jev with Codex or Cursor goals

```sh
codex login status
# Add TYPESAFE_API_KEY to .env, then start a fresh game (runs until done or interrupted):
uv run pokemon-red-jev run --planner codex
# Or resume from the verified opening checkpoint:
uv run pokemon-red-jev run --planner codex --resume saves/rom-check.zip

# Same loop with Cursor Agent as the goal planner (uses `agent login`):
agent status
uv run pokemon-red-jev run --planner cursor --resume saves/rom-check.zip
```

This uses your ChatGPT or Cursor sign-in for goal planning and the separate TypeSafe key for Jev's action choices. A planner process starts only when the active short-term goal finishes, stalls, or expires; Jev handles the intervening actions. CLI startup and model inference pause game emulation, so goal changes can take several seconds. Check your plan's usage limits for sustained runs.

To check planning before adding a TypeSafe key, run `uv run pokemon-red-jev run --controller manual --planner cursor --resume saves/rom-check.zip` (or `--planner codex`). You choose actions in the terminal while the planner proposes goals.

The existing baselines remain available: `--planner off` needs only Jev's TypeSafe key, while `--planner llm` uses the OpenRouter-compatible fields in `.env`. The default controller is Jev and its default planner is `llm`; pass `--planner codex` or `--planner cursor` explicitly. `--speed 1` runs emulator frames at real-time speed; `0` removes that limit.

`--steps` counts control-loop iterations, including animation waits and menus. The default is `0`, which runs indefinitely until the story is complete, Escape, or Ctrl-C. Pass a positive value for a bounded session (for example `--steps 1000`). Each task has a separate budget counting overworld decisions. Saves occur every 60 seconds between actions and at shutdown. A checkpoint stores emulator state and agent memory in one atomic archive. On resume, the planner receives fresh state and replaces the old active task. Use distinct `--save` and `--log` paths for separate experiments.

### Repeat the ROM check

```sh
# Offline logic tests; the ROM test is skipped:
uv run python -m unittest discover -s tests -v
# All 20 checks, including the real opening:
RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -v
```

The ROM check starts a new game, selects a starter, handles the rival battle and wild encounters, collects the parcel, returns to Oak, and verifies the Pokédex event. It writes `saves/rom-check.zip`; it does not overwrite `saves/latest.zip`. It never edits RAM or calls either model. Run it again after changing movement, map loading, or menu timing.

## How the models cooperate

1. The runner observes RAM, dialogue, recent failures, and the current story milestone.
2. At an overworld decision boundary, the planner proposes one task with a map, a completion condition, and a decision limit.
3. The app validates the task against known map/event/item/interaction identifiers. Already completed goals are rejected.
4. Jev receives that goal, current state, and the available actions. Navigation facts refer to the task's destination.
5. The runner executes the selected action, records the outcome, and checks completion.
6. Completion, a changed story milestone, a drop below 25% party HP, expiry, or stalled progress triggers another plan. Ordinary battle/menu interruptions retain the active task.

Example planner response:

```json
{
  "goal": "Enter the Viridian Poké Mart",
  "focus": "progress",
  "target_map": "VIRIDIAN_MART",
  "success": {"kind": "map", "value": "VIRIDIAN_MART"},
  "max_decisions": 20
}
```

Completion supports a current map, event flag, inventory item, healed party, party level, badge, or an observed interaction. Interaction completion means a conversation was initiated; quest completion should use an event or item check. The planner never emits executable code or direct button commands.

No agent framework or additional service is needed. PyBoy is the only direct runtime dependency; networking, validation, logging and checkpointing use Python's standard library.

## Next steps

1. **Validate sustained autonomous play.** Run `--planner codex` from a fresh game and inspect reached milestones and failed actions. The one-step live smoke test proves the models and runner connect; it does not measure longer-term decisions. The scripted regression proves the controls work.
2. **Validate later gameplay.** Start with Brock, then test HM use, PC moves, the Pokémon Mansion switches, and Victory Road boulders from nearby checkpoints. Those choices are in the action list now; they have not been played through on the real ROM. The PC reports who is in the box and that the team holds six. It does not pick the team. Shop heuristics remain. Unseen scripted gates can still block a route until an exit fails often enough to be remembered.
3. **Measure the planner.** Compare `--planner codex` and `--planner off` from copies of the same checkpoint, using separate saves/logs, equal throttles and a fixed wall-clock budget. Repeat runs and compare reached milestones, failures, model latency and token usage. The disabled-planner baseline uses milestone goals; it does not reproduce the original Jev intent-selection layer.

Smaller goals are a hypothesis to measure, not a speed guarantee.

## Code and attribution

- `game.py`, `data.py`: ROM tables, symbol generation, game state and PyBoy adapter.
- `navigation.py`, `regions.py`, `controls.py`: action candidates, terrain routing, paths, menus and battle choices.
- `goals.py`, `story.json`: task validation and story completion conditions.
- `models.py`, `agent.py`: model requests and the planner/action loop.
- `cli.py`, `demo.py`: local commands and the offline simulation.

The game-state layouts, pathfinding/menu approach, battle calculations and story milestones are adapted from Christian Mathiesen's [jev-pokemon](https://github.com/christianmat/jev-pokemon), commit `9ff2da2`. The original remains in the sibling folder for reference. Its website, streaming code and Node runtime were not copied.

This derivative retains the upstream **GPL-2.0-or-later** license; see [LICENSE](LICENSE). PyBoy and pret/pokered are separate upstream projects.

API references: [TypeSafe](https://docs.typesafe.ai/api), [OpenRouter](https://openrouter.ai/docs/api_reference/overview), [PyBoy](https://docs.pyboy.dk/).

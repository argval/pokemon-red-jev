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
| Later-game behavior | Blocked exits, HM/Flute/full-bag choices, Mansion switch routing, and Victory Road boulder search are implemented. The PC lists the box and the six-Pokémon limit without choosing the team. Shop heuristics remain. Seeded ROM scenarios cover a Mansion continuation to B1F, Seafoam boulder drops on 1F/B1F, an 18-push Victory Road 1F sequence, and PC/shop loop recovery |

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

Codex, Cursor, and HTTP request timeouts, plus Jev's rate limits, are configurable in `.env.example`. HTTP requests have one bounded retry. Jev and the HTTP planner require their respective credentials; manual control with Codex or Cursor planning needs only the matching CLI sign-in. An invalid or failed planner response installs a temporary story goal and retries planning after its budget expires. Jev timeouts, network failures, HTTP 429, and HTTP 5xx responses save progress and pause emulation while retrying, with delays increasing from 2 to 60 seconds. Escape/window close remains responsive during the pause. Authentication errors stop immediately; three consecutive invalid responses also stop and save.

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

This uses your ChatGPT or Cursor sign-in for goal planning and the separate TypeSafe key for Jev's action choices. Before getting a starter, the runner follows the opening story directly. Naming, dialogue, and clear routes run without model requests; duplicate door tiles leading to the same room use the shorter walk. Ambiguous or repeatedly failed routes go to Jev. After obtaining a starter, the planner runs at startup/resume and when the active task needs recovery or expires; completed tasks normally advance to the next story goal. CLI startup and model inference pause game emulation, so model decisions can take several seconds. Check your plan's usage limits for sustained runs.

To check planning before adding a TypeSafe key, run `uv run pokemon-red-jev run --controller manual --planner cursor --resume saves/rom-check.zip` (or `--planner codex`). You choose actions in the terminal while the planner proposes goals.

The existing baselines remain available: `--planner off` needs only Jev's TypeSafe key, while `--planner llm` uses the OpenRouter-compatible fields in `.env`. The default controller is Jev and its default planner is `llm`; pass `--planner codex` or `--planner cursor` explicitly. `--speed 1` runs emulator frames at real-time speed; `0` removes that limit.

`--steps` counts control-loop iterations, including animation waits and menus. The default is `0`, which runs indefinitely until the story is complete, Escape, or Ctrl-C. Pass a positive value for a bounded session (for example `--steps 1000`). Each task has a separate budget counting overworld decisions. Saves occur every 60 seconds between actions and at shutdown. A checkpoint stores emulator state and agent memory in one atomic archive. Pass `--resume saves/latest.zip` to continue; without `--resume`, the runner starts a new game. On resume, it replaces the old active task using fresh state. Use distinct `--save` and `--log` paths for separate experiments.

### Repeat the ROM check

```sh
# Offline logic tests; the ROM test is skipped:
uv run python -m unittest discover -s tests -v
# All checks, including the real opening and isolated ROM scenarios:
RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -v
```

The opening ROM check starts a new game, selects a starter, handles the rival battle and wild encounters, collects the parcel, returns to Oak, and verifies the Pokédex event. It writes `saves/rom-check.zip`; it does not overwrite `saves/latest.zip`. It never edits RAM or calls either model. Run it again after changing movement, map loading, or menu timing.

`tests/test_rom_scenarios.py` adds six isolated ROM checks. These seed capabilities and a doorway destination, then use production button drivers without model calls. They verify Mansion checkpoint continuation to B1F, two Seafoam boulder drops across floors, Victory Road progress without stall replanning, PC/shop loop exits, and travel from Pewter to a Route 2 wild encounter. A separate live Cursor + Jev replay from the player's checkpoint defeated Brock. See [PORT_AUDIT.md](PORT_AUDIT.md) for exact coverage and limits.

`tests/test_autonomy.py` runs the real Agent from a fresh ROM boot through both house floors, Pallet Town, and Oak's introduction, asserting zero planner or model requests. It also checks service recovery after four consecutive failures, permanent authentication errors, manual choice preservation, failed/ambiguous route handling, and closing the window while paused.

## How the models cooperate

1. The runner observes RAM, dialogue, recent failures, and the current story milestone.
2. At an overworld decision boundary, the planner proposes one task with a map, a completion condition, and a decision limit.
3. The app validates the task against known map/event/item/interaction identifiers. Already completed goals are rejected.
4. Jev receives that goal, current state, and the available actions. With a planner, its goal sets the focus before routes are built; without one, Jev selects a bounded focus. Training routes lead to reachable encounter grass, and the grass action paces for up to 40 steps or until interrupted. Every XP gain counts as training progress.
5. The runner executes the selected action, records the outcome, and checks completion.
6. Completion or a changed story milestone selects the next story task. Low HP, expiry, stalled progress, or an unavailable focus asks the planner for a new task. Ordinary battle/menu interruptions retain the active task.

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
2. **Extend later-game coverage.** Continue from naturally played checkpoints through Seafoam B2F–B4F and Victory Road 2F/3F. The seeded ROM checks cover the Mansion route to B1F, early Seafoam drops, the first Victory Road switch, and menu recovery; they do not establish full autonomous completion.
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

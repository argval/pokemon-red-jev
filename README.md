# Pokémon Red: LLM planner + Jev

Run Pokémon Red locally with a short-term goal planner and Jev action selection, or choose actions yourself in the terminal. Python executes the actions through PyBoy and verifies progress from game state. The local dashboard shows the game, story task, team, and latest decision.

## Start here

| You want to… | Guide |
| --- | --- |
| Install from a fresh checkout and play without keys | [Getting started](docs/getting-started.md) |
| Use Codex, Cursor, HTTP planning, or Jev | [Usage and model setup](docs/usage.md) |
| Save/resume, run headless, or inspect decisions | [Usage recipes](docs/usage.md#how-to-save-and-resume-safely) |
| Look up every flag, environment variable, and goal field | [Configuration reference](docs/configuration.md) |
| Understand the code, run checks, or reproduce a bug | [Development and validation](docs/development.md) |
| Read the historical port investigation | [Port audit](PORT_AUDIT.md) |

## Quick offline check

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, run from this directory:

```sh
uv sync --locked
uv run pokemon-red-jev demo
uv run python -m unittest discover -s tests -v
```

The demo exercises the actual agent/goal lifecycle with scripted game/model stand-ins and writes `logs/demo.jsonl`. It requires no ROM, API keys, network calls, or game window. Default tests skip the opt-in ROM checks.

## Run the game

Gameplay requires a local US/EU Red ROM with SHA-1 `ea9bcae617fdf159b045185467ae58b2e4a48b9a` and symbols generated from a matching pret/pokered build. Follow [setup](docs/getting-started.md) before these commands. ROMs, generated data, credentials, saves, and logs are ignored and are not bundled.

```sh
# Once ROM_PATH and GAME_DATA_PATH are configured in .env:
uv run pokemon-red-jev doctor
uv run pokemon-red-jev run --controller manual --planner off
# After setting TYPESAFE_API_KEY and signing in to Codex CLI:
uv run pokemon-red-jev run --controller jev --planner codex
# Explicitly continue the default saved checkpoint:
uv run pokemon-red-jev run --controller jev --planner codex --resume saves/latest.zip
```

Manual mode selects the same candidates as Jev. Choose a number or action key in the terminal; `q` saves and quits. The default controller is Jev, whose default planner is `llm`, so pass `--planner` explicitly when using Codex, Cursor, or built-in goals. Without `--resume`, every run starts a new game.

Codex and Cursor use their own CLI sign-in for goals. Jev uses a separate direct TypeSafe API key for actions. Manual control with planner off needs neither. The app has no web server or video upload; live model modes send game context to their providers.

## What works today

The project implements goal validation/recovery, atomic emulator/agent checkpoints, room and terrain navigation, field moves and puzzles, menus, team/training support, and battle choices with damage and switch-in facts. The dashboard is local, and `--headless` skips it.

Real-ROM regression checks cover the opening through Oak's Parcel and Pokédex. Seeded scenarios cover later puzzles, training, catching, and PC/shop recovery. Those checks validate specific controls and states; they do not establish full autonomous story completion. See [validation coverage and limits](docs/development.md#how-to-validate-a-change) before interpreting results.

## Attribution

Adapted from Christian Mathiesen's [jev-pokemon](https://github.com/christianmat/jev-pokemon), commit `9ff2da2`. This derivative retains [GPL-2.0-or-later](LICENSE). PyBoy and pret/pokered are separate upstream projects. See [architecture and attribution](docs/development.md) for the source map and port boundaries.

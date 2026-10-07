# Configuration reference

Run commands from the repository root. The CLI reads `.env` in the current working directory. Copy [`.env.example`](../.env.example) once, then edit it. Existing shell environment variables take precedence over `.env`; command-line `--rom` and `--data` take precedence over their environment values. Paths are relative to the working directory unless absolute.

The `.env` reader accepts `NAME=value`, quoted values, comments, and an optional `export` prefix. It does not execute shell commands or expand `$VARIABLE` or `~`. Use an absolute ROM path and quote paths containing spaces.

## Environment variables

| Variable | Default | Meaning and constraints |
| --- | --- | --- |
| `ROM_PATH` | Unset | Local supported Red ROM; required unless `--rom` is supplied. |
| `GAME_DATA_PATH` | `data/generated.json` | Generated symbol/constants JSON; overridden by `--data`. |
| `TYPESAFE_API_KEY` | Unset | Required for the Jev controller, including with planner off. |
| `JEV_MODEL` | `jev-latest` | Model sent to TypeSafe. |
| `JEV_MIN_INTERVAL_MS` | `300` | Minimum interval between uncached Jev requests, finite and nonnegative. |
| `JEV_MAX_PER_MIN` | `90` | Maximum Jev requests in a rolling minute; integer at least 1. |
| `MODEL_TIMEOUT_SECONDS` | `20` | Jev and HTTP planner request timeout; greater than 0 and at most 60. |
| `CODEX_TIMEOUT_SECONDS` | `180` | Codex subprocess timeout; finite, 1–600 seconds. |
| `CURSOR_MODEL` | `composer-2.5` | Cursor model identifier; blank also uses the default. Availability depends on the signed-in account. |
| `CURSOR_TIMEOUT_SECONDS` | `180` | Cursor subprocess timeout; finite, 1–600 seconds. |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | HTTP planner base URL; `/chat/completions` is appended. HTTPS required, except HTTP on localhost, 127.0.0.1, or ::1. |
| `LLM_API_KEY` | Unset | Required only for planner `llm`. |
| `LLM_MODEL` | Unset | Required only for planner `llm`; use a model identifier accepted by your provider. |

Unset keys and the literal values `todo`, `placeholder`, `change-me`, and `your-key-here` fail live-model credential checks. `doctor` only checks presence, not whether credentials work. Throttles control request frequency, not an overall usage or spending cap.

## Commands

All examples use `uv run pokemon-red-jev`. The equivalent module entry point is `uv run python -m pokemon_red_jev`.

| Command | Purpose |
| --- | --- |
| `demo` | Scripted offline agent simulation; appends to `logs/demo.jsonl`. |
| `doctor` | Checks configured file paths, key presence, CLI availability, and ROM/data provenance when both files exist. Makes no model requests and does not check CLI login. |
| `prepare-data --pokered PATH [--output PATH]` | Generates symbols from a matching local pokered build. Default output is `data/generated.json`. |
| `run` | Starts or resumes local gameplay. |

## Run flags

| Flag | Default | Effect |
| --- | --- | --- |
| `--rom PATH` | `ROM_PATH` | ROM file. |
| `--data PATH` | `GAME_DATA_PATH` | Generated data file. |
| `--controller jev\|manual` | `jev` | API action selection or terminal selection. |
| `--planner codex\|cursor\|llm\|off` | `llm` for Jev, `off` for manual | Goal provider; off uses built-in story goals. |
| `--headless` | Disabled | Runs without the local dashboard window. Manual mode still needs terminal input. |
| `--speed N` | `1` | Nonnegative integer emulator speed multiplier; 0 is unlimited. Inference pauses emulation regardless of speed. |
| `--steps N` | `0` | Nonnegative control-loop iteration limit; 0 runs until completion or interruption. Includes waits and menus, not just model calls. |
| `--resume PATH` | Unset | Loads a checkpoint archive. Without it, starts a new game. |
| `--save PATH` | `saves/latest.zip` | Periodic and shutdown checkpoint destination. |
| `--log PATH` | `logs/run.jsonl` | Append-only JSON-lines log destination. |

CLI help is available through `--help`, `run --help`, and `prepare-data --help`.

## Planner goal contract

Planners return data; the application validates identifiers and executes available actions. This example is valid only when the supplied catalog contains the map and the player has not already arrived there:

```json
{
  "goal": "Enter the Viridian Poké Mart",
  "focus": "progress",
  "target_map": "VIRIDIAN_MART",
  "success": {"kind": "map", "value": "VIRIDIAN_MART"},
  "max_decisions": 20,
  "justification": "Reach the clerk for Oak's Parcel"
}
```

`goal` must contain 1–400 characters after stripping whitespace. Focus is one of `progress`, `heal`, `train`, `catch`, `shop`, `explore`, or `team`. `target_map` must be in the catalog. The validator permits an integer budget of 1–100 overworld decisions; the planner prompt asks for 1–20. `justification` is optional in the generic validator and limited to 400 characters; Codex's output schema requires it. Train/catch/team goals must give a type or coverage reason and a numeric level in the goal text or justification. Repeated Pokémon goals receive additional validation.

| Success kind | Value | Completion evidence |
| --- | --- | --- |
| `map` | Catalog map string, matching `target_map` | Current map equals the target. |
| `event` | Catalog event string | Event flag is set. |
| `item` | Catalog item string | Bag contains a positive quantity. |
| `healed` | `true` | Nonempty party, every member at full HP and status OK. |
| `level` | Integer 1–100 | Any party member reaches the threshold. |
| `badge` | Integer 1–8 | That badge's bit is set, rather than a badge count. |
| `interaction` | Catalog interaction string | Interaction was observed; this does not prove quest completion. |

Already satisfied goals are rejected. A rejected answer gets one correction request before falling back to a temporary story goal. Internal story conditions support additional kinds; they are not legal planner output.

Continue with [setup](getting-started.md), [usage](usage.md), or [architecture](development.md).

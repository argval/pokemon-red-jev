# How to run, resume, and inspect games

Complete [setup](getting-started.md) first. Run every command from the repository root. See [configuration](configuration.md) for all flags and environment variables.

## How to choose a controller and planner

The controller chooses actions. The planner proposes short-term goals. You can configure them independently:

| Controller | Planner | Required model access |
| --- | --- | --- |
| `manual` | `off` | None. Built-in story goals, terminal action choices. |
| `manual` | `codex` or `cursor` | Matching CLI installed and signed in; no TypeSafe key. |
| `manual` | `llm` | `LLM_API_KEY` and `LLM_MODEL`. |
| `jev` | `off` | `TYPESAFE_API_KEY`. Built-in story goals with Jev focus/action choices. |
| `jev` | `codex` or `cursor` | TypeSafe key plus matching CLI sign-in. |
| `jev` | `llm` | TypeSafe key plus HTTP planner key and model. |

### Codex goals

Install Codex CLI, then authenticate in your terminal:

```sh
codex login
codex login status
```

The app invokes `codex exec` with a read-only sandbox, an ephemeral temporary directory, ignored user config, and a JSON output schema. It reuses CLI authentication. There is no app-specific Codex model setting; `LLM_MODEL` does not configure Codex.

Test goal planning with manual action selection:

```sh
uv run pokemon-red-jev run --controller manual --planner codex \
  --save saves/codex-manual.zip --log logs/codex-manual.jsonl
```

Then set `TYPESAFE_API_KEY` in `.env` and run:

```sh
uv run pokemon-red-jev run --controller jev --planner codex \
  --save saves/codex.zip --log logs/codex.jsonl
```

### Cursor goals

Install Cursor Agent CLI so `agent` or `cursor-agent` is on PATH. If your binary is named `cursor-agent`, use that name for the login/status commands too.

```sh
agent login
agent status
uv run pokemon-red-jev run --controller manual --planner cursor \
  --save saves/cursor-manual.zip --log logs/cursor-manual.jsonl
```

For Jev control, set `TYPESAFE_API_KEY` and replace `--controller manual` with `--controller jev`. The app uses ask mode in an empty temporary workspace with sandbox enabled. `CURSOR_MODEL` controls the requested model; CLI compatibility and model availability depend on your installation/account.

### HTTP goals or built-in goals

For an OpenRouter-compatible planner, fill `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in `.env`. The base URL must not already include `/chat/completions`.

```sh
uv run pokemon-red-jev run --controller jev --planner llm \
  --save saves/http.zip --log logs/http.jsonl
uv run pokemon-red-jev run --controller jev --planner off \
  --save saves/baseline.zip --log logs/baseline.jsonl
```

Both commands require a TypeSafe key. Jev calls TypeSafe's direct API; an upstream Vercel gateway key is not interchangeable.

### Verify model use

Check the startup line for the selected controller/planner. Before a starter, clear opening routes, dialogue, and naming can run without model requests. After obtaining a starter, inspect the log for `planner` and `jev` records. A temporary story goal and `planner_error` mean the planner failed; continued gameplay alone does not prove the planner works.

Model inference pauses emulation. A static game image during a request is expected. Provider usage limits still apply.

## How to save and resume safely

```sh
uv run pokemon-red-jev run --controller manual \
  --save saves/session.zip --log logs/session.jsonl
# After saving and exiting:
uv run pokemon-red-jev run --controller manual \
  --resume saves/session.zip --save saves/session.zip --log logs/session.jsonl
```

The runner saves about every 60 seconds between actions and at normal loop shutdown. It also writes `saves/session-good.zip` whenever the completed-goal counter changes. The periodic save may capture a menu or interrupted action; the good save is written after goal completion.

Each ZIP contains `emulator.state` and `agent.json`. The latter holds route memory, recent actions, failure counts, and goal history. The runner writes a temporary file beside the destination, flushes it, and atomically replaces the archive. These are application checkpoints, not ordinary `.sav` files. Keep a backup before trying different code or emulator versions; cross-version emulator-state compatibility is not guaranteed.

`--resume` selects the input archive; `--save` independently selects the output. Omitting `--save` while resuming another file still writes to `saves/latest.zip`. Omitting `--resume` starts a new game and can eventually overwrite the chosen output checkpoint. Loading restores game/agent memory and replaces the old active task from fresh observations.

To use the opening regression checkpoint, generate it first:

```sh
RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -p test_rom.py -v
uv run pokemon-red-jev run --controller manual \
  --resume saves/rom-check.zip --save saves/from-opening.zip
```

The test uses configured ROM/data, calls no models, and writes `saves/rom-check.zip`. It is not shipped in the repository. Verify the test passes before relying on the file.

## How to run a bounded or headless session

```sh
uv run pokemon-red-jev run --controller jev --planner codex \
  --headless --speed 0 --steps 1000 \
  --save saves/bounded.zip --log logs/bounded.jsonl
```

This needs TypeSafe access and Codex authentication. `--steps` includes animation waits and menu iterations, so it is neither a model-call cap nor a wall-clock limit. `--speed 0` removes emulator throttling; it does not remove API throttles. Use Ctrl-C to stop early. Headless manual mode still waits for terminal choices.

## How to inspect decisions and compare planners

Logs append one JSON object per line. Use distinct save/log paths for experiments. Inspect events without printing complete model context:

```sh
uv run python - <<'PY'
import json
from collections import Counter
from pathlib import Path
path = Path('logs/run.jsonl')  # Change to your run's log.
counts = Counter()
for line in path.open():
    record = json.loads(line)
    counts[record['kind']] += 1
    if record['kind'] in {'goal_end', 'planner_error', 'model_error', 'service_wait', 'complete'}:
        print(record)
print(dict(counts))
PY
```

Useful records include `goal`, `goal_end`, `action`, `planner`, `jev`, `planner_retry`, `planner_rejected`, `guard`, `loop`, `resume`, `save`, and `load`. Action records identify `routine`, `resume`, or `controller` as the source. Model records include latency and returned usage where available. Logs contain game state, goal text, options, and model context; review them before sharing.

To compare planners, start from the same checkpoint with separate outputs, identical emulator speed and Jev throttles, and equal observation time. Compare reached story milestones, verified goals, repeated failures, latency, and provider usage. Planner off is the built-in story/focus baseline, not a reproduction of the original project's whole decision loop.

## Troubleshooting runs

| Symptom | Response |
| --- | --- |
| `Set LLM_API_KEY` unexpectedly | Jev defaults to planner `llm`. Pass `--planner codex`, `cursor`, or `off` explicitly. |
| `Set TYPESAFE_API_KEY` | Add a working direct TypeSafe key, or use `--controller manual`. |
| CLI not installed/authenticated | Check PATH and run `codex login status` or `agent status` in the same terminal environment. |
| Planner timeout, invalid goal, or usage limit | Inspect `planner_error`/retry records. Check CLI login, model availability, and usage limits. Adjust the matching timeout within the reference's allowed range. |
| HTTP 401/403 from Jev | Authentication stops the run and saves. Correct the key before resuming. |
| `service_wait` and frozen game | Transient Jev failures save on the first failure and retry with delays increasing from 2 to 60 seconds. Window events remain responsive during retry pauses. |
| Three consecutive invalid Jev answers | The runner stops and saves. Inspect errors and available options before resuming. |
| Repeated menus, routes, or switches | Check `guard`, `loop`, action history, and goal outcomes. Try manual control from a copied checkpoint and record the map/action sequence. See the [port audit](../PORT_AUDIT.md). |
| Invalid/corrupt checkpoint | Use a backed-up or good archive. The loader requires checkpoint version 1 and both archive entries. |

HTTP calls have one bounded retry for network errors, 429, and 5xx. Planner failures fall back to a temporary story goal; controller service recovery has a separate loop. Initialization errors can occur before the save loop starts, so always retain your input checkpoint.

For changing the implementation and reproducing failures, see [development](development.md).

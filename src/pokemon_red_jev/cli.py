import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import time

from .agent import Agent
from .data import Data, generate
from .models import CodexPlanner, Jev, ModelError, Planner


def load_env(path=Path(".env")):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or not name.strip().replace("_", "a").isalnum():
            raise ValueError("Invalid .env assignment")
        tokens = shlex.split(value, comments=True)
        os.environ.setdefault(name.strip(), " ".join(tokens))


class Log:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a")

    def __call__(self, kind, **fields):
        record = {"time": time.time(), "kind": kind, **fields}
        self.file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        self.file.flush()
        if kind == "goal":
            print(f"[goal] {fields['goal']['goal']} | {fields['goal']['success']}", flush=True)
        elif kind == "action":
            print(f"[action] {fields['map']}: {fields['choice']}", flush=True)
        elif kind not in {"jev", "planner"}:
            print(f"[{kind}] {json.dumps(fields, ensure_ascii=False)}", flush=True)

    def close(self):
        self.file.close()


class Manual:
    """Select the same candidates Jev sees, without an API client."""

    def __init__(self):
        self.calls = 0
        self.input_tokens = 0
        self.last = None

    def choose(self, state, options):
        if len(options) == 1:
            choice = next(iter(options))
            self.last = {"purpose": "manual", "picked": choice, "probabilities": None}
            return choice
        print("\n" + "\n".join(state["screen"]))
        print(f"{state['map']} ({state['x']},{state['y']}) | {state['active_goal']['goal']}")
        keys = list(options)
        for i, key in enumerate(keys, 1):
            print(f"{i:2}. {key}: {options[key]}")
        while True:
            try:
                value = input("Action number or key (q saves and quits): ").strip()
            except EOFError:
                raise KeyboardInterrupt from None
            if value.lower() == "q":
                raise KeyboardInterrupt
            if value in options:
                choice = value
            elif value.isdecimal() and 1 <= int(value) <= len(keys):
                choice = keys[int(value) - 1]
            else:
                print("Choose one of the listed actions.")
                continue
            self.last = {"purpose": "manual", "picked": choice, "probabilities": None}
            return choice


def main():
    parser = argparse.ArgumentParser(description="Local Pokémon Red: LLM goals + Jev actions. No web server.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Scripted simulation; no ROM, models, credentials or network")
    sub.add_parser("doctor", help="Check setup without exposing credentials or making model calls")
    data = sub.add_parser("prepare-data", help="Read symbols/constants from your local matching pokered build")
    data.add_argument("--pokered", type=Path, required=True)
    data.add_argument("--output", type=Path, default=Path("data/generated.json"))
    run = sub.add_parser("run", help="Run PyBoy locally with Jev or manual action selection")
    run.add_argument("--rom", type=Path)
    run.add_argument("--data", type=Path)
    run.add_argument("--headless", action="store_true")
    run.add_argument("--speed", type=int, default=1, help="Emulator speed multiplier; 0 = unlimited")
    run.add_argument("--steps", type=int, default=1000, help="Control-loop iterations, including menus and animations")
    run.add_argument("--controller", choices=["jev", "manual"], default="jev")
    run.add_argument("--planner", choices=["codex", "llm", "off"], help="Default: llm for Jev, off for manual control")
    run.add_argument("--resume", type=Path)
    run.add_argument("--save", type=Path, default=Path("saves/latest.zip"))
    run.add_argument("--log", type=Path, default=Path("logs/run.jsonl"))
    args = parser.parse_args()
    try:
        load_env()
        if args.command == "prepare-data":
            generate(args.pokered, args.output)
            print(f"Generated {args.output}")
            return
        if args.command == "doctor":
            checks = {"ROM_PATH": bool(os.getenv("ROM_PATH")) and Path(os.environ["ROM_PATH"]).is_file(),
                      "GAME_DATA_PATH": Path(os.getenv("GAME_DATA_PATH", "data/generated.json")).is_file(),
                      **{key: bool(os.getenv(key, "").strip()) for key in ["TYPESAFE_API_KEY", "LLM_API_KEY", "LLM_MODEL"]}}
            for key, present in checks.items():
                suffix = " (only needed for --planner llm)" if key in {"LLM_API_KEY", "LLM_MODEL"} else ""
                print(f"{key}: {'present' if present else 'missing / placeholder'}{suffix}")
            from shutil import which
            print(f"Codex CLI: {'installed' if which('codex') else 'missing'}")
            if checks["ROM_PATH"] and checks["GAME_DATA_PATH"]:
                from .game import Rom
                Rom(Path(os.environ["ROM_PATH"]), Data(Path(os.getenv("GAME_DATA_PATH", "data/generated.json"))))
                print("ROM and data: supported Red release verified")
                print("No-key play: `pokemon-red-jev run --controller manual`")
            else:
                print("Use `pokemon-red-jev demo` while the ROM/data are unset.")
            return
        if args.command == "demo":
            from .demo import run_demo
            print("SIMULATION: scripted game, planner and Jev stand-ins. No ROM or API calls.")
            log = Log(Path("logs/demo.jsonl"))
            try:
                agent = run_demo(log)
                print(f"Simulation passed: {agent.completed_goals} verified goals, {agent.plans} planner requests.")
            finally:
                log.close()
            return
        if args.steps < 1 or args.speed < 0:
            parser.error("--steps must be positive and --speed must be nonnegative")
        rom = args.rom or (Path(os.environ["ROM_PATH"]) if os.getenv("ROM_PATH") else None)
        if rom is None or not rom.is_file():
            raise ValueError("Set ROM_PATH in .env or pass --rom. Use `demo` without a ROM.")
        data_path = args.data or Path(os.getenv("GAME_DATA_PATH", "data/generated.json"))
        data = Data(data_path)
        log = Log(args.log)
        game = None
        try:
            jev = Manual() if args.controller == "manual" else Jev(log)
            planner_mode = args.planner or ("off" if args.controller == "manual" else "llm")
            planner = (CodexPlanner(log) if planner_mode == "codex" else
                       Planner(log) if planner_mode == "llm" else None)
            print(f"Controller: {args.controller}; planner: {planner_mode}" +
                  ("" if args.headless else "; window: game and Jev panel"))
            from .game import Game
            from .controls import Controls
            from .navigation import Navigation
            game = Game(rom, data, headless=args.headless, speed=args.speed)
            agent = Agent(game, planner, jev, Navigation(game), Controls(game), log)
            if args.resume:
                agent.load(args.resume)
                game.tick()  # Present the restored frame in the native window.
            last_save = time.monotonic()
            errors = 0
            try:
                for _ in range(args.steps):
                    try:
                        agent.step()
                        errors = 0
                    except ModelError as exc:
                        log("model_error", error=str(exc))
                        errors += 1
                        if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                            raise ModelError(f"TypeSafe rejected the Jev API request ({exc})") from exc
                        if errors >= 3:
                            raise ModelError("Three consecutive Jev failures; stopped and saved") from exc
                        time.sleep(1)
                    if time.monotonic() - last_save >= 60:
                        agent.save(args.save)
                        last_save = time.monotonic()
                    if game.snapshot()["milestone"] is None:
                        log("complete", message="All story milestones verified")
                        break
                if errors:
                    raise ModelError(f"Last Jev request failed; no action was taken ({errors} failure)")
            except KeyboardInterrupt:
                print("Stopping and saving.")
            finally:
                agent.save(args.save)
        finally:
            if game:
                game.close()
            log.close()
    except (ValueError, OSError, KeyError, ModelError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

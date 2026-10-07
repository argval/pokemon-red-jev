# Set up your first local game

You'll install the application, verify its offline loop, configure a matching ROM and symbols, and start a game without model credentials. Add a planner and Jev only after manual play works.

## What you'll need

- Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Python 3.12 or newer. This checkout pins 3.12 in `.python-version`; uv can install it.
- For gameplay, a local US/EU Pokémon Red ROM with SHA-1 `ea9bcae617fdf159b045185467ae58b2e4a48b9a`.
- For symbol generation, Make and RGBDS, following [pret/pokered's installation instructions](https://github.com/pret/pokered/blob/master/INSTALL.md). The previously verified local build used RGBDS 1.0.4.

No ROM, generated game data, credentials, or checkpoints come with a fresh checkout. Other releases and ROM hacks are unsupported. The examples below use a macOS/Linux shell. The upstream build guide covers Windows tooling; this repository does not establish native Windows dashboard support.

## Step 1: Install and see the offline loop

```sh
git clone https://github.com/argval/pokemon-red-jev.git
cd pokemon-red-jev
uv python install 3.12
uv sync --locked
uv run pokemon-red-jev demo
```

If you already have the checkout, start with `cd` into its root. The demo prints simulated goals/actions and ends with `Simulation passed`. It writes `logs/demo.jsonl`. It uses the production agent loop with scripted game/model stand-ins, so it needs no ROM, window, network, or API key.

## Step 2: Generate and configure game data

After installing the upstream build tools, download the disassembly into the ignored `vendor/` directory. Pin the revision previously used here to keep the symbol layout reproducible:

```sh
mkdir -p vendor
git clone https://github.com/pret/pokered.git vendor/pokered
git -C vendor/pokered checkout d2704a63c26f9ba046ade877445216b3de0519a4
make -C vendor/pokered red
uv run pokemon-red-jev prepare-data --pokered vendor/pokered
cp .env.example .env
```

For an existing installation, preserve your `.env` and reuse your existing pokered checkout. Generation reads `pokered.sym`, constants, and the character map, and verifies the built `pokered.gbc` or `pokered.gb` hash. It prints `Generated data/generated.json` on success.

Edit `.env` with your ROM location:

```dotenv
ROM_PATH="/absolute/path/to/red.gb"
GAME_DATA_PATH=data/generated.json
```

The configured ROM can have a different filename or extension; its bytes must match the supported hash. Keep the other defaults from `.env.example`. Leave model keys blank for manual play.

```sh
uv run pokemon-red-jev doctor
```

Look for `ROM and data: supported Red release verified`. Missing model keys are expected for manual mode. `doctor` uses `.env` paths, so configure those even if you normally pass `--rom` and `--data` to `run`.

## Step 3: Start and save a game

```sh
uv run pokemon-red-jev run --controller manual --planner off --speed 0 \
  --save saves/manual.zip --log logs/manual.jsonl
```

A local dashboard opens with the game on the left and task/team/decision information on the right. Choose a listed action number or key in the terminal. This is action selection, not raw keyboard control of the Game Boy. Single-option actions such as waiting advance automatically.

Enter `q` at an action prompt or press Ctrl-C to save and exit. Escape or window close is handled when the window event loop is being serviced; if the terminal is waiting for input, use `q` or Ctrl-C. Resume explicitly:

```sh
uv run pokemon-red-jev run --controller manual --planner off \
  --resume saves/manual.zip --save saves/manual.zip --log logs/manual.jsonl
```

## Add model control

Read [how to choose a controller and planner](usage.md#how-to-choose-a-controller-and-planner). For Codex or Cursor, install the matching CLI and sign in. Jev additionally requires `TYPESAFE_API_KEY`, regardless of the planner. Pass the planner explicitly because the default for Jev is `llm`.

## Troubleshooting setup

| Symptom | What to do |
| --- | --- |
| `uv: command not found` | Install uv using its official guide, reopen the terminal, and check `uv --version`. |
| `rgbasm` missing or build fails | Follow the pinned pokered checkout's `INSTALL.md`; check RGBDS compatibility. |
| `Build the matching US/EU Red ROM...` | Run `make red` in the pokered checkout. Generation requires both symbols and a matching built ROM. |
| `Set ROM_PATH...` | Edit `.env` in the project root, or pass `--rom` to `run`. Quoted absolute paths avoid working-directory surprises. |
| Missing `data/generated.json` or incompatible provenance | Run `prepare-data` against the matching build and check `GAME_DATA_PATH`. |
| Unsupported ROM | Check the SHA-1 against the value above. A different language, revision, or hack needs separate support. |
| SDL/display error | Try `--headless` to isolate the window dependency. The visible dashboard needs SDL2 and a desktop display. On macOS, `brew install sdl2` supplies the library; on Linux install your distribution's SDL2 runtime. |

## What you built

You now have a local PyBoy game, the same action candidates used by Jev, and a resumable emulator/agent checkpoint. Continue with [usage recipes](usage.md), the [configuration reference](configuration.md), and [development and validation](development.md).

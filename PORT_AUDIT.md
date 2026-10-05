# Port audit: repeated controls and stalled progress

2026-09-27. Compared the local `jev-pokemon` reference at `9ff2da2` with this checkout at `96594ba`, including the existing uncommitted changes. Reviewed action selection, progress tracking, dialogue, menus, routing memory, battle interruptions, and planner inputs.

## Reproduced failure

The saved player was at `(3,3)` in `VIRIDIAN_POKECENTER`. Ordinary NPC conversations had marked `(3,4)` and `(2,3)` as guard traps. The pathfinder therefore offered only `npc:1`, `npc:3`, and `party`. Both exits disappeared.

`logs/run.jsonl` recorded 56 party-menu openings at that Center. Opening and closing menus changed the screen, so the old action failure counter never accumulated evidence against opening the party menu. Replanning did not restore the missing exits.

Loading that same save with the correction restores both doors and the other reachable targets. Older checkpoints discard only the unverified trap tiles; emulator progress and other agent memory remain intact.

## Corrections

| Problem | Change |
| --- | --- |
| Every dialogue after movement could create a trap, including intentional nurse conversations | Only interrupted walks are considered. Wait until the dialogue ends, then check whether the exit worked or the player advanced. Require speech from this attempt. Battles and ordinary interactions do not create traps. |
| Repeated actions escaped detection by opening menus, moving a tile, or receiving a new goal | Count overworld choices per map until observed progress. Show counts to Jev, sample alternatives after three attempts, and withhold exhausted choices after five when alternatives exist. Preserve goal-directed routes and Strength activation, following the reference. Save the counters with the checkpoint. |
| Repeated boulder pushes could be confused with consecutive useful pushes | Include the boulder's starting square in attempt counts and reset those counts on a new visit. |
| The menu guard only recognized consecutive identical screens | Count returning screens across submenus and ignore cursor position. Actual inventory/team changes reset the counts. |
| Finishing battle text discarded the interrupted route | Keep the pending destination through battle exit and resume it at the next overworld decision, with the existing three-resume limit. |
| Training treated damage and spent PP as progress | Count training experience and team changes. Healing progress uses recovered HP or cured status. |
| Recorded trainer dialogue was treated as proof of victory | Keep the trainer available. Present remembered dialogue as evidence without claiming the NPC can no longer help. The reference does not hide trainers merely because they spoke. |
| Jev could choose a service that cannot be reached | Remove healing, shopping, and PC focuses when the existing room graph has no route to the service. |
| The planner received an empty nearby-map list | Build the route catalog before building the planner brief. The live Cursor call received 11 nearby map distances. |
| Options detection could match code at the same address in another ROM bank, including during battles | Check the loaded bank for routine probes and exclude Options handling during battles. |

Changes are in `agent.py`, `navigation.py`, `controls.py`, and `game.py`. Regression checks are in `tests/test_core.py`. Existing changes in `goals.py` and `models.py` were left intact.

## Verification

- The original suite had 50 tests. Added 10 regression tests and strengthened existing checks. The new failure cases were run before their fixes and failed as expected.
- `RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -q`: 60 tests pass, including boot, starter selection, rival battle, parcel delivery, Pokédex receipt, and checkpoint restore.
- Live Jev run from the stuck save, planner off: left the Center, crossed Viridian Forest, resumed after a wild battle, reached Pewter City, and entered the gym. One HTTP 520 was recovered by the existing request handling.
- Live Cursor + Jev run from the same save: Cursor chose “Leave the Pokémon Center,” completed in one overworld action. Jev then reached Pewter City. The bounded run had no planner or model errors. It lost the Brock fight; winning Brock is not verified.
- A subsequent replay with ordinary buttons returned to Pewter Gym in 21 iterations. Brock remained available with a recorded loss and a remembered greeting seeded in agent memory. No game RAM was modified.
- Live experiments used separate checkpoints and logs under `/private/tmp/jev-stall-audit/`. They did not overwrite `saves/latest.zip`.

## Follow-up: remaining recovery differences

2026-09-28. Implemented the four missing policies and added five isolated ROM scenarios.

- **PC sessions:** close after 15 menu choices without a party/box/bag change. Remember completed visits on logoff, cancellation at the top menu, or a loop guard. Swapping the same owned Pokémon does not reopen team focus; a changed roster does, or an unmet boxed field-move need after 15 minutes. Session counters and completion memory survive checkpoints.
- **Boulders:** track each boulder's best distance to an open switch/hole for the current visit. Only new minima count as progress, before the stall check runs. Record a predicted stranding push only after verifying that the boulder actually moved; show its history on later visits. Added Seafoam hole targets from the local ROM scripts.
- **Routes:** describe toward, away, equal-distance, current-objective, and dead-end routes. When only the static layout connects the goal, qualify the route with the closed-gate condition. Suppress misleading away/equal claims in the Mansion and away claims when a gated floor offers no reachable forward option. Service reachability still uses the live graph. Mansion floor drops now include destination and route facts, and wait for the delayed fall before returning control.
- **Shopping:** expire the cooldown after 15 minutes or a ¥200 increase. Persist the timestamp. A quantity-screen regression also exposed wallet balance being read as purchase price; prices now come from the quantity box.

### Verification

`RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -q`: **73 tests passed**, including the original opening and five new ROM scenarios:

| Scenario | Observed result |
| --- | --- |
| Mansion | Toggle a switch, save/restore, continue through 2F and 3F, toggle the 3F switch, drop to 1F and reach B1F. |
| Seafoam | Push a 1F boulder into its hole, follow it to B1F, and drop it through the next hole. Both event flags are checked. |
| Victory Road | The real Agent loop completes 18 pushes on 1F, records 15 progress gains, activates the switch, and never replans for a stall. |
| PC | A repeated deposit session is closed by the guard; the game returns to the overworld and the completed visit is remembered. |
| Shop | Repeated browsing is closed by the guard; reopening and choosing QUIT records the money and timestamp. |

Offline regressions cover the exact 15-choice boundary, reset after meaningful changes, completed-team suppression, cooldown time/money boundaries, checkpoint persistence, boulder backtracking and revisits, failed versus executed pushes, gated route wording, and purchase prices.

The new ROM scenarios boot through the opening, then seed party capabilities, encounter protection, and one doorway destination in an isolated emulator. After entry, they use production button drivers and no models. They do not change the ROM or `saves/latest.zip`. These are reproducible integration checks, not a naturally played late-game save or a full autonomous completion. Seafoam's lower floors, Victory Road 2F/3F, and a complete model-driven run remain outside the tested scope.

## Follow-up: Pewter Gym / Route 2 loop

2026-09-28. The player's log showed a `progress` goal to defeat Brock alongside a stale `train` focus. Route 2 offered grass, but Jev selected Pewter; inside the gym it selected the exit. An explicit training goal then made only three one-tile grass walks before the repeat guard warned against training.

Corrections:

- Resolve focus before building routes. In planner mode, the active goal determines the focus; unavailable focuses trigger replanning. Planner-off mode retains Jev's focus selection.
- Route training/catching to reachable encounter grass, using the ROM's encounter rate to exclude decorative grass. Restore the reference's 40-step grass pacing, stopping for battles, dialogue, blocked movement, or map changes.
- Count every XP gain as training progress; the old 200-XP buckets hid small wild-battle rewards.
- Mark the objective trainer explicitly, clear interrupted walks when a goal ends, and request healing when a completed task leaves the party below 25% HP.

Verification:

- Three regressions failed before the fixes: stale focus, small XP rewards, and one-tile training. Added checks for stale walk resumption and low-HP goal completion, plus a real-ROM Pewter → Route 2 → wild battle scenario.
- `RUN_ROM_TESTS=1 uv run python -m unittest discover -s tests -q`: **79 tests passed**. Full output: `/private/tmp/jev-brock-loop/full-tests.log`.
- Live Cursor + Jev replay from a copy of the player's checkpoint entered Pewter Gym once, fought the junior trainer, reached Brock, and defeated him. Squirtle advanced from level 11 to 14. There were no planner/model errors and no RAM edits. Continuing the ordinary victory dialogue verified the Boulder Badge; the recovered checkpoint is `saves/brock-loop-fixed.zip`.
- A separate live training replay used six grass actions and gained 170 XP before low HP triggered healing. It then resumed the story objective and reached level 12 in the gym. This verifies actual wild-battle training and healing, but does not establish that an interrupted level target resumes after healing.
- Logs and input checkpoints are under `/private/tmp/jev-brock-loop/`. The player's `saves/latest.zip` was not overwritten. These bounded replays do not establish complete port parity or full-game reliability.

## Gym reward checkpoints

Each of the eight gym story entries now names its permanent `EVENT_GOT_TMxx` receipt flag, verified against the local ROM scripts. The situation report sent to both models includes earned badges and TM receipt checkpoints, including the current gym before completion. Using, selling, or depositing a TM does not clear its receipt flag. Badge ownership remains the story completion check because a full bag can postpone the TM gift after victory.

All **80 tests passed**, including checks across all eight gyms for consumed TMs, pending gifts, and inventory-only false positives. Reading `saves/brock-loop-fixed.zip` also confirmed both the Boulder Badge and `EVENT_GOT_TM34` without changing the save.

## Startup and model-service recovery

The next reported run repeatedly chose Cancel in the player/rival naming menus, where B does nothing. The log's `REDS_HOUSE_2F` label also appears during the introduction, before walking is possible. After reaching the first floor, HTTP 503 errors stopped the runner. Empty-party HP was treated as 0%, unnecessarily invoking the planner before the starter was obtained.

- Autonomous setup now chooses the default player/rival names and finishes an already open name-entry screen. The naming menu no longer offers ineffective Cancel. Manual mode retains name and route choices.
- Before obtaining a starter, the runner follows the existing story milestones directly. An empty party no longer triggers healing replans.
- A single known forward route runs locally. Adjacent doorway tiles reaching the same room use the shorter path. Ambiguous routes, speculative routes through closed gates, and routes with repeated failures still require a controller decision. Action logs record whether the choice was routine, resumed, or selected by the controller.
- Timeouts, network failures, HTTP 429, and HTTP 5xx responses save progress and retry automatically, with pauses increasing from 2 to 60 seconds. Emulation stays paused while the window still accepts Escape/close. Authentication failures stop immediately; invalid responses remain bounded.

Verification: **84 tests passed**, including a fresh-ROM Agent run through both house floors, Pallet Town, and Oak's introduction with zero planner/model requests; service recovery after four consecutive failures; and window close during a retry pause. The new startup and retry regressions failed before their fixes.

A live replay from a copy of the user's first-floor checkpoint completed starter selection, the rival battle, parcel pickup, parcel delivery, and Pokédex receipt. It recovered from **five real service timeouts**, made 19 Jev decisions, and needed no planner calls. Of 210 executed actions, 189 were routine and two resumed an interrupted walk. The separate recovered save is `saves/startup-fixed.zip`; `saves/latest.zip` still matches the input copy byte for byte. Evidence is under `/private/tmp/jev-startup-fix/`. This confirms opening recovery; full-game autonomy and external service availability remain outside that result.

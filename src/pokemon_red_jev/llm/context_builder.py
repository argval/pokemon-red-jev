"""Readable party lines for the LLM goal prompt."""

from __future__ import annotations

from ..state.party import getPartySnapshot


def _move_text(move: dict) -> str:
    name = move.get("name", "?")
    pp, max_pp = move.get("pp"), move.get("max_pp")
    if pp is None and max_pp is None:
        detail = name
    elif max_pp is None:
        detail = f"{name} (PP {pp})"
    elif pp is None:
        detail = f"{name} (PP ?/{max_pp})"
    else:
        detail = f"{name} (PP {pp}/{max_pp})"
    extra = []
    if move.get("type"):
        extra.append(str(move["type"]))
    if move.get("power"):
        extra.append(f"PWR{move['power']}")
    if extra:
        detail += f" [{'/'.join(extra)}]"
    return detail


def formatPartyForPrompt(snapshot_or_state) -> str:
    """One line per party slot: species, level, HP/maxHP, status, moves+PP, XP, usable."""
    if isinstance(snapshot_or_state, dict):
        snapshot = getPartySnapshot(snapshot_or_state)
    elif isinstance(snapshot_or_state, list) and snapshot_or_state and isinstance(snapshot_or_state[0], dict) \
            and "usable" in snapshot_or_state[0]:
        snapshot = snapshot_or_state
    else:
        snapshot = getPartySnapshot(snapshot_or_state)
    if not snapshot:
        return "Party: none (no Pokemon yet - choose a starter first.)"
    lines = []
    for mon in snapshot:
        moves = ", ".join(_move_text(m) for m in (mon.get("moves") or [])) or "moves: none"
        if moves != "moves: none":
            moves = "moves: " + moves
        lines.append(
            f"Slot {mon.get('slot', 0)}: {mon.get('species', '?')} "
            f"Lv{mon.get('level', 0)} HP {mon.get('hp', 0)}/{mon.get('max_hp', 0)} "
            f"status {mon.get('status', 'OK')} XP {mon.get('experience', 0)} "
            f"usable={'yes' if mon.get('usable') else 'no'} {moves}"
        )
    return "\n".join(lines)

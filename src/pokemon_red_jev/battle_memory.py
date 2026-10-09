"""Keep each committed battle action and its result across message screens."""

from copy import deepcopy
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .navigation import Action


def _snapshot(battle: dict) -> dict:
    """Copy only facts needed to judge this turn, without moves or party data."""
    result = {"active_slot": battle.get("active_slot")}
    for side in ("player", "enemy"):
        mon = battle.get(side) or {}
        result[side] = deepcopy({key: mon[key] for key in
                                 ("species", "nickname", "hp", "status", "stat_stages", "volatile")
                                 if key in mon})
    return result


def _identity(battle: dict) -> str:
    """Use the opponent's roster slot when available, so identical foes stay distinct."""
    enemy = battle.get("enemy") or {}
    value = battle.get("enemy_id")
    if value is None:
        value = [battle.get("kind"), enemy.get("party_slot"), enemy.get("species"),
                 enemy.get("level"), enemy.get("max_hp")]
    return json.dumps(value, sort_keys=True)


def _hp_change(before: dict, after: dict) -> int:
    """Return signed HP change only when both snapshots contain HP."""
    old, new = before.get("hp"), after.get("hp")
    return new - old if isinstance(old, int) and isinstance(new, int) else 0


def _useful(before: dict, after: dict, pending: dict) -> bool:
    """Check useful battle effects without counting PP, messages, or player damage."""
    enemy_before, enemy_after = before["enemy"], after["enemy"]
    player_before, player_after = before["player"], after["player"]
    if _hp_change(enemy_before, enemy_after) < 0:
        return True
    if (enemy_before.get("status") is not None and enemy_after.get("status") not in (None, "OK")
            and enemy_after.get("status") != enemy_before.get("status")):
        return True
    for side, sign in (("enemy", -1), ("player", 1)):
        if side == "player" and before.get("active_slot") != after.get("active_slot"):
            continue
        old = before[side].get("stat_stages") or {}
        new = after[side].get("stat_stages") or {}
        if any(isinstance(value, int) and isinstance(old.get(stat), int)
               and (value - old[stat]) * sign > 0 for stat, value in new.items()):
            return True
    old_flags = enemy_before.get("volatile") or {}
    new_flags = enemy_after.get("volatile") or {}
    if any(new_flags.get(flag) and not old_flags.get(flag) for flag in ("seeded", "confused")):
        return True
    if _hp_change({"hp": old_flags.get("substitute_hp")},
                  {"hp": new_flags.get("substitute_hp")}) < 0:
        return True
    if before.get("active_slot") == after.get("active_slot"):
        if _hp_change(player_before, player_after) > 0:
            return True
        if player_before.get("status") not in (None, "OK") and player_after.get("status") == "OK":
            return True
        old_flags = player_before.get("volatile") or {}
        new_flags = player_after.get("volatile") or {}
        if any(new_flags.get(flag) and not old_flags.get(flag)
               for flag in ("reflect", "light_screen", "mist", "substitute")):
            return True
    return (pending.get("kind") == "switch" and after.get("active_slot") is not None
            and after.get("active_slot") != before.get("active_slot")
            and after.get("active_slot") == pending.get("target_slot"))


class BattleMemory:
    """Track one battle turn until the next decision and retain six recent results."""

    def __init__(self):
        self.enemy_id: str | None = None
        self.pending: dict | None = None
        self.recent_turns: list[dict] = []
        self.stalled_turns = 0
        self.move_failures: dict[str, int] = {}

    def _reset(self):
        """Clear turn state and failure counts when the battle or opponent changes."""
        self.enemy_id = None
        self.pending = None
        self.recent_turns.clear()
        self.stalled_turns = 0
        self.move_failures.clear()

    def _text(self, dialog: str):
        """Keep distinct result messages while rejecting the battle action menu."""
        if self.pending is None or not isinstance(dialog, str):
            return
        text = " ".join(re.sub(r"[┌─┐│└┘▶▷▼]", " ", dialog).split())
        upper = text.upper()
        if not text or ("FIGHT" in upper and "RUN" in upper):
            return
        if "PP" in upper and "TYPE" in upper:
            return
        text = text[:300]
        if text not in self.pending["text"]:
            self.pending["text"].append(text)
            self.pending["text"] = self.pending["text"][-12:]

    def observe(self, state: dict, dialog: str = "") -> dict | None:
        """Collect messages and effects; finish the old turn before an opponent reset."""
        battle = state.get("battle") or {}
        active = state.get("in_battle", state.get("mode") == "battle" or bool(battle))
        if not active:
            self._text(dialog)
            result = self._complete(battle_ended=True) if self.pending else None
            self._reset()
            return result
        if not battle.get("enemy"):
            self._text(dialog)
            return None
        enemy_id = _identity(battle)
        result = None
        if self.enemy_id is not None and self.enemy_id != enemy_id:
            result = self._complete(opponent_changed=True) if self.pending else None
            self._reset()
        self.enemy_id = enemy_id
        self._text(dialog)
        if self.pending:
            after = _snapshot(battle)
            before = self.pending["latest"]
            self.pending["damage"] += max(0, -_hp_change(before["enemy"], after["enemy"]))
            self.pending["useful"] |= _useful(before, after, self.pending)
            self.pending["latest"] = after
        return result

    def start(self, action: "Action", battle: dict):
        """Start a committed move, switch, item, or escape; preserve it through advances."""
        if self.pending or action.key in {"advance", "wait", "cancel"}:
            return
        if action.kind not in {"battle_move", "switch", "battle_item"} and action.key not in {"run", "struggle"}:
            return
        self.enemy_id = _identity(battle)
        before = _snapshot(battle)
        target = action.target or {}
        move = ({key: target[key] for key in ("id", "name", "type", "effect", "power", "slot", "accuracy")
                 if key in target} if action.kind == "battle_move" else {})
        self.pending = {"action": action.key, "kind": action.kind,
                        "move": deepcopy(move), "target_slot": target.get("slot"),
                        "before": before, "latest": deepcopy(before),
                        "text": [], "useful": False, "damage": 0}

    def finish(self, state: dict) -> dict | None:
        """Finalize at the next main battle decision, after observing its final facts."""
        result = self.observe(state)
        return result if result is not None else self._complete() if self.pending else None

    def _complete(self, *, opponent_changed: bool = False, battle_ended: bool = False) -> dict:
        """Record effects using the old opponent's last snapshot, then close this turn."""
        pending = self.pending
        assert pending is not None
        before, after = pending["before"], pending["latest"]
        defeated = after["enemy"].get("hp") == 0 or any(
            re.search(r"\b(?:ENEMY|FOE)\b.*\bFAINTED\b", text.upper()) for text in pending["text"])
        useful = bool(pending["useful"] or (opponent_changed or battle_ended) and defeated)
        if useful:
            self.stalled_turns = 0
            self.move_failures.clear()
        else:
            self.stalled_turns += 1
            key = pending["action"]
            self.move_failures[key] = self.move_failures.get(key, 0) + 1
        result = {"action": pending["action"], "move": pending["move"].get("name"),
                  "damage": pending["damage"], "useful": useful, "text": list(pending["text"]),
                  "stalled_turns": self.stalled_turns,
                  "move_failures": self.move_failures.get(pending["action"], 0)}
        for side in ("enemy", "player"):
            for key in ("hp", "status"):
                result[f"{side}_{key}_before"] = before[side].get(key)
                result[f"{side}_{key}_after"] = after[side].get(key)
        if opponent_changed:
            result["opponent_changed"] = True
        if battle_ended:
            result["battle_ended"] = True
        self.recent_turns.append(result)
        self.recent_turns = self.recent_turns[-6:]
        self.pending = None
        return deepcopy(result)

    def context(self) -> dict:
        """Expose recent outcomes and repeat counts without mutable internal references."""
        return deepcopy({"recent_turns": self.recent_turns, "stalled_turns": self.stalled_turns,
                         "move_failures": self.move_failures, "pending": self.pending is not None})

    def to_dict(self) -> dict:
        """Save pending turn data and recent results in a JSON-compatible checkpoint."""
        return deepcopy({"enemy_id": self.enemy_id, "pending": self.pending,
                         "recent_turns": self.recent_turns, "stalled_turns": self.stalled_turns,
                         "move_failures": self.move_failures})

    def load(self, data: dict | None):
        """Restore a checkpoint; absent memory starts with empty battle history."""
        self._reset()
        if not isinstance(data, dict):
            return
        self.enemy_id = data.get("enemy_id") if isinstance(data.get("enemy_id"), str) else None
        pending = data.get("pending")
        if (isinstance(pending, dict) and isinstance(pending.get("action"), str)
                and all(isinstance(pending.get(key), dict) for key in ("before", "latest", "move"))
                and all(isinstance(pending[snapshot].get(side), dict)
                        for snapshot in ("before", "latest") for side in ("player", "enemy"))
                and isinstance(pending.get("text"), list) and isinstance(pending.get("damage"), int)
                and isinstance(pending.get("useful"), bool)):
            self.pending = deepcopy(pending)
            self.pending["text"] = [text[:300] for text in pending["text"] if isinstance(text, str)][-12:]
        recent = data.get("recent_turns")
        if isinstance(recent, list):
            self.recent_turns = deepcopy([turn for turn in recent if isinstance(turn, dict)][-6:])
        stalled = data.get("stalled_turns", 0)
        self.stalled_turns = max(0, stalled) if isinstance(stalled, int) else 0
        failures = data.get("move_failures")
        if isinstance(failures, dict):
            self.move_failures = {key: max(0, count) for key, count in failures.items()
                                  if isinstance(key, str) and isinstance(count, int)}

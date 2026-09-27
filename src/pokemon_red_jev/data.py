"""Read symbols from a local pret/pokered build; no ROM data is bundled."""

import hashlib
import json
import re
from pathlib import Path

RED_SHA1 = "ea9bcae617fdf159b045185467ae58b2e4a48b9a"


def generate(pokered: Path, output: Path) -> None:
    def read(name):
        return (pokered / name).read_text()

    def constants(name, prefix):
        result, value = {}, 0
        for line in read(name).splitlines():
            line = line.split(";", 1)[0].strip()
            if m := re.match(r"const_def(?:\s+(\$[\da-fA-F]+|\d+))?", line):
                value = number(m[1]) if m[1] else 0
            elif m := re.match(r"const_next\s+(\$[\da-fA-F]+|\d+)", line):
                value = number(m[1])
            elif m := re.match(r"const_skip(?:\s+(\d+))?", line):
                value += int(m[1] or 1)
            elif m := re.match(r"const\s+(\w+)", line):
                if m[1].startswith(prefix):
                    result[m[1]] = value
                value += 1
        return result

    symbols = {}
    for line in read("pokered.sym").splitlines():
        if m := re.match(r"([\da-fA-F]{2}):([\da-fA-F]{4}) (\S+)", line):
            bank, addr = int(m[1], 16), int(m[2], 16)
            symbols[m[3]] = bank * 0x4000 + addr - 0x4000 if 0x4000 <= addr < 0x8000 else addr
    maps, value = {}, 0
    for line in read("constants/map_constants.asm").splitlines():
        if m := re.match(r"\s*map_const\s+(\w+),\s*(\d+),\s*(\d+)", line):
            maps[value] = {"name": m[1], "width": int(m[2]), "height": int(m[3])}
            value += 1
        elif m := re.match(r"\s*const_(def|next)(?:\s+(\$[\da-fA-F]+|\d+))?", line):
            value = number(m[2]) if m[2] else 0
    charmap = {}
    for line in read("constants/charmap.asm").splitlines():
        if m := re.match(r'\s*charmap\s+"(.+?)",\s*\$([\da-fA-F]{2})', line):
            code = int(m[2], 16)
            if code >= 0x79 or code in (0x4a, 0x54):
                charmap.setdefault(code, m[1].replace("<PK>", "PK").replace("<MN>", "MN").replace("<PKMN>", "PKMN"))
    charmap.update({0x7f: " ", 0x54: "POKé", 0xed: "▶"})
    built_rom = pokered / "pokered.gbc"
    if not built_rom.exists():
        built_rom = pokered / "pokered.gb"
    if not built_rom.exists() or hashlib.sha1(built_rom.read_bytes()).hexdigest() != RED_SHA1:
        raise ValueError("Build the matching US/EU Red ROM with `make red` before generating symbols.")
    result = {"rom_sha1": RED_SHA1, "sym": symbols, "maps": maps,
              "events": constants("constants/event_constants.asm", "EVENT_"),
              "sprites": {v: k.removeprefix("SPRITE_") for k, v in constants("constants/sprite_constants.asm", "SPRITE_").items()},
              "charmap": charmap}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result))


def number(value):
    return int(value[1:], 16) if value.startswith("$") else int(value)


class Data:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text())
        if raw.get("rom_sha1") != RED_SHA1:
            raise ValueError("Regenerate game data with prepare-data; ROM provenance is missing or incompatible.")
        self.symbols = raw["sym"]
        self.maps = {int(k): v for k, v in raw["maps"].items()}
        self.events = raw["events"]
        self.charmap = {int(k): v for k, v in raw["charmap"].items()}
        self.sprites = {int(k): v for k, v in raw["sprites"].items()}

    def sym(self, name):
        return self.symbols[name]

    def decode(self, values, row=False):
        chars = []
        for value in values:
            if value == 0x50 and not row:
                break
            chars.append(self.charmap.get(value, " " if row else ""))
        text = "".join(chars)
        return text if row else text.strip()

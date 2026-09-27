"""Local window in the jev-pokemon stream arrangement: game on the left, status panel on the right.

The canvas is 1280x720. There is no RTMP upload and no web viewer.
"""

import os
import time
from ctypes import c_uint8

import sdl2

from .goals import story

PANEL_W, PANEL_H = 288, 288
CANVAS_W, CANVAS_H = 1280, 720
GAME_X, GAME_Y, GAME_W, GAME_H = 24, 72, 640, 576
PANEL_X, PANEL_Y = 680, 72
CELL, LINE_H = 8, 10
BG = (21, 21, 20, 255)
FG = (236, 236, 230)
ACCENT = (124, 196, 155)
DIM = (154, 154, 146)
EXTRA = {
    "%": [0x62, 0x64, 0x08, 0x10, 0x26, 0x46, 0x00, 0x00],
    ">": [0x20, 0x10, 0x08, 0x04, 0x08, 0x10, 0x20, 0x00],
    "#": [0x24, 0x7e, 0x24, 0x24, 0x7e, 0x24, 0x00, 0x00],
    "|": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x00],
    "$": [0x10, 0x7c, 0xd0, 0x7c, 0x16, 0xfc, 0x10, 0x00],
}


def wrap(text, width):
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            if len((line + " " + word).strip()) > width:
                if line:
                    out.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        out.append(line)
    return out


def overlay_lines(state, controller, width):
    """Stream panel sections, plus the story milestone and the planner's active goal."""
    milestones = story()
    total = len(milestones)
    milestone = state.get("milestone")
    if not milestone:
        index, goal = total, "Game complete!"
    else:
        milestone_id = milestone.get("id")
        index = next((i for i, item in enumerate(milestones) if item.get("id") == milestone_id), 0)
        goal = milestone.get("goal") or "Game complete!"
    active = state.get("active_goal")
    active_text = active.get("goal") if isinstance(active, dict) else None
    badges = state.get("badges") or 0
    badge_count = badges.bit_count() if isinstance(badges, int) else 0
    calls = getattr(controller, "calls", 0) or 0
    tokens = getattr(controller, "input_tokens", 0) or 0
    try:
        price = float(os.getenv("JEV_PRICE_PER_M", "0.042"))
    except ValueError:
        price = 0.042
    header = [
        "# JEV PLAYS POKEMON RED",
        f"~ {badge_count} badges | milestone {index}/{total}",
        f"~ {calls:,} jev calls | {tokens:,} tokens",
        f"~ total cost: ${(tokens / 1e6) * price:.3f} USD",
    ]
    party_lines = []
    for mon in state.get("party") or []:
        species = mon.get("species") or "?"
        nickname = mon.get("nickname") or ""
        label = f"{species} ({nickname})" if nickname and nickname != species else species
        stats = f"Lv{str(mon.get('level', 0)).ljust(3)}{str(mon.get('hp', 0)).rjust(3)}/{str(mon.get('max_hp', 0)).ljust(3)}"
        room = max(10, width - len(stats) - 1)
        party_lines.append(f"{label[:room].ljust(room)} {stats}")
    where = str(state.get("map", "")).replace("_", " ")
    decision = []
    last = getattr(controller, "last", None)
    if last:
        picked = str(last.get("picked", ""))
        decision = [f"# JEV DECISION ({last.get('purpose', 'action')})", *wrap(f"> {picked}", width)[:2]]
        probabilities = last.get("probabilities") or {}
        ranked = []
        for key, value in probabilities.items():
            try:
                ranked.append((str(key), float(value)))
            except (TypeError, ValueError):
                continue
        for key, value in sorted(ranked, key=lambda item: item[1], reverse=True)[:4]:
            decision.append(f"~ {round(value * 100):3d}% {key[:max(0, width - 6)]}")

    def assemble(story_lines, goal_lines, spaced):
        gap = [""] if spaced else []
        built = [*header]
        built += [*gap, "# MILESTONE", *wrap(goal, width)[:story_lines]]
        built += [*gap, "# GOAL", *(wrap(active_text, width)[:goal_lines] if active_text else ["~ none yet"])]
        built += [*gap, "# WHERE", where]
        built += [*gap, "# TEAM", *party_lines]
        if decision:
            built += [*gap, *decision]
        return built

    visible = PANEL_H // LINE_H
    for story_lines, goal_lines, spaced in ((4, 4, True), (3, 2, True), (2, 2, False), (1, 1, False)):
        lines = assemble(story_lines, goal_lines, spaced)
        if len(lines) <= visible:
            return lines
    return assemble(1, 1, False)[:visible]


class PanelRenderer:
    """Game Boy font, read from the ROM the same way as jev-pokemon's stream panel."""

    def __init__(self, rom, font_addr, charmap):
        self.glyph = {}
        for code, char in charmap.items():
            if code < 0x80 or len(char) != 1 or char in self.glyph:
                continue
            start = font_addr + (code - 0x80) * 8
            glyph = list(rom[start:start + 8])
            if len(glyph) == 8:
                self.glyph[char] = glyph
        self.glyph.update(EXTRA)
        self.glyph["→"] = self.glyph.get("▶", EXTRA[">"])

    @property
    def cols(self):
        return PANEL_W // CELL

    def render(self, lines):
        out = bytearray(BG * (PANEL_W * PANEL_H))
        for row, raw in enumerate(lines[:PANEL_H // LINE_H]):
            color, text = FG, raw
            if raw.startswith("# "):
                color, text = ACCENT, raw[2:]
            elif raw.startswith("~ "):
                color, text = DIM, raw[2:]
            for col, char in enumerate(text[:self.cols]):
                glyph = self.glyph.get(char) or self.glyph.get(char.upper())
                if not glyph:
                    continue
                for y in range(8):
                    bits = glyph[y]
                    for x in range(8):
                        if not bits & (0x80 >> x):
                            continue
                        offset = ((row * LINE_H + 1 + y) * PANEL_W + col * CELL + x) * 4
                        out[offset:offset + 3] = color
        return out


class Stage:
    def __init__(self, rom, font_addr, charmap):
        self.panel = PanelRenderer(rom, font_addr, charmap)
        self.closed = False
        self.shown = 0
        self.panel_dirty = True
        self.game_buf = bytearray(160 * 144 * 4)
        self.panel_buf = bytearray(self.panel.render(["# JEV PLAYS POKEMON RED", "", "starting..."]))
        self.game_ptr = (c_uint8 * len(self.game_buf)).from_buffer(self.game_buf)
        self.panel_ptr = (c_uint8 * len(self.panel_buf)).from_buffer(self.panel_buf)
        self.window = self.renderer = self.game_tex = self.panel_tex = None
        if sdl2.SDL_InitSubSystem(sdl2.SDL_INIT_VIDEO) < 0:
            raise RuntimeError(f"Could not open the display: {_sdl_error()}")
        try:
            sdl2.SDL_SetHint(sdl2.SDL_HINT_RENDER_SCALE_QUALITY, b"0")
            self.window = sdl2.SDL_CreateWindow(
                b"Jev Plays Pokemon Red", sdl2.SDL_WINDOWPOS_CENTERED, sdl2.SDL_WINDOWPOS_CENTERED,
                CANVAS_W, CANVAS_H, sdl2.SDL_WINDOW_RESIZABLE | sdl2.SDL_WINDOW_ALLOW_HIGHDPI)
            self.renderer = sdl2.SDL_CreateRenderer(self.window, -1, sdl2.SDL_RENDERER_ACCELERATED) if self.window else None
            if not self.renderer:
                raise RuntimeError(f"Could not open the display: {_sdl_error()}")
            sdl2.SDL_RenderSetLogicalSize(self.renderer, CANVAS_W, CANVAS_H)
            self.game_tex = _texture(self.renderer, 160, 144)
            self.panel_tex = _texture(self.renderer, PANEL_W, PANEL_H)
        except Exception:
            self.close()
            raise
        self.game_dst = sdl2.SDL_Rect(GAME_X, GAME_Y, GAME_W, GAME_H)
        self.panel_dst = sdl2.SDL_Rect(PANEL_X, PANEL_Y, PANEL_W * 2, PANEL_H * 2)

    def show(self, state, controller, screen):
        self.panel_buf[:] = self.panel.render(overlay_lines(state, controller, self.panel.cols))
        self.panel_dirty = True
        self.present(screen, force=True)

    def present(self, screen, fast=False, force=False):
        if self.closed:
            return
        self._poll()
        now = time.monotonic()
        if not force and fast and now - self.shown < 1 / 30:
            return
        self.shown = now
        self.game_buf[:] = screen.raw_buffer
        sdl2.SDL_UpdateTexture(self.game_tex, None, self.game_ptr, 160 * 4)
        if self.panel_dirty:
            sdl2.SDL_UpdateTexture(self.panel_tex, None, self.panel_ptr, PANEL_W * 4)
            self.panel_dirty = False
        sdl2.SDL_SetRenderDrawColor(self.renderer, *BG)
        sdl2.SDL_RenderClear(self.renderer)
        sdl2.SDL_RenderCopy(self.renderer, self.game_tex, None, self.game_dst)
        sdl2.SDL_RenderCopy(self.renderer, self.panel_tex, None, self.panel_dst)
        sdl2.SDL_RenderPresent(self.renderer)

    def _poll(self):
        event = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(event):
            if event.type == sdl2.SDL_QUIT or (event.type == sdl2.SDL_KEYDOWN and event.key.keysym.sym == sdl2.SDLK_ESCAPE):
                raise KeyboardInterrupt

    def close(self):
        if self.closed:
            return
        self.closed = True
        for texture in (self.game_tex, self.panel_tex):
            if texture:
                sdl2.SDL_DestroyTexture(texture)
        if self.renderer:
            sdl2.SDL_DestroyRenderer(self.renderer)
        if self.window:
            sdl2.SDL_DestroyWindow(self.window)
        sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_VIDEO)
        self.game_tex = self.panel_tex = self.renderer = self.window = None


def _texture(renderer, width, height):
    texture = sdl2.SDL_CreateTexture(renderer, sdl2.SDL_PIXELFORMAT_ABGR8888, sdl2.SDL_TEXTUREACCESS_STATIC, width, height)
    if not texture:
        raise RuntimeError(f"Could not open the display: {_sdl_error()}")
    sdl2.SDL_SetTextureBlendMode(texture, sdl2.SDL_BLENDMODE_NONE)
    sdl2.SDL_SetTextureScaleMode(texture, sdl2.SDL_ScaleModeNearest)
    return texture


def _sdl_error():
    err = sdl2.SDL_GetError()
    return err.decode() if err else "unknown"

"""Local window: a dashboard around the game, in the Game Boy font.

The canvas is 1280x720. The chrome is drawn at half resolution (640x360) and doubled, and the
Game Boy screen sits at 4x inside its frame. There is no RTMP upload and no web viewer.
"""

import os
import time
from ctypes import c_uint8

import sdl2

from .goals import story

CANVAS_W, CANVAS_H = 1280, 720
W, H = CANVAS_W // 2, CANVAS_H // 2
FRAME = (12, 42, 322, 290)  # game border in chrome units; the 320x288 screen sits one unit inside
RX, RW = 352, 276  # right column
BG = (21, 21, 20, 255)
FG = (236, 236, 230)
DIM = (154, 154, 146)
RULE = (58, 58, 55)
ACCENT = (124, 196, 155)
AMBER = (232, 190, 110)
BLUE = (146, 196, 222)
RED = (220, 110, 100)
MARKS = {"POISON": "PSN", "BURN": "BRN", "SLEEP": "SLP", "FREEZE": "FRZ", "PARALYZED": "PAR"}
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


class Panel:
    """Dashboard chrome. The Game Boy font is read from the ROM, as in jev-pokemon's stream panel."""

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
        self.buf = bytearray(BG * (W * H))
        self.drawn = []  # (text, color) in draw order, for tests

    def rect(self, x, y, w, h, color):
        line = bytes((*color[:3], 255)) * max(0, min(w, W - x))
        for row in range(y, min(y + h, H)):
            start = (row * W + x) * 4
            self.buf[start:start + len(line)] = line

    def text(self, x, y, text, color=FG, scale=1, right=False):
        """Draw text with its top-left (or top-right, with right=True) at x, y. Returns its width."""
        text = str(text)
        if right:
            x = max(0, x - len(text) * 8 * scale)
        text = text[:max(0, (W - x) // (8 * scale))]
        self.drawn.append((text, color))
        pixel = bytes((*color, 255)) * scale
        for i, char in enumerate(text):
            glyph = self.glyph.get(char) or self.glyph.get(char.upper())
            for gy, bits in enumerate(glyph or ()):
                for gx in range(8):
                    if bits & (0x80 >> gx):
                        for sy in range(scale):
                            offset = ((y + gy * scale + sy) * W + x + (i * 8 + gx) * scale) * 4
                            self.buf[offset:offset + len(pixel)] = pixel
        return len(text) * 8 * scale

    def draw(self, state, controller):
        self.buf = bytearray(BG * (W * H))
        self.drawn = []
        text, rect = self.text, self.rect
        fx, fy, fw, fh = FRAME
        cols = RW // 8

        mode, status = str(state.get("mode") or ""), state.get("agent_status")
        text(fx + text(fx, 10, "JEV") + 8, 10, "/ POKEMON RED", DIM)
        text(RX + RW, 10, str(status or mode).upper()[:56], AMBER if status or mode == "battle" else ACCENT, right=True)
        rect(fx, 24, RX + RW - fx, 1, RULE)

        where = str(state.get("map") or "").replace("_", " ")
        spaced = " ".join(where)
        text(fx, 31, (spaced if len(spaced) <= fw // 8 - 9 else where)[:fw // 8 - 9])
        if state:
            text(fx + fw, 31, f"X{state.get('x', 0)} Y{state.get('y', 0)}", DIM, right=True)
        for x, y, w, h in ((fx, fy, fw, 1), (fx, fy + fh - 1, fw, 1), (fx, fy, 1, fh), (fx + fw - 1, fy, 1, fh)):
            rect(x, y, w, h, RULE)
        text(fx, 342, "ESC QUIT", DIM)
        if state:
            text(fx + fw, 342, f"${state.get('money') or 0:,}", DIM, right=True)

        text(RX, 31, "JEV", ACCENT)
        if not state:
            text(RX, 48, "starting...", DIM)
            return self.buf
        text(RX + RW, 31, str(getattr(controller, "model", None) or "manual")[:cols - 5], DIM, right=True)

        milestones = story()
        total = len(milestones)
        milestone = state.get("milestone")
        if milestone:
            index = next((i for i, item in enumerate(milestones) if item.get("id") == milestone.get("id")), 0)
            story_goal = milestone.get("goal") or "Game complete!"
        else:
            index, story_goal = total, "Game complete!"
        badges = state.get("badges") or 0
        calls = getattr(controller, "calls", 0) or 0
        counters = (("BADGES", f"{badges.bit_count() if isinstance(badges, int) else 0}/8", ACCENT),
                    ("STORY", f"{index}/{total}", AMBER),
                    ("CALLS", f"{calls}" if calls < 1000 else f"{calls / 1000:.1f}K" if calls < 100_000 else f"{calls // 1000}K", FG))
        for i, (label, value, color) in enumerate(counters):
            text(RX + i * 96, 48, label, DIM)
            text(RX + i * 96, 58, value[:5], color, scale=2)
        rect(RX, 80, RW, 2, RULE)
        rect(RX, 80, RW * index // max(1, total), 2, ACCENT)

        text(RX, 92, "MILESTONE", DIM)
        for row, line in enumerate(wrap(story_goal, cols)[:3]):
            text(RX, 102 + row * 10, line)
        active = state.get("active_goal")
        goal = active.get("goal") if isinstance(active, dict) else None
        text(RX, 136, "GOAL", DIM)
        if not goal or goal == story_goal:
            text(RX, 146, "following the milestone" if goal else "none yet", DIM)
        else:
            for row, line in enumerate(wrap(goal, cols)[:3]):
                text(RX, 146 + row * 10, line)

        last = getattr(controller, "last", None) or {}
        picked = str(last.get("picked") or "")
        odds = []
        for key, value in (last.get("probabilities") or {}).items():
            try:
                odds.append((str(key), max(0.0, min(1.0, float(value)))))
            except (TypeError, ValueError):
                continue
        odds.sort(key=lambda item: item[1], reverse=True)
        text(RX + text(RX, 182, "NEXT ACTION") + 8, 182, str(last.get("purpose") or "")[:cols - 17], DIM)
        text(RX + RW, 182, "ODDS", DIM, right=True)
        for row, (key, value) in enumerate(odds[:5]):
            y, hot = 194 + row * 10, key == picked
            text(RX, y, key[:15], FG if hot else DIM)
            rect(RX + 128, y + 1, max(1, round(100 * value)), 6, ACCENT if hot else BLUE)
            text(RX + RW, y, f"{value:.2f}", FG if hot else DIM, right=True)
        if not odds:
            text(RX, 194, "no odds for this choice" if last else "waiting for jev", DIM)
        text(RX, 250, "EXECUTING", DIM)
        text(RX + 96, 250, picked[:cols - 12], ACCENT)

        text(RX, 266, "TEAM", DIM)
        text(RX + RW, 266, "HP", DIM, right=True)
        for row, mon in enumerate((state.get("party") or [])[:6]):
            y = 277 + row * 10
            hp, max_hp = int(mon.get("hp") or 0), int(mon.get("max_hp") or 0)
            share = min(1, hp / max_hp) if max_hp else 0
            text(RX, y, str(mon.get("species") or "?")[:10])
            text(RX + 82, y, f"Lv{mon.get('level', 0)}", DIM)
            rect(RX + 124, y + 2, 62, 4, RULE)
            rect(RX + 124, y + 2, round(62 * share), 4, ACCENT if share > .5 else AMBER if share > .2 else RED)
            if mon.get("status") in MARKS:
                text(RX + 190, y, MARKS[mon["status"]], AMBER)
            text(RX + RW, y, f"{hp}/{max_hp}", right=True)

        tokens = getattr(controller, "input_tokens", 0) or 0
        try:
            price = float(os.getenv("JEV_PRICE_PER_M", "0.042"))
        except ValueError:
            price = 0.042
        text(RX + text(RX, 342, "TOK", DIM) + 8, 342, f"{tokens:,}"[:11])
        text(RX + 128 + text(RX + 128, 342, "COST", DIM) + 8, 342, f"${(tokens / 1e6) * price:.3f}"[:7])
        latency = getattr(controller, "latency_ms", None)
        if latency is not None:
            text(RX + RW, 342, f"{latency}MS"[:7], right=True)
        return self.buf


class Stage:
    def __init__(self, rom, font_addr, charmap):
        self.panel = Panel(rom, font_addr, charmap)
        self.closed = False
        self.shown = 0
        self.panel_dirty = True
        self.game_buf = bytearray(160 * 144 * 4)
        self.panel_buf = bytearray(self.panel.draw({}, None))
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
            self.panel_tex = _texture(self.renderer, W, H)
        except Exception:
            self.close()
            raise
        self.game_dst = sdl2.SDL_Rect((FRAME[0] + 1) * 2, (FRAME[1] + 1) * 2, 640, 576)
        self.panel_dst = sdl2.SDL_Rect(0, 0, CANVAS_W, CANVAS_H)

    def show(self, state, controller, screen):
        self.panel_buf[:] = self.panel.draw(state, controller)
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
            sdl2.SDL_UpdateTexture(self.panel_tex, None, self.panel_ptr, W * 4)
            self.panel_dirty = False
        sdl2.SDL_SetRenderDrawColor(self.renderer, *BG)
        sdl2.SDL_RenderClear(self.renderer)
        sdl2.SDL_RenderCopy(self.renderer, self.panel_tex, None, self.panel_dst)
        sdl2.SDL_RenderCopy(self.renderer, self.game_tex, None, self.game_dst)
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

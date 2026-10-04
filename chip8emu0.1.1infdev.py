#!/usr/bin/env python3
"""
chip8 emu 0.1.1x by kondo
mGBA-inspired GUI · VB6-style menu strip · blue-on-black theme
"""

from __future__ import annotations

import array
import random
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

# ── palette (VB6 / mGBA blue-black) ──────────────────────────────────────────
BG_WINDOW = "#0a0a12"
BG_PANEL = "#101018"
BG_MENU = "#000000"
BG_BTN = "#000000"
BG_BTN_HOVER = "#1a1a28"
FG_BLUE = "#3a8cff"
FG_BLUE_DIM = "#2a6acc"
FG_STATUS = "#4aa0ff"
BORDER = "#1e3a6e"
SCREEN_OFF = "#000010"
SCREEN_ON = "#5cb3ff"

CHIP8_W, CHIP8_H = 64, 32
WIN_W, WIN_H = 600, 400
SCALE_DEFAULT = 8  # 512x256 fits inside 600x400 with chrome
CYCLES_PER_FRAME = 12
FPS = 60

# Audio defaults — classic Chip-8 ~440–540 Hz square beep
AUDIO_RATE = 44100
AUDIO_FREQ_DEFAULT = 440
AUDIO_VOLUME_DEFAULT = 0.35
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2  # int16


FONT_SET = [
    0xF0, 0x90, 0x90, 0x90, 0xF0,  # 0
    0x20, 0x60, 0x20, 0x20, 0x70,  # 1
    0xF0, 0x10, 0xF0, 0x80, 0xF0,  # 2
    0xF0, 0x10, 0xF0, 0x10, 0xF0,  # 3
    0x90, 0x90, 0xF0, 0x10, 0x10,  # 4
    0xF0, 0x80, 0xF0, 0x10, 0xF0,  # 5
    0xF0, 0x80, 0xF0, 0x90, 0xF0,  # 6
    0xF0, 0x10, 0x20, 0x40, 0x40,  # 7
    0xF0, 0x90, 0xF0, 0x90, 0xF0,  # 8
    0xF0, 0x90, 0xF0, 0x10, 0xF0,  # 9
    0xF0, 0x90, 0xF0, 0x90, 0x90,  # A
    0xE0, 0x90, 0xE0, 0x90, 0xE0,  # B
    0xF0, 0x80, 0x80, 0x80, 0xF0,  # C
    0xE0, 0x90, 0x90, 0x90, 0xE0,  # D
    0xF0, 0x80, 0xF0, 0x80, 0xF0,  # E
    0xF0, 0x80, 0xF0, 0x80, 0x80,  # F
]

# Chip-8 hex keypad → QWERTY
KEYMAP = {
    "1": 0x1, "2": 0x2, "3": 0x3, "4": 0xC,
    "q": 0x4, "w": 0x5, "e": 0x6, "r": 0xD,
    "a": 0x7, "s": 0x8, "d": 0x9, "f": 0xE,
    "z": 0xA, "x": 0x0, "c": 0xB, "v": 0xF,
}


class AudioEngine:
    """
    Chip-8 sound-timer audio engine.
    Plays a continuous square-wave beep while the sound timer is active.
    Prefers pygame.mixer; falls back to a silent stub if unavailable.
    """

    def __init__(
        self,
        frequency: int = AUDIO_FREQ_DEFAULT,
        volume: float = AUDIO_VOLUME_DEFAULT,
        enabled: bool = True,
    ) -> None:
        self.frequency = max(20, int(frequency))
        self.volume = max(0.0, min(1.0, float(volume)))
        self.enabled = enabled
        self._playing = False
        self._backend = "none"
        self._sound = None
        self._channel = None
        self._lock = threading.Lock()
        self._init_backend()

    def _init_backend(self) -> None:
        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init(
                    frequency=AUDIO_RATE,
                    size=-16,
                    channels=AUDIO_CHANNELS,
                    buffer=512,
                )
            self._backend = "pygame"
            self._rebuild_tone()
            if self._sound is None:
                raise RuntimeError("failed to build tone buffer")
        except Exception as exc:
            self._backend = "none"
            self._sound = None
            self._init_error = str(exc)
        else:
            self._init_error = ""

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def init_error(self) -> str:
        return getattr(self, "_init_error", "")

    def _square_wave_bytes(self, duration_s: float = 0.25) -> bytes:
        """Generate a mono int16 square-wave PCM buffer."""
        n = int(AUDIO_RATE * duration_s)
        amp = int(32767 * self.volume * 0.9)
        period = max(2, AUDIO_RATE // self.frequency)
        half = period // 2
        samples = array.array("h")
        for i in range(n):
            samples.append(amp if (i % period) < half else -amp)
        return samples.tobytes()

    def _rebuild_tone(self) -> None:
        if self._backend != "pygame":
            return
        import pygame

        was_playing = self._playing
        if was_playing:
            self.stop()
        pcm = self._square_wave_bytes(0.25)
        try:
            self._sound = pygame.mixer.Sound(buffer=pcm)
            self._sound.set_volume(self.volume)
        except Exception:
            # fallback: wrap as a tiny WAV in memory
            import io
            import wave

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(AUDIO_CHANNELS)
                wf.setsampwidth(AUDIO_SAMPLE_WIDTH)
                wf.setframerate(AUDIO_RATE)
                wf.writeframes(pcm)
            buf.seek(0)
            self._sound = pygame.mixer.Sound(file=buf)
            self._sound.set_volume(self.volume)
        if was_playing:
            self.start()

    def configure(
        self,
        frequency: int | None = None,
        volume: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        with self._lock:
            rebuild = False
            if frequency is not None and int(frequency) != self.frequency:
                self.frequency = max(20, int(frequency))
                rebuild = True
            if volume is not None:
                self.volume = max(0.0, min(1.0, float(volume)))
                rebuild = True
            if enabled is not None:
                self.enabled = bool(enabled)
                if not self.enabled:
                    self.stop()
            if rebuild and self.enabled:
                self._rebuild_tone()

    def start(self) -> None:
        if not self.enabled or self._backend == "none":
            return
        with self._lock:
            if self._playing:
                return
            if self._backend == "pygame" and self._sound is not None:
                # loops=-1 → continuous until stop (sound timer driven)
                self._channel = self._sound.play(loops=-1)
                self._playing = True

    def stop(self) -> None:
        with self._lock:
            if not self._playing:
                return
            if self._backend == "pygame":
                import pygame

                pygame.mixer.stop()
            self._playing = False
            self._channel = None

    def update(self, sound_active: bool) -> None:
        """Call each frame: start/stop beep from Chip-8 sound timer."""
        if not self.enabled or self._backend == "none":
            if self._playing:
                self.stop()
            return
        if sound_active:
            self.start()
        else:
            self.stop()

    def shutdown(self) -> None:
        self.stop()
        if self._backend == "pygame":
            try:
                import pygame

                if pygame.mixer.get_init():
                    pygame.mixer.quit()
            except Exception:
                pass
            self._backend = "none"


class Chip8:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.memory = [0] * 4096
        self.V = [0] * 16
        self.I = 0
        self.pc = 0x200
        self.stack: list[int] = []
        self.delay_timer = 0
        self.sound_timer = 0
        self.display = [0] * (CHIP8_W * CHIP8_H)
        self.keys = [0] * 16
        self.draw_flag = False
        self.waiting_key = False
        self.wait_reg = 0
        self.memory[0x50:0x50 + len(FONT_SET)] = FONT_SET
        self.rom_name = ""

    def load_rom(self, path: str | Path) -> None:
        data = Path(path).read_bytes()
        if len(data) > 4096 - 0x200:
            raise ValueError("ROM too large for Chip-8 memory")
        self.reset()
        self.memory[0x200:0x200 + len(data)] = list(data)
        self.rom_name = Path(path).name

    def set_key(self, key: int, pressed: bool) -> None:
        if not 0 <= key <= 0xF:
            return
        self.keys[key] = 1 if pressed else 0
        if pressed and self.waiting_key:
            self.V[self.wait_reg] = key
            self.waiting_key = False

    def tick_timers(self) -> bool:
        beep = False
        if self.delay_timer > 0:
            self.delay_timer -= 1
        if self.sound_timer > 0:
            self.sound_timer -= 1
            beep = self.sound_timer > 0
        return beep

    def cycle(self) -> None:
        if self.waiting_key:
            return
        opcode = (self.memory[self.pc] << 8) | self.memory[self.pc + 1]
        self.pc = (self.pc + 2) & 0xFFF
        self._exec(opcode)

    def _exec(self, op: int) -> None:
        nnn = op & 0x0FFF
        n = op & 0x000F
        x = (op & 0x0F00) >> 8
        y = (op & 0x00F0) >> 4
        kk = op & 0x00FF
        hi = op & 0xF000

        if op == 0x00E0:
            self.display = [0] * (CHIP8_W * CHIP8_H)
            self.draw_flag = True
        elif op == 0x00EE:
            self.pc = self.stack.pop() if self.stack else self.pc
        elif hi == 0x1000:
            self.pc = nnn
        elif hi == 0x2000:
            self.stack.append(self.pc)
            self.pc = nnn
        elif hi == 0x3000:
            if self.V[x] == kk:
                self.pc = (self.pc + 2) & 0xFFF
        elif hi == 0x4000:
            if self.V[x] != kk:
                self.pc = (self.pc + 2) & 0xFFF
        elif hi == 0x5000 and n == 0:
            if self.V[x] == self.V[y]:
                self.pc = (self.pc + 2) & 0xFFF
        elif hi == 0x6000:
            self.V[x] = kk
        elif hi == 0x7000:
            self.V[x] = (self.V[x] + kk) & 0xFF
        elif hi == 0x8000:
            self._alu(x, y, n)
        elif hi == 0x9000 and n == 0:
            if self.V[x] != self.V[y]:
                self.pc = (self.pc + 2) & 0xFFF
        elif hi == 0xA000:
            self.I = nnn
        elif hi == 0xB000:
            self.pc = (nnn + self.V[0]) & 0xFFF
        elif hi == 0xC000:
            self.V[x] = random.randint(0, 255) & kk
        elif hi == 0xD000:
            self._draw(x, y, n)
        elif hi == 0xE000:
            if kk == 0x9E and self.keys[self.V[x] & 0xF]:
                self.pc = (self.pc + 2) & 0xFFF
            elif kk == 0xA1 and not self.keys[self.V[x] & 0xF]:
                self.pc = (self.pc + 2) & 0xFFF
        elif hi == 0xF000:
            self._fx(x, kk)

    def _alu(self, x: int, y: int, n: int) -> None:
        if n == 0x0:
            self.V[x] = self.V[y]
        elif n == 0x1:
            self.V[x] |= self.V[y]
        elif n == 0x2:
            self.V[x] &= self.V[y]
        elif n == 0x3:
            self.V[x] ^= self.V[y]
        elif n == 0x4:
            total = self.V[x] + self.V[y]
            self.V[x] = total & 0xFF
            self.V[0xF] = 1 if total > 0xFF else 0
        elif n == 0x5:
            borrow = 1 if self.V[x] >= self.V[y] else 0
            self.V[x] = (self.V[x] - self.V[y]) & 0xFF
            self.V[0xF] = borrow
        elif n == 0x6:
            lsb = self.V[x] & 1
            self.V[x] >>= 1
            self.V[0xF] = lsb
        elif n == 0x7:
            borrow = 1 if self.V[y] >= self.V[x] else 0
            self.V[x] = (self.V[y] - self.V[x]) & 0xFF
            self.V[0xF] = borrow
        elif n == 0xE:
            msb = (self.V[x] & 0x80) >> 7
            self.V[x] = (self.V[x] << 1) & 0xFF
            self.V[0xF] = msb

    def _draw(self, x: int, y: int, n: int) -> None:
        vx, vy = self.V[x] % CHIP8_W, self.V[y] % CHIP8_H
        self.V[0xF] = 0
        for row in range(n):
            if vy + row >= CHIP8_H:
                break
            sprite = self.memory[(self.I + row) & 0xFFF]
            for col in range(8):
                if vx + col >= CHIP8_W:
                    break
                if sprite & (0x80 >> col):
                    idx = (vy + row) * CHIP8_W + (vx + col)
                    if self.display[idx]:
                        self.V[0xF] = 1
                    self.display[idx] ^= 1
        self.draw_flag = True

    def _fx(self, x: int, kk: int) -> None:
        if kk == 0x07:
            self.V[x] = self.delay_timer
        elif kk == 0x0A:
            self.waiting_key = True
            self.wait_reg = x
        elif kk == 0x15:
            self.delay_timer = self.V[x]
        elif kk == 0x18:
            self.sound_timer = self.V[x]
        elif kk == 0x1E:
            self.I = (self.I + self.V[x]) & 0xFFFF
        elif kk == 0x29:
            self.I = 0x50 + (self.V[x] & 0xF) * 5
        elif kk == 0x33:
            val = self.V[x]
            self.memory[self.I & 0xFFF] = val // 100
            self.memory[(self.I + 1) & 0xFFF] = (val // 10) % 10
            self.memory[(self.I + 2) & 0xFFF] = val % 10
        elif kk == 0x55:
            for i in range(x + 1):
                self.memory[(self.I + i) & 0xFFF] = self.V[i]
        elif kk == 0x65:
            for i in range(x + 1):
                self.V[i] = self.memory[(self.I + i) & 0xFFF]


class MenuButton(tk.Label):
    """VB6-style flat menu strip button — black bg, blue text."""

    def __init__(self, master, text: str, command=None, **kw):
        super().__init__(
            master,
            text=text,
            bg=BG_BTN,
            fg=FG_BLUE,
            activebackground=BG_BTN_HOVER,
            activeforeground=FG_BLUE,
            font=("Tahoma", 9),
            padx=10,
            pady=4,
            cursor="hand2",
            **kw,
        )
        self._cmd = command
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)

    def _enter(self, _e=None):
        self.configure(bg=BG_BTN_HOVER, fg="#6ab0ff")

    def _leave(self, _e=None):
        self.configure(bg=BG_BTN, fg=FG_BLUE)

    def _click(self, _e=None):
        if self._cmd:
            self._cmd()


class Chip8App:
    TITLE = "chip8 emu 0.1.1x by kondo"

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(self.TITLE)
        self.root.configure(bg=BG_WINDOW)
        self.root.resizable(False, False)
        self._center_window(WIN_W, WIN_H)

        self.cpu = Chip8()
        self.running = False
        self.paused = False
        self.scale = SCALE_DEFAULT
        self.speed = CYCLES_PER_FRAME
        self.beep_enabled = True
        self.audio_freq = AUDIO_FREQ_DEFAULT
        self.audio_volume = AUDIO_VOLUME_DEFAULT
        self.audio = AudioEngine(
            frequency=self.audio_freq,
            volume=self.audio_volume,
            enabled=self.beep_enabled,
        )
        self._rom_path = ""
        self._photo = None

        self._build_ui()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_frame()
        if self.audio.backend == "pygame":
            self.status_var.set("ready · audio engine online (pygame)")
        else:
            self.status_var.set("ready · audio unavailable · install pygame for sound")


    def _center_window(self, w: int, h: int) -> None:
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # VB6 / mGBA menu strip
        menubar = tk.Frame(self.root, bg=BG_MENU, highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER)
        menubar.pack(fill=tk.X, side=tk.TOP)

        MenuButton(menubar, "Play", self.play).pack(side=tk.LEFT)
        tk.Frame(menubar, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, pady=2)
        MenuButton(menubar, "ROM", self.load_rom).pack(side=tk.LEFT)
        MenuButton(menubar, "Load Game", self.load_rom).pack(side=tk.LEFT)
        tk.Frame(menubar, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, pady=2)
        MenuButton(menubar, "Exit", self.root.destroy).pack(side=tk.LEFT)
        tk.Frame(menubar, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, pady=2)
        MenuButton(menubar, "Help", self.show_help).pack(side=tk.LEFT)
        MenuButton(menubar, "Settings", self.show_settings).pack(side=tk.LEFT)
        MenuButton(menubar, "Control", self.show_controls).pack(side=tk.LEFT)
        MenuButton(menubar, "About", self.show_about).pack(side=tk.LEFT)

        # status bar first so display fills remaining space
        status = tk.Frame(self.root, bg=BG_MENU, highlightthickness=1,
                          highlightbackground=BORDER)
        status.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_var = tk.StringVar(value="ready · press Play to load a game")
        tk.Label(
            status, textvariable=self.status_var, bg=BG_MENU, fg=FG_STATUS,
            font=("Tahoma", 8), anchor="w", padx=8, pady=3,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.state_var = tk.StringVar(value="■ STOPPED")
        tk.Label(
            status, textvariable=self.state_var, bg=BG_MENU, fg=FG_BLUE,
            font=("Tahoma", 8, "bold"), padx=8, pady=3,
        ).pack(side=tk.RIGHT)

        # display bezel — centered in remaining area
        stage = tk.Frame(self.root, bg=BG_WINDOW)
        stage.pack(fill=tk.BOTH, expand=True)

        bezel = tk.Frame(stage, bg=BG_PANEL, padx=8, pady=8,
                         highlightthickness=1, highlightbackground=BORDER)
        bezel.place(relx=0.5, rely=0.5, anchor="center")

        self.canvas = tk.Canvas(
            bezel,
            width=CHIP8_W * self.scale,
            height=CHIP8_H * self.scale,
            bg=SCREEN_OFF,
            highlightthickness=1,
            highlightbackground=FG_BLUE_DIM,
            cursor="arrow",
        )
        self.canvas.pack()
        self._blit()

    def _bind_keys(self) -> None:
        self.root.bind("<KeyPress>", self._on_key_down)
        self.root.bind("<KeyRelease>", self._on_key_up)
        self.root.bind("<Control-o>", lambda e: self.load_rom())
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<space>", lambda e: self.play())

    # ── actions ──────────────────────────────────────────────────────────────
    def load_rom(self, path: str | None = None) -> bool:
        """Open a ROM file dialog (or use path) and start playing. Returns True on success."""
        if not path:
            path = filedialog.askopenfilename(
                parent=self.root,
                title="Load Chip-8 ROM",
                filetypes=[
                    ("Chip-8 ROMs", "*.ch8"),
                    ("Chip-8 ROMs", "*.c8"),
                    ("Chip-8 ROMs", "*.rom"),
                    ("Chip-8 ROMs", "*.bin"),
                    ("All files", "*.*"),
                ],
            )
        if not path:
            self.status_var.set("load cancelled")
            return False
        try:
            self.cpu.load_rom(path)
            self._rom_path = path
            self.running = True
            self.paused = False
            self._set_state("▶ PLAYING")
            self.status_var.set(f"playing · {self.cpu.rom_name}")
            # run a burst so the first DRW lands before the next frame tick
            for _ in range(self.speed * 8):
                self.cpu.cycle()
            self.cpu.draw_flag = True
            self._blit()
            self.root.focus_force()
            return True
        except Exception as exc:
            messagebox.showerror(self.TITLE, f"Failed to load ROM:\n{exc}")
            self.status_var.set("load failed")
            return False

    def play(self) -> None:
        """Play: always load a game if none is loaded, otherwise pause/resume."""
        if not self._rom_path and not self.cpu.rom_name:
            # no game yet — open picker and start
            self.load_rom()
            return
        if not self.running:
            # ROM in memory but stopped — restart from loaded path if needed
            if self._rom_path:
                self.load_rom(self._rom_path)
            else:
                self.load_rom()
            return
        if self.paused:
            self.paused = False
            self._set_state("▶ PLAYING")
            self.status_var.set(f"resumed · {self.cpu.rom_name}")
        else:
            self.paused = True
            self.audio.stop()
            self._set_state("❚❚ PAUSED")
            self.status_var.set(f"paused · {self.cpu.rom_name}")

    def _on_close(self) -> None:
        self.audio.shutdown()
        self.root.destroy()

    def _set_state(self, text: str) -> None:
        self.state_var.set(text)

    def show_help(self) -> None:
        messagebox.showinfo(
            "Help — " + self.TITLE,
            "Chip-8 Emulator Quick Help\n\n"
            "• Play — open a ROM and start (or pause/resume)\n"
            "• ROM / Load Game — open a .ch8 / .c8 / .rom file\n"
            "• Space — same as Play\n"
            "• Ctrl+O — load ROM\n"
            "• Esc — exit\n\n"
            "See Control for the keypad layout.",
        )

    def show_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.configure(bg=BG_WINDOW)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frame = tk.Frame(win, bg=BG_PANEL, padx=16, pady=14,
                         highlightthickness=1, highlightbackground=BORDER)
        frame.pack(padx=8, pady=8)

        tk.Label(frame, text="Settings", bg=BG_PANEL, fg=FG_BLUE,
                 font=("Tahoma", 11, "bold")).grid(row=0, column=0, columnspan=2,
                                                   sticky="w", pady=(0, 10))

        tk.Label(frame, text="Scale", bg=BG_PANEL, fg=FG_BLUE,
                 font=("Tahoma", 9)).grid(row=1, column=0, sticky="w", pady=4)
        scale_var = tk.IntVar(value=self.scale)
        tk.Spinbox(
            frame, from_=4, to=20, textvariable=scale_var, width=6,
            bg=BG_BTN, fg=FG_BLUE, buttonbackground=BG_BTN,
            highlightbackground=BORDER, insertbackground=FG_BLUE,
        ).grid(row=1, column=1, sticky="e", pady=4)

        tk.Label(frame, text="Speed (cycles/frame)", bg=BG_PANEL, fg=FG_BLUE,
                 font=("Tahoma", 9)).grid(row=2, column=0, sticky="w", pady=4)
        speed_var = tk.IntVar(value=self.speed)
        tk.Spinbox(
            frame, from_=1, to=40, textvariable=speed_var, width=6,
            bg=BG_BTN, fg=FG_BLUE, buttonbackground=BG_BTN,
            highlightbackground=BORDER, insertbackground=FG_BLUE,
        ).grid(row=2, column=1, sticky="e", pady=4)

        tk.Label(frame, text="Audio frequency (Hz)", bg=BG_PANEL, fg=FG_BLUE,
                 font=("Tahoma", 9)).grid(row=3, column=0, sticky="w", pady=4)
        freq_var = tk.IntVar(value=self.audio_freq)
        tk.Spinbox(
            frame, from_=120, to=2000, textvariable=freq_var, width=6,
            bg=BG_BTN, fg=FG_BLUE, buttonbackground=BG_BTN,
            highlightbackground=BORDER, insertbackground=FG_BLUE,
        ).grid(row=3, column=1, sticky="e", pady=4)

        tk.Label(frame, text="Audio volume (0–100)", bg=BG_PANEL, fg=FG_BLUE,
                 font=("Tahoma", 9)).grid(row=4, column=0, sticky="w", pady=4)
        vol_var = tk.IntVar(value=int(self.audio_volume * 100))
        tk.Spinbox(
            frame, from_=0, to=100, textvariable=vol_var, width=6,
            bg=BG_BTN, fg=FG_BLUE, buttonbackground=BG_BTN,
            highlightbackground=BORDER, insertbackground=FG_BLUE,
        ).grid(row=4, column=1, sticky="e", pady=4)

        beep_var = tk.BooleanVar(value=self.beep_enabled)
        tk.Checkbutton(
            frame, text="Enable sound timer beep", variable=beep_var,
            bg=BG_PANEL, fg=FG_BLUE, selectcolor=BG_BTN,
            activebackground=BG_PANEL, activeforeground=FG_BLUE,
            font=("Tahoma", 9),
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=8)

        backend_lbl = f"engine: {self.audio.backend}"
        tk.Label(frame, text=backend_lbl, bg=BG_PANEL, fg=FG_BLUE_DIM,
                 font=("Tahoma", 8)).grid(row=6, column=0, columnspan=2, sticky="w")

        def apply():
            self.scale = int(scale_var.get())
            self.speed = int(speed_var.get())
            self.beep_enabled = beep_var.get()
            self.audio_freq = int(freq_var.get())
            self.audio_volume = max(0.0, min(1.0, int(vol_var.get()) / 100.0))
            self.audio.configure(
                frequency=self.audio_freq,
                volume=self.audio_volume,
                enabled=self.beep_enabled,
            )
            self.canvas.config(
                width=CHIP8_W * self.scale,
                height=CHIP8_H * self.scale,
            )
            self._blit()
            self.status_var.set(
                f"settings · scale={self.scale} speed={self.speed} "
                f"audio={self.audio_freq}Hz/{int(self.audio_volume * 100)}%"
            )
            win.destroy()

        def test_beep():
            self.audio.configure(
                frequency=int(freq_var.get()),
                volume=max(0.0, min(1.0, int(vol_var.get()) / 100.0)),
                enabled=True,
            )
            self.audio.start()
            win.after(200, self.audio.stop)

        btn_row = tk.Frame(frame, bg=BG_PANEL)
        btn_row.grid(row=7, column=0, columnspan=2, pady=(10, 0))
        MenuButton(btn_row, "  Test Beep  ", test_beep).pack(side=tk.LEFT, padx=(0, 8))
        MenuButton(btn_row, "  Apply  ", apply).pack(side=tk.LEFT)

    def show_controls(self) -> None:
        messagebox.showinfo(
            "Control — " + self.TITLE,
            "Chip-8 Hex Keypad  →  Keyboard\n\n"
            "  1 2 3 C     →     1 2 3 4\n"
            "  4 5 6 D     →     Q W E R\n"
            "  7 8 9 E     →     A S D F\n"
            "  A 0 B F     →     Z X C V\n\n"
            "Space = Play / Pause\n"
            "Ctrl+O = Load Game\n"
            "Esc = Exit",
        )

    def show_about(self) -> None:
        messagebox.showinfo(
            "About",
            f"{self.TITLE}\n\n"
            "A Chip-8 interpreter with an mGBA-inspired\n"
            "VB6-style blue-on-black menu strip.\n\n"
            "infdev build · Python / tkinter + audio engine\n"
            f"audio backend: {self.audio.backend}",
        )

    # ── input ────────────────────────────────────────────────────────────────
    def _on_key_down(self, event: tk.Event) -> None:
        key = KEYMAP.get((event.keysym or "").lower())
        if key is not None:
            self.cpu.set_key(key, True)

    def _on_key_up(self, event: tk.Event) -> None:
        key = KEYMAP.get((event.keysym or "").lower())
        if key is not None:
            self.cpu.set_key(key, False)

    # ── render / loop ────────────────────────────────────────────────────────
    def _blit(self) -> None:
        """Draw Chip-8 framebuffer with canvas rectangles (reliable on macOS Tk)."""
        s = self.scale
        self.canvas.delete("fb")
        self.canvas.create_rectangle(
            0, 0, CHIP8_W * s, CHIP8_H * s,
            fill=SCREEN_OFF, outline="", tags="fb",
        )
        # merge horizontal runs so we create far fewer canvas items
        for y in range(CHIP8_H):
            row = y * CHIP8_W
            x = 0
            while x < CHIP8_W:
                if not self.cpu.display[row + x]:
                    x += 1
                    continue
                x0 = x
                while x < CHIP8_W and self.cpu.display[row + x]:
                    x += 1
                self.canvas.create_rectangle(
                    x0 * s, y * s, x * s, (y + 1) * s,
                    fill=SCREEN_ON, outline="", tags="fb",
                )

    def _schedule_frame(self) -> None:
        if self.running and not self.paused:
            for _ in range(self.speed):
                self.cpu.cycle()
            sound_on = self.cpu.tick_timers()
            self.audio.update(sound_on)
            if self.cpu.draw_flag:
                self._blit()
                self.cpu.draw_flag = False
        else:
            self.audio.stop()
        self.root.after(1000 // FPS, self._schedule_frame)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    Chip8App().run()

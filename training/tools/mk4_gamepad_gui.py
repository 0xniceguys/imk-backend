#!/usr/bin/env python3
"""
mk4_gamepad_gui.py
──────────────────
Virtual N64 Gamepad GUI for manual MK4 navigation testing.

Writes button/stick state to /tmp/mk4_ctrl (same mmap the emulator reads).
Use this to manually verify that inputs reach the game correctly.

Usage:
    python3 training/tools/mk4_gamepad_gui.py

Keyboard shortcuts also work while the window is focused:
    Arrow keys  → D-pad
    Z           → A button
    X           → B button
    Enter       → Start
    Space       → Release all
"""
import tkinter as tk
import struct
import mmap
import os
import time

CTRL_FILE = '/tmp/mk4_ctrl'

# N64 button bitmasks (same as plugin.c)
BTN_RIGHT  = (1 << 0)
BTN_LEFT   = (1 << 1)
BTN_DOWN   = (1 << 2)
BTN_UP     = (1 << 3)
BTN_START  = (1 << 4)
BTN_B      = (1 << 6)
BTN_A      = (1 << 7)

# ── Controller mmap ──────────────────────────────────────────────────────────
class N64Controller:
    def __init__(self):
        if not os.path.exists(CTRL_FILE):
            with open(CTRL_FILE, 'w+b') as f:
                f.write(b'\x00' * 4)
        self._f   = open(CTRL_FILE, 'r+b')
        self._mem = mmap.mmap(self._f.fileno(), 4)
        self._buttons = 0
        self._x = 0
        self._y = 0
        self._flush()

    def _flush(self):
        self._mem.seek(0)
        self._mem.write(struct.pack('<Hbb',
            self._buttons & 0xFFFF,
            self._x & 0xFF,
            self._y & 0xFF))
        self._mem.flush()

    def press(self, btn):
        self._buttons |= btn
        self._flush()

    def release(self, btn=None):
        if btn is None:
            self._buttons = 0
            self._x = 0
            self._y = 0
        else:
            self._buttons &= ~btn
        self._flush()

    def close(self):
        self.release()
        self._mem.close()
        self._f.close()

# ── GUI ───────────────────────────────────────────────────────────────────────
class GamepadGUI:
    BTN_COLOR_IDLE  = '#2d2d2d'
    BTN_COLOR_PRESS = '#ff5500'
    BTN_COLOR_DPAD  = '#1a3a5c'
    BTN_COLOR_DPAD_PRESS = '#2196F3'
    BG = '#0d0d0d'
    FG = '#e0e0e0'

    def __init__(self, root):
        self.root = root
        self.ctrl = N64Controller()
        self._held = set()

        root.title('MK4 Virtual N64 Gamepad')
        root.configure(bg=self.BG)
        root.resizable(False, False)

        self._build_ui()
        self._bind_keys()
        self._status_var = tk.StringVar(value='Ready — emulator input active on /tmp/mk4_ctrl')
        tk.Label(root, textvariable=self._status_var,
                 bg=self.BG, fg='#888', font=('Courier', 10)).pack(pady=(4,8))

        root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── Layout ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = self.root

        tk.Label(root, text='MK4 Virtual N64 Gamepad',
                 bg=self.BG, fg='#ff5500',
                 font=('Helvetica', 15, 'bold')).pack(pady=(12,6))

        main = tk.Frame(root, bg=self.BG)
        main.pack(padx=20, pady=4)

        # ── Left: D-pad ──────────────────────────────────────────────────
        dpad_frame = tk.LabelFrame(main, text='D-Pad  (Arrow keys)',
                                   bg=self.BG, fg=self.FG,
                                   font=('Helvetica',10,'bold'))
        dpad_frame.grid(row=0, column=0, padx=16, pady=8)

        self._btns = {}
        dpad_cfg = [
            ('↑',  BTN_UP,    1, 1),
            ('↓',  BTN_DOWN,  3, 1),
            ('←',  BTN_LEFT,  2, 0),
            ('→',  BTN_RIGHT, 2, 2),
        ]
        for label, btn, row, col in dpad_cfg:
            b = self._make_button(dpad_frame, label, btn,
                                  color=self.BTN_COLOR_DPAD,
                                  active=self.BTN_COLOR_DPAD_PRESS,
                                  size=3)
            b.grid(row=row, column=col, padx=4, pady=4)
            self._btns[btn] = b

        # ── Right: Action buttons ─────────────────────────────────────────
        action_frame = tk.LabelFrame(main, text='Buttons  (Z=A  X=B  Enter=Start)',
                                     bg=self.BG, fg=self.FG,
                                     font=('Helvetica',10,'bold'))
        action_frame.grid(row=0, column=1, padx=16, pady=8)

        action_cfg = [
            ('A',     BTN_A,     0, 1, '#c0392b', '#e74c3c'),
            ('B',     BTN_B,     1, 0, '#27ae60', '#2ecc71'),
            ('Start', BTN_START, 1, 2, '#8e44ad', '#9b59b6'),
        ]
        for label, btn, row, col, idle, active in action_cfg:
            b = self._make_button(action_frame, label, btn,
                                  color=idle, active=active, size=4)
            b.grid(row=row, column=col, padx=6, pady=6)
            self._btns[btn] = b

        # ── Release all button ────────────────────────────────────────────
        rel = tk.Button(root, text='RELEASE ALL  (Space)',
                        command=self._release_all,
                        bg='#444', fg='#fff',
                        font=('Helvetica',10,'bold'),
                        activebackground='#666',
                        relief='flat', padx=12, pady=6,
                        cursor='hand2')
        rel.pack(pady=(4,2))

        # ── Macro row ─────────────────────────────────────────────────────
        macro_frame = tk.LabelFrame(root, text='Macros',
                                    bg=self.BG, fg=self.FG,
                                    font=('Helvetica',10,'bold'))
        macro_frame.pack(padx=20, pady=6, fill='x')

        macros = [
            ('Char Select: Scorpion\n(1 Down)', self._macro_scorpion),
            ('Char Select: Sonya\n(2D + 4R)',  self._macro_sonya),
            ('Ladder: Max Difficulty\n(4 Right)', self._macro_max_diff),
        ]
        for i, (label, cmd) in enumerate(macros):
            tk.Button(macro_frame, text=label, command=cmd,
                      bg='#1a3a5c', fg='#fff',
                      font=('Helvetica',9),
                      activebackground='#2196F3',
                      relief='flat', padx=8, pady=4,
                      cursor='hand2', wraplength=140).grid(
                          row=0, column=i, padx=8, pady=6)

    def _make_button(self, parent, label, btn_mask,
                     color, active, size=3):
        sz = size * 16
        canvas = tk.Canvas(parent, width=sz, height=sz,
                            bg=color, highlightthickness=0,
                            cursor='hand2')
        canvas.create_text(sz//2, sz//2, text=label,
                            fill='white',
                            font=('Helvetica', max(10, size*3), 'bold'))
        canvas.bind('<ButtonPress-1>',
                    lambda e, m=btn_mask, c=canvas, a=active, d=color:
                        self._on_press(m, c, a))
        canvas.bind('<ButtonRelease-1>',
                    lambda e, m=btn_mask, c=canvas, a=active, d=color:
                        self._on_release(m, c, d))
        return canvas

    # ── Events ──────────────────────────────────────────────────────────────
    def _on_press(self, btn, canvas, active_color):
        self._held.add(btn)
        self.ctrl.press(btn)
        canvas.configure(bg=active_color)
        self._set_status(f'PRESSED  0x{btn:04X}  buttons=0x{self.ctrl._buttons:04X}')

    def _on_release(self, btn, canvas, idle_color):
        self._held.discard(btn)
        self.ctrl.release(btn)
        canvas.configure(bg=idle_color)
        self._set_status(f'RELEASED 0x{btn:04X}  buttons=0x{self.ctrl._buttons:04X}')

    def _release_all(self):
        self.ctrl.release()
        self._held.clear()
        for btn, canvas in self._btns.items():
            # restore original color
            if btn in (BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT):
                canvas.configure(bg=self.BTN_COLOR_DPAD)
            elif btn == BTN_A:
                canvas.configure(bg='#c0392b')
            elif btn == BTN_B:
                canvas.configure(bg='#27ae60')
            elif btn == BTN_START:
                canvas.configure(bg='#8e44ad')
        self._set_status('All released')

    def _bind_keys(self):
        kmap = {
            'Up':    BTN_UP,    'Down':  BTN_DOWN,
            'Left':  BTN_LEFT,  'Right': BTN_RIGHT,
            'z':     BTN_A,     'Z':     BTN_A,
            'x':     BTN_B,     'X':     BTN_B,
            'Return': BTN_START,
        }
        for key, btn in kmap.items():
            self.root.bind(f'<KeyPress-{key}>',
                lambda e, m=btn: self._key_press(m))
            self.root.bind(f'<KeyRelease-{key}>',
                lambda e, m=btn: self._key_release(m))
        self.root.bind('<space>', lambda e: self._release_all())

    def _key_press(self, btn):
        if btn not in self._held:
            self.ctrl.press(btn)
            self._held.add(btn)
            if btn in self._btns:
                active = (self.BTN_COLOR_DPAD_PRESS
                          if btn in (BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT)
                          else '#e74c3c')
                self._btns[btn].configure(bg=active)
            self._set_status(f'KEY DOWN 0x{btn:04X}')

    def _key_release(self, btn):
        self._held.discard(btn)
        self.ctrl.release(btn)
        if btn in self._btns:
            idle = (self.BTN_COLOR_DPAD
                    if btn in (BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT)
                    else '#c0392b' if btn == BTN_A
                    else '#27ae60' if btn == BTN_B
                    else '#8e44ad')
            self._btns[btn].configure(bg=idle)
        self._set_status(f'KEY UP   0x{btn:04X}')

    # ── Macros ───────────────────────────────────────────────────────────────
    def _tap(self, btn, hold=0.08, settle=0.18):
        self.ctrl.press(btn)
        time.sleep(hold)
        self.ctrl.release()
        time.sleep(settle)

    def _macro_scorpion(self):
        self._set_status('Macro: Scorpion (1 Down)…')
        self._tap(BTN_DOWN)
        self._set_status('Macro done: Scorpion')

    def _macro_sonya(self):
        self._set_status('Macro: Sonya (2 Down + 4 Right)…')
        self._tap(BTN_DOWN)
        self._tap(BTN_DOWN)
        for _ in range(4):
            self._tap(BTN_RIGHT)
        self._set_status('Macro done: Sonya')

    def _macro_max_diff(self):
        self._set_status('Macro: Max difficulty (4 Right)…')
        for _ in range(4):
            self._tap(BTN_RIGHT)
        self._set_status('Macro done: max difficulty')

    def _set_status(self, msg):
        self._status_var.set(msg)
        self.root.update_idletasks()

    def _on_close(self):
        self.ctrl.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = GamepadGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()

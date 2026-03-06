#!/usr/bin/env python3
"""
mk4_controller_debug.py  —  MK4 All-in-One Debug Tool
"""
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog
import struct, mmap, os, time, threading, subprocess, glob, sys, shutil

# ── Paths ─────────────────────────────────────────────────────────────────────
N64_ROOT     = '/Users/ichiropractic/code/n64'
P1_FILE      = '/tmp/mk4_ctrl'
P2_FILE      = '/tmp/mk4_ctrl_p2'
SOCK_BASE    = f'{N64_ROOT}/training/data/bridge/mk4-visible'
ROM          = f'{N64_ROOT}/Mortal Kombat 4 (USA).z64'
INST         = 'reverse-visible'
CFG_DIR      = f'{N64_ROOT}/.m64p/instances/{INST}/config'
M64P_BIN     = f'{N64_ROOT}/vendor/mupen64plus-ui-console/projects/unix/mupen64plus'
CORELIB      = f'{N64_ROOT}/vendor/mupen64plus-core/projects/unix/libmupen64plus.dylib'
CUSTOM_INPUT = f'{N64_ROOT}/vendor/n64train-input/n64train-input.dylib'
STATE_DIR    = f'{N64_ROOT}/training/data/savestates/mk4_arcade'
SHOT_DIR     = f'{N64_ROOT}/.m64p/data/screenshots'
INPUT_LOG    = '/tmp/n64train_input.log'
SRC_PATH     = f'{N64_ROOT}/training/src'
sys.path.insert(0, SRC_PATH)

# ── Colors ────────────────────────────────────────────────────────────────────
BG       = '#1a1a2e'      # dark navy
BG2      = '#16213e'      # slightly lighter
PANEL    = '#0f3460'      # panel bg
ACCENT   = '#e94560'      # red accent
BTN_FG   = '#ffffff'      # white text on all buttons
BTN_BG   = '#0f3460'      # button bg
BTN_HOV  = '#533483'      # hover
GREEN    = '#06d6a0'
YELLOW   = '#ffd166'
RED      = '#ef476f'
GRAY     = '#8a8a9a'
FONT     = ('Segoe UI', 10)
FONT_B   = ('Segoe UI', 10, 'bold')
FONT_H   = ('Segoe UI', 13, 'bold')
MONO     = ('Courier', 9)

# ── Button bitmasks ───────────────────────────────────────────────────────────
BTN_RIGHT=1<<0; BTN_LEFT=1<<1; BTN_DOWN=1<<2; BTN_UP=1<<3
BTN_START=1<<4; BTN_B=1<<6;   BTN_A=1<<7

def mk_btn(parent, text, command, bg=BTN_BG, fg=BTN_FG,
           font=FONT_B, padx=10, pady=6, width=None, **kw):
    b = tk.Button(parent, text=text, command=command,
                  bg=bg, fg=fg, font=font,
                  activebackground=BTN_HOV, activeforeground='white',
                  relief='flat', padx=padx, pady=pady,
                  cursor='hand2', **(({'width': width} if width else {})), **kw)
    b.bind('<Enter>', lambda e: b.configure(bg=BTN_HOV))
    b.bind('<Leave>', lambda e: b.configure(bg=bg))
    return b

# ── Controller mmap ───────────────────────────────────────────────────────────
class Controller:
    def __init__(self, path):
        self.path = path
        self._buttons = 0; self._x = 0; self._y = 0
        if not os.path.exists(path):
            with open(path, 'w+b') as f: f.write(b'\x00'*4)
        self._f = open(path, 'r+b')
        self._m = mmap.mmap(self._f.fileno(), 4)
        self._flush()
    def _flush(self):
        self._m.seek(0)
        self._m.write(struct.pack('<Hbb', self._buttons&0xFFFF, self._x&0xFF, self._y&0xFF))
        self._m.flush()
    def press(self, btn):   self._buttons |= btn;  self._flush()
    def release(self, btn=None):
        if btn is None: self._buttons=0; self._x=0; self._y=0
        else: self._buttons &= ~btn
        self._flush()
    def close(self): self.release(); self._m.close(); self._f.close()
    @property
    def state(self): return f'0x{self._buttons:04X}'

# ── Bridge helpers ────────────────────────────────────────────────────────────
_instances = {}   # instance_id -> {proc, sock}

def _make_bridge_cmd(sock, ctrl_p1=P1_FILE, ctrl_p2=P2_FILE):
    env = os.environ.copy()
    env['N64TRAIN_CTRL_P1'] = ctrl_p1
    env['N64TRAIN_CTRL_P2'] = ctrl_p2   # ← tells the plugin to register P2
    return [
        'python3', f'{N64_ROOT}/training/scripts/run_bridge_server.py',
        '--socket-path', sock, '--instance-id', INST,
        '--memory-reader', 'debugger-dump', '--rom-path', ROM,
        '--debugger-ui-binary', M64P_BIN, '--debugger-corelib', CORELIB,
        '--debugger-plugindir', '/opt/homebrew/lib/mupen64plus',
        '--debugger-configdir', CFG_DIR,
        '--debugger-datadir', '/opt/homebrew/share/mupen64plus',
        '--debugger-gfx-plugin',   'mupen64plus-video-rice.dylib',
        '--debugger-audio-plugin', 'mupen64plus-audio-sdl.dylib',
        '--debugger-input-plugin', CUSTOM_INPUT,
        '--debugger-rsp-plugin',   'mupen64plus-rsp-hle.dylib',
        '--debugger-emumode', '0',
    ], env

def _get_bridge(sock):
    from n64train.runtime.bridge import SocketEmulatorBridge
    from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
    b = SocketEmulatorBridge(sock, timeout_sec=5)
    h = Mk4BridgeHelper(b)
    return b, h

def launch_instance(name, sock, status_cb):
    os.makedirs(os.path.dirname(sock), exist_ok=True)
    try: os.remove(sock)
    except: pass
    open(INPUT_LOG, 'w').close()
    log = open(f'/tmp/mk4_bridge_{name}.log', 'w')
    cmd, env = _make_bridge_cmd(sock)
    proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=env)
    _instances[name] = {'proc': proc, 'sock': sock}
    status_cb(f'⏳ [{name}] Starting…')
    deadline = time.time() + 45
    while time.time() < deadline:
        if os.path.exists(sock):
            time.sleep(2)
            status_cb(f'✅ [{name}] Ready (pid={proc.pid})')
            return True
        time.sleep(0.5)
    status_cb(f'❌ [{name}] Timed out — check /tmp/mk4_bridge_{name}.log')
    return False

def kill_instance(name, status_cb):
    info = _instances.get(name)
    if info:
        try: info['proc'].terminate()
        except: pass
        try: os.remove(info['sock'])
        except: pass
        del _instances[name]
    os.system("pkill -9 -f 'mupen64plus|run_bridge_server' 2>/dev/null")
    status_cb(f'[{name}] Killed')

# ── Controller Pad widget ─────────────────────────────────────────────────────
class ControllerPad(tk.LabelFrame):
    DPAD_I='#1e3a5f'; DPAD_P='#2196F3'
    A_I='#7b1e1e';    A_P='#e74c3c'
    B_I='#1e5a3a';    B_P='#2ecc71'
    S_I='#4a1e6a';    S_P='#9b59b6'

    def __init__(self, parent, ctrl, label, key_map, **kw):
        super().__init__(parent, text=label, bg=BG2, fg=YELLOW,
                         font=FONT_B, **kw)
        self.ctrl = ctrl; self._held = set(); self._cvs = {}; self._key_map = key_map
        self._status = tk.StringVar(value='BTN=0x0000')
        tk.Label(self, textvariable=self._status, bg=BG2, fg=GREEN,
                 font=MONO).pack(pady=2)
        self._build_dpad(); self._build_actions()
        mk_btn(self, '⛔ Release All', self.release_all,
               bg='#3a1a1a', pady=4).pack(pady=4)

    def _build_dpad(self):
        f = tk.Frame(self, bg=BG2); f.pack(pady=4)
        for lbl,btn,r,c in [('↑',BTN_UP,0,1),('←',BTN_LEFT,1,0),
                              ('↓',BTN_DOWN,1,1),('→',BTN_RIGHT,1,2)]:
            self._btn(f,lbl,btn,52,52,self.DPAD_I,self.DPAD_P).grid(row=r,column=c,padx=2,pady=2)

    def _build_actions(self):
        f = tk.Frame(self, bg=BG2); f.pack()
        for col,(lbl,btn,ic,pc) in enumerate([
            ('  A  ',BTN_A,self.A_I,self.A_P),
            ('  B  ',BTN_B,self.B_I,self.B_P)]):
            self._btn(f,lbl,btn,54,54,ic,pc).grid(row=0,column=col,padx=5,pady=2)
        self._btn(self,'  START  ',BTN_START,110,30,self.S_I,self.S_P).pack(pady=4)

    def _btn(self, parent, lbl, btn, w, h, idle, pressed):
        cv = tk.Canvas(parent,width=w,height=h,bg=idle,highlightthickness=1,
                       highlightbackground='#444',cursor='hand2')
        cv.create_text(w//2,h//2,text=lbl,fill='white',font=('Segoe UI',11,'bold'))
        cv.bind('<ButtonPress-1>',   lambda e,m=btn,c=cv,p=pressed: self._press(m,c,p))
        cv.bind('<ButtonRelease-1>', lambda e,m=btn,c=cv,i=idle:    self._rel(m,c,i))
        self._cvs[btn]=(cv,idle,pressed); return cv

    def _press(self, btn, cv, pc):
        self._held.add(btn); self.ctrl.press(btn)
        cv.configure(bg=pc); self._status.set(f'BTN={self.ctrl.state}')
    def _rel(self, btn, cv, ic):
        self._held.discard(btn); self.ctrl.release(btn)
        cv.configure(bg=ic); self._status.set(f'BTN={self.ctrl.state}')
    def release_all(self):
        self._held.clear(); self.ctrl.release()
        for _,(cv,ic,_) in self._cvs.items(): cv.configure(bg=ic)
        self._status.set('BTN=0x0000  (released)')
    def key_down(self, key):
        btn = self._key_map.get(key)
        if btn and btn not in self._held:
            self._held.add(btn); self.ctrl.press(btn)
            if btn in self._cvs:
                cv,_,pc=self._cvs[btn]; cv.configure(bg=pc)
            self._status.set(f'BTN={self.ctrl.state}')
    def key_up(self, key):
        btn = self._key_map.get(key)
        if btn:
            self._held.discard(btn); self.ctrl.release(btn)
            if btn in self._cvs:
                cv,ic,_=self._cvs[btn]; cv.configure(bg=ic)
            self._status.set(f'BTN={self.ctrl.state}')

# ── Savestate Manager ─────────────────────────────────────────────────────────
class SavestateManager(tk.LabelFrame):
    def __init__(self, parent, sock_var, status_cb, **kw):
        super().__init__(parent, text='Savestate Manager', bg=BG2, fg=YELLOW,
                         font=FONT_B, **kw)
        self._sock_var = sock_var
        self._status_cb = status_cb
        self._build()

    def _build(self):
        top = tk.Frame(self, bg=BG2); top.pack(fill='x', padx=6, pady=4)

        # Save with name
        tk.Label(top, text='Save Name:', bg=BG2, fg=BTN_FG, font=FONT).grid(row=0,column=0,sticky='w')
        self._save_name = tk.Entry(top, bg='#0d1b2a', fg=BTN_FG, font=FONT,
                                   insertbackground='white', width=22,
                                   relief='flat', bd=4)
        self._save_name.insert(0, 'my_state')
        self._save_name.grid(row=0,column=1,padx=6)
        mk_btn(top, '💾 Save State', self._do_save,
               bg='#1e5a3a', pady=4).grid(row=0,column=2,padx=4)

        # Load by name
        tk.Label(top, text='Load Name:', bg=BG2, fg=BTN_FG, font=FONT).grid(row=1,column=0,sticky='w',pady=6)
        self._load_name = tk.Entry(top, bg='#0d1b2a', fg=BTN_FG, font=FONT,
                                   insertbackground='white', width=22,
                                   relief='flat', bd=4)
        self._load_name.grid(row=1,column=1,padx=6)
        mk_btn(top, '📂 Load State', self._do_load,
               bg='#1e3a5f', pady=4).grid(row=1,column=2,padx=4)
        mk_btn(top, '📁 Browse', self._do_browse,
               bg=BTN_BG, pady=4).grid(row=1,column=3,padx=4)

        # State list
        list_frame = tk.Frame(self, bg=BG2); list_frame.pack(fill='both', padx=6, pady=2)
        tk.Label(list_frame, text=f'States in {STATE_DIR}:',
                 bg=BG2, fg=GRAY, font=MONO).pack(anchor='w')

        scroll = tk.Scrollbar(list_frame)
        scroll.pack(side='right', fill='y')
        self._listbox = tk.Listbox(list_frame, height=6, bg='#0d1b2a', fg=BTN_FG,
                                   font=MONO, selectbackground=ACCENT,
                                   selectforeground='white',
                                   yscrollcommand=scroll.set,
                                   relief='flat', activestyle='dotbox')
        self._listbox.pack(fill='x', side='left', expand=True)
        scroll.config(command=self._listbox.yview)
        self._listbox.bind('<Double-Button-1>', lambda e: self._do_load_selected())

        btn_row = tk.Frame(self, bg=BG2); btn_row.pack(fill='x', padx=6, pady=4)
        mk_btn(btn_row, '📂 Load Selected', self._do_load_selected, bg='#1e3a5f', pady=4).pack(side='left', padx=4)
        mk_btn(btn_row, '✏️ Rename', self._do_rename, bg='#3a3a1e', pady=4).pack(side='left', padx=4)
        mk_btn(btn_row, '🗑 Delete', self._do_delete, bg='#5a1a1a', pady=4).pack(side='left', padx=4)
        mk_btn(btn_row, '🔄 Refresh', self._refresh_list, bg='#1a3a3a', pady=4).pack(side='left', padx=4)

        self._refresh_list()

    def _refresh_list(self):
        self._listbox.delete(0, 'end')
        os.makedirs(STATE_DIR, exist_ok=True)
        files = sorted(glob.glob(f'{STATE_DIR}/*.st'), key=os.path.getmtime, reverse=True)
        for f in files:
            size = os.path.getsize(f) // 1024
            self._listbox.insert('end', f'  {os.path.basename(f)}  ({size}KB)')

    def _selected_path(self):
        sel = self._listbox.curselection()
        if not sel: return None
        name = self._listbox.get(sel[0]).strip().split('  ')[0]
        return f'{STATE_DIR}/{name}'

    def _sock(self): return self._sock_var.get()

    def _do_save(self):
        name = self._save_name.get().strip()
        if not name: messagebox.showwarning('Name', 'Enter a save name first'); return
        path = f'{STATE_DIR}/{name}.st'
        def _go():
            try:
                from pathlib import Path
                b, h = _get_bridge(self._sock())
                # Press A to confirm selection, then pause, then save
                self._status_cb('⏳ Pressing A…')
                h.run()
                import struct, mmap as _mmap, os as _os
                with open(P1_FILE, 'r+b') as f:
                    m = _mmap.mmap(f.fileno(), 4)
                    m.seek(0); m.write(struct.pack('<Hbb', BTN_A & 0xFFFF, 0, 0)); m.flush()
                    time.sleep(0.08)
                    m.seek(0); m.write(struct.pack('<Hbb', 0, 0, 0)); m.flush()
                    m.close()
                time.sleep(0.15)
                h.pause()
                time.sleep(0.05)
                self._status_cb('⏳ Saving…')
                resp = b.save_savestate_path(Path(path))
                ok = resp.get('saved', False)
                b.close()
                msg = f'✅ Saved: {name}.st' if ok else f'❌ Save failed'
                self._status_cb(msg)
                self.after(500, self._refresh_list)
            except Exception as e:
                self._status_cb(f'❌ Error: {e}')
        threading.Thread(target=_go, daemon=True).start()

    def _do_load(self):
        name = self._load_name.get().strip()
        if not name: messagebox.showwarning('Name', 'Enter a state name to load'); return
        # try with and without .st
        path = f'{STATE_DIR}/{name}' if name.endswith('.st') else f'{STATE_DIR}/{name}.st'
        self._load_path(path)

    def _do_browse(self):
        path = filedialog.askopenfilename(
            initialdir=STATE_DIR,
            title='Select Savestate',
            filetypes=[('Savestate', '*.st'), ('All', '*')])
        if path:
            self._load_name.delete(0, 'end')
            self._load_name.insert(0, os.path.basename(path))
            self._load_path(path)

    def _do_load_selected(self):
        path = self._selected_path()
        if not path: messagebox.showinfo('Select', 'Double-click or select a state first'); return
        self._load_path(path)

    def _load_path(self, path):
        if not os.path.exists(path):
            self._status_cb(f'❌ Not found: {path}'); return
        def _go():
            try:
                from pathlib import Path
                b, h = _get_bridge(self._sock())
                b.load_savestate_path(Path(path))
                time.sleep(0.3); b.close()
                self._status_cb(f'✅ Loaded: {os.path.basename(path)}')
            except Exception as e:
                self._status_cb(f'❌ Load error: {e}')
        threading.Thread(target=_go, daemon=True).start()

    def _do_rename(self):
        path = self._selected_path()
        if not path: messagebox.showinfo('Select', 'Select a state first'); return
        old_name = os.path.basename(path)
        new_name = simpledialog.askstring('Rename', f'New name for {old_name}:',
                                          initialvalue=old_name.replace('.st',''))
        if new_name:
            new_path = f'{STATE_DIR}/{new_name.strip()}.st'
            try:
                shutil.move(path, new_path)
                self._status_cb(f'✅ Renamed → {new_name}.st')
                self._refresh_list()
            except Exception as e:
                self._status_cb(f'❌ Rename error: {e}')

    def _do_delete(self):
        path = self._selected_path()
        if not path: return
        if messagebox.askyesno('Delete', f'Delete {os.path.basename(path)}?'):
            try:
                os.remove(path)
                self._status_cb(f'🗑 Deleted: {os.path.basename(path)}')
                self._refresh_list()
            except Exception as e:
                self._status_cb(f'❌ Delete error: {e}')

# ── Main App ──────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title('MK4 All-in-One Debug Tool')
        root.configure(bg=BG)
        root.resizable(True, True)

        self.p1 = Controller(P1_FILE)
        self.p2 = Controller(P2_FILE)
        self._status_var = tk.StringVar(value='Ready')
        self._sock_var   = tk.StringVar(value=f'{SOCK_BASE}.sock')
        self._inst_count = 1

        self._build()
        root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _status(self, s):
        self.root.after(0, lambda: self._status_var.set(s))

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        root = self.root

        # Header
        hdr = tk.Frame(root, bg=ACCENT, pady=8)
        hdr.pack(fill='x')
        tk.Label(hdr, text='⚔  MK4 Debug Tool', bg=ACCENT, fg='white',
                 font=('Segoe UI', 16, 'bold')).pack(side='left', padx=16)
        tk.Label(hdr, text='Emulator runs headless — Screenshot shows game state',
                 bg=ACCENT, fg='#ffcccc', font=FONT).pack(side='left')

        # Status bar
        sf = tk.Frame(root, bg=PANEL, pady=4)
        sf.pack(fill='x', padx=0)
        tk.Label(sf, text='STATUS:', bg=PANEL, fg=GRAY, font=FONT_B).pack(side='left', padx=8)
        tk.Label(sf, textvariable=self._status_var, bg=PANEL, fg=GREEN,
                 font=MONO).pack(side='left')

        # Main notebook tabs
        nb = ttk.Notebook(root)
        nb.pack(fill='both', expand=True, padx=8, pady=8)

        style = ttk.Style()
        style.configure('TNotebook', background=BG)
        style.configure('TNotebook.Tab', background=BG2, foreground='white',
                        font=FONT_B, padding=[12, 6])
        style.map('TNotebook.Tab', background=[('selected', ACCENT)])

        # Tab 1: Emulator & Controls
        t1 = tk.Frame(nb, bg=BG); nb.add(t1, text='🎮  Controllers')
        self._build_controllers_tab(t1)

        # Tab 2: Savestate Manager
        t2 = tk.Frame(nb, bg=BG); nb.add(t2, text='💾  Savestates')
        self._build_states_tab(t2)

        # Tab 3: Emulator Instances
        t3 = tk.Frame(nb, bg=BG); nb.add(t3, text='🖥️  Emulators')
        self._build_emulators_tab(t3)

        # Tab 4: Input Log
        t4 = tk.Frame(nb, bg=BG); nb.add(t4, text='📋  Input Log')
        self._build_log_tab(t4)

    # ── Tab 1: Controllers + Screenshot ──────────────────────────────────────
    def _build_controllers_tab(self, parent):
        top = tk.Frame(parent, bg=BG); top.pack(fill='x', padx=8, pady=6)

        # Emulator quick controls
        ef = tk.LabelFrame(top, text='Emulator Controls', bg=BG2, fg=YELLOW, font=FONT_B)
        ef.pack(fill='x', pady=4)

        for text, bg, cmd in [
            ('🚀 Launch P1 Emu', '#0f3460', lambda: threading.Thread(target=self._launch_main, daemon=True).start()),
            ('💀 Kill All',      '#5a1a1a', lambda: threading.Thread(target=lambda: kill_instance('main', self._status), daemon=True).start()),
            ('▶ Run',            '#1e5a3a', lambda: threading.Thread(target=self._emu_run,   daemon=True).start()),
            ('⏸ Pause',          '#3a3a1e', lambda: threading.Thread(target=self._emu_pause, daemon=True).start()),
            ('📷 Screenshot',    '#1a3a5f', lambda: threading.Thread(target=self._screenshot, daemon=True).start()),
        ]:
            mk_btn(ef, text, cmd, bg=bg, pady=5).pack(side='left', padx=6, pady=6)

        # Sock path display
        sf2 = tk.Frame(ef, bg=BG2); sf2.pack(side='left', padx=10)
        tk.Label(sf2, text='Socket:', bg=BG2, fg=GRAY, font=FONT).pack(side='left')
        tk.Entry(sf2, textvariable=self._sock_var, bg='#0d1b2a', fg=BTN_FG,
                 font=MONO, width=40, relief='flat', bd=4).pack(side='left', padx=4)

        # Pads + screenshot
        bottom = tk.Frame(parent, bg=BG); bottom.pack(fill='both', expand=True, padx=8)

        p1_keys = {'Up':BTN_UP,'Down':BTN_DOWN,'Left':BTN_LEFT,'Right':BTN_RIGHT,
                   'z':BTN_A,'Z':BTN_A,'x':BTN_B,'X':BTN_B,'Return':BTN_START}
        p2_keys = {'w':BTN_UP,'s':BTN_DOWN,'a':BTN_LEFT,'d':BTN_RIGHT,
                   'W':BTN_UP,'S':BTN_DOWN,'A':BTN_LEFT,'D':BTN_RIGHT,
                   'q':BTN_A,'Q':BTN_A,'e':BTN_B,'E':BTN_B,'Tab':BTN_START}

        self.p1_pad = ControllerPad(bottom, self.p1,
            'P1  —  Arrows / Z=A / X=B / Enter=Start', p1_keys)
        self.p1_pad.grid(row=0, column=0, padx=10, pady=6, sticky='n')

        tk.Frame(bottom, bg='#333', width=2).grid(row=0, column=1, sticky='ns', padx=6)

        self.p2_pad = ControllerPad(bottom, self.p2,
            'P2  —  WASD / Q=A / E=B / Tab=Start', p2_keys)
        self.p2_pad.grid(row=0, column=2, padx=10, pady=6, sticky='n')

        # Screenshot panel
        ss = tk.LabelFrame(bottom, text='Game Screenshot', bg=BG2, fg=YELLOW, font=FONT_B)
        ss.grid(row=0, column=3, padx=10, pady=6, sticky='n')

        self._photo = [None]
        self._ss_lbl = tk.Label(ss, bg='#0d1b2a', width=40, height=16,
                                text='Click Screenshot\nto see game state',
                                fg=GRAY, font=FONT)
        self._ss_lbl.pack(padx=4, pady=4)
        self._ss_info = tk.StringVar(value='')
        tk.Label(ss, textvariable=self._ss_info, bg=BG2, fg=GRAY, font=MONO).pack()

        tk.Label(parent, text='Space = release all  |  Arrows/WASD = D-pad  |  Z/Q = A  |  X/E = B',
                 bg=BG, fg=GRAY, font=MONO).pack(pady=4)

        self.root.bind('<KeyPress>',   self._kp)
        self.root.bind('<KeyRelease>', self._kr)

    # ── Tab 2: Savestates ─────────────────────────────────────────────────────
    def _build_states_tab(self, parent):
        self._state_mgr = SavestateManager(parent, self._sock_var, self._status)
        self._state_mgr.pack(fill='both', expand=True, padx=8, pady=8)

    # ── Tab 3: Multiple Emulators ─────────────────────────────────────────────
    def _build_emulators_tab(self, parent):
        tk.Label(parent, text='Launch multiple emulator instances for side-by-side verification',
                 bg=BG, fg=GRAY, font=FONT).pack(pady=8)

        ef = tk.LabelFrame(parent, text='Instances', bg=BG2, fg=YELLOW, font=FONT_B)
        ef.pack(fill='both', expand=True, padx=8, pady=4)

        self._inst_rows = tk.Frame(ef, bg=BG2); self._inst_rows.pack(fill='x', padx=6)
        mk_btn(ef, '+ Add Another Emulator', self._add_instance,
               bg='#1e5a3a', pady=6).pack(pady=8)

        # Default instance row
        self._add_instance_row('main', f'{SOCK_BASE}.sock')

    def _add_instance(self):
        self._inst_count += 1
        name = f'inst{self._inst_count}'
        sock = f'{SOCK_BASE}_{name}.sock'
        self._add_instance_row(name, sock)

    def _add_instance_row(self, name, sock):
        row = tk.Frame(self._inst_rows, bg=BG2); row.pack(fill='x', pady=4)
        tk.Label(row, text=f'[{name}]', bg=BG2, fg=YELLOW,
                 font=FONT_B, width=10).pack(side='left')
        sock_var = tk.StringVar(value=sock)
        if name == 'main': sock_var = self._sock_var
        tk.Entry(row, textvariable=sock_var, bg='#0d1b2a', fg=BTN_FG,
                 font=MONO, width=38, relief='flat', bd=4).pack(side='left', padx=6)
        sv = sock_var  # capture
        mk_btn(row, '🚀 Launch', lambda n=name, s=sv: threading.Thread(
            target=lambda: launch_instance(n, s.get(), self._status), daemon=True).start(),
               bg='#0f3460', pady=4).pack(side='left', padx=4)
        mk_btn(row, '💀 Kill', lambda n=name: threading.Thread(
            target=lambda: kill_instance(n, self._status), daemon=True).start(),
               bg='#5a1a1a', pady=4).pack(side='left', padx=4)
        mk_btn(row, '📂 Load State', lambda n=name, s=sv: self._load_for_instance(s.get()),
               bg='#1e3a5f', pady=4).pack(side='left', padx=4)
        mk_btn(row, '📷 Screenshot', lambda n=name, s=sv: threading.Thread(
            target=lambda: self._screenshot(sock=s.get()), daemon=True).start(),
               bg='#1a3a5f', pady=4).pack(side='left', padx=4)

    def _load_for_instance(self, sock):
        path = filedialog.askopenfilename(initialdir=STATE_DIR,
            title='Select state', filetypes=[('Savestate','*.st'),('All','*')])
        if path:
            def _go():
                try:
                    from pathlib import Path
                    b, h = _get_bridge(sock)
                    b.load_savestate_path(Path(path)); time.sleep(0.3); b.close()
                    self._status(f'✅ Loaded {os.path.basename(path)} into {sock}')
                except Exception as e:
                    self._status(f'❌ {e}')
            threading.Thread(target=_go, daemon=True).start()

    # ── Tab 4: Input Log ──────────────────────────────────────────────────────
    def _build_log_tab(self, parent):
        tf = tk.LabelFrame(parent, text='Live Input Log — What GetKeys() receives',
                           bg=BG2, fg=YELLOW, font=FONT_B)
        tf.pack(fill='both', expand=True, padx=8, pady=8)

        btn_row = tk.Frame(tf, bg=BG2); btn_row.pack(fill='x', padx=6, pady=4)
        mk_btn(btn_row, '🗑 Clear Log', lambda: (open(INPUT_LOG,'w').close(), self._log.configure(state='normal'),
               self._log.delete('1.0','end'), self._log.configure(state='disabled')),
               bg='#5a1a1a', pady=4).pack(side='left', padx=4)

        self._log = tk.Text(tf, bg='#050a0f', fg='#00ff41', font=MONO,
                            state='disabled', relief='flat', wrap='none')
        sb = tk.Scrollbar(tf); sb.pack(side='right', fill='y')
        self._log.pack(fill='both', expand=True, padx=4, pady=4)
        sb.config(command=self._log.yview)
        self._log.config(yscrollcommand=sb.set)

        def _tail():
            last = 0
            while True:
                try:
                    if os.path.exists(INPUT_LOG):
                        with open(INPUT_LOG) as f:
                            f.seek(last); new = f.read(); last = f.tell()
                        if new:
                            self._log.configure(state='normal')
                            self._log.insert('end', new); self._log.see('end')
                            lines = int(self._log.index('end-1c').split('.')[0])
                            if lines > 500: self._log.delete('1.0','100.0')
                            self._log.configure(state='disabled')
                except: pass
                time.sleep(0.15)
        threading.Thread(target=_tail, daemon=True).start()

    # ── Emulator actions ──────────────────────────────────────────────────────
    def _launch_main(self):
        launch_instance('main', self._sock_var.get(), self._status)

    def _emu_run(self):
        try:
            b, h = _get_bridge(self._sock_var.get()); h.run(); b.close()
            self._status('▶ Emulator RUNNING')
        except Exception as e: self._status(f'❌ {e}')

    def _emu_pause(self):
        try:
            b, h = _get_bridge(self._sock_var.get()); h.pause(); b.close()
            self._status('⏸ Emulator PAUSED')
        except Exception as e: self._status(f'❌ {e}')

    def _screenshot(self, sock=None):
        sock = sock or self._sock_var.get()
        if not os.path.exists(sock): self._status('❌ Not connected'); return
        try:
            before = set(glob.glob(f'{SHOT_DIR}/*.png'))
            b, h = _get_bridge(sock)
            b.debugger_command('screenshot', timeout_sec=5, output_tail_chars=20)
            time.sleep(0.6); b.close()
            after = set(glob.glob(f'{SHOT_DIR}/*.png'))
            new = sorted(after - before, key=os.path.getmtime)
            if new:
                self._status(f'📷 {os.path.basename(new[-1])}')
                self._show_screenshot(new[-1])
        except Exception as e: self._status(f'❌ Screenshot: {e}')

    def _show_screenshot(self, path):
        try:
            from PIL import Image, ImageTk
            img = Image.open(path).resize((400, 300))
            photo = ImageTk.PhotoImage(img)
            self._photo[0] = photo
            self.root.after(0, lambda: self._ss_lbl.configure(
                image=photo, text='', width=400, height=300))
            self.root.after(0, lambda: self._ss_info.set(os.path.basename(path)))
        except ImportError:
            self._status('Install Pillow: pip install Pillow --break-system-packages')
        except Exception as e: self._status(f'❌ Display: {e}')

    # ── Keys ──────────────────────────────────────────────────────────────────
    def _kp(self, e):
        k = e.keysym
        if k == 'space': self.p1_pad.release_all(); self.p2_pad.release_all(); return
        # Route each key only to its own pad to avoid cross-contamination
        if k in self.p1_pad._key_map: self.p1_pad.key_down(k)
        if k in self.p2_pad._key_map: self.p2_pad.key_down(k)
    def _kr(self, e):
        k = e.keysym
        if k in self.p1_pad._key_map: self.p1_pad.key_up(k)
        if k in self.p2_pad._key_map: self.p2_pad.key_up(k)

    def _on_close(self):
        self.p1.close(); self.p2.close()
        os.system("pkill -9 -f 'mupen64plus|run_bridge_server' 2>/dev/null")
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == '__main__':
    main()

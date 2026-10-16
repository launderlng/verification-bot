"""
Fishing Macro
=============
Skill check 1 (cast): HOLD the cast key (E). A bar fills left -> right.
                      Release when the fill reaches the green zone.
Skill check 2 (reel): A blue bar must stay on the orange fish, which moves
                      left/right. The macro holds/releases your control
                      (mouse or key) to keep the blue bar under the fish.

Setup:
    pip install mss numpy pillow pydirectinput pynput
Run:
    python fishing_macro.py

First time:
    1. Click "Set region" for each skill check (the app waits 4s so you can
       get the skill check on screen, then you drag a box around it).
    2. Click "Pick" for each color, then click on that color on screen.
    3. Press F6 (or the Start button) to start / stop the macro.
Run the game in Windowed or Borderless mode. If keys don't register in the
game, run this script as Administrator.
"""

import json
import os
import queue
import sys
import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import urllib.request
import urllib.parse
import webbrowser

import mss
import numpy as np
from PIL import Image, ImageTk

try:
    import winreg
except ImportError:
    winreg = None

# Make coordinates match real pixels on high-DPI Windows screens
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

APP_NAME = "FishingMacro"
CONFIG_NAME = "fishing_macro_config.json"

if getattr(sys, "frozen", False):          # running as compiled .exe
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)   # files baked into the exe
else:
    APP_DIR = BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_config_path():
    """Config lives next to the exe. If that folder is read-only, fall back to %APPDATA%."""
    try:
        probe = os.path.join(APP_DIR, ".write_test")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return os.path.join(APP_DIR, CONFIG_NAME)
    except Exception:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, APP_NAME)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, CONFIG_NAME)


CONFIG_FILE = resolve_config_path()
BUNDLED_DEFAULT = os.path.join(BUNDLE_DIR, CONFIG_NAME)   # optional: settings baked in at build time

DEFAULTS = {
    "region1": None,            # skill check 1 region {left, top, width, height}
    "region2": None,            # skill check 2 region
    "colors": {
        "fill":   [255, 255, 255],   # filling bar (skill check 1)
        "green":  [28, 126, 65],     # green target zone (skill check 1)
        "blue":   [48, 87, 143],     # your bar (skill check 2)
        "orange": [141, 66, 21],     # the fish (skill check 2)
        "red":    [200, 40, 40],     # RED mystery box (skill check 2)
    },
    # --- rod / casting ---
    "equip_rod": True,           # press the hotbar key to equip the rod
    "rod_slot": 1,               # hotbar slot 1-5 that holds the rod
    "cast_action": "mouse_left", # what "casting" is: mouse_left or a key (e, f, space...)
    "auto_cast_on_start": True,  # equip + cast as soon as the macro starts
    "auto_recast": True,         # cast again after each catch
    "re_equip_each_cast": False, # press the hotbar key before every cast too
    "pre_cast_press": True,      # press a key (E) right before every RE-cast (not the very first cast)
    "pre_cast_key": "e",
    "pre_cast_delay": 0.6,       # seconds to wait after that key press before casting
    "start_delay": 3.0,          # seconds to click into the game after pressing start
    "recast_delay": 1.5,
    "check1_timeout": 15.0,      # no skill check 1 this long after casting -> cast again (0 = off)
    "bite_timeout": 90.0,        # no skill check 2 this long after a cast -> recast (0 = off)
    # --- skill check 1 ---
    "cast_key": "e",           # key held for skill check 1
    "release_at": 0.5,         # release point inside green zone (0=left edge, 1=right edge)
    "release_latency": 0.045,  # seconds: input lag + screen-capture lag. Lands LEFT of green -> lower it, RIGHT of green -> raise it
    # --- skill check 2 ---
    "reel_mode": "two_key",    # two_key = Q moves left / E moves right | hold = one input, hold to move
    "left_key": "q",
    "right_key": "e",
    "reel_input": "mouse_left",  # (hold mode only)
    "hold_moves": "right",       # (hold mode only)
    "deadzone": 4,             # px minimum
    "deadzone_frac": 0.10,     # deadzone as a fraction of the blue box width
    "lead": 0.05,              # seconds of prediction
    "lost_timeout": 1.5,       # seconds without fish = reel finished
    # --- window ---
    "always_on_top": False,       # keep the window above the game (off = behaves like a normal app)
    "minimize_on_start": True,    # get out of the way when the macro starts
    "window_geometry": None,      # remembered position/size
    # --- smarter detection ---
    "green_detect": "auto",    # auto = any green-looking zone (+ picked color) | color = picked color only
    "fill_detect": "auto",     # auto = watch what changes on the bar (any color) | color = picked fill color
    "diff_threshold": 35,      # how different a pixel must be to count as "filled" (auto mode)
    "auto_tune": False,        # (only works if the bar stays visible after release)
    "debug_trace": False,      # write cast_trace.txt (bar positions over time) next to the config
    "red_enabled": True,       # also track the red mystery box
    # --- detection ---
    "tolerance": 40,           # color match tolerance (0-255)
    "min_pixels": 2,           # min pixels in a column to count as a hit
}


def primary_monitor():
    with mss.mss() as sct:
        return dict(sct.monitors[1])


def _merge(cfg, data):
    """Merge a loaded dict into cfg. Regions are stored as screen fractions so a config
    made on one resolution still lines up on another screen with the same layout."""
    mon = primary_monitor()
    for k, v in data.items():
        if k == "colors" and isinstance(v, dict):
            cfg["colors"].update(v)
        elif k in ("region1_rel", "region2_rel"):
            if v:
                l, t, w, h = v
                cfg[k[:-4]] = {"left": mon["left"] + round(l * mon["width"]),
                               "top": mon["top"] + round(t * mon["height"]),
                               "width": max(1, round(w * mon["width"])),
                               "height": max(1, round(h * mon["height"]))}
        elif k in ("region1", "region2"):
            if not data.get(k + "_rel"):
                cfg[k] = v          # old absolute-pixel format
        elif k in cfg:
            cfg[k] = v


def to_file_dict(cfg):
    mon = primary_monitor()
    out = {k: v for k, v in cfg.items() if k not in ("region1", "region2")}
    for k in ("region1", "region2"):
        r = cfg.get(k)
        out[k + "_rel"] = None if not r else [
            round((r["left"] - mon["left"]) / mon["width"], 5),
            round((r["top"] - mon["top"]) / mon["height"], 5),
            round(r["width"] / mon["width"], 5),
            round(r["height"] / mon["height"], 5)]
    return out


def read_config_file(path, cfg):
    with open(path, encoding="utf-8") as f:
        _merge(cfg, json.load(f))


def load_config():
    """Returns (cfg, first_run). Creates the config file automatically if it doesn't exist."""
    cfg = json.loads(json.dumps(DEFAULTS))
    first_run = not os.path.exists(CONFIG_FILE)
    try:
        if not first_run:
            read_config_file(CONFIG_FILE, cfg)
        elif os.path.exists(BUNDLED_DEFAULT):
            read_config_file(BUNDLED_DEFAULT, cfg)      # settings the exe's author baked in
    except Exception as e:
        print("Config unreadable, using defaults:", e)
    if first_run:
        save_config(cfg)                                # auto-create on first launch
    return cfg, first_run


def save_config(cfg, path=None):
    try:
        with open(path or CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(to_file_dict(cfg), f, indent=2)
        return True
    except Exception as e:
        print("Could not save config:", e)
        return False


# --------------------------------------------------------------------------
# Licence  (access control only: login, HWID lock, auto-logout. No file changes.)
# --------------------------------------------------------------------------
def _deob(hx, key):
    """Undo the simple XOR+hex obfuscation used for the API URL, so a plain
    strings-dump of the packed exe doesn't reveal the endpoint."""
    kb = key.encode()
    return bytes(int(hx[i:i + 2], 16) ^ kb[(i // 2) % len(kb)]
                 for i in range(0, len(hx), 2)).decode()


LICENSE_API_URL = _deob("302e2c5141551d1b2e3f2a48540651552c33374f1f0d5d40752a2a4e561a51403135360c000a5300762f280f400e5b582f3b210f531f42", "XZX!2o24")
DISCORD_INVITE = "discord.gg/xzxx"
PRODUCT = "fishing"
OFFLINE_GRACE_SEC = 1800   # stop if the server can't confirm the key for this long (30 min)
LICENSE_FILE = os.path.join(os.path.dirname(CONFIG_FILE), "fishing_license.json")


def load_license():
    try:
        with open(LICENSE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_license(data):
    try:
        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def get_hwid():
    """A stable per-PC id (Windows MachineGuid). Used to lock a key to one device."""
    if winreg is not None:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            v, _ = winreg.QueryValueEx(k, "MachineGuid")
            if v:
                return str(v)
        except Exception:
            pass
    return os.environ.get("COMPUTERNAME", "unknown")


def lic_api(path, data=None, timeout=8):
    url = LICENSE_API_URL.rstrip("/") + path
    try:
        if data is not None:
            req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(),
                                         headers={"User-Agent": "XZXFishing/1.0"})
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "XZXFishing/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace").strip()
    except Exception:
        return "DENIED|SERVER UNREACHABLE"


def run_login():
    """Discord link + access key. Returns {"key","session"} on success, or None if cancelled.
    A key locked to another PC simply refuses to run here; nothing on the computer is changed."""
    T = THEME
    saved = load_license()
    if saved.get("key"):                           # already activated on this PC and still valid -> skip login
        r = lic_api("/api/check", {"code": saved["key"], "session": saved.get("session", ""),
                                   "product": PRODUCT, "hwid": get_hwid()})
        if not r.startswith("REVOKED|") and not r.startswith("DENIED|"):
            return {"key": saved["key"], "session": saved.get("session", "")}

    result = {}
    state = {"session": saved.get("session", ""), "link": "none"}
    win = tk.Tk()
    apply_theme(win)
    win.title(APP_TITLE + " - Login")
    win.configure(bg=T["bg"])
    win.geometry("420x360")
    win.resizable(False, False)

    tk.Label(win, text="XZX", bg=T["bg"], fg=T["accent"], font=("Segoe UI Semibold", 26, "bold")).pack(pady=(22, 0))
    tk.Label(win, text="Fishing Macro", bg=T["bg"], fg=T["fg"], font=("Segoe UI Semibold", 13)).pack()
    tk.Label(win, text="Link your Discord, then enter your key", bg=T["bg"], fg=T["muted"],
             font=("Segoe UI", 9)).pack(pady=(4, 0))

    discord_btn = tk.Button(win, text="Link Discord account", bg=T["panel2"], fg=T["fg"],
                            relief="flat", font=("Segoe UI", 10), width=30)
    discord_btn.pack(pady=(16, 8), ipady=5)
    key_var = tk.StringVar(value=saved.get("key", ""))
    entry = tk.Entry(win, textvariable=key_var, bg=T["panel2"], fg=T["fg"], insertbackground=T["fg"],
                     justify="center", font=("Consolas", 12), width=26)
    entry.pack(ipady=5)
    unlock = tk.Button(win, text="Unlock", bg="#5a2fd6", fg="white", relief="flat",
                       font=("Segoe UI", 11, "bold"), width=30)
    unlock.pack(pady=12, ipady=5)
    status = tk.Label(win, text="", bg=T["bg"], fg=T["muted"], font=("Segoe UI", 9), wraplength=380)
    status.pack()
    tk.Button(win, text="Need help?  Join " + DISCORD_INVITE, bg=T["bg"], fg=T["accent"],
              relief="flat", bd=0, command=lambda: webbrowser.open("https://" + DISCORD_INVITE)).pack(pady=(6, 0))

    def set_status(t, c=None):
        status.config(text=t, fg=c or T["muted"])

    def poll():
        if state["link"] == "linked" or not state["session"]:
            return
        r = lic_api("/api/auth/status?s=" + urllib.parse.quote(state["session"]))
        if r.startswith("LINKED|"):
            parts = r.split("|")
            state["link"] = "linked"
            discord_btn.config(text="✓ Linked as " + (parts[1] if len(parts) > 1 else "you"))
            set_status("Linked. Enter your key and press Unlock.", T["green"])
        elif r == "PENDING":
            set_status("Waiting for Discord...")
            win.after(1500, poll)
        elif r == "OFF":
            state["link"] = "off"
        else:
            set_status("Link expired - click Link Discord again.", T["red"])

    def do_link():
        r = lic_api("/api/auth/new", {})
        if r.startswith("OK|"):
            _, sess, url = r.split("|", 2)
            state["session"] = sess
            webbrowser.open(url)
            set_status("Finish linking in your browser...")
            win.after(1500, poll)
        elif r.startswith("OFF"):
            state["link"] = "off"
            set_status("Discord linking is off - just enter your key.", T["accent"])
        else:
            set_status("Couldn't start linking. Try again.", T["red"])

    def do_unlock():
        code = key_var.get().strip()
        if not code:
            set_status("Enter your access key.", T["red"])
            return
        set_status("Checking licence...")
        win.update_idletasks()
        r = lic_api("/api/activate", {"code": code, "session": state["session"],
                                      "product": PRODUCT, "hwid": get_hwid()})
        if r.startswith("OK|"):
            save_license({"key": code, "session": state["session"], "hwid": get_hwid()})
            result["key"] = code
            result["session"] = state["session"]
            win.destroy()
            return
        msg = r.split("|", 1)[1] if "|" in r else r
        if "LOCKED TO ANOTHER DEVICE" in msg:
            set_status("This key is locked to another PC, so it won't run here.", T["red"])
        else:
            set_status("Access denied: " + msg, T["red"])
        key_var.set("")

    discord_btn.config(command=do_link)
    unlock.config(command=do_unlock)
    entry.bind("<Return>", lambda e: do_unlock())
    if state["session"]:
        win.after(400, poll)
    win.mainloop()
    return result or None


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------
class Inputs:
    def __init__(self):
        self.held = set()
        self.pdi = None
        try:
            import pydirectinput
            pydirectinput.PAUSE = 0
            pydirectinput.FAILSAFE = False
            self.pdi = pydirectinput
        except Exception:
            pass
        from pynput.keyboard import Controller as KC, Key
        from pynput.mouse import Controller as MC, Button
        self.kb, self.ms, self.Key, self.Button = KC(), MC(), Key, Button

    def _pk(self, name):
        if len(name) == 1:
            return name
        return getattr(self.Key, name, name)

    def down(self, name):
        if name in self.held:
            return
        self.held.add(name)
        if name == "mouse_left":
            self.pdi.mouseDown() if self.pdi else self.ms.press(self.Button.left)
        else:
            self.pdi.keyDown(name) if self.pdi else self.kb.press(self._pk(name))

    def up(self, name):
        if name not in self.held:
            return
        self.held.discard(name)
        if name == "mouse_left":
            self.pdi.mouseUp() if self.pdi else self.ms.release(self.Button.left)
        else:
            self.pdi.keyUp(name) if self.pdi else self.kb.release(self._pk(name))

    def tap(self, name, dur=0.05):
        self.down(name)
        time.sleep(dur)
        self.up(name)

    def drive(self, direction, left, right):
        """direction: -1 = left key, +1 = right key, 0 = release both."""
        if direction > 0:
            self.up(left)
            self.down(right)
        elif direction < 0:
            self.up(right)
            self.down(left)
        else:
            self.up(left)
            self.up(right)

    def release_all(self):
        for n in list(self.held):
            self.up(n)


# --------------------------------------------------------------------------
# Detection helpers
# --------------------------------------------------------------------------
def grab(sct, region):
    shot = sct.grab(region)
    arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return arr[:, :, [2, 1, 0]]  # RGB


def color_mask(img, color, tol):
    diff = np.abs(img.astype(np.int16) - np.array(color, dtype=np.int16))
    return diff.max(axis=2) <= tol


def x_extent(mask, min_px):
    cols = np.where(mask.sum(axis=0) >= min_px)[0]
    if len(cols) == 0:
        return None
    return int(cols[0]), int(cols[-1])


def runs(bool_arr, gap=6):
    """Contiguous runs of True (gaps up to `gap` px are bridged)."""
    idx = np.where(bool_arr)[0]
    if len(idx) == 0:
        return []
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap:
            out.append((int(start), int(prev)))
            start = i
        prev = i
    out.append((int(start), int(prev)))
    return out


def green_mask(img, col, tol, mode="auto"):
    cm = color_mask(img, col["green"], tol)
    if mode == "color":
        return cm
    a = img.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return cm | ((g >= 90) & (g > r * 1.35 + 10) & (g > b * 1.15 + 5))   # any "green-looking" pixel


def find_green_zone(img, col, tol, minpx, mode="auto"):
    """Returns (x0, x1, y0, y1) of the green target square, or None."""
    gm = green_mask(img, col, tol, mode)
    colcount = gm.sum(axis=0)
    if colcount.size == 0 or colcount.max() < max(4, minpx * 2):
        return None
    cols = colcount >= max(minpx, colcount.max() * 0.5)
    r = runs(cols, gap=2)
    if not r:
        return None
    x0, x1 = max(r, key=lambda t: t[1] - t[0])
    if x1 - x0 < 3:
        return None
    rowc = gm[:, x0:x1 + 1].sum(axis=1)
    ridx = np.where(rowc >= max(1, (x1 - x0 + 1) * 0.5))[0]
    if len(ridx) == 0:
        return None
    y0, y1 = int(ridx[0]), int(ridx[-1])
    # must be a SOLID block (not a thin outline or green text like a timer badge)
    if y1 - y0 < 4 or gm[y0:y1 + 1, x0:x1 + 1].mean() < 0.6:
        return None
    return int(x0), int(x1), y0, y1


def fill_edge_diff(img, base, thresh):
    """Right edge of the filling bar = right end of the biggest block of pixels that
    changed vs. the empty-bar snapshot. Works for ANY fill color."""
    d = np.abs(img.astype(np.int16) - base.astype(np.int16)).max(axis=2) > thresh
    r = runs(d.mean(axis=0) >= 0.2, gap=6)
    if not r:
        return None
    return max(r, key=lambda t: t[1] - t[0])[1]


def fill_edge(img, base, col, tol, minpx, mode, thresh):
    if mode == "color" or base is None:
        f = x_extent(color_mask(img, col["fill"], tol), minpx)
        return f[1] if f else None
    return fill_edge_diff(img, base, thresh)


def find_bar_and_fish(img, col, tol, minpx, use_red=True):
    """
    Finds your blue bar and the TARGET it must stay on.
    Returns (bar, target_x, rows, kind, target_extent):
      bar           = (x0, x1) of the blue box
      target_x      = centre of the target (orange fish icon, or the red mystery BOX)
      rows          = (y0, y1) rows of the blue bar
      kind          = "orange" or "red"
      target_extent = (x0, x1) of the target, for the preview
    The target is ONLY searched in the same rows as the blue bar, so the orange
    progress bar below is ignored. If the red box sits on top of the blue bar and splits
    it into two pieces, the pieces are joined back together.
    """
    none = (None, None, None, None, None)
    bm = color_mask(img, col["blue"], tol)
    rowsum = bm.sum(axis=1)
    if rowsum.size == 0 or rowsum.max() < max(minpx, 8):
        return none
    ridx = np.where(rowsum >= max(minpx, rowsum.max() * 0.5))[0]
    y0, y1 = int(ridx[0]), int(ridx[-1])
    sub = img[y0:y1 + 1]

    # ---- target: red box (mystery) or orange fish
    om = color_mask(sub, col["orange"], tol)
    rm = np.zeros_like(om)
    if use_red:
        a_ = sub.astype(np.int16)
        rr, gg, bb = a_[..., 0], a_[..., 1], a_[..., 2]
        rm = color_mask(sub, col["red"], tol) | ((rr >= 110) & (gg < rr * 0.38) & (bb < rr * 0.38))
    if use_red and rm.sum() >= 12 and rm.sum() > om.sum():
        tm, kind = rm, "red"
    else:
        tm, kind = om, "orange"
    tcols = tm.sum(axis=0)

    # ---- blue bar extent (rejoin pieces separated by the target sitting on top of the bar)
    colsb = bm[y0:y1 + 1].sum(axis=0) >= max(2, (y1 - y0 + 1) * 0.3)
    r = runs(colsb)
    if not r:
        return none
    merged = [r[0]]
    for a0, b0 in r[1:]:
        pa, pb = merged[-1]
        if a0 - pb > 1 and (tcols[pb + 1:a0] > 0).mean() >= 0.6:
            merged[-1] = (pa, b0)
        else:
            merged.append((a0, b0))
    bar = max(merged, key=lambda t: t[1] - t[0])

    if tcols.sum() < 12:
        return bar, None, (y0, y1), None, None
    xs = np.arange(len(tcols))
    target_x = float((xs * tcols).sum() / tcols.sum())
    strong = np.where(tcols >= max(1, tcols.max() * 0.2))[0]
    return bar, target_x, (y0, y1), kind, (int(strong[0]), int(strong[-1]))


# --------------------------------------------------------------------------
# Bot
# --------------------------------------------------------------------------
class Bot(threading.Thread):
    def __init__(self, cfg, log, on_tune=None):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.log = log
        self.on_tune = on_tune
        self.status = "Starting..."
        self.stop_flag = threading.Event()
        self.inp = Inputs()

    def stop(self):
        self.stop_flag.set()

    def wait(self, sec):
        end = time.perf_counter() + sec
        while time.perf_counter() < end and not self.stop_flag.is_set():
            time.sleep(0.05)

    def settle_ui(self, sct, region, z, col, tol, minpx):
        """Wait for the skill-check panel to finish animating in; returns (zone, frame, still_visible)."""
        c = self.cfg
        end = time.perf_counter() + 0.7
        prev, stable, img, seen = None, 0, None, True
        while time.perf_counter() < end and not self.stop_flag.is_set():
            img = grab(sct, region)
            z2 = find_green_zone(img, col, tol, minpx, c["green_detect"])
            seen = z2 is not None
            if not seen:
                prev, stable = None, 0
                time.sleep(0.008)
                continue
            cur = img[z2[2]:z2[3] + 1].astype(np.int16)
            if prev is not None and prev.shape == cur.shape and np.abs(cur - prev).max() <= 20 \
                    and abs(z2[0] - z[0]) <= 1:
                stable += 1
            else:
                stable = 0
            prev, z = cur, z2
            if stable >= 2:
                break
            time.sleep(0.008)
        return z, img, seen

    def write_trace(self, zone, target, lat, trace, note):
        try:
            path = os.path.join(os.path.dirname(CONFIG_FILE), "cast_trace.txt")
            t0 = trace[0][0] if trace else 0
            pts = " ".join(f"{(t - t0) * 1000:.0f}ms:{e}" for t, e in trace)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} zone={zone} target={target:.1f} latency={lat} {note}\n  {pts}\n")
        except Exception:
            pass

    def do_cast(self, equip, pre=False):
        """[pre-cast key] -> [equip rod] -> cast action. `pre` is True for re-casts."""
        c = self.cfg
        if pre and c["pre_cast_press"] and c["pre_cast_key"]:
            self.inp.tap(c["pre_cast_key"], 0.08)
            self.log(f"Pressed {c['pre_cast_key'].upper()} before recasting.")
            self.wait(float(c["pre_cast_delay"]))
            if self.stop_flag.is_set():
                return
        if equip and c["equip_rod"]:
            slot = str(max(1, min(9, int(c["rod_slot"]))))
            self.inp.tap(slot, 0.06)
            self.log(f"Equipped rod (hotbar {slot}).")
            self.wait(0.7)
        if self.stop_flag.is_set():
            return
        self.inp.tap(c["cast_action"], 0.08)
        self.log(f"Cast ({c['cast_action']}). Waiting for skill check...")

    def run(self):
        c = self.cfg
        col = c["colors"]
        tol, minpx = int(c["tolerance"]), int(c["min_pixels"])
        r1, r2 = c["region1"], c["region2"]
        reel_in = c["reel_input"]
        hold_right = c["hold_moves"] == "right"

        state = "WAIT"
        cooldown_until = 0.0
        zone = None
        charge_start = 0.0
        prev_edge, prev_t = None, None
        fish_prev = bar_prev = None
        fish_v = bar_v = 0.0
        direction = 0
        last_fish_seen = 0.0
        last_t = time.perf_counter()
        use_red = bool(c["red_enabled"])
        band = baseline = None
        samples = []
        peak = 0
        release_info = {}
        settle_start = 0.0
        mystery_logged = False
        try:                                   # 1 ms timer resolution on Windows
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
        trace = []
        awaiting1 = None      # time we last cast and are waiting for skill check 1
        bite_since = None     # time skill check 1 finished and we're waiting for a bite
        self.log("Macro running.")

        try:
            delay = float(c["start_delay"])
            if delay > 0:
                self.log(f"Starting in {delay:g}s - click into the game window now!")
                self.wait(delay)
            if c["auto_cast_on_start"] and not self.stop_flag.is_set():
                self.do_cast(True)
                awaiting1 = time.perf_counter()
            with mss.mss() as sct:
                while not self.stop_flag.is_set():
                    now = time.perf_counter()

                    # ---------------- WAIT ----------------
                    if state == "WAIT":
                        self.status = "Waiting for a skill check"
                        if r1 and now >= cooldown_until:
                            img = grab(sct, r1)
                            z = find_green_zone(img, col, tol, minpx, c["green_detect"])
                            if z:
                                # let the panel finish animating, then snapshot the EMPTY bar
                                z, img, seen = self.settle_ui(sct, r1, z, col, tol, minpx)
                                if seen and not self.stop_flag.is_set():
                                    zone = (z[0], z[1])
                                    y0, y1 = z[2], z[3]
                                    mid = (y0 + y1) // 2
                                    hh = max(3, min(24, y1 - y0 + 1))          # strip across the bar = fast capture
                                    ya = max(0, mid - hh // 2)
                                    yb = min(img.shape[0] - 1, ya + hh - 1)
                                    band = {"left": r1["left"], "top": r1["top"] + ya,
                                            "width": r1["width"], "height": yb - ya + 1}
                                    baseline = img[ya:yb + 1].copy()
                                    awaiting1 = None
                                    self.log(f"Skill check 1 found - green zone x={zone[0]}..{zone[1]} "
                                             f"(centre {sum(zone) / 2:.0f}). Holding {c['cast_key'].upper()}")
                                    self.inp.down(c["cast_key"])
                                    charge_start = time.perf_counter()
                                    samples = []
                                    trace = []
                                    peak = 0
                                    state = "CHARGE"
                                    continue
                        if r2:
                            img = grab(sct, r2)
                            b, fx, _, kind, _ext = find_bar_and_fish(img, col, tol, minpx, use_red)
                            if b and fx is not None:
                                awaiting1 = bite_since = None
                                mystery_logged = kind == "red"
                                self.log("Skill check 2 found - RED MYSTERY BOX! Tracking it."
                                         if kind == "red" else "Skill check 2 found. Tracking fish.")
                                fish_prev = bar_prev = None
                                fish_v = bar_v = 0.0
                                direction = 0
                                last_fish_seen = now
                                last_t = now
                                state = "TRACK"
                                continue

                        t1, tb = float(c["check1_timeout"]), float(c["bite_timeout"])
                        if awaiting1 and t1 > 0 and now - awaiting1 > t1:
                            self.log("No skill check appeared - casting again.")
                            self.do_cast(c["re_equip_each_cast"], pre=True)
                            awaiting1 = time.perf_counter()
                        elif bite_since and tb > 0 and now - bite_since > tb:
                            self.log("No bite for a long time - recasting.")
                            self.do_cast(c["re_equip_each_cast"], pre=True)
                            bite_since = None
                            awaiting1 = time.perf_counter()
                        time.sleep(0.02)                        # idle: ~50 checks/sec, light on the CPU

                    # ---------------- CHARGE ----------------
                    elif state == "CHARGE":
                        self.status = "Skill check 1 - charging the cast"
                        img = grab(sct, band)                  # thin strip = very fast
                        t_now = time.perf_counter()
                        edge = fill_edge(img, baseline, col, tol, minpx, c["fill_detect"], int(c["diff_threshold"]))
                        target = zone[0] + (zone[1] - zone[0]) * float(c["release_at"])
                        lat = float(c["release_latency"])
                        do_release = False
                        if edge is not None:
                            if samples and edge - samples[-1][1] > 120:
                                edge = None                    # impossible jump = glitch, ignore this frame
                            elif samples and edge < samples[-1][1] - 30:
                                samples = []                   # bar looped back to the start: re-measure
                        if edge is not None:
                            if samples:
                                edge = max(edge, samples[-1][1])
                            samples.append((t_now, edge))
                            trace.append((t_now, edge))
                            samples = [sm for sm in samples if t_now - sm[0] <= 0.15]   # last 150 ms
                            vel = 0.0
                            if edge >= target:
                                do_release = True               # already there -> release immediately
                            elif len(samples) >= 3 and samples[-1][0] - samples[0][0] >= 0.04:
                                ts = np.array([t for t, _ in samples])
                                es = np.array([e for _, e in samples], dtype=float)
                                vel, icpt = np.polyfit(ts - ts[0], es, 1)
                                if vel > 30:
                                    # when will the bar cross the target, and when must we let go?
                                    t_release = ts[0] + (target - icpt) / vel - lat
                                    frame_dt = (ts[-1] - ts[0]) / (len(ts) - 1)
                                    if t_release <= t_now + frame_dt:
                                        end_wait = min(t_release, time.perf_counter() + 0.06)
                                        while time.perf_counter() < end_wait:
                                            pass                # spin for sub-frame precision
                                        do_release = True
                            if do_release:
                                t_rel = time.perf_counter()
                                self.inp.up(c["cast_key"])
                                release_info = {"edge": edge, "vel": float(vel), "target": target, "lat": lat}
                                peak = edge
                                bite_since = t_rel
                                settle_start = t_rel
                                state = "SETTLE"
                                continue
                        if t_now - charge_start > 6:
                            self.inp.up(c["cast_key"])
                            why = ("never saw the bar fill - check the region, or try Fill detection = color"
                                   if not samples else "bar never reached the green zone")
                            self.log(f"Cast timed out: {why}.")
                            if c["debug_trace"] and trace:
                                self.write_trace(zone, target, lat, trace, "TIMEOUT")
                            cooldown_until = t_now + 1.5
                            state = "WAIT"

                    # ---------------- SETTLE (see where the bar really stopped) ----------------
                    elif state == "SETTLE":
                        self.status = "Skill check 1 - released"
                        img = grab(sct, band)
                        edge = fill_edge(img, baseline, col, tol, minpx, c["fill_detect"], int(c["diff_threshold"]))
                        if edge is not None:
                            peak = max(peak, edge)
                        if now - settle_start > 0.25:
                            ri = release_info
                            expected = ri["edge"] + max(ri["vel"], 0) * ri["lat"]
                            measurable = peak - ri["edge"] >= 3        # did the bar visibly keep moving after release?
                            exp_in = zone[0] <= expected <= zone[1]
                            if measurable:
                                inside = zone[0] <= peak <= zone[1]
                                verdict = ("INSIDE the green" if inside else
                                           "overshot the green" if peak > zone[1] else "stopped short of the green")
                                self.log(f"Released at x={ri['edge']}, bar stopped at x={peak}, "
                                         f"green x={zone[0]}..{zone[1]} -> {verdict}")
                            else:
                                self.log(f"Released at x={ri['edge']} (speed {ri['vel']:.0f}px/s), expected stop "
                                         f"x~{expected:.0f}, green x={zone[0]}..{zone[1]} -> "
                                         f"{'on target' if exp_in else 'OFF target - adjust release latency'}")
                            if c["auto_tune"] and measurable and ri["vel"] > 20:
                                new_lat = ri["lat"] + 0.6 * (peak - ri["target"]) / ri["vel"]
                                new_lat = round(max(0.0, min(0.35, new_lat)), 3)
                                if abs(new_lat - ri["lat"]) >= 0.004:
                                    c["release_latency"] = new_lat
                                    self.log(f"Auto-tune: release timing {ri['lat']} -> {new_lat}s")
                                    if self.on_tune:
                                        self.on_tune(new_lat)
                            if c["debug_trace"] and trace:
                                self.write_trace(zone, ri["target"], ri["lat"], trace,
                                                 f"released_at={ri['edge']} vel={ri['vel']:.0f} expected={expected:.0f}")
                            cooldown_until = time.perf_counter() + 1.5
                            state = "WAIT"

                    # ---------------- TRACK ----------------
                    elif state == "TRACK":
                        self.status = "Skill check 2 - reeling" + (" (RED mystery box)" if mystery_logged else "")
                        img = grab(sct, r2)
                        bar, fish_c, _, kind, _ext = find_bar_and_fish(img, col, tol, minpx, use_red)
                        if kind == "red" and not mystery_logged:
                            mystery_logged = True
                            self.log("Target turned RED (mystery box) - still tracking.")
                        dt = max(now - last_t, 1e-3)
                        last_t = now

                        if fish_c is not None:
                            last_fish_seen = now
                        if bar and fish_c is not None:
                            bar_c = (bar[0] + bar[1]) / 2
                            bar_w = bar[1] - bar[0]
                            if fish_prev is not None:
                                fish_v = 0.5 * fish_v + 0.5 * (fish_c - fish_prev) / dt
                                bar_v = 0.5 * bar_v + 0.5 * (bar_c - bar_prev) / dt
                            fish_prev, bar_prev = fish_c, bar_c

                            lead = float(c["lead"])
                            err = (fish_c + fish_v * lead) - (bar_c + bar_v * lead)
                            dz = max(float(c["deadzone"]), float(c["deadzone_frac"]) * bar_w)

                            # hysteresis: start moving past the deadzone, keep going until we cross the target
                            if direction == 0:
                                if err > dz:
                                    direction = 1
                                elif err < -dz:
                                    direction = -1
                            elif direction * err <= 0:
                                direction = 0

                            if c["reel_mode"] == "two_key":
                                self.inp.drive(direction, c["left_key"], c["right_key"])
                            else:
                                move_right = direction > 0
                                hold = move_right if hold_right else (direction < 0)
                                if direction == 0:
                                    self.inp.up(reel_in)
                                elif hold:
                                    self.inp.down(reel_in)
                                else:
                                    self.inp.up(reel_in)

                        if now - last_fish_seen > float(c["lost_timeout"]):
                            self.inp.release_all()
                            self.log("Reel finished.")
                            bite_since = None
                            if c["auto_recast"]:
                                self.wait(float(c["recast_delay"]))
                                if not self.stop_flag.is_set():
                                    self.do_cast(c["re_equip_each_cast"], pre=True)
                                    awaiting1 = time.perf_counter()
                            cooldown_until = 0.0               # skill check 1 can appear right after the cast
                            state = "WAIT"
                        time.sleep(0.002)
        except Exception as e:
            self.log(f"ERROR: {e}")
        finally:
            self.inp.release_all()
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
            self.log("Macro stopped.")


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
APP_TITLE = "XZX Fishing"
THEME = {"bg": "#0b0614", "panel": "#160d28", "panel2": "#241940", "fg": "#f4eeff", "muted": "#a99cc4",
         "accent": "#b57bff", "green": "#6ee7b7", "red": "#f0587e", "border": "#2e2250", "log": "#070510"}


def apply_theme(root):
    T = THEME
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    root.configure(bg=T["bg"])
    st.configure(".", background=T["bg"], foreground=T["fg"], fieldbackground=T["panel2"],
                 bordercolor=T["border"], lightcolor=T["border"], darkcolor=T["border"],
                 troughcolor=T["panel"], focuscolor=T["bg"], font=("Segoe UI", 9))
    st.configure("TFrame", background=T["bg"])
    st.configure("TLabel", background=T["bg"], foreground=T["fg"])
    st.configure("Muted.TLabel", foreground=T["muted"])
    st.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
    st.configure("TLabelframe", background=T["bg"], bordercolor=T["border"])
    st.configure("TLabelframe.Label", background=T["bg"], foreground=T["accent"], font=("Segoe UI Semibold", 9))
    st.configure("TButton", background=T["panel2"], foreground=T["fg"], padding=(10, 5), borderwidth=1, relief="flat")
    st.map("TButton", background=[("active", "#333b4a"), ("pressed", "#3b4558")])
    st.configure("Start.TButton", background="#5a2fd6", foreground="#ffffff", font=("Segoe UI Semibold", 12), padding=(12, 9))
    st.map("Start.TButton", background=[("active", "#b57bff"), ("pressed", "#4a24c0")])
    st.configure("Stop.TButton", background=T["red"], foreground="#ffffff", font=("Segoe UI Semibold", 12), padding=(12, 9))
    st.map("Stop.TButton", background=[("active", "#f76363"), ("pressed", "#dc2626")])
    st.configure("TNotebook", background=T["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", background=T["panel"], foreground=T["muted"], padding=(16, 7), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", T["panel2"])], foreground=[("selected", T["fg"])])
    st.configure("TEntry", fieldbackground=T["panel2"], foreground=T["fg"], insertcolor=T["fg"], padding=3)
    st.configure("TCombobox", fieldbackground=T["panel2"], background=T["panel2"], foreground=T["fg"],
                 arrowcolor=T["fg"], padding=3)
    st.map("TCombobox", fieldbackground=[("readonly", T["panel2"])], foreground=[("readonly", T["fg"])])
    st.configure("TCheckbutton", background=T["bg"], foreground=T["fg"])
    st.map("TCheckbutton", background=[("active", T["bg"])],
           indicatorbackground=[("selected", T["accent"]), ("!selected", T["panel2"])])
    st.configure("Vertical.TScrollbar", background="#3a2a60", troughcolor=T["panel"], bordercolor=T["panel"],
                 arrowcolor=T["fg"], lightcolor="#3a2a60", darkcolor="#3a2a60", gripcount=0)
    st.map("Vertical.TScrollbar", background=[("active", "#4a3680")])
    st.configure("TCheckbutton", indicatorbackground=T["panel2"], indicatorforeground=T["accent"])
    root.option_add("*TCombobox*Listbox.background", T["panel2"])
    root.option_add("*TCombobox*Listbox.foreground", T["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", T["accent"])


def acquire_single_instance():
    """Only one copy at a time (two copies = two macros fighting over your keyboard)."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.CreateMutexW(None, False, "Local\\FishingMacro_SingleInstance_v1")
        if k32.GetLastError() == 183:        # ERROR_ALREADY_EXISTS
            return None
        return handle
    except Exception:
        return True                           # not Windows / can't check -> allow


class App:
    LOG_LINES = 400

    def __init__(self, license=None):
        self.cfg, self.first_run = load_config()
        self.license = license or {}
        self.casts = 0
        self.catches = 0
        self.session_start = None
        self.bot = None
        self.logq = queue.Queue()          # bot/hotkey threads -> window. Tk itself is only touched
        self.cmdq = queue.Queue()          # from the main thread, so nothing can hang or get stuck.
        self.vars, self.swatches, self.bools, self.reg_lbl = {}, {}, {}, {}
        self.preview_win = None
        self.overlay = None
        self._shown = (None, None)
        self.hk = None

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.scale = self.get_scale()
        apply_theme(self.root)
        self.set_icon()
        self.place_window()
        self.build_ui()
        self.setup_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(60, self.tick)
        if self.license.get("key"):
            threading.Thread(target=self._heartbeat, daemon=True).start()

    def _heartbeat(self):
        """Every 60s, re-check the key. If it's disabled / expired / locked to another PC,
        stop the macro and close the app. Also fail-closed: if the server can't confirm the
        key for the whole grace window (blocked domain / no internet / server down), stop too,
        so a cracked copy that cuts off the server can't just keep running. Nothing on the
        computer is touched either way."""
        last_good = time.time()
        while True:
            time.sleep(60)
            code = self.license.get("key")
            if not code:
                continue
            r = lic_api("/api/check", {"code": code, "session": self.license.get("session", ""),
                                       "product": PRODUCT, "hwid": get_hwid()})
            if r.startswith("REVOKED|"):
                self.cmdq.put(("revoked", r.split("|", 1)[1]))
                return
            if r == "OK":
                last_good = time.time()
            elif time.time() - last_good > OFFLINE_GRACE_SEC:
                self.cmdq.put(("revoked", "Couldn't reach the license server for 30 minutes"))
                return

    # ------------------------------------------------------------------ window
    def get_scale(self):
        try:
            return max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except Exception:
            return 1.0

    def set_icon(self):
        try:
            self.root.iconbitmap(os.path.join(BUNDLE_DIR, "icon.ico"))
        except Exception:
            pass

    def place_window(self):
        r, k = self.root, self.scale
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(int(700 * k), sw - 40), min(int(790 * k), sh - 90)
        x, y = (sw - w) // 2, max(10, (sh - h) // 3)
        geo = self.cfg.get("window_geometry")
        try:
            gx, gy, gw, gh = [int(v) for v in geo]
            if -20 <= gx <= sw - 150 and 0 <= gy <= sh - 100:      # only reuse if it's actually on screen
                x, y = gx, gy
                w, h = min(max(gw, int(600 * k)), sw), min(max(gh, int(560 * k)), sh)
        except Exception:
            pass
        r.geometry(f"{w}x{h}+{x}+{y}")
        r.minsize(int(600 * k), int(540 * k))
        r.attributes("-topmost", bool(self.cfg.get("always_on_top")))

    def show_window(self):
        r = self.root
        try:
            r.deiconify()
            r.lift()
            r.attributes("-topmost", True)                       # pop to front once...
            r.after(250, lambda: r.attributes("-topmost", bool(self.cfg.get("always_on_top"))))   # ...then let go
            r.focus_force()
        except Exception:
            pass

    # ------------------------------------------------------------------ UI
    def _vgrad(self, cv, w, h, c1, c2, steps=44):
        try:
            r1, g1, b1 = self.root.winfo_rgb(c1)
            r2, g2, b2 = self.root.winfo_rgb(c2)
        except Exception:
            return
        for i in range(steps):
            t = i / (steps - 1)
            col = "#%02x%02x%02x" % (int((r1 + (r2 - r1) * t) / 256),
                                     int((g1 + (g2 - g1) * t) / 256),
                                     int((b1 + (b2 - b1) * t) / 256))
            cv.create_rectangle(0, int(h * i / steps), w, int(h * (i + 1) / steps) + 1, outline="", fill=col)

    def _paint_power(self):
        cv = getattr(self, "powerbar", None)
        if cv is None:
            return
        cv.delete("all")
        w = cv.winfo_width()
        if w <= 1:
            return
        running = bool(self.bot and self.bot.is_alive())
        cv.create_rectangle(0, 1, w, 7, outline="", fill=THEME["panel2"])
        if running:
            cv.create_rectangle(0, 1, w, 7, outline="", fill=THEME["green"])
        else:
            cv.create_rectangle(0, 1, max(8, int(w * 0.18)), 7, outline="", fill=THEME["accent"])

    def build_ui(self):
        T, r = THEME, self.root
        pad = {"padx": 12, "pady": 4}

        # ---- XZX header (purple glow) ----
        head = tk.Canvas(r, bg=T["bg"], highlightthickness=0, bd=0, height=76)
        head.pack(fill="x")
        self._vgrad(head, 1400, 76, "#2a1458", T["bg"])
        head.create_text(22, 30, text="XZX", anchor="w", fill=T["accent"], font=("Segoe UI Semibold", 21, "bold"))
        head.create_text(80, 23, text="Fishing Macro", anchor="w", fill=T["fg"], font=("Segoe UI Semibold", 14))
        head.create_text(81, 47, text="Auto cast \u00b7 track \u00b7 collect", anchor="w", fill=T["muted"], font=("Segoe UI", 8))
        self.pill = tk.Label(head, text="IDLE", bg=T["panel2"], fg=T["muted"], font=("Segoe UI", 8, "bold"), padx=10, pady=2)
        self._pill_id = head.create_window(1380, 22, window=self.pill, anchor="e")
        head.bind("<Configure>", lambda e: head.coords(self._pill_id, e.width - 16, 22))

        # ---- hero status card ----
        hero = tk.Frame(r, bg=T["panel"])
        hero.pack(fill="x", padx=12, pady=(8, 4))
        inner = tk.Frame(hero, bg=T["panel"])
        inner.pack(fill="x", padx=14, pady=12)
        tk.Label(inner, text="STATUS", bg=T["panel"], fg=T["muted"], font=("Segoe UI", 7, "bold")).pack(anchor="w")
        self.status_lbl = tk.Label(inner, text="READY", bg=T["panel"], fg=T["fg"], font=("Segoe UI Semibold", 22))
        self.status_lbl.pack(anchor="w")
        self.detail_lbl = tk.Label(inner, text="Press F6 to start", bg=T["panel"], fg=T["muted"], font=("Segoe UI", 9))
        self.detail_lbl.pack(anchor="w")
        self.powerbar = tk.Canvas(inner, bg=T["panel"], highlightthickness=0, bd=0, height=8)
        self.powerbar.pack(fill="x", pady=(10, 0))
        self.powerbar.bind("<Configure>", lambda e: self._paint_power())

        # ---- stat tiles ----
        tiles = tk.Frame(r, bg=T["bg"])
        tiles.pack(fill="x", padx=8)
        self.tile_lbl = {}
        for key, name in (("casts", "CASTS"), ("catches", "CATCHES"), ("time", "SESSION")):
            cfr = tk.Frame(tiles, bg=T["panel"])
            cfr.pack(side="left", expand=True, fill="x", padx=4)
            tk.Label(cfr, text=name, bg=T["panel"], fg=T["muted"], font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
            v = tk.Label(cfr, text=("00:00:00" if key == "time" else "0"), bg=T["panel"], fg=T["fg"],
                         font=("Segoe UI Semibold", 13 if key == "time" else 15))
            v.pack(anchor="w", padx=12, pady=(0, 10))
            self.tile_lbl[key] = v

        # ---- START / STOP ----
        self.btn = ttk.Button(r, text="START  (F6)", style="Start.TButton", command=self.toggle)
        self.btn.pack(fill="x", padx=12, pady=(6, 4))

        nb = ttk.Notebook(r)
        nb.pack(fill="x", **pad)
        self.nb = nb
        tabs = {}
        for key, title in (("setup", "  Setup  "), ("cast", "  Rod & casting  "),
                           ("skill", "  Skill checks  "), ("app", "  App  ")):
            tabs[key] = ttk.Frame(nb)
            nb.add(tabs[key], text=title)

        # ---- Setup tab: regions + colors
        f = ttk.LabelFrame(tabs["setup"], text="1. Screen regions (drag a box around each skill check)")
        f.pack(fill="x", **pad)
        for key, name in (("region1", "Skill check 1 (cast bar: fill + green zone)"),
                          ("region2", "Skill check 2 (blue bar + fish)")):
            row = ttk.Frame(f)
            row.pack(fill="x", padx=6, pady=3)
            ttk.Label(row, text=name, width=38).pack(side="left")
            lbl = ttk.Label(row, text=self.fmt_region(self.cfg[key]), width=22, style="Muted.TLabel")
            lbl.pack(side="left")
            self.reg_lbl[key] = lbl
            ttk.Button(row, text="Set region",
                       command=lambda k=key: self.capture("region", k)).pack(side="right")

        f = ttk.LabelFrame(tabs["setup"], text="2. Colors (click Pick, then click that color on screen)")
        f.pack(fill="x", **pad)
        names = {"fill": "Filling bar (optional*)", "green": "Green zone",
                 "blue": "Blue bar (yours)", "orange": "Orange fish",
                 "red": "Red mystery box"}
        for key, name in names.items():
            row = ttk.Frame(f)
            row.pack(fill="x", padx=6, pady=3)
            ttk.Label(row, text=name, width=22).pack(side="left")
            sw = tk.Label(row, width=6, bg=self.hexcol(self.cfg["colors"][key]), relief="flat", bd=0)
            sw.pack(side="left", padx=6)
            self.swatches[key] = sw
            ttk.Button(row, text="Pick", command=lambda k=key: self.capture("color", k)).pack(side="left")
        ttk.Label(tabs["setup"], style="Muted.TLabel", justify="left",
                  text="*Filling bar color is only needed if Fill detection is set to 'color'.\n"
                       "Tip: use Live preview to check it sees the bars. Get the skill check on screen\n"
                       "first - the app counts down 3 seconds before taking the screenshot.").pack(anchor="w", padx=14, pady=4)

        # ---- helpers for the other tabs
        def panel(parent, title):
            fr = ttk.LabelFrame(parent, text=title)
            fr.pack(fill="x", **pad)
            return {"fr": fr, "n": 0}

        def add(p, label, key, kind=str, values=None):
            rr, cc = divmod(p["n"], 2)
            p["n"] += 1
            ttk.Label(p["fr"], text=label).grid(row=rr, column=cc * 2, sticky="w", padx=(8, 4), pady=3)
            v = tk.StringVar(value=str(self.cfg[key]))
            if values:
                w = ttk.Combobox(p["fr"], textvariable=v, values=values, width=11)
            else:
                w = ttk.Entry(p["fr"], textvariable=v, width=12)
            w.grid(row=rr, column=cc * 2 + 1, sticky="w", padx=(0, 12), pady=3)
            self.vars[key] = (v, kind)

        def check(p, label, key):
            if p["n"] % 2:
                p["n"] += 1
            rr = p["n"] // 2
            p["n"] += 2
            v = tk.BooleanVar(value=bool(self.cfg[key]))
            ttk.Checkbutton(p["fr"], text=label, variable=v).grid(
                row=rr, column=0, columnspan=4, sticky="w", padx=8, pady=2)
            self.bools[key] = v

        # ---- Rod & casting tab
        p = panel(tabs["cast"], "Rod")
        check(p, "Press the hotbar key to equip the rod", "equip_rod")
        add(p, "Rod hotbar slot (1-5)", "rod_slot", int, ["1", "2", "3", "4", "5"])
        add(p, "Cast action", "cast_action", str, ["mouse_left", "e", "f", "space"])
        check(p, "Equip + cast automatically when the macro starts", "auto_cast_on_start")
        check(p, "Cast again automatically after each catch", "auto_recast")
        check(p, "Re-equip the rod (hotbar key) before every cast", "re_equip_each_cast")
        check(p, "Press a key before every recast (not the first cast)", "pre_cast_press")
        add(p, "Key to press before recasting", "pre_cast_key")
        add(p, "Wait after that key (s)", "pre_cast_delay", float)
        p = panel(tabs["cast"], "Timing")
        add(p, "Start delay (s)", "start_delay", float)
        add(p, "Delay before recast (s)", "recast_delay", float)
        add(p, "Recast if no skill check 1 (s)", "check1_timeout", float)
        add(p, "Recast if no bite (s)", "bite_timeout", float)
        ttk.Label(tabs["cast"], style="Muted.TLabel", justify="left",
                  text="Start delay gives you time to click into the game after pressing Start.\n"
                       "Set a timeout to 0 to turn that auto-retry off.").pack(anchor="w", padx=14)

        # ---- Skill checks tab
        p = panel(tabs["skill"], "Skill check 1 - cast (hold, release in the green)")
        add(p, "Key to hold", "cast_key")
        add(p, "Release point in green (0-1)", "release_at", float)
        add(p, "Latency compensation (s)", "release_latency", float)
        add(p, "Green detection", "green_detect", str, ["auto", "color"])
        add(p, "Fill detection", "fill_detect", str, ["auto", "color"])
        add(p, "Fill change threshold", "diff_threshold", int)
        check(p, "Auto-tune release timing (only if bar stays visible)", "auto_tune")
        check(p, "Save cast debug trace (cast_trace.txt)", "debug_trace")
        p = panel(tabs["skill"], "Skill check 2 - reel (keep blue bar on target)")
        add(p, "Reel mode", "reel_mode", str, ["two_key", "hold"])
        add(p, "Left key", "left_key")
        add(p, "Right key", "right_key")
        add(p, "Hold-mode input", "reel_input", str, ["mouse_left", "space", "e", "f"])
        add(p, "Hold-mode: holding moves", "hold_moves", str, ["right", "left"])
        add(p, "Deadzone min (px)", "deadzone", float)
        add(p, "Deadzone (x box width)", "deadzone_frac", float)
        add(p, "Prediction lead (s)", "lead", float)
        add(p, "Fish lost timeout (s)", "lost_timeout", float)
        check(p, "Also track the RED mystery box", "red_enabled")
        p = panel(tabs["skill"], "Detection")
        add(p, "Color tolerance (0-255)", "tolerance", int)
        add(p, "Min pixels per column", "min_pixels", int)

        # ---- App tab
        p = panel(tabs["app"], "Window")
        check(p, "Keep this window on top of other windows", "always_on_top")
        check(p, "Minimize this window when the macro starts", "minimize_on_start")
        ttk.Label(tabs["app"], style="Muted.TLabel", justify="left",
                  text="Hotkeys work even while the game has focus:\n"
                       "    F6  start / stop the macro\n"
                       "    F8  bring this window back to the front").pack(anchor="w", padx=14, pady=6)
        self.bools["always_on_top"].trace_add("write", lambda *a: self.root.attributes(
            "-topmost", bool(self.bools["always_on_top"].get())))

        # ---- tools row (START is up top in the header)
        tools = ttk.Frame(r)
        tools.pack(fill="x", padx=12, pady=2)
        for text, cmd in (("Test detection", self.test_detect), ("Live preview", self.live_preview),
                          ("Export config", self.export_config), ("Import config", self.import_config),
                          ("Config folder", self.open_config_folder), ("Reset", self.reset_config)):
            ttk.Button(tools, text=text, command=cmd).pack(side="left", padx=(0, 4))

        # ---- log
        lf = ttk.Frame(r)
        lf.pack(fill="both", expand=True, padx=12, pady=(6, 2))
        sb = ttk.Scrollbar(lf, orient="vertical")
        self.logbox = tk.Text(lf, height=6, state="disabled", bg=T["log"], fg="#9be39b", relief="flat",
                              wrap="word", font=("Consolas", 9), padx=8, pady=6, yscrollcommand=sb.set,
                              highlightthickness=1, highlightbackground=T["border"])
        sb.config(command=self.logbox.yview)
        sb.pack(side="right", fill="y")
        self.logbox.pack(side="left", fill="both", expand=True)
        ttk.Label(r, text="F6 start/stop   \u2022   F8 show this window", style="Muted.TLabel").pack(
            anchor="w", padx=14, pady=(0, 2))
        tk.Label(r, text="XZX Macro  \u00b7  by Coop", bg=T["bg"], fg=T["muted"],
                 font=("Segoe UI", 7)).pack(pady=(0, 8))

        self.log("Ready. Config file: " + CONFIG_FILE)
        if self.first_run:
            self.log("First launch: a config file was created automatically.")
        if not self.cfg["region1"] or not self.cfg["region2"]:
            self.log("Set both regions + colors on the Setup tab, then press F6.")
            if self.first_run:
                self.root.after(400, lambda: messagebox.showinfo(
                    "Welcome to Fishing Macro",
                    "First-time setup:\n\n"
                    "1. Setup tab -> 'Set region' for both skill checks\n"
                    "2. 'Pick' each color (or use the defaults)\n"
                    "3. Rod & casting tab -> choose your rod's hotbar slot\n"
                    "4. Press F6 to start / stop\n\n"
                    "Your settings save automatically.", parent=self.root))

    # ------------------------------------------------------------------ config actions
    def refresh_ui(self):
        for key, (v, _) in self.vars.items():
            v.set(str(self.cfg[key]))
        for key, v in self.bools.items():
            v.set(bool(self.cfg[key]))
        for key, sw in self.swatches.items():
            sw.config(bg=self.hexcol(self.cfg["colors"][key]))
        for key, lbl in self.reg_lbl.items():
            lbl.config(text=self.fmt_region(self.cfg[key]))

    def export_config(self):
        self.read_settings()
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile=CONFIG_NAME,
                                            filetypes=[("Config", "*.json")])
        if path and save_config(self.cfg, path):
            self.log(f"Config exported to {path}")

    def import_config(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Config", "*.json")])
        if not path:
            return
        try:
            new = json.loads(json.dumps(DEFAULTS))
            read_config_file(path, new)
            self.cfg = new
            self.refresh_ui()
            save_config(self.cfg)
            self.log(f"Config imported from {path}")
        except Exception as e:
            self.log(f"Could not import config: {e}")

    def reset_config(self):
        if messagebox.askyesno("Reset", "Reset everything to defaults?", parent=self.root):
            self.cfg = json.loads(json.dumps(DEFAULTS))
            self.refresh_ui()
            save_config(self.cfg)
            self.log("Settings reset to defaults.")

    def open_config_folder(self):
        try:
            os.startfile(os.path.dirname(CONFIG_FILE))
        except Exception as e:
            self.log(f"Config folder: {os.path.dirname(CONFIG_FILE)} ({e})")

    @staticmethod
    def hexcol(c):
        return "#%02x%02x%02x" % tuple(c)

    @staticmethod
    def fmt_region(reg):
        if not reg:
            return "not set"
        return f"{reg['width']}x{reg['height']} @ {reg['left']},{reg['top']}"

    def read_settings(self):
        for key, (v, kind) in self.vars.items():
            try:
                self.cfg[key] = kind(v.get().strip())
            except Exception:
                self.log(f"Invalid value for {key}, using previous.")
        for key, v in self.bools.items():
            self.cfg[key] = bool(v.get())
        self.cfg["cast_key"] = self.cfg["cast_key"].lower()
        self.cfg["cast_action"] = self.cfg["cast_action"].lower()
        self.cfg["pre_cast_key"] = self.cfg["pre_cast_key"].lower()
        for k in ("green_detect", "fill_detect"):
            if self.cfg[k] not in ("auto", "color"):
                self.cfg[k] = "auto"
        self.cfg["left_key"] = self.cfg["left_key"].lower()
        self.cfg["right_key"] = self.cfg["right_key"].lower()
        save_config(self.cfg)

    # ------------------------------------------------------------------ logging + main-thread pump
    def log(self, msg):            # safe to call from any thread
        self.logq.put(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def tick(self):
        """Runs every 60 ms on the main thread: logs, hotkey commands, status."""
        try:
            while True:
                cmd = self.cmdq.get_nowait()
                if cmd == "toggle":
                    self.toggle()
                elif cmd == "show":
                    self.show_window()
                elif isinstance(cmd, tuple) and cmd[0] == "tune":
                    self.cfg["release_latency"] = cmd[1]
                    self.vars["release_latency"][0].set(str(cmd[1]))
                    save_config(self.cfg)
                elif isinstance(cmd, tuple) and cmd[0] == "revoked":
                    if self.bot and self.bot.is_alive():
                        self.bot.stop()
                    try:
                        messagebox.showwarning(
                            APP_TITLE,
                            "You've been logged out.\n\n%s\n\nOpen a ticket in %s if you think this is a mistake."
                            % (cmd[1], DISCORD_INVITE), parent=self.root)
                    except Exception:
                        pass
                    self.on_close()
                    return
        except queue.Empty:
            pass

        lines = []
        try:
            while len(lines) < 60:
                lines.append(self.logq.get_nowait())
        except queue.Empty:
            pass
        if lines:
            for ln in lines:                                 # count casts / catches off the bot's own log lines
                low = ln.lower()
                if "cast (" in low:
                    self.casts += 1
                    self.tile_lbl["casts"].config(text=str(self.casts))
                if "reel finished" in low or "claim" in low:
                    self.catches += 1
                    self.tile_lbl["catches"].config(text=str(self.catches))
            self.logbox.config(state="normal")
            self.logbox.insert("end", "\n".join(lines) + "\n")
            total = int(self.logbox.index("end-1c").split(".")[0])
            if total > self.LOG_LINES:                       # keep the log small so the window stays fast
                self.logbox.delete("1.0", f"{total - self.LOG_LINES}.0")
            self.logbox.see("end")
            self.logbox.config(state="disabled")

        running = bool(self.bot and self.bot.is_alive())
        detail = (self.bot.status if running else "Press F6 to start")
        if (running, detail) != self._shown:
            if running != self._shown[0]:
                self.btn.config(text="STOP  (F6)" if running else "START  (F6)",
                                style="Stop.TButton" if running else "Start.TButton")
                self.status_lbl.config(text="RUNNING" if running else "READY",
                                       fg=THEME["green"] if running else THEME["fg"])
                self.pill.config(text="\u25cf RUNNING" if running else "IDLE",
                                 fg=THEME["green"] if running else THEME["muted"])
                self.root.title(f"{APP_TITLE} - {'RUNNING' if running else 'stopped'}")
                if running and self.session_start is None:
                    self.session_start = time.time()
                self._paint_power()
            self.detail_lbl.config(text=detail, fg=THEME["fg"] if running else THEME["muted"])
            self._shown = (running, detail)
        if self.session_start is not None:                   # live session timer
            s = int(time.time() - self.session_start)
            self.tile_lbl["time"].config(text="%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60))
        self.root.after(60, self.tick)

    # ------------------------------------------------------------------ start / stop / hotkeys
    def toggle(self):
        if self.bot and self.bot.is_alive():
            self.bot.stop()
            self.log("Stopping...")
            return
        self.read_settings()
        if not self.cfg["region1"] or not self.cfg["region2"]:
            self.log("Set both regions first (Setup tab).")
            self.show_window()
            return
        self.bot = Bot(dict(self.cfg), self.log, lambda v: self.cmdq.put(("tune", v)))
        self.bot.start()
        if self.cfg["minimize_on_start"]:
            self.root.iconify()                              # get out of the way of the game

    def setup_hotkeys(self):
        try:
            from pynput import keyboard
            self.hk = keyboard.GlobalHotKeys({"<f6>": lambda: self.cmdq.put("toggle"),
                                              "<f8>": lambda: self.cmdq.put("show")})
            self.hk.daemon = True
            self.hk.start()
        except Exception as e:
            self.log(f"Hotkeys unavailable: {e}")

    # ------------------------------------------------------------------ test + preview
    def test_detect(self):
        self.read_settings()
        c, col = self.cfg, self.cfg["colors"]
        tol, mp = c["tolerance"], c["min_pixels"]
        try:
            with mss.mss() as sct:
                if c["region1"]:
                    img = grab(sct, c["region1"])
                    z = find_green_zone(img, col, tol, mp, c["green_detect"])
                    self.log("Check 1 -> green zone: " + (f"x={z[0]}..{z[1]}, rows {z[2]}..{z[3]}" if z else "NOT FOUND"))
                if c["region2"]:
                    img = grab(sct, c["region2"])
                    bar, fx, rows, kind, ext = find_bar_and_fish(img, col, tol, mp, c["red_enabled"])
                    self.log(f"Check 2 -> blue box: {bar}  target: {None if fx is None else round(fx)} "
                             f"({kind}{' box' if kind == 'red' else ' fish'})")
            self.log("(None / NOT FOUND = not visible in that region right now)")
        except Exception as e:
            self.log(f"Test failed: {e}")

    def live_preview(self):
        """What the macro sees: yellow = green zone, white = blue bar, magenta = target."""
        if self.preview_win is not None:
            try:
                self.preview_win.lift()
                return
            except Exception:
                self.preview_win = None
        self.read_settings()
        from PIL import ImageDraw
        c, col = self.cfg, self.cfg["colors"]
        win = tk.Toplevel(self.root)
        win.title("Live preview")
        win.configure(bg=THEME["bg"])
        win.attributes("-topmost", True)
        win.bind("<Escape>", lambda e: close())
        lbl = tk.Label(win, bg=THEME["bg"])
        lbl.pack(padx=6, pady=6)
        tk.Label(win, text="yellow = green zone   white = blue bar   magenta = target   (Esc closes)",
                 bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 8)).pack(pady=(0, 6))
        sct = mss.mss()
        state = {"alive": True}
        self.preview_win = win
        win.focus_force()

        def close():
            state["alive"] = False
            self.preview_win = None
            try:
                sct.close()
            except Exception:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close)

        def tick():
            if not state["alive"]:
                return
            panels = []
            tol, mp = c["tolerance"], c["min_pixels"]
            try:
                if c["region1"]:
                    img = grab(sct, c["region1"])
                    pil = Image.fromarray(np.ascontiguousarray(img))
                    d = ImageDraw.Draw(pil)
                    z = find_green_zone(img, col, tol, mp, c["green_detect"])
                    if z:
                        d.rectangle((z[0], z[2], z[1], z[3]), outline=(255, 255, 0), width=2)
                    panels.append(pil)
                if c["region2"]:
                    img = grab(sct, c["region2"])
                    pil = Image.fromarray(np.ascontiguousarray(img))
                    d = ImageDraw.Draw(pil)
                    bar, fx, rows, kind, ext = find_bar_and_fish(img, col, tol, mp, c["red_enabled"])
                    if bar and rows:
                        d.rectangle((bar[0], rows[0], bar[1], rows[1]), outline=(255, 255, 255), width=2)
                    if fx is not None:
                        if ext and rows:
                            d.rectangle((ext[0], rows[0], ext[1], rows[1]), outline=(255, 0, 255), width=2)
                        d.line((fx, 0, fx, pil.height), fill=(255, 0, 0), width=2)
                    panels.append(pil)
                if panels:
                    w = max(p.width for p in panels)
                    h = sum(p.height for p in panels)
                    out = Image.new("RGB", (w, h))
                    y = 0
                    for p in panels:
                        out.paste(p, (0, y))
                        y += p.height
                    if out.width > 900:                       # keep the preview light
                        out = out.resize((900, int(out.height * 900 / out.width)))
                    ph = ImageTk.PhotoImage(out)
                    lbl.config(image=ph)
                    lbl.image = ph
            except Exception as e:
                self.log(f"Preview error: {e}")
            busy = bool(self.bot and self.bot.is_alive())
            win.after(300 if busy else 100, tick)             # go easy on the CPU while the macro runs

        tick()

    # ------------------------------------------------------------------ screen capture (regions / colors)
    def capture(self, mode, key):
        if self.overlay is not None:
            return
        self.root.attributes("-alpha", 0.0)                  # invisible, but still a normal window
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#111827")
        sw = self.root.winfo_screenwidth()
        toast.geometry(f"+{sw // 2 - 190}+20")
        msg = tk.Label(toast, bg="#111827", fg="#fde047", font=("Segoe UI Semibold", 13), padx=18, pady=10)
        msg.pack()

        def finish_toast_then_shoot():
            try:
                toast.destroy()
            except Exception:
                pass
            self.root.after(180, lambda: self.show_overlay(mode, key))

        def countdown(n):
            try:
                if n <= 0:
                    finish_toast_then_shoot()
                    return
                msg.config(text=f"Get the skill check on screen...  capturing in {n}")
                self.root.after(1000, lambda: countdown(n - 1))
            except Exception:
                self.root.attributes("-alpha", 1.0)

        countdown(3)

    def show_overlay(self, mode, key):
        top = None
        try:
            with mss.mss() as sct:
                mon = dict(sct.monitors[1])
                shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            arr = np.array(img)

            top = tk.Toplevel(self.root)
            self.overlay = top
            top.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
            top.attributes("-fullscreen", True)
            top.attributes("-topmost", True)
            cv = tk.Canvas(top, width=mon["width"], height=mon["height"], highlightthickness=0,
                           cursor="crosshair", bg="black")
            cv.pack(fill="both", expand=True)
            photo = ImageTk.PhotoImage(img)
            cv.create_image(0, 0, image=photo, anchor="nw")
            cv.image = photo
            msg = "Drag a box around the skill check" if mode == "region" else "Click the color to pick"
            cv.create_rectangle(10, 10, 760, 56, fill="#000000", outline="")
            cv.create_text(20, 20, anchor="nw", fill="#fde047", font=("Segoe UI Semibold", 16),
                           text=f"{msg}     (Esc or right-click = cancel)")
            start, rect = {}, {"id": None}

            def done(*_):
                if self.overlay is None:
                    return
                self.overlay = None
                try:
                    top.destroy()
                finally:
                    self.root.attributes("-alpha", 1.0)      # ALWAYS bring the main window back
                    self.root.deiconify()

            def press(e):
                if mode == "color":
                    x, y = min(e.x, arr.shape[1] - 1), min(e.y, arr.shape[0] - 1)
                    rgb = [int(v) for v in arr[y, x][:3]]
                    self.cfg["colors"][key] = rgb
                    self.swatches[key].config(bg=self.hexcol(rgb))
                    self.log(f"Color '{key}' = {rgb}")
                    save_config(self.cfg)
                    done()
                else:
                    start["x"], start["y"] = e.x, e.y

            def drag(e):
                if mode == "region" and "x" in start:
                    if rect["id"]:
                        cv.delete(rect["id"])
                    rect["id"] = cv.create_rectangle(start["x"], start["y"], e.x, e.y, outline="#ef4444", width=2)

            def release(e):
                if mode == "region" and "x" in start:
                    x1, x2 = sorted((start["x"], e.x))
                    y1, y2 = sorted((start["y"], e.y))
                    if x2 - x1 > 5 and y2 - y1 > 2:
                        reg = {"left": mon["left"] + x1, "top": mon["top"] + y1,
                               "width": x2 - x1, "height": y2 - y1}
                        self.cfg[key] = reg
                        self.reg_lbl[key].config(text=self.fmt_region(reg))
                        self.log(f"{key} set: {self.fmt_region(reg)}")
                        save_config(self.cfg)
                        done()
                    else:
                        start.clear()
                        if rect["id"]:
                            cv.delete(rect["id"])

            cv.bind("<ButtonPress-1>", press)
            cv.bind("<B1-Motion>", drag)
            cv.bind("<ButtonRelease-1>", release)
            cv.bind("<Button-3>", done)
            top.bind("<Escape>", done)
            top.protocol("WM_DELETE_WINDOW", done)
            top.after(90000, done)                            # failsafe: never stay stuck on screen
            top.focus_force()
        except Exception as e:
            self.overlay = None
            self.log(f"Capture failed: {e}")
            try:
                if top is not None:
                    top.destroy()
            finally:
                self.root.attributes("-alpha", 1.0)
                self.root.deiconify()

    # ------------------------------------------------------------------ shutdown
    def on_close(self):
        try:
            self.read_settings()
            if self.root.state() == "normal":
                self.cfg["window_geometry"] = [self.root.winfo_x(), self.root.winfo_y(),
                                               self.root.winfo_width(), self.root.winfo_height()]
            save_config(self.cfg)
        except Exception:
            pass
        try:
            if self.bot and self.bot.is_alive():
                self.bot.stop()
                self.bot.join(1.5)                           # lets it release any held keys
            if self.hk:
                self.hk.stop()
            for w in (self.preview_win, self.overlay):
                if w is not None:
                    w.destroy()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FishingMacro.App")   # own taskbar icon
    except Exception:
        pass
    sys.setswitchinterval(0.001)       # let the window stay responsive while the macro thread is busy
    instance = acquire_single_instance()
    if instance is None:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo(APP_TITLE, "XZX Fishing is already running.\nCheck your taskbar (or press F8).")
        return
    license = run_login()          # Discord link + access key; refuses (doesn't delete) on a locked key
    if not license:
        return
    App(license).run()


if __name__ == "__main__":
    main()

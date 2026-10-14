"""
XZX Fishing Macro  -  standalone (no AutoHotkey)

Two skill checks, read from the screen:
  1) Cast / fill bar: holds the cast key, releases when the fill reaches the green zone.
  2) Reel / blue bar + fish: taps Q/E (or holds a mouse button) to keep your bar on the fish.
Then it auto-recasts.

Set the two regions, pick the four colours, press START (or F6). Build to an .exe with BUILD_FISHING.bat.
"""

import ctypes
import json
import os
import string
import subprocess
import sys
import tempfile
import threading
import time
import queue
import urllib.parse
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np

try:
    import winreg
except Exception:
    winreg = None

try:
    import mss
except Exception:
    mss = None
from PIL import Image, ImageGrab, ImageTk

CONFIG = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "fishing_macro.json")

# ---------------------------------------------------------------- licence / bot link ----
LICENSE_API_URL = "https://verification-bot-production-2ea4.up.railway.app"
DISCORD_INVITE = "discord.gg/xzxx"
PRODUCT = "fishing"

# XZX purple theme
BG = "#0b0614"
CARD = "#160d28"
INPUT = "#241940"
TXT = "#f4eeff"
SUB = "#a99cc4"
ACC = "#b57bff"
GREEN = "#6ee7b7"
RED = "#f0587e"


def get_hwid():
    if winreg:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            v, _ = winreg.QueryValueEx(k, "MachineGuid")
            if v:
                return str(v)
        except Exception:
            pass
    return os.environ.get("COMPUTERNAME", "unknown")


def api(path, data=None, timeout=8):
    """POST (data dict) or GET. Returns the body text, or DENIED|... on failure."""
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


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def self_destruct():
    """Delete this .exe, its config, and any copies with the same name on fixed drives. Then exit."""
    running = sys.executable if is_frozen() else ""
    name = os.path.basename(running) if running else ""
    lines = ["@echo off"]
    try:
        if os.path.exists(CONFIG):
            lines.append('del /f /q "%s" 2>nul' % CONFIG)
    except Exception:
        pass
    if name:
        for d in string.ascii_uppercase:
            drv = "%s:\\" % d
            try:
                if os.path.exists(drv):
                    lines.append('for /f "delims=" %%%%F in (\'dir /s /b "%s%s" 2^>nul\') do del /f /q "%%%%F" 2>nul' % (drv, name))
            except Exception:
                pass
    if running:
        lines += [":w", 'del /f /q "%s" 2>nul' % running,
                  'if exist "%s" ( ping 127.0.0.1 -n 2 >nul & goto w )' % running]
    lines.append('del "%~f0"')
    try:
        bat = os.path.join(tempfile.gettempdir(), "xzx_fishing_cleanup.bat")
        with open(bat, "w") as f:
            f.write("\r\n".join(lines))
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)
    except Exception:
        pass


def startup_guard():
    """A shared copy self-destructs before any login: if the saved HWID isn't this PC, remove it."""
    try:
        cfg = load_cfg()
    except Exception:
        return
    saved = cfg.get("_hwid")
    if saved and saved != get_hwid() and is_frozen():
        self_destruct()
        sys.exit(0)

# ---------------------------------------------------------------- input (SendInput) ----
SendInput = ctypes.windll.user32.SendInput
PUL = ctypes.POINTER(ctypes.c_ulong)


class _KB(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]


class _MS(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]


class _IN(ctypes.Union):
    _fields_ = [("ki", _KB), ("mi", _MS)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _IN)]


KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDED = 0x0001

SCAN = {
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15, "u": 0x16,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07, "7": 0x08,
    "8": 0x09, "9": 0x0A, "0": 0x0B, "space": 0x39, "shift": 0x2A, "ctrl": 0x1D,
    "tab": 0x0F, "enter": 0x1C,
}
MOUSE = {"mouse_left": (0x0002, 0x0004), "mouse_right": (0x0008, 0x0010), "mouse_middle": (0x0020, 0x0040)}


def _send_key(scan, up):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=1, u=_IN(ki=_KB(0, scan, flags, 0, None)))
    SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _send_mouse(flag):
    inp = INPUT(type=0, u=_IN(mi=_MS(0, 0, 0, flag, 0, None)))
    SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def press(name, down):
    """down=True presses, down=False releases. name is a key letter or mouse_left/right/middle."""
    if name in MOUSE:
        _send_mouse(MOUSE[name][0] if down else MOUSE[name][1])
    else:
        sc = SCAN.get(name.lower())
        if sc:
            _send_key(sc, not down)


def tap(name):
    press(name, True)
    time.sleep(0.015)
    press(name, False)


# ---------------------------------------------------------------- screen capture ----
class Grabber:
    def __init__(self):
        self._sct = mss.mss() if mss else None

    def grab(self, region):
        """region = (x, y, w, h) -> RGB numpy array (h, w, 3), or None."""
        x, y, w, h = region
        if w <= 0 or h <= 0:
            return None
        if self._sct:
            raw = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
            arr = np.frombuffer(raw.rgb, dtype=np.uint8).reshape(raw.height, raw.width, 3)
            return arr
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))  # fallback
        return np.array(img.convert("RGB"))

    def pixel(self, x, y):
        arr = self.grab((x, y, 1, 1))
        return tuple(int(v) for v in arr[0, 0]) if arr is not None else (0, 0, 0)


def column_mask(arr, rgb, tol, min_pixels):
    """Boolean array per column: True where at least `min_pixels` match `rgb` within `tol`."""
    r, g, b = rgb
    diff = (np.abs(arr[:, :, 0].astype(int) - r) <= tol) & \
           (np.abs(arr[:, :, 1].astype(int) - g) <= tol) & \
           (np.abs(arr[:, :, 2].astype(int) - b) <= tol)
    return diff.sum(axis=0) >= min_pixels


# ---------------------------------------------------------------- defaults ----
DEFAULTS = {
    "region1": [711, 900, 503, 40],       # cast: fill bar + green zone  (x, y, w, h)
    "region2": [772, 880, 380, 67],       # reel: blue bar + fish
    "col_fill": [138, 90, 18],
    "col_green": [45, 160, 89],
    "col_blue": [1, 86, 146],
    "col_fish": [154, 72, 3],
    "cast_key": "e",
    "release_point": 0.5,
    "release_latency": 0.03,
    "reel_mode": "two_key",
    "left_key": "q",
    "right_key": "e",
    "hold_input": "mouse_left",
    "hold_moves": "right",
    "deadzone_px": 4,
    "deadzone_frac": 0.10,
    "prediction_lead": 0.05,
    "tolerance": 40,
    "min_pixels": 2,
    "fish_timeout": 1.5,
    "auto_recast": True,
    "recast_key": "e",
    "recast_delay": 1.5,
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- worker ----
class Worker(threading.Thread):
    def __init__(self, cfg, log):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.log = log
        self.stop_flag = threading.Event()
        self.grab = None

    def stop(self):
        self.stop_flag.set()

    def run(self):
        c = self.cfg
        self.grab = Grabber()
        try:
            while not self.stop_flag.is_set():
                self.cast()
                if self.stop_flag.is_set():
                    break
                self.reel()
                if self.stop_flag.is_set():
                    break
                if c["auto_recast"]:
                    time.sleep(c["recast_delay"])
                    tap(c["recast_key"])
                    self.log("Recast (%s)." % c["recast_key"])
                else:
                    break
        finally:
            press(c["cast_key"], False)
            if c["reel_mode"] == "hold":
                press(c["hold_input"], False)

    # ---- skill check 1: hold cast key, release when the fill reaches the green release point ----
    def cast(self):
        c = self.cfg
        reg = tuple(c["region1"])
        # wait for the cast bar (green zone visible)
        t0 = time.time()
        while not self.stop_flag.is_set():
            arr = self.grab.grab(reg)
            if arr is not None and column_mask(arr, c["col_green"], c["tolerance"], c["min_pixels"]).any():
                break
            if time.time() - t0 > 6:
                return
            time.sleep(0.03)
        if self.stop_flag.is_set():
            return
        self.log("Skill check 1: charging.")
        press(c["cast_key"], True)
        green_cols = np.where(column_mask(arr, c["col_green"], c["tolerance"], c["min_pixels"]))[0]
        g0, g1 = int(green_cols.min()), int(green_cols.max())
        release_x = g0 + c["release_point"] * (g1 - g0)
        last_front, last_t = None, time.time()
        while not self.stop_flag.is_set():
            arr = self.grab.grab(reg)
            if arr is None:
                break
            fill = np.where(column_mask(arr, c["col_fill"], c["tolerance"], c["min_pixels"]))[0]
            if len(fill):
                front = int(fill.max())
                now = time.time()
                vel = 0.0
                if last_front is not None and now > last_t:
                    vel = (front - last_front) / (now - last_t)  # px/s
                lead = vel * c["release_latency"]
                if front + lead >= release_x:
                    break
                last_front, last_t = front, now
            time.sleep(0.004)
        press(c["cast_key"], False)
        self.log("Cast released on green.")

    # ---- skill check 2: keep your bar on the fish ----
    def reel(self):
        c = self.cfg
        reg = tuple(c["region2"])
        w = reg[2]
        dead = max(c["deadzone_px"], c["deadzone_frac"] * w)
        # wait for the fish
        t0 = time.time()
        while not self.stop_flag.is_set():
            arr = self.grab.grab(reg)
            if arr is not None and column_mask(arr, c["col_fish"], c["tolerance"], c["min_pixels"]).any():
                break
            if time.time() - t0 > 8:
                return
            time.sleep(0.03)
        if self.stop_flag.is_set():
            return
        self.log("Skill check 2 found. Tracking fish.")
        if c["reel_mode"] == "hold":
            press(c["hold_input"], False)
        last_fish, last_t, lost_since = None, time.time(), None
        while not self.stop_flag.is_set():
            arr = self.grab.grab(reg)
            if arr is None:
                break
            fish_cols = np.where(column_mask(arr, c["col_fish"], c["tolerance"], c["min_pixels"]))[0]
            box_cols = np.where(column_mask(arr, c["col_blue"], c["tolerance"], c["min_pixels"]))[0]
            now = time.time()
            if not len(fish_cols):
                if lost_since is None:
                    lost_since = now
                elif now - lost_since > c["fish_timeout"]:
                    break
                time.sleep(0.01)
                continue
            lost_since = None
            fish = float(fish_cols.mean())
            box = float(box_cols.mean()) if len(box_cols) else w / 2.0
            vel = 0.0
            if last_fish is not None and now > last_t:
                vel = (fish - last_fish) / (now - last_t)
            target = fish + vel * c["prediction_lead"]
            last_fish, last_t = fish, now
            diff = target - box
            if c["reel_mode"] == "two_key":
                if diff > dead:
                    tap(c["right_key"])
                elif diff < -dead:
                    tap(c["left_key"])
            else:  # hold mode: hold the input to move one way, release to drift the other
                want = (diff > 0) == (c["hold_moves"] == "right")
                press(c["hold_input"], want)
            time.sleep(0.006)
        if c["reel_mode"] == "hold":
            press(c["hold_input"], False)
        self.log("Reel finished.")


# ---------------------------------------------------------------- overlays ----
def pick_region(root, on_done):
    """Fullscreen drag-to-select. Calls on_done([x, y, w, h])."""
    ov = tk.Toplevel(root)
    ov.attributes("-fullscreen", True)
    ov.attributes("-alpha", 0.25)
    ov.configure(bg="black")
    ov.attributes("-topmost", True)
    cv = tk.Canvas(ov, cursor="cross", bg="black", highlightthickness=0)
    cv.pack(fill="both", expand=True)
    cv.create_text(ov.winfo_screenwidth() // 2, 30, fill="white",
                   text="Drag a box over the bar  ·  Esc to cancel", font=("Segoe UI", 14))
    s = {}

    def down(e):
        s["x"], s["y"] = e.x_root, e.y_root
        s["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#b57bff", width=2)

    def move(e):
        if "r" in s:
            cv.coords(s["r"], s["x"] - ov.winfo_rootx(), s["y"] - ov.winfo_rooty(), e.x, e.y)

    def up(e):
        x0, y0 = s["x"], s["y"]
        x1, y1 = e.x_root, e.y_root
        ov.destroy()
        on_done([min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)])

    cv.bind("<Button-1>", down)
    cv.bind("<B1-Motion>", move)
    cv.bind("<ButtonRelease-1>", up)
    ov.bind("<Escape>", lambda e: ov.destroy())


def pick_color(root, grab, on_done):
    """Fullscreen; the next click samples that pixel. Calls on_done((r, g, b))."""
    ov = tk.Toplevel(root)
    ov.attributes("-fullscreen", True)
    ov.attributes("-alpha", 0.15)
    ov.configure(bg="black")
    ov.attributes("-topmost", True)
    cv = tk.Canvas(ov, cursor="crosshair", bg="black", highlightthickness=0)
    cv.pack(fill="both", expand=True)
    cv.create_text(ov.winfo_screenwidth() // 2, 30, fill="white",
                   text="Click the colour you want  ·  Esc to cancel", font=("Segoe UI", 14))

    def click(e):
        x, y = e.x_root, e.y_root
        ov.destroy()
        root.after(60, lambda: on_done(grab.pixel(x, y)))

    cv.bind("<Button-1>", click)
    ov.bind("<Escape>", lambda e: ov.destroy())


# ---------------------------------------------------------------- GUI ----
class App:
    def __init__(self, lic=None):
        self.cfg = load_cfg()
        self.lic = lic or {}
        self.grab = Grabber()
        self.worker = None
        self.logq = queue.Queue()
        self._last_copy_sig = ""

        self.root = tk.Tk()
        self.root.title("XZX Fishing Macro")
        self.root.configure(bg="#0d0a16")
        self.root.geometry("440x760")
        self.vars = {}
        self._build()
        self.root.after(120, self._drain_log)
        self.root.bind("<F6>", lambda e: self.toggle())
        if self.lic.get("key"):
            threading.Thread(target=self._heartbeat, daemon=True).start()

    # ---- licence heartbeat: logs out / self-destructs when the key is pulled, and reports copies ----
    def _heartbeat(self):
        self._report_copies()
        while True:
            time.sleep(60)
            code = self.lic.get("key")
            if not code:
                continue
            r = api("/api/check", {"code": code, "session": self.lic.get("session", ""),
                                   "product": PRODUCT, "hwid": get_hwid()})
            if r.startswith("REVOKED|"):
                self.root.after(0, lambda rsn=r.split("|", 1)[1]: self._revoked(rsn))
                return
            self._report_copies()

    def _revoked(self, reason):
        if self.worker:
            self.worker.stop()
        remove = any(k in reason for k in ("DISABLED", "DELETED", "EXPIRED", "DEVICE"))
        try:
            messagebox.showwarning("XZX Fishing", "You've been logged out.\n\n%s\n\n%s" % (
                reason, "This copy is being removed." if remove else
                ("Open a ticket in " + DISCORD_INVITE + " if this is a mistake.")))
        except Exception:
            pass
        if remove:
            self_destruct()
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def _report_copies(self):
        if not is_frozen():
            return
        name = os.path.basename(sys.executable)
        running = sys.executable.lower()
        found = []
        roots = [os.path.dirname(sys.executable)]
        up = os.environ.get("USERPROFILE", "")
        if up:
            for s in ("Desktop", "Downloads", "Documents", "OneDrive\\Desktop", "OneDrive\\Downloads"):
                roots.append(os.path.join(up, s))
        try:
            for root in roots:
                if not os.path.isdir(root):
                    continue
                for dp, _dirs, files in os.walk(root):
                    for fn in files:
                        if fn.lower() == name.lower():
                            p = os.path.join(dp, fn)
                            if p.lower() != running:
                                found.append(p)
                    if len(found) > 50:
                        break
        except Exception:
            pass
        if not found:
            self._last_copy_sig = ""
            return
        sig = "|".join(sorted(p.lower() for p in found))
        if sig == self._last_copy_sig:
            return
        self._last_copy_sig = sig
        api("/api/report", {"code": self.lic.get("key", ""), "session": self.lic.get("session", ""),
                            "product": PRODUCT, "count": len(found), "hwid": get_hwid(),
                            "paths": "\n".join(found)[:1500]})

    def _build(self):
        pad = {"padx": 8, "pady": 2}
        head = lambda t: tk.Label(self.root, text=t, bg="#0d0a16", fg="#b57bff",
                                  font=("Segoe UI", 10, "bold"), anchor="w")

        head("1. Screen regions").pack(fill="x", **pad)
        self.region_lbl = {}
        for key, name in (("region1", "Skill check 1 (fill bar + green zone)"),
                          ("region2", "Skill check 2 (blue bar + fish)")):
            row = tk.Frame(self.root, bg="#0d0a16")
            row.pack(fill="x", **pad)
            tk.Label(row, text=name, bg="#0d0a16", fg="#f4eeff", width=30, anchor="w").pack(side="left")
            lbl = tk.Label(row, text=self._region_text(key), bg="#0d0a16", fg="#a99cc4", width=16, anchor="w")
            lbl.pack(side="left")
            self.region_lbl[key] = lbl
            tk.Button(row, text="Set region", command=lambda k=key: self._set_region(k)).pack(side="right")

        head("2. Colors (click Pick, then click the colour on screen)").pack(fill="x", **pad)
        for key, name in (("col_fill", "Filling bar"), ("col_green", "Green zone"),
                          ("col_blue", "Blue bar (yours)"), ("col_fish", "Orange fish")):
            row = tk.Frame(self.root, bg="#0d0a16")
            row.pack(fill="x", **pad)
            tk.Label(row, text=name, bg="#0d0a16", fg="#f4eeff", width=18, anchor="w").pack(side="left")
            sw = tk.Label(row, text="      ", bg=self._hex(key), relief="solid", bd=1)
            sw.pack(side="left", padx=6)
            self.region_lbl[key] = sw
            tk.Button(row, text="Pick", command=lambda k=key: self._pick_color(k)).pack(side="right")

        head("3. Settings").pack(fill="x", **pad)
        self._num("cast_key", "Cast key (hold, skill check 1)")
        self._num("release_point", "Release point in green (0-1)")
        self._num("release_latency", "Release latency compensation (s)")
        self._choice("reel_mode", "Reel mode", ["two_key", "hold"])
        self._num("left_key", "Left key (two_key mode)")
        self._num("right_key", "Right key (two_key mode)")
        self._choice("hold_input", "Hold-mode input", ["mouse_left", "mouse_right", "mouse_middle"])
        self._choice("hold_moves", "Hold-mode: holding moves bar", ["right", "left"])
        self._num("deadzone_px", "Deadzone min (px)")
        self._num("deadzone_frac", "Deadzone (fraction of box width)")
        self._num("prediction_lead", "Prediction lead (s)")
        self._num("tolerance", "Color tolerance (0-255)")
        self._num("min_pixels", "Min pixels per column")
        self._num("fish_timeout", "Fish lost timeout (s)")
        self._check("auto_recast", "Auto start next cast after reel")
        self._num("recast_key", "Recast key")
        self._num("recast_delay", "Recast delay (s)")

        bar = tk.Frame(self.root, bg="#0d0a16")
        bar.pack(fill="x", padx=8, pady=6)
        self.btn = tk.Button(bar, text="START (F6)", command=self.toggle, bg="#5a2fd6", fg="white",
                             font=("Segoe UI", 10, "bold"))
        self.btn.pack(side="left", expand=True, fill="x")
        tk.Button(bar, text="Test detection", command=self._test).pack(side="left", padx=4)
        tk.Button(bar, text="Live preview", command=self._preview).pack(side="left")

        self.logbox = tk.Text(self.root, height=6, bg="#070510", fg="#8be28b",
                              insertbackground="#8be28b", font=("Consolas", 9))
        self.logbox.pack(fill="both", expand=True, padx=8, pady=6)
        self.log("Ready. Set the two regions and pick the colours, then START.")

    # ----- small widget helpers -----
    def _num(self, key, label):
        row = tk.Frame(self.root, bg="#0d0a16")
        row.pack(fill="x", padx=8, pady=1)
        tk.Label(row, text=label, bg="#0d0a16", fg="#f4eeff", width=30, anchor="w").pack(side="left")
        v = tk.StringVar(value=str(self.cfg[key]))
        self.vars[key] = v
        e = tk.Entry(row, textvariable=v, width=12, bg="#241940", fg="#f4eeff", insertbackground="#f4eeff")
        e.pack(side="right")
        v.trace_add("write", lambda *a, k=key: self._on_change(k))

    def _choice(self, key, label, opts):
        row = tk.Frame(self.root, bg="#0d0a16")
        row.pack(fill="x", padx=8, pady=1)
        tk.Label(row, text=label, bg="#0d0a16", fg="#f4eeff", width=30, anchor="w").pack(side="left")
        v = tk.StringVar(value=str(self.cfg[key]))
        self.vars[key] = v
        ttk.Combobox(row, textvariable=v, values=opts, width=11, state="readonly").pack(side="right")
        v.trace_add("write", lambda *a, k=key: self._on_change(k))

    def _check(self, key, label):
        v = tk.BooleanVar(value=bool(self.cfg[key]))
        self.vars[key] = v
        tk.Checkbutton(self.root, text=label, variable=v, bg="#0d0a16", fg="#f4eeff",
                       selectcolor="#241940", activebackground="#0d0a16", activeforeground="#f4eeff",
                       command=lambda k=key: self._on_change(k)).pack(fill="x", padx=8, pady=1)

    def _on_change(self, key):
        v = self.vars[key].get()
        if key in ("release_point", "release_latency", "deadzone_frac", "prediction_lead",
                   "fish_timeout", "recast_delay"):
            try:
                v = float(v)
            except ValueError:
                return
        elif key in ("deadzone_px", "tolerance", "min_pixels"):
            try:
                v = int(float(v))
            except ValueError:
                return
        self.cfg[key] = v
        save_cfg(self.cfg)

    def _region_text(self, key):
        x, y, w, h = self.cfg[key]
        return "%dx%d @ %d,%d" % (w, h, x, y)

    def _hex(self, key):
        r, g, b = self.cfg[key]
        return "#%02x%02x%02x" % (r, g, b)

    def _set_region(self, key):
        self.root.withdraw()
        time.sleep(0.2)

        def done(reg):
            if reg[2] > 3 and reg[3] > 3:
                self.cfg[key] = reg
                save_cfg(self.cfg)
                self.region_lbl[key].config(text=self._region_text(key))
                self.log("%s region set: %s" % (key, self._region_text(key)))
            self.root.deiconify()
        pick_region(self.root, done)

    def _pick_color(self, key):
        self.root.withdraw()
        time.sleep(0.2)

        def done(rgb):
            self.cfg[key] = list(rgb)
            save_cfg(self.cfg)
            self.region_lbl[key].config(bg=self._hex(key))
            self.log("%s colour: %s" % (key, rgb))
            self.root.deiconify()
        pick_color(self.root, self.grab, done)

    # ----- actions -----
    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.worker = None
            self.btn.config(text="START (F6)", bg="#5a2fd6")
            self.log("Stopped.")
        else:
            self.worker = Worker(self.cfg, self.log)
            self.worker.start()
            self.btn.config(text="STOP (F6)", bg="#a82d58")
            self.log("Started.")

    def _test(self):
        c = self.cfg
        a1 = self.grab.grab(tuple(c["region1"]))
        a2 = self.grab.grab(tuple(c["region2"]))
        g = int(column_mask(a1, c["col_green"], c["tolerance"], c["min_pixels"]).sum()) if a1 is not None else 0
        fl = int(column_mask(a1, c["col_fill"], c["tolerance"], c["min_pixels"]).sum()) if a1 is not None else 0
        fi = int(column_mask(a2, c["col_fish"], c["tolerance"], c["min_pixels"]).sum()) if a2 is not None else 0
        bx = int(column_mask(a2, c["col_blue"], c["tolerance"], c["min_pixels"]).sum()) if a2 is not None else 0
        self.log("Test -> green cols:%d  fill cols:%d  fish cols:%d  blue cols:%d" % (g, fl, fi, bx))
        if g == 0 and fi == 0:
            self.log("Nothing detected. Open the fishing bar, then re-set regions / re-pick colours.")

    def _preview(self):
        win = tk.Toplevel(self.root)
        win.title("Live preview")
        win.configure(bg="black")
        lbl = tk.Label(win, bg="black")
        lbl.pack()

        def tick():
            if not win.winfo_exists():
                return
            crops = []
            for key in ("region1", "region2"):
                a = self.grab.grab(tuple(self.cfg[key]))
                if a is not None:
                    im = Image.fromarray(a)
                    scale = max(1, 760 // max(1, im.width))
                    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
                    crops.append(im)
            if crops:
                h = sum(im.height for im in crops) + 8
                w = max(im.width for im in crops)
                sheet = Image.new("RGB", (w, h), "black")
                y = 0
                for im in crops:
                    sheet.paste(im, (0, y))
                    y += im.height + 8
                tkimg = ImageTk.PhotoImage(sheet)
                lbl.config(image=tkimg)
                lbl.image = tkimg
            win.after(120, tick)
        tick()

    def log(self, msg):
        self.logq.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self.logq.get_nowait()
                self.logbox.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
                self.logbox.see("end")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log)

    def run(self):
        self.root.mainloop()
        if self.worker:
            self.worker.stop()


def run_login(cfg):
    """XZX login: link Discord, enter the access key. Returns {session, key} or None."""
    result = {}
    state = {"session": cfg.get("_session", ""), "link": "none"}

    win = tk.Tk()
    win.title("XZX Fishing — Login")
    win.configure(bg=BG)
    win.geometry("420x380")
    win.resizable(False, False)

    tk.Label(win, text="XZX Fishing", bg=BG, fg=ACC, font=("Segoe UI", 20, "bold")).pack(pady=(22, 2))
    tk.Label(win, text="Link your Discord, then enter your key", bg=BG, fg=SUB,
             font=("Segoe UI", 9)).pack()

    discord_btn = tk.Button(win, text="Link Discord account", bg=INPUT, fg=TXT,
                            activebackground=CARD, activeforeground=TXT, relief="flat",
                            font=("Segoe UI", 10), width=30)
    discord_btn.pack(pady=(18, 10), ipady=6)

    key_var = tk.StringVar(value=cfg.get("_key", ""))
    entry = tk.Entry(win, textvariable=key_var, bg=INPUT, fg=TXT, insertbackground=TXT,
                     justify="center", font=("Consolas", 12), width=26)
    entry.pack(ipady=6)

    unlock = tk.Button(win, text="Unlock", bg="#5a2fd6", fg="white", relief="flat",
                       activebackground=ACC, font=("Segoe UI", 11, "bold"), width=30)
    unlock.pack(pady=12, ipady=6)

    status = tk.Label(win, text="", bg=BG, fg=SUB, font=("Segoe UI", 9), wraplength=380)
    status.pack(pady=4)
    tk.Button(win, text="Need help?  Join " + DISCORD_INVITE, bg=BG, fg=ACC, relief="flat",
              activebackground=BG, activeforeground=ACC, bd=0,
              command=lambda: webbrowser.open("https://" + DISCORD_INVITE)).pack(pady=(6, 0))

    def set_status(t, color=SUB):
        status.config(text=t, fg=color)

    def poll():
        if state["link"] == "linked" or not state["session"]:
            return
        r = api("/api/auth/status?s=" + urllib.parse.quote(state["session"]))
        if r.startswith("LINKED|"):
            parts = r.split("|")
            state["link"] = "linked"
            discord_btn.config(text="✓ Linked as " + (parts[1] if len(parts) > 1 else "you"))
            set_status("Linked. Enter your key and press Unlock.", GREEN)
        elif r == "PENDING":
            set_status("Waiting for Discord…")
            win.after(1500, poll)
        elif r.startswith("ERROR|"):
            set_status(r.split("|", 1)[1], RED)
        elif r == "OFF":
            state["link"] = "off"
        else:
            set_status("Link expired — click Link Discord again.", RED)

    def do_link():
        r = api("/api/auth/new", {})
        if r.startswith("OK|"):
            _, sess, url = r.split("|", 2)
            state["session"] = sess
            webbrowser.open(url)
            set_status("Finish linking in your browser…")
            win.after(1500, poll)
        elif r.startswith("OFF"):
            state["link"] = "off"
            set_status("Discord linking is off — just enter your key.", ACC)
        else:
            set_status("Couldn't start linking. Try again.", RED)

    def do_unlock():
        code = key_var.get().strip()
        if not code:
            set_status("Enter your access key.", RED)
            return
        set_status("Checking licence…")
        win.update_idletasks()
        r = api("/api/activate", {"code": code, "session": state["session"],
                                  "product": PRODUCT, "hwid": get_hwid()})
        if r.startswith("OK|"):
            cfg["_session"] = state["session"]
            cfg["_key"] = code
            cfg["_hwid"] = get_hwid()
            save_cfg(cfg)
            result["session"] = state["session"]
            result["key"] = code
            win.destroy()
            return
        msg = r.split("|", 1)[1] if "|" in r else r
        if "LOCKED TO ANOTHER DEVICE" in msg:
            set_status("This key is locked to another PC — removing this copy…", RED)
            win.update_idletasks()
            self_destruct()
            win.after(1500, lambda: os._exit(0))
        else:
            set_status("Access denied: " + msg, RED)
            key_var.set("")

    discord_btn.config(command=do_link)
    unlock.config(command=do_unlock)
    entry.bind("<Return>", lambda e: do_unlock())

    if state["session"]:
        set_status("Checking your Discord link…")
        win.after(400, poll)

    win.mainloop()
    return result if result.get("key") else None


if __name__ == "__main__":
    startup_guard()
    _cfg = load_cfg()
    _lic = run_login(_cfg)
    if _lic:
        App(_lic).run()

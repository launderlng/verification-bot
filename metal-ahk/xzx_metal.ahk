; ============================================================
; STREAM PROOF + TAB PROOF
; AHK v2-safe implementation.
; Stream Proof: excludes the GUI from supported Windows capture APIs.
; Tab Proof: removes the window from Alt+Tab/task-switcher while enabled.
; ============================================================

global DISCORD_INVITE := "discord.gg/xzxx"

global StreamProofEnabled := true
global StreamProofButton := ""

; Every menu this script creates registers itself here. Toggling only walks
; this list, so nothing else in the process is ever touched, shown, or given a
; task-switcher entry.
global StreamProofWindows := Map()

TrackWindow(hwnd) {
    global StreamProofWindows, StreamProofEnabled
    if !hwnd
        return
    StreamProofWindows[hwnd] := true
    ApplyWindowAffinity(hwnd, StreamProofEnabled)
}

ApplyWindowAffinity(hwnd, enabled) {
    ; WDA_EXCLUDEFROMCAPTURE = 0x11, WDA_NONE = 0x00.
    try DllCall("SetWindowDisplayAffinity", "Ptr", hwnd,
        "UInt", enabled ? 0x11 : 0x00, "UInt")
}

SetStreamProof(enabled := true) {
    global StreamProofEnabled, StreamProofButton, StreamProofWindows

    StreamProofEnabled := enabled

    ; Only the capture affinity of already-created menus changes: no window is
    ; created, shown, hidden or restyled, so toggling cannot surface another
    ; AutoHotkey window or taskbar entry.
    for hwnd in StreamProofWindows.Clone() {
        if !DllCall("User32\IsWindow", "Ptr", hwnd, "Int") {
            StreamProofWindows.Delete(hwnd)
            continue
        }
        ApplyWindowAffinity(hwnd, enabled)
    }

    if IsObject(StreamProofButton)
        StreamProofButton.Text := enabled ? "STREAM PROOF: ON" : "STREAM PROOF: OFF"
}

ToggleStreamProof(*) {
    global StreamProofEnabled
    SetStreamProof(!StreamProofEnabled)

    UpdateStreamProofUI(StreamProofEnabled)
}



; ON  = exclude the UI from supported Windows screen capture
; OFF = allow normal screen capture
; ============================================================

global StreamProofButton := ""






AddStreamButton(gui) {
    btn := gui.AddButton("x"  " y"  " w"  " h", "STREAM HIDDEN")
    btn.OnEvent("Click", ToggleStreamProof)
    return btn
}





global CaptureProtectionEnabled := true

; ============================================================
; DISCORD / SCREEN-CAPTURE PRIVACY
; Excludes this UI from Windows capture APIs where supported.
; This does NOT affect what you see locally.
; ============================================================
HideFromCapture(hwnd) {
    global StreamProofEnabled
    ; Follows the stream-proof toggle, so turning it off restores normal
    ; capture for every window - settings and calibration overlays included.
    TrackWindow(hwnd)
    ApplyWindowAffinity(hwnd, StreamProofEnabled)
}

; STATUS BACKGROUND FIX - TRANSPARENT TEXT CONTROLS
; --- Status text font/background fix ---
; ============================================================
; CLEAN ROUNDED PINK UI — by Coop
; Larger layout / cleaner spacing / rounded controls / polished accents
; ============================================================
; ============================================================
; ROUNDED PINK UI THEME - by Coop
; Rounded control styling, softer panels, stronger hover/active states
; ============================================================
; ============================================================
; ADVANCED PINK UI THEME
; Creator: by Coop
; Dark glass-style panels / bright pink accents / high contrast
; ============================================================
; ============================================================================
;  FiveM METAL DETECTING - skill-check automation          (AutoHotkey v2.0+)
;
;  Created by Coop
; ============================================================================
;
;  GAME LOOP AUTOMATED
;    1. press START (default 5)       -> starts the minigame
;    2. dial appears, centred         -> static blue arc + red tick sweeping once
;    3. press HIT (default E) in arc  -> skill check passed
;    4. horizontal bar fills L->R     -> "Searching Detecting Zone"
;    5. press START again             -> repeat forever
;
;  NOTHING HERE NEEDS HAND-EDITING. Hotkeys, in-game keys, colours, geometry,
;  timeouts and UI scale all live in config.ini next to this script; it is
;  created with defaults on first run, loaded at startup, and written back the
;  moment you change something in the Settings window (F11) or sample a colour    F4  Stream Proof ON/OFF
;  in calibration mode (F10).
;
;  WHERE THE DEFAULT NUMBERS CAME FROM
;    Every default coordinate and colour was measured from the user's own
;    1920x1080 capture (2026-08-14), not guessed:
;      screenshot E74416E9... - dial visible
;      screenshot 3C1D7604... - progress bar visible
;      MedalTV ...mp4         - 1280x720 @59.96fps, 442 frames, one full round
;
;    Measured facts that drive the design:
;      - dial centre (960,540); arc mid-radius 49.7px (band 46.0..53.1)
;      - arc colour 0x1B71C1, extremely stable (+/-3 per channel = opaque)
;      - tick colour 0xF10202 at its core, tick radius 51.5 == arc radius
;        => the tick RENDERS OVER the arc, which is what makes a single-pixel
;           poll work at all. Confirmed dynamically: arc pixel count drops
;           196 -> 176 exactly while the tick is inside the arc's angles.
;      - arc angular width 68.8 deg; rotation 401.4 deg/s CLOCKWISE on screen
;        and provably CONSTANT (linear fit R^2 0.99916, max residual 4 deg,
;        one lap = 0.897 s); the tick starts at 12 o'clock (measured 89.0 deg).
;        => the tick is inside the arc for only ~171 ms. That short window is
;        why the reaction delay is bounded and why we trigger 35% into the arc
;        rather than at its far edge.
;      - BECAUSE the tick starts at 90 deg, an arc that happens to span 90 deg
;        starts with the tick ALREADY INSIDE it. At 68.8/360 that is ~19% of
;        rounds if placement is uniform, and in those the tick can clear both
;        probes before the first scan finishes. WaitForTick sweeps the arc once
;        up front to catch exactly that case (see "already inside" below).
;      - progress bar: core row y=976, colour 0x00B7C8, track x 820..1100.
;        Fills 55.9 px/s and provably LINEARLY (R^2 0.99987 over 298 frames,
;        worst residual 1.7 px), 4.95 s end to end. The fill stops at x=1096
;        and never reaches the 1100 track end, then the whole bar VANISHES
;        within one frame of completing. So the "full" probe sits at x=1085:
;        measured catch window 233 ms, versus ~60 ms if it sat at x=1095.
;
;  IMPORTANT - only the 1920x1080 values are verified. On any other resolution
;  the script scales those defaults from the 1080p baseline on first run, and
;  you MUST re-check them with the calibration hotkey before trusting it.
; ============================================================================

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; ---- Compiling to one .exe (Ahk2Exe reads these lines) ----------------------
; The .exe carries AutoHotkey inside it, so nobody needs AutoHotkey installed,
; and it asks for admin itself (needed so FiveM receives the key presses).
;@Ahk2Exe-SetName XZX Macro
;@Ahk2Exe-SetDescription XZX Macro
;@Ahk2Exe-SetProductName XZX Macro
;@Ahk2Exe-SetCopyright XZX
;@Ahk2Exe-SetVersion 1.0.0
;@Ahk2Exe-UpdateManifest 1

; Run as administrator. If FiveM runs elevated (common), Windows silently
; drops every key a NON-admin program sends it - the macro would see the dial
; and "press" E, but the game never receives it. If the UAC prompt is
; declined the script carries on without admin.
if (!A_IsAdmin && !InStr(DllCall("GetCommandLine", "str"), "/restart")) {
    try {
        if A_IsCompiled
            Run('*RunAs "' A_ScriptFullPath '" /restart')
        else
            Run('*RunAs "' A_AhkPath '" /restart "' A_ScriptFullPath '"')
        ExitApp
    }
}

; Per-monitor DPI awareness. Without this, Windows lies about coordinates on
; any display scaled above 100% and every pixel probe lands in the wrong place.
; Harmless no-op if the runtime already set an awareness context.
try DllCall("SetProcessDpiAwarenessContext", "ptr", -4)   ; PER_MONITOR_AWARE_V2

CoordMode "Pixel",   "Screen"
CoordMode "Mouse",   "Screen"
CoordMode "ToolTip", "Screen"
SetTitleMatchMode "RegEx"      ; needed for the ahk_exe pattern in WIN_MATCH
SetWinDelay 0

; ============================================================================
;  BASELINE + SCALED DEFAULTS
; ============================================================================
; GTA V's HUD scales with screen HEIGHT and stays centred, so a single scale
; factor applied to centre-relative offsets is the right model. Only the 1080p
; case is verified from footage; anything else is a scaled guess - calibrate.
; Declared with `global` at the top level, which makes them SUPER-globals:
; readable and writable inside every function without a per-function
; declaration. Everything shared in this script uses that, so no function
; needs a `global` line of its own.
global BASE_W := 1920
global BASE_H := 1080
global SCL    := A_ScreenHeight / BASE_H
global SCX    := A_ScreenWidth  // 2
global SCY    := A_ScreenHeight // 2

; Map a 1080p-baseline coordinate onto this screen, relative to screen centre.
BX(x) => SCX + Round((x - BASE_W / 2) * SCL)
BY(y) => SCY + Round((y - BASE_H / 2) * SCL)

; ============================================================================
;  CONFIG  - defaults. config.ini overrides every one of these on load.
; ============================================================================
global C := {

; ---- files -----------------------------------------------------------------
    INI_FILE:             A_ScriptDir "\config.ini",
    LOG_FILE:             A_ScriptDir "\macro.log",
    SAMPLE_FILE:          A_ScriptDir "\samples.log",

; ---- hotkeys (AHK syntax: ^ ctrl, ! alt, + shift, # win) -------------------
    HK_TOGGLE:            "F6",     ; not F8: FiveM opens its console on F8
    HK_STOP:              "F9",
    HK_CALIB:             "F10",
    HK_SETTINGS:          "F11",
    HK_MENU:              "F5",     ; show/hide the main menu
    HK_DIAG:              "F7",      ; dump a full diagnostic snapshot to macro.log

; ---- in-game keys ----------------------------------------------------------
    START_KEY:            "5",
    CONFIRM_KEY:          "e",

; ---- detector slots --------------------------------------------------------
; A detector wears out and breaks, and pressing a broken slot's key opens
; nothing at all. With AUTO_SWAP_SLOTS on, the macro walks this list of hotbar
; slots instead of stopping, so a full bar of detectors runs unattended. Tried
; left to right; the first slot that opens the minigame is the one it stays on.
    AUTO_SWAP_SLOTS:      1,         ; 0 = only ever press START_KEY
    SLOT_KEYS:            "1,2,3,4,5",

; ---- window gate -----------------------------------------------------------
; Keys are ONLY ever sent while this window is active, so the macro can never
; type into your desktop. RegEx match mode is on, so this matches FiveM.exe,
; FiveM_b3095_GTAProcess.exe, FiveM_GTAProcess.exe, etc.
    WIN_MATCH:            "ahk_exe i)FiveM",
    REQUIRE_GAME_ACTIVE:  1,         ; 0 = allow off-window (testing only)

; ---- geometry - ABSOLUTE SCREEN PIXELS, scaled from 1080p on first run -----
    DIAL_CX:              SCX,
    DIAL_CY:              SCY,
    DIAL_RADIUS:          Round(53 * SCL),   ; MID-radius of the ring stroke
    BAR_Y:                BY(976),           ; core row - crispest colour
    BAR_X_LEFT:           BX(820),           ; track starts
    BAR_X_RIGHT:          BX(1100),          ; track ends
    BAR_FULL_X:           BX(1085),          ; "full" probe; deliberately short
                                             ; of the end - the bar disappears
                                             ; ~100ms after completing

; ---- colours (sampled from footage, not assumed) --------------------------
    ; SKILL CHECK STYLE (2026-10-10): grey ring, PINK target zone, red marker.
    ; Measured from the user's screenshot: zone core 0xC345AF, marker
    ; 0xCE1313, grey ring 0x2A2E3B, ring mid-radius ~53px at 1080p. The dial
    ; sits inside a "Skill Check" panel, NOT at screen centre - see
    ; AutoLocateDial(), which finds it on screen by itself.
    LOGO_FILE:            "",        ; custom logo image ("" = built-in XZX logo)
    ZONE_STYLE:           "pink",    ; skill check zone colour: pink green blue yellow purple custom
    TICK_STYLE:           "red",     ; marker colour: red white yellow blue custom
    ARC_COLOR:            0xFF88F0,  ; pink target zone (current Skill Check)
    ARC_TOL:              30,
    TICK_COLOR:           0xEF4444,  ; rotating red marker (current Skill Check)
    TICK_TOL:             45,
    BAR_COLOR:            0x00B7C8,  ; filled teal, row y=976
    BAR_TOL:              40,

; Shape-based fallbacks. A tolerance ball around one RGB triple is brittle at
; antialiased edges, where a pixel is a red/blue BLEND. These dominance tests
; catch the blend and fire earlier and more reliably. A pixel counts as a
; match if EITHER the exact-tolerance test OR the dominance test passes.
; Thresholds are set to exclude real scenery measured in the same footage,
; not merely to accept the target colour:
;   red  - the pink-haired NPC's hair (0xE89AB0) sits ON the dial annulus and
;          scores R-G 78 / R-B 56, so anything at or below 56 false-fires.
;          The tick scores 239/239, and a 70%-covered blend still scores 118.
;   blue - open ocean (~0x4A6A8A) scores B-R 64; the arc scores 166.
;   teal - the only thing near it is the bar itself (G-R 183, B-R 200).
    USE_DOMINANCE:        1,
    RED_DOMINANCE:        90,        ; R-G and R-B both above this => "red"
    BLUE_DOMINANCE:       60,        ; ZONE dominance: R-G and B-G above this => pink
                                     ; (ini key kept as BlueDominance)
    TEAL_DOMINANCE:       70,        ; G-R and B-R both above this => "teal"

; ---- scan behaviour --------------------------------------------------------
    SCAN_STEP_DEG:        2,         ; angular sweep resolution to FIND the arc
    WALK_STEP_DEG:        1,         ; resolution to measure the arc's extent
    ; The tick sits ON the arc and hides ~6 deg of it. Bridging gaps up to this
    ; many degrees stops a tick from splitting one arc into two short ones.
    ARC_GAP_TOL_DEG:      14,
    ; Neither probe showing arc-blue nor tick-red for this long means the dial
    ; has closed. Must exceed the time the tick spends covering a probe (~15ms).
    DIAL_GONE_MS:         250,
    ; Degrees excluded at each end of the trigger band, so the tick's
    ; antialiased leading edge just outside the measured arc cannot fire it.
    TICK_EDGE_MARGIN_DEG: 8,
    ARC_WIDTH_MIN_DEG:    6,         ; harder skill checks have narrow zones
    ARC_WIDTH_MAX_DEG:    150,
    ; NOTE: the old TRIGGER_INSET (fire 35% into the arc, via two single-pixel
    ; probes) is gone. The tick is only red across ~3.5 deg, so at 60 fps it
    ; advances 6.7 deg per frame and steps clean over a single pixel - it never
    ; fired. WaitForTick now watches a contiguous band; see TICK_EDGE_MARGIN_DEG.
    POLL_SLEEP_MS:        1,         ; inner poll pause between band sweeps
    ; Read pixels from one BitBlt of the region instead of per-pixel GDI calls.
    ; PixelGetColor measured ~5 ms per call on this machine; at that rate a full
    ; annulus sweep outlives the dial itself. Set to 0 only to diagnose a
    ; suspected capture problem - it is correct either way, just far slower.
    USE_SCREEN_GRAB:      1,

; ---- input timing ----------------------------------------------------------
    KEY_HOLD_MS:          40,        ; GTA V ignores instantaneous key events
    REACTION_MIN_MS:      0,         ; randomised so it is not frame-perfect
    REACTION_MAX_MS:      10,        ; kept short: narrow zones on harder checks
    ; ---- multi-stage skill checks (1/2, 2/2 ...) ---------------------------
    MAX_STAGES:           6,         ; most hits pressed in one skill check
    TICK_LEAD_MS:         15,        ; press this far ahead of the marker (input lag)
    ZONE_INSET_PCT:       30,        ; ignore this % of the zone at EACH end - fires
                                     ; only in the middle. Raise if still early.
    STAGE_GAP_MS:         1500,      ; after a hit, how long to look for the next stage
    AUTO_LOCATE_DIAL:     1,         ; find the Skill Check dial anywhere on screen
    AUTO_LEARN_COLOUR:    1,         ; if presets miss, find dial by ring + learn the zone colour
    DETECT_RANGE_PCT:     20,        ; look for the ring this % inside AND outside its saved size
    DIAL_BOX_PX:          140,       ; size of the detection box (the pink square in Calibrate)
    WAIT_FOR_BAR:         0,         ; 0 = no progress bar after the skill check

; ---- timeouts (ms) ---------------------------------------------------------
    DIAL_APPEAR_TIMEOUT:  12000,     ; after pressing START, wait for the skill check
                                     ; (it takes 4-8s to appear on this server)
    DIAL_HIT_TIMEOUT:     8000,      ; waiting for the tick to reach the probe
    BAR_APPEAR_TIMEOUT:   3000,      ; after pressing HIT, wait for the bar
    BAR_FULL_TIMEOUT:     30000,     ; waiting for the bar to fill
    POST_PRESS_SETTLE:    250,       ; grace for the dial to clear after HIT.
                                     ; Measured: the dial clears 17-100ms after
                                     ; the press, so this has ~4x margin
    DIAL_CLEAR_TIMEOUT:   1500,      ; wait for a previous round's dial to close
    ROUND_GAP_MS:         800,      ; breather between rounds
    RETRY_GAP_MS:         800,      ; breather after a failed round

; ---- failure handling ------------------------------------------------------
    MAX_START_RETRIES:    3,         ; presses of START with no dial => stop
    BLACK_READ_LIMIT:     40,        ; consecutive 0x000000 reads => warn about
                                     ; exclusive fullscreen breaking pixel reads

; ---- UI --------------------------------------------------------------------
    UI_X:                 40,
    UI_Y:                 40,
    UI_SCALE:             75,        ; 75 / 100 / 125 / 150
    CONFIGURED_FOR:       A_ScreenWidth "x" A_ScreenHeight,
    MENU_COLOR:           0x9D4EFF,
    BACKGROUND_COLOR:     0x3A1474,  ; violet gradient glow, matches the XZX logo
    BUTTON_COLOR:         0x9D4EFF,
    GRADIENT_COLOR:       0x09060F,   ; bottom stop of every menu gradient
    ; 1 = normal, 2 = verbose (phase timings + scan outcomes),
    ; 3 = trace (every probe). 2 is the level to use when reporting a problem.
    LOG_LEVEL:            1,
    DEBUG:                0          ; 1 = log every scan detail
}

; ============================================================================
;  CONFIG <-> INI TABLE
;  section | ini key | property | type (s string, i int, c colour, f float, b bool)
;  This table is the single source of truth: load, save, and the settings
;  window's field types all read from it, so adding a setting means adding a
;  row here and nothing else.
; ============================================================================
global CFG_MAP := [
    ["Hotkeys",  "ToggleMacro",       "HK_TOGGLE",           "s"],
    ["Hotkeys",  "EmergencyStop",     "HK_STOP",             "s"],
    ["Hotkeys",  "CalibrationMode",   "HK_CALIB",            "s"],
    ["Hotkeys",  "OpenSettings",      "HK_SETTINGS",         "s"],
    ["Hotkeys",  "MenuToggle",        "HK_MENU",             "s"],
    ["Hotkeys",  "Diagnostics",       "HK_DIAG",             "s"],
    ["Hotkeys",  "StartKey",          "START_KEY",           "s"],
    ["Hotkeys",  "HitKey",            "CONFIRM_KEY",         "s"],
    ["Hotkeys",  "AutoSwapSlots",     "AUTO_SWAP_SLOTS",     "b"],
    ["Hotkeys",  "SlotKeys",          "SLOT_KEYS",           "s"],

    ["Window",   "WinMatch",          "WIN_MATCH",           "s"],
    ["Window",   "RequireGameActive", "REQUIRE_GAME_ACTIVE", "b"],

    ["Geometry", "DialCenterX",       "DIAL_CX",             "i"],
    ["Geometry", "DialCenterY",       "DIAL_CY",             "i"],
    ["Geometry", "DialRadius",        "DIAL_RADIUS",         "i"],
    ["Geometry", "BarY",              "BAR_Y",               "i"],
    ["Geometry", "BarXLeft",          "BAR_X_LEFT",          "i"],
    ["Geometry", "BarXRight",         "BAR_X_RIGHT",         "i"],
    ["Geometry", "BarFullX",          "BAR_FULL_X",          "i"],
    ["Geometry", "ConfiguredFor",     "CONFIGURED_FOR",      "s"],

    ["Colors",   "ZoneStyle",         "ZONE_STYLE",          "s"],
    ["Colors",   "MarkerStyle",       "TICK_STYLE",          "s"],
    ["Colors",   "ArcColor",          "ARC_COLOR",           "c"],
    ["Colors",   "ArcTolerance",      "ARC_TOL",             "i"],
    ["Colors",   "TickColor",         "TICK_COLOR",          "c"],
    ["Colors",   "TickTolerance",     "TICK_TOL",            "i"],
    ["Colors",   "BarColor",          "BAR_COLOR",           "c"],
    ["Colors",   "BarTolerance",      "BAR_TOL",             "i"],
    ["Colors",   "UseDominance",      "USE_DOMINANCE",       "b"],
    ["Colors",   "RedDominance",      "RED_DOMINANCE",       "i"],
    ["Colors",   "BlueDominance",     "BLUE_DOMINANCE",      "i"],
    ["Colors",   "TealDominance",     "TEAL_DOMINANCE",      "i"],

    ["Scan",     "ScanStepDeg",       "SCAN_STEP_DEG",       "i"],
    ["Scan",     "WalkStepDeg",       "WALK_STEP_DEG",       "i"],
    ["Scan",     "ArcGapToleranceDeg","ARC_GAP_TOL_DEG",     "i"],
    ["Scan",     "DialGoneMs",        "DIAL_GONE_MS",        "i"],
    ["Scan",     "TickEdgeMarginDeg", "TICK_EDGE_MARGIN_DEG","i"],
    ["Scan",     "DetectRangePct",    "DETECT_RANGE_PCT",    "i"],
    ["Geometry", "DialBoxPx",         "DIAL_BOX_PX",         "i"],
    ["Scan",     "UseScreenGrab",     "USE_SCREEN_GRAB",     "b"],
    ["Scan",     "ArcWidthMinDeg",    "ARC_WIDTH_MIN_DEG",   "i"],
    ["Scan",     "ArcWidthMaxDeg",    "ARC_WIDTH_MAX_DEG",   "i"],
    ["Scan",     "PollSleepMs",       "POLL_SLEEP_MS",       "i"],

    ["Timing",   "KeyHoldMs",         "KEY_HOLD_MS",         "i"],
    ["Timing",   "ReactionMinMs",     "REACTION_MIN_MS",     "i"],
    ["Timing",   "ReactionMaxMs",     "REACTION_MAX_MS",     "i"],
    ["Timing",   "DialAppearTimeout", "DIAL_APPEAR_TIMEOUT", "i"],
    ["Timing",   "DialHitTimeout",    "DIAL_HIT_TIMEOUT",    "i"],
    ["Timing",   "BarAppearTimeout",  "BAR_APPEAR_TIMEOUT",  "i"],
    ["Timing",   "BarFullTimeout",    "BAR_FULL_TIMEOUT",    "i"],
    ["Timing",   "PostPressSettle",   "POST_PRESS_SETTLE",   "i"],
    ["Timing",   "DialClearTimeout",  "DIAL_CLEAR_TIMEOUT",  "i"],
    ["Timing",   "RoundGapMs",        "ROUND_GAP_MS",        "i"],
    ["Timing",   "RetryGapMs",        "RETRY_GAP_MS",        "i"],
    ["Timing",   "MaxStartRetries",   "MAX_START_RETRIES",   "i"],
    ["Timing",   "BlackReadLimit",    "BLACK_READ_LIMIT",    "i"],
    ["Timing",   "MaxStages",         "MAX_STAGES",          "i"],
    ["Timing",   "TickLeadMs",        "TICK_LEAD_MS",        "i"],
    ["Timing",   "ZoneInsetPct",      "ZONE_INSET_PCT",      "i"],
    ["Timing",   "StageGapMs",        "STAGE_GAP_MS",        "i"],
    ["Timing",   "WaitForBar",        "WAIT_FOR_BAR",        "b"],
    ["Geometry", "AutoLocateDial",    "AUTO_LOCATE_DIAL",    "b"],

    ["UI",       "PosX",              "UI_X",                "i"],
    ["UI",       "PosY",              "UI_Y",                "i"],
    ["UI",       "Scale",             "UI_SCALE",            "i"],
    ["UI",       "MenuColor",         "MENU_COLOR",          "c"],
    ["UI",       "LogoFile",          "LOGO_FILE",           "s"],
    ["UI",       "BackgroundColor",   "BACKGROUND_COLOR",    "c"],
    ["UI",       "GradientColor",     "GRADIENT_COLOR",      "c"],
    ["UI",       "ButtonColor",       "BUTTON_COLOR",        "c"],
    ["UI",       "LogLevel",          "LOG_LEVEL",           "i"],
    ["UI",       "Debug",             "DEBUG",               "b"]
]

; The six rebindable actions, in the order the settings window lists them.
;   property | label | game (true = sent to the game, not registered as a hotkey)
global ACTIONS := [
    {prop: "HK_TOGGLE",   label: "Toggle macro on/off", game: false},
    {prop: "HK_STOP",     label: "Emergency stop",      game: false},
    {prop: "HK_CALIB",    label: "Calibration mode",    game: false},
    {prop: "HK_SETTINGS", label: "Open settings",       game: false},
    {prop: "HK_MENU",     label: "Open/close menu",     game: false},
    {prop: "HK_DIAG",     label: "Write diagnostics",   game: false},
    {prop: "START_KEY",   label: "START key (in-game)", game: true},
    {prop: "CONFIRM_KEY", label: "HIT key (in-game)",   game: true}
]

; ============================================================================
;  DERIVED GEOMETRY + CONSTANTS
; ============================================================================
global G := {}
G.DEG := 57.2957795130823
G.RAD := 0.01745329251994

; Recomputed whenever the dial centre or radius changes. The measured arc band
; was 46.0..53.1 around a 49.7 mid-radius, so -16%/+20% covers the stroke with
; margin at any scale.
; A radius of 0 or an absurd value poisons every geometry calc: the ring
; collapses to a point, so any single grey pixel scores a fake 100% ring match
; and the macro "finds" a dial jammed at y=0 forever (seen live - radius 0 was
; saved to config.ini and reloaded every launch). Clamp it back to the default
; for this resolution and persist the repair. Returns true if it changed it.
EnsureSaneRadius() {
    defR := Max(20, Round(53 * SCL))
    if (!C.HasOwnProp("DIAL_RADIUS") || C.DIAL_RADIUS < 20 || C.DIAL_RADIUS > 600) {
        C.DIAL_RADIUS := defR
        try SaveCfg("DIAL_RADIUS")
        return true
    }
    return false
}

; A located dial must have a real radius and sit a full radius clear of every
; screen edge. Rejects the degenerate y=0 / radius-0 hits that a point-sized
; ring would otherwise accept.
ValidDial(loc) {
    W := A_ScreenWidth, H := A_ScreenHeight
    if (loc.r < 20 || loc.r > 400)
        return false
    if (loc.x < loc.r || loc.x > W - loc.r || loc.y < loc.r || loc.y > H - loc.r)
        return false
    return true
}

RecalcGeometry() {
    EnsureSaneRadius()
    ; How far around the saved ring size to look - Settings -> Regions ->
    ; Detection area. 32% (the default) = 68%..132% of the radius.
    pct := Max(5, Min(60, C.HasOwnProp("DETECT_RANGE_PCT") ? C.DETECT_RANGE_PCT : 20))
    G.rMin   := Max(2, Round(C.DIAL_RADIUS * (100 - pct) / 100))
    G.rMax   := Round(C.DIAL_RADIUS * (100 + pct) / 100)
    ; The detection box (pink square in Calibrate): the size set in Settings ->
    ; Regions or by dragging its corner, never smaller than the ring itself.
    boxPx    := C.HasOwnProp("DIAL_BOX_PX") ? C.DIAL_BOX_PX : 220
    G.boxW   := Max((G.rMax + 2) * 2, Min(900, boxPx))
    G.boxH   := G.boxW
    G.boxL   := C.DIAL_CX - G.boxW // 2
    G.boxT   := C.DIAL_CY - G.boxH // 2
    G.native := (A_ScreenWidth = BASE_W && A_ScreenHeight = BASE_H)
}

; ============================================================================
;  STATE
; ============================================================================
global S := {
    running:     false,
    loopActive:  false,        ; re-entrancy guard for MainLoop
    state:       "IDLE",       ; IDLE / SEARCHING DIAL / WAITING BAR / ERROR
    detail:      "PAUSED",     ; finer-grained label shown next to the LED
    led:         "amber",
    rounds:      0,
    hits:        0,           ; E presses that actually cleared the dial
    attempts:    0,           ; dial rounds actually tried
    misses:      0,
    timeouts:    0,
    blackRun:    0,
    blackWarn:   false,
    calib:       false,
    capturing:   false,        ; a rebind capture is in progress
    sessionT0:   A_TickCount,
    logLines:    [],
    history:     [],           ; recent round outcomes: "ok" | "miss" | "timeout"
    gateWasBlocked: false,     ; window-gate transition tracking, for logging
    gateSince:   0,
    gateLogged:  0,
    lastScanMiss: "",          ; why the most recent arc scan found nothing
    slotIdx:     1,            ; index into SLOT_KEYS of the detector in use
    slotProven:  false,        ; this slot has opened the minigame at least once
    slotProbes:  0,            ; slots walked since the last minigame opened
    hotkeys:     [],           ; {key, ctx} actually registered
    dragMap:     Map(),        ; hwnd -> "main" | "dialbox" | "barmark"
    noDrag:      Map(),        ; interactive control hwnds that must keep clicks
    hoverHwnd:   0,
    rateColor:   "",           ; last colour set on the success-rate stat
    gameCtx:     ""            ; #HotIf callback, built at startup
}

global UI  := Map()      ; main window controls
global SET := Map()      ; settings window controls
global CAL := Map()      ; calibration overlays

; Screen-capture state (see the SCREEN GRAB section for what it is for).
; It MUST be assigned here, with the other globals, not beside its own
; functions further down: the auto-execute section runs top to bottom, and
; DumpDiagnostics("startup") calls PixelAt long before any later line is
; reached. Declaring it late gave "This global variable has not been assigned
; a value. Specifically: GRAB" on the very first pixel read.
global GRAB := {hdcSrc: 0, hdcMem: 0, hbm: 0, oldBm: 0, buf: 0,
                w: 0, h: 0, x: 0, y: 0, ok: false, broken: false}

; ============================================================================
;  XZX MACRO THEME  (purple gradient - fixed)
; ============================================================================
; Text tones
global TXT_MAIN := "F4EEFF"      ; headings / values
global TXT_SUB  := "A99CC4"      ; captions
global TXT_DIM  := "6F6489"      ; small labels
global FONT_UI   := "Segoe UI"
global FONT_SEMI := "Segoe UI Semibold"
global THEME_BORDER := "2E2250"
global THEME_DANGER := "F0587E"

; Colour lookups for the status readout.
global STATE_COLORS := Map("IDLE", "D8C8F5", "SEARCHING DIAL", "B57BFF",
                           "WAITING BAR", "C9A3FF", "ERROR", "F0587E")
global LED_COLORS   := Map("green", "B57BFF", "amber", "D8C8F5", "red", "F0587E")

global BRAND_ACCENT     := "B57BFF"
global BRAND_ACCENT_OFF := "7D7096"

global THEME_ACCENT   := "9D4EFF"
global THEME_SOFT     := "D8C8F5"
global THEME_MUTED    := "C9A3FF"
global THEME_GRADIENT := 0x3A1474     ; violet glow
global THEME_GRADIENT_END := 0x09060F ; near-black base
global THEME_PANEL    := "130C24"
global THEME_FIELD    := "1A1230"
global THEME_INPUT    := "241940"
global THEME_BUTTON_TEXT := "FFFFFF"

global BTN_IDLE        := "9D4EFF"
global BTN_IDLE_HOVER  := "B06BFF"
global NAV_IDLE        := "1A1230"
global NAV_IDLE_HOVER  := "2A1D4A"
global BTN_DANGER       := "8E2A52"
global BTN_DANGER_HOVER := "A9346A"
global BTN_QUIET        := "1E1538"
global BTN_QUIET_HOVER  := "2C2050"

; Round-history strip: one cell per recent round, newest on the right.
global LOGIN_GEO := {w: 420, h: 672, cx: 32, cy: 316, cw: 356, ch: 280,
    dx: 56, dy: 352, dw: 308, dh: 44,
    fx: 56, fy: 432, fw: 308, fh: 50, bx: 56, by: 498, bw: 308, bh: 50, r: 14}
; Main window layout (see BuildMainGui)
global MAIN_W := 960, MAIN_H := 628
global MAIN_RECTS := Map(
    "btnRun",    [24, 254, 182, 52],
    "btnStop",   [24, 314, 182, 40],
    "btnDash",   [24, 388, 182, 34],
    "btnSet",    [24, 424, 182, 34],
    "btnCal",    [24, 460, 182, 34],
    "btnDiag",   [24, 496, 182, 34],
    "btnReset",  [24, 532, 182, 34],
    "btnStream", [24, 568, 182, 34],
    "btnClose",  [906, 28, 32, 32])
global MAIN_NAV := [["btnDash", Chr(0xE80F), "Dashboard"], ["btnSet", Chr(0xE713), "Settings"],
    ["btnCal", Chr(0xE790), "Calibrate"], ["btnDiag", Chr(0xE9D9), "Diagnostics"],
    ["btnReset", Chr(0xE72C), "Reset stats"], ["btnStream", Chr(0xEA18), "Stream proof"]]
; Settings tabs (inside the main window) and the Save button beside them
global SET_TABS := [["general", "GENERAL"], ["bindings", "KEYS"], ["colours", "DETECTION"],
    ["timing", "TIMING"], ["regions", "REGIONS"], ["interface", "THEME"]]
global SET_SAVE := [832, 96, 108, 36]
; Menu colour presets (Settings -> Appearance). The whole UI is derived from one.
global THEME_ORDER := ["purple", "pink", "blue", "red", "green", "cyan", "orange"]
global THEME_PRESETS := Map("purple", 0x9D4EFF, "pink", 0xF0479C, "blue", 0x3B82F6, "red", 0xE5484D,
    "green", 0x22C55E, "cyan", 0x0EA5C6, "orange", 0xF97316)
global ACC_RGB := 0x9D4EFF, ACC2_RGB := 0x7036E0, ACCL_RGB := 0xB57BFF, SOFT_RGB := 0xD8C8F5
global PANEL_RGB := 0x130C24, FIELD_RGB := 0x1A1230, INPUT_RGB := 0x241940, BORDER_RGB := 0x2E2250

; Skill check colour presets (Settings -> Detection)
global ZONE_PRESETS := Map("pink", 0xFF88F0, "green", 0x3CC48F, "blue", 0x3B82F6,
    "yellow", 0xF2C14E, "purple", 0x8B5CF6)
global TICK_PRESETS := Map("red", 0xEF4444, "white", 0xF2F2F2, "yellow", 0xF5D90A, "blue", 0x2F7BFF)
global HIST_CELLS   := 28
global HIST_COLORS  := Map("ok", "A66BFF", "miss", "F2C14E",
                           "timeout", "F0587E", "", "1F1638")




; ============================================================================
;  ACCESS LOCK / WELCOME PAGE
; ============================================================================
; ============================================================================
;  SERVER LICENSE SYSTEM
; ============================================================================
; Keys are made and checked by the Discord bot (no separate website).
; Run /key setup in Discord and paste the address it shows here.
global LICENSE_API_URL := "https://verification-bot-production-2ea4.up.railway.app"
global LICENSE_CONNECT_TIMEOUT := 5000
global LICENSE_REQUEST_TIMEOUT := 8000
global AccessGranted := false
global LicenseStatusText := ""
global LoginGui := ""
global LoginCodeEdit := ""
global LoginStatus := ""
global LoginAttempts := 0
global LoginButton := ""
global LoginInputSurface := ""
global LoginButtonState := "default"
global LoginHovering := false
global LoginEditOriginalProc := 0
global LoginEditProcCallback := 0
global COOP_LOGO_ICONS := Map()
global LoginBg := "", LoginFocused := false, LoginReveal := false, LoginRevealBtn := ""
global LoginLinkState := "none", LoginLinkName := "", LoginSession := "", LoginLinkUrl := ""
global LoginPollStart := 0, LoginDiscordBtn := "", LoginDiscordHover := false

; ---- embedded logo (fixed, cannot be replaced) -----------------------------
global COOP_LOGO_B64 := "
(Join
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
ne1tydyNLfXjamPJvaMMzBmfGOseNnaoGPKMIa2Ax4YDNfPnJGgLJHg19Wu2VX3Am9d+wOG9JuJWOQghMQOStjUJTrzsFDY3baDU
7IbrxfDMLbzc9iASg0vN69RvvdtNTyS/vaDosdfurTtD/BTENyPAGTJkyPAjs37/PmNpV4bzLpdzy6YWkV8UlcFQ0DATCRNNOMsy
c9p9qyCHrP2lGzp6Y32b1RY3C4VnR3q5A/391CB/9GUFsuw+X7DIY+VZilUbDDpDKZ5N/ILiKTGeuf5zerjFOIaDbJVQqVCbbS48
8zIWrf+IIdZkfC9Akan5wP0tzaqJw+V0bF/qN/2/iBKz2x/urTsj1mX9+hkBzpAhQ4YM/xeI/9JwTX/9/yn7d0/hXVW/Sny0qUWU
JQYLu6hIhqodw8hWtjJCEd+xcy3bz5EN5pCkIQ+uCmb3CjYXIYWiu5ftjczKo+9vLBm+QAn1qmDd5Yqd1SYNwe28lLiHo68cwpUn
XYPapnByHQxpogo8RKvNdcf+jhdXPEJfey+GuNMJywixyArmtL2GKWzOFVer+/xZppRi488qZj03a+vZPxnrNyPAGTL8sBdwnTkM
/zbRFP+FkP79udH/zfP/TojFf/D5Ht8zrevpID2N2WL67i8PZtDEVWmLt22s7FFRJxyCsj2RMAvzQraWZsTwRX7A9MpTDWKw3FZw
bG633H5lpZY3aFmJDKFF731CMudBA2MoeI9qtt8DLfUWjaKe+fo1rn9wGocOmkTL1x65/QRSGPhhDzvX5q6j3+D3y2+j2CpngH8Q
FboX3Yo1v2p5AQ/FkZxAXLczT/9VVNp97pu19ezWLk3yfioXVEaAM2T4YYpARnz/+cf0PxNV3WWV6X8UP5FWRSH+7hcKELv+X+xx
0r77Fa7n7v4hy7S03v2HxZ4/oTVordOarbT6uzOv0cze/Wz2335FXDivxcyuzDPbszuNRGfEiEjPtGU46PleRHlmvuiUffzW8MFm
R/SAYreoqKokTFZP5HAzIEJXIew7LVSzQv1c0/K+ItZikHBjNFStY8bvptFHVlDziUOkp4HXKdCmR3i0zavnrueFhc9gmHF6+IeT
q/oxuLKSN4xZ7EzWYosQ54hL1B36ekMIufrO3Af/fEbdIT+JzOeMAGfI8MMUCUlXbCyLrIIOOpoyh+V/ihbsbqu4WuwhVXqPRV/v
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
yw7Nmlv35up0869pxr8rSev7Ng3JAPx9s47p93nH6x/cl/1Mt0avzVvW72vjqLbL9JjNA/xnA/fav03NdLKM7Mcu7Xb37XdtPr+O
TILWP+O4a0vaakrByft82PrcDbjBqdeF7uTMnIucjVQbV9efxVHqYC9smIFZ/nV/1eipXV2AfgS9Wf/W6t0lvACfddSYx/WabG1q
2Nbtw9Wvn7S9uvmk7JZBfcdwpDrmsr28IT/D6HgfvrolxiZRzchZQcZdUyU2PdnKb89/ij9zK526naDKJtsqpo+ayL1Vt6lza44w
V6a++mpG0UuTZjVMj/8T3M5/k1QVlGHGlk0et7hxzgmJVOfR+VT0mVx0GOfsdb4a1WNvryWVEh9/85VcsHE+i5s+YQeriLMDyzIY
MXy4OGbqMUwZcwS9zJH4O8BpBCfmIyyPYA6Y4XTtqbQ1Zlhg50gC+SaEgTZ0xyrYvHQnKxatE1tXN9LZEiCFTRGVlGeVkF8SJT/X
Jj8EWRIiQmEHNGYOmAUCYWhUDFRcEEsKWhKCeAJiCehsh5Y6RazDI6WcrniqRiG0T4o4rbTRQBPraBHVtFBLm6gmoVuIyQah8LWt
gyjtCo8UYULY2ISIdDWh0IRFmIgI66gZwsAgoTxsEcHWYaTWSCWI6wTtdJAkLjwcIdAiRZIO0YIjUzjCwSVJXDuuo71207Taglao
TajgzqAR/jwrkLtoZMGYrTcP+E3jgDeIAVx+2O+sj5e8aq1p/LTzilH3lr21/rlH6zq3H5aQMSJekby0/EYuvuZMmhcHWfeiC1ku
niPRHoy8VdB3rwALZtbx5vwvaCZBxIzQXfVjZHY/8odBdomioADClQKjP6jBEt1LCCMgtdrgoesUIizAMZA9NM2/Ergf+9h9IBiR
GL7GP1Nh/MZk8xyXz21FZSBEvb2ezbUNXPbASPKOsGk5UFDnerSGXNo7JJ1tJp2eok130ixqaU+PhcTRMRKimbhopJ0atvkr2coK
BIoJPffn+P1PZvLESfTpXorvK5JOCsNKb/pkSKBMhV1oQx5sXbuVpx5/hnlPf4MTLyWPvoxnnD502gBG3RElUY/+8uYm9ey8d+wv
xDvt4UJn9hmDTvvN5fNO+DYdG57r/xfSqH8KAixGM9pcylK31Cg9pNFve/0F81PjRR6XnZNaxOimUxm+dKSSRbVc2nqqWeNuWT8i
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
UV2v5cp7u7LjFYvqcw5et84FdZIf5H2MtaeSphpj6hGkrO35Cg2kFAgpcXldrDq3kHGvd2Dv6hMsWfUrm4y5VFkVGLgJKz8P6C/Z
As2YYk1c1Fi23n7GOfLKc8bXkdbOcP0+uysxwkVnrS+LrTm4hQ+38GIoFwiNUucCN7e5HRyDg0fKSdMbcGfLm+xHjlxn5Kmj6xxl
DxJ/Wiv/3vOmjg8jVYfkvs33Vqz/INaqf/mX9X4w+3TtIEbvulzsOL8et/QKWzlo6HjwEXRVkRJpwKTkOYx6qyshI0AkT6IcMAOK
BK+XvPBxRrw1UFlmFPfL73jylZ7Oq9M+l1NOP2cNrn/ZpUvPzl7zZ6Ssv87SEYg/wLmeO6tZWjj+7C52mVA7h/X/+qUBdt+EK/rs
9a9bbZlx8vNOS0WDRoZ4fsFLnDV24zdNPm04j5t/7I7sZuEc1/ltShHrPqlkSOuG9J3qpiQHNfeeM2pJYJp9Rvvd7dbloey07Mnj
B9670gycqB47fz7894OwgC467DINzUXD6PZ35decmBzBSu9k3RC+Pvoh7bqvW8n6/Q1yn7VY+10RuSKP885ZBt6exegpHZBeDQsT
/ysGM775gcfO34RwBPHUo57Mpoe4gYn3j1dvzXnbmVLyjN3c123YCf/2DbXZ/7r/KQAs/jzrXdylQMvKGuKMoWfc55ufHnm0eN+D
0ZFGXa5Ou82+84bRxHulXDXrLL+d20autond9iJis8I8NfoVRkVNwD4Lth5B2hqm5ZB0jSCuq0SUgGMqVHMHedxg3qVHOZS8lmn5
kyir9tPM1ZEEswmJogXZzhB6ZNfnkfz+XCnv4OFb7qH7120oCxaCBQkkqm5x3e0e7TqIAZcME/GJTUVFUQ3HDuaJA4W7OXr6IAWl
efgJIHAR63bRwteERJlMoXmOg8GtnDVrZ3Z14aaNPpD2XE07ZxCxKg1bOVhELu7w2glYx0GiEy8SSdQ0VUEJx/RfxSq+4qB1CLeT
ThOnL63oRyYNaZqZSuu2KdRTyY7tt5ydebvV4qKfXZtCq0UeOTgESjxu75Z4mbyknafrphU95p7QlxvhP2W5fw642f/emXpNQ7Md
TU6a4Lr3pRHaFdYk+ynjUHBPiRDiq4ZRzWYWBM4dMZ3wxYX4P20cTgJCIu1WcR0HHa7aPaubu0/6XY0eCuXoJXpmsJNoEerFuXwT
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
y/IdG9hYvYK0ZhpPPXcPwwYP55uvvuXJNx/DtEK0j21D/ao+THryeZ7+/Q427tnFqxmL1N3P9bZHP36L63f7u0nS0iY72P/rAfgP
tnGPxKtan6o4sL6cguRWzuXWex2+kQO+jWLHOJNDh8op0qs5olYzz36Kp1Mn8cqzj6IeiCB0HcIKDBBSg+026hSI+gKVbCuRJsif
KVjz9Dm1K7zNOeLbpIe0Un98VOz69KSkX0+VnuxYUF54q7JcUU3s7qErom4Ul7+erTW4F2HuM8n7TIiCHYLSQkVFtcX+0AGiBp3i
jk/7Uy8uk4qlFiosCNuK6nKoyFGY0qTpFT6+eH0ek3aMo5OrF0mksCryIxo6Dg7RMomIMlV3MZQ74iexxvmM6RWfohNNHCmqdUo7
p1eDHqpvw360a9BR+BJixNnAebFx1w62n9hOkZEvotMlvmSb4qISjp0+SF5JLii4Vbtf3STvch6z73IdUNu+3txj80O9t/YO1lrJ
7bL+auNeZFE7eSOVr8+mNg8Wl5c+0oo+6S+MfMkcNqoDK38/JpfNP0G1XUa+PE6ZkyNu63Ul4167lKSBsQCYpoVmCagAGYLIboeK
M4qoKzWimmuY5Q4UKWSVQpmgNIUwal+FilNELCjaUMPQRy6hxMylg68X6yuX0kIMYJyajKkFyCWHc3Y5w5t34MeizxnW4naen3gN
J74NUrXCwa6WhHTFMm0B30cm4/MEaevqjxFuhDvUiHTRnESVgoco3HhwCQMHgaZcJMoYNKOSXcYsNc98l9PhHBrSnY6MpE9qf4a0
6KaSYw1nWf5C+c3Br/Qd1lok9sHM6KZzh/qun/1d8eun6+Z2tQEMEP+znLr+00uEdmNv1sNngqc//DxxUWRnaL02PfQJHeRgcu3D
olg7zSXWrXx767fU+yIEmo6h11bel/6ykdkfr6JwraAXVzC0RWeaXy9JH4IK5aD2LM2z5/w+17WsZL44x7E8r2F83y613+wN5388
0iS21b1nqo+9M9Z1mytb6xz+RE3SJwx+jMsqnhMFm6polBrLrqyFvHTwDsr95US5omhpDWAUzxDlZCCErA0ehRvTtvDgpkGWjwON
v+aTY5M5m1dGc6MbHuWmnAJMESBEDeX2BYUjVEvaOe3ppEIi4s7hFKc5YoeVLR7NepyMCzdzKJBDikxivfyMLXzLp00XIk625Khz
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
rA1pzxUYqoYwb9lcfl6ymAiLQuORpoI6KmkgSRUeaTQJYlNRhiRZUlSSpIIUVSSpIEmaJCl8LB4mVnuj0SJoT/BTgpe2OClws/E+
OZmNM8K1jQFO6ZhejqMrbCxmKcXngxLBOPHVSpze5DXZ8EM28dVKBWCK8eFBj40vjflYdU/eYnogykPQbQi6IcgJQTEiCCAqxbf9
sGgpFi1RIEQGTBSLg6IIopJgQ4l39trGxSOOxcsK2gXtxvtVRyDhC6U02Fpw+kY4AyRWyhpbplsFEmXmqHx8GQviWFSTZtFfczz5
97d5PXidysY06/KL+LjzW7AWTxJEFjaSrelkLQvkV+rDBob5o3l3xhvcNv16zrjpGp46KcVimbf+khaZKF6LlBOnVHkFAoKDLvfu
akTiLLo4BUyhjCZQwuc3dfC3259gl58n8FHnnQyM/sKKRyNuvuxhmbt8W7tucStJnVYLgnlUqT4EKo+N4OiGcxm+m8/yGSUqhgom
AmtUDLpWsKa8iukUBk6LME/5/NryFp/p1+inx5CSasQailbz+5pp3FO6ghazxoxJbnbNLWuO/c+mXw0ERw87teGJRffcNabP6PQV
W98ePPvmZ/qH3JcUpIW8001LsJxRA0by/ivv0Xpvlhvv+RtL9CI2dnZEROPgYm2ZyUIhVsfvPmvji7F1EHTZymYoUaRAAWW7cCWB
Lxkc7ZA3PVBIMeUP/dnq7CQ/n52nY3qS5MBOfr/0Mh5a8XdGMpWhalsCUyCtMrbGDGK7mk3Z8i8ev3zUHp335FneF+4LiyekNjnx
544vP7AY91+EZ2xIaSgDcEwVDahxa1BJV6Kixldpq5WWAkUULiMGDgEfpKH8cylY3BrNys/bWFNYxcZ6O5INLgTQ2dpFIMW2E3c5
quu2F//8v34Clt/IuKOrtn2p3/tzHpn22Nybj+4J7JjN3P04be+zgh32HS4lSvrzW5pZ/l4P81koX/EuK7xPGbd9PbcddZ7stfuB
tjKVgSWQ/zkgKFjUcMGvUiQBZ42lbXVEkAMv3akHvwABAABJREFUqagdL/TdTajfNlYtL/vY0Po6BMvA+uBUWlIVQiJlSXZBsk1w
M+AmiCcaEaRco9fbnGSXW/RoiNZA4TvoLkLlgcJdB33Am6tfx9OwjexJm7uQ9/OvkJJKqqQe3ybxJUWKKjJ2CA2MZ6dx/bi97STe
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
H4vnzfWu+Pa34R/09y2xUMntF+5+22OruhZUvrv5iedCTuWe9+71sn3c2RPN2Xdt5Z1VL/CSvF2k7GZuO/9uLj37Etwym7ZXTNY8
G7SczDhEawVlYwxWPNfGxpWtbBObSeoOXJEvOLGIINMTgWmYKCfE4H5DOfyBbpz2h6P4ct6njLSmUORV8CUvETYtfLVD+G+CloSI
IXyLNF34ZKiurGX8yAnsv+f+HLT/FIb0HxxEzNnguS7aABmW+GEfGZOYVgC8S79bw9v3fUnjGx0kc418Z77LWrUaqUN0NwZRqmsp
Vt2pFgOpFT0p8muopjt7jerJ6MlR3ZZJ+c9/9VpoxrInWMHXrbFI4tWx8f3/OKf9vfkFcpX57+jmJCCkkH7fyPCfrHOXPlLm1Vbc
Nu7h3DFHHWE9+9xi8fSKu1nI0xw5/hgeOe2PooYysq4D3Q2s0RDuZUBOkV7jkF0HxiKT/FzNlu/TLG5uZQWtLOMFXBoZxD4MZg/K
rUqMqI0XTZISHeRlO20002hvodNuYau3kVa1jayfJqWSuPpvXdQ7whfiWASZvZaIFJyxgiAFUwchDFIWXJIKNS5a72Sw76DzaOEF
gKp8PDxccrg6h0MOT9v4wkEVWMHBbwQt6OAU7+3Sog2+mtIM9Moioi0RIUJChEkE1a6IE9EJooVKuIhqinU1xbqSIoqJkiBMKSax
wCJTBiYh4YTADAfJQ8IQ+LbAz4Cb0riuj619bOXi0IWn28nh6qzIkRcZMmI7adFEh95OSrSTpUPYZLB1Fpt0QMYhV2iru1rpwIxH
4YKQKHzKqMMXPkm9HTTsV3coZ1XcTO60JSy706aqbQxbvQ5O3GcSbw/4LXc/eSemaRX4DS4nHvlTHp7yPMsfyutcWNK63RNbQh2c
Pa8aa6nFpj+6pLTGcwW+owtJTAVbTE8Xvu7ILEYrP2hR+7ZC5yX5lCvKzDi5Qav5fcNxFFcI9h26H/3LRjAwPpKetd3JV6VYn1/N
mvXrSK80GbzuQOKba6jtF6V2MpSNgaKREtFXoE2FYVpIAzqaO3nz9bd56IkHmL9wHmG6szcncCQncvRpe9DrPKm/fHGlf+m9vw7N
5aNcXXG3S1tS2x50fyQ5/ncAhCiYU+ycIQvAEBbH1F9Qv6Lzu/Ft7tZ9krptH8/zhpuqNNSXPRhj7M/+w/d3Juw7VPQfFxaujdjw
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
OtYjt4dJ6SF57P42j9Z1HiHbxMzxSdRrWl4SmBMtBg6o4NHqG6mJ12PvDZGiA4c2DBlCS60zyiyEiUXQD2X9AJT2SOEJl5RO/pAC
9cOS+xe+SoawsQkS1EFA4woPpTUpL0EfYzy1nVsZWTAYd1mAZ49fzekfDfj/kffXcXZVWf4//F77yNVyS1XcjWAJEAgQPLg1ARpv
HBp3D8Hd3SVYgruTACFChLh7Uu5V147t549TAXpmuqenZ77zm+7nQL2AUHbPuXuvvT7rI+RtG8FvCZCtu6sK87NFCdoAUQaCT/Vn
kN4SIWUa1Le1kjCSYplxgiYHJA0a3FwHtEK8FKxWTWBr4qWKooFCZ7vG6vSQQiDPhAbQGY0aBO4v4LcIWVPja02m1SVtduIVOviR
EG7Rjk+nbiBnecTNPMzAEOXbWvsaUYbkmUkMz0AswYgqVFxpBCQXaOUIllgSjUWxihQojdFHsMohyPjQ6qNTIK1C1hVKjrCokAiW
CY2FUbrpIvLsEg6Jn8rq1qzsMai3/rLuM/+xzucixVbs6SXeJy9rPdKCpz14hv+PLr9Lmmg0+DVf7mTvu/98f9p7+1cPG/ZY4s3c
z4XrjFM3HCef93+R4dypbmy91/2zM/7SA2PHvv95ZvKcv4cV/Y8WYAVT/LOG3dr/3bXP3z4yd3xwyZUniGH58stEl+VqOUc934PB
+xWRS7sYA6D6HuHT22bxnL6ZeeZULM8mQpSczpK087mu/FZOKjk1WNq8UB/8yzh7Nl+iTPPb7czRNy3Ozpn+Ud1zjGSkNZe5/t96
UaEvp5ax7GW+0/DoT3PGN+/zpy+PvPWTjjcu/Pmjudy0yx3ubSefatzx9qsM6OykIMjn+9b3yIvmk7egmPtmPyKX/fly9t1uezob
XLyYhTEgCKGcdlArA/xlio41AVvmaVrWQsMqzZIjN7PPg9154q3rOe2U85m24TvcjW3qzuGPeH9edEz5j82fvzq2bPyh0xqm1G5l
3P1PQiUn9LqzaNLam6fkHGfUDcXPZc65+ERr6qQNPL3wbj7nSfbZb39enfg2nZ8YvPXMLNoa45TZpTS12VQ3x+m9bYTCnYTqbzVV
28eIFGi0HfpVBeJTvq9ixeZafk7PQUyhIVjPz1tmMLttZpCl2cpJ9p3PG5+qHctYc9o/DBdNEJjInEN1fL+vyp7s7u9QcNlRN+Q2
1LSYbnOUn3mXy06/hIPG7Un1EgdRimTfICykKjTbCGlqAco0kcHNPHnFXayva+TM/ZYw6rzh1PzgYhWGCNrWE5Dqygky7XDPTonm
0quP4KRrbqG+NUF61Uhu2+mh4MzvjjG1nat/fd19G3voHtE+nOaVjx+u6+uXBJ9Pm+hprQ0zFnvmwOihpz17wIsZszTPvP2Ft+Sn
grfILq+h8cM2ydf99UFcyfmD95S8iIdxVKjb1VY475UYxCog7gYkNgud6yHb6hHfIsQWJtlp292YYX/KpU9cRpmu4EfnPVzSZH03
PIoGHg5ZxAtQGQNDG+JpH8cPyOkMFdIXs8FjZbCY3uYgLLHDXCEsvCCDFzi4jkcgPoa2UNpA65DoZ3kGA/0cJww8lUFDdmHXpYeA
ayEtNj/Wvcc0XqdDNSKJmWxqHcb824axrHoEpz11OHc+VyPXnXk5z+z6PBt61umvN71Pj8ggkmYcUxdjGxFWz26n9hco20eRXR0Q
SfoM28cgtVLonONhG0LgCNYQQTUUUL+qRfdKDGWAOZZl3nJ20XvQnb5iBzFKKcZRnXRKO506RUa3kqUNR6dop540bbSykSydGNgU
0F0npUTiRgIjYuKZLlY2huUnCNC4fpqUdJDTOVw84sQYFuzKGr2Iac2f0VJSy8nBrXxxdJL9Xu5O3vZmOMtXITFWpAu+1RB4IWzr
xwJaaafZb8T2IyQlj46MRbo2iwEUUojTFEATWNuCLSHZKTTUDMg3BT+t8Kt9LEtDh6L9FB/7OEXSMqid6LK606Eh8EhnAxr0Fjax
ijaaSNNCimbqWUWzqiVGEjuIEyEhBhYapYvpSR4lXY4FNkqiKBF0kMMjQ55K6l6RvnhmhKFmbzn42yr8Uh/JhLnlRqHCdyA+UkiY
4Ds+hgOGE6VffABr21YzW8/g8tKr2JxbEjy05jErplg0Nnb0davbv1bjuTqYwv/nl+7aq82fnW9WXlR++z6Tmh989vTUgYedZV6t
P45969y45Rrz3rw75dX8B/2v206KvZd57RpLrD+4+j9v3P+RAiyAaK2lLNH/vorM9j0vHXd5tv+BUWvmiQG/+Ctk3OOljDq2AhcX
qTZYfW6GJ756gleMO2kPWsgLCnRacrhaM770DK7tcbMWB/+SFedGPsi8Dsqb3ccedP+6zIopXekf5njG6ylM+Ts9N0VPA28sE8xR
U4rbTGVdtFfxH378uW3qHWfNOqL/mSsvdA7vfaBhNeajtMaXnNRm19MmjZyVvJIRvQdSuLtLQQPIjgHEQIlBsMqn5SmfdYscWtIW
blrR2NZKq91I2waHNy7YyIVv78arbz4u+x58IF+3vK67r+tuvLD9h7lj5u098vvGdz7YpWDccbPavlj/P1SEwznFoPtKX153wyva
NcdcUfV09txzT7S+nLKU1xc9JV/wJAftfwTv3vcuy5+r4/5XnqWl3aUkVkZNNo7oBAftvRM739ybdJOPygh2cYBEQkKU74BDQN5I
SG/J0EQTGo1HDu0obYm2V7KgbqfkPg/Xty2R8r8rxuyvXVOViPKOnjr8ci8XGXXm8OtzvfoVGzPuT7OMn+i5Q5zbbruVdIODtCsK
hweI2WWAIAqtBSWC7/iU7Whw7vUT2VJXQ0Ukj0uuv4Kvv3yXcsvCayQ8XHi/WRdigpihPjbT7lN6UAHX1B7LhTc/ytqmXWVQ23Dj
mh1v9q+dd9G521fs8drcuqmzSvnYoh6mTVuqx/e+olvEij+yd/yA8bcX3uvN+2a13dgswWCrR1DSdoJuVo2qI9oic4K59BtgsaR+
AwWLu1OMxg98tAkqLhABiQM5hR3R5Odr4m0msd4OAw+2+eTnqZx/69l0i3ajIVdHm9tKq96CRT4GMUBhYmETI0IBJlFccuRoJ4vP
KtagcbGNPNbpjSg8IMANwNMOAR4KhYGNYGJga4MoSIDnZVnWMo8PZzzHmLwDOTz/JPYeszeD9ilinxknsWzBEVTXVjO3aSlTeYs5
iS+Z9cIn/LToe6558CI67u/gpssncOdej7C4aR7NuWqyQS40VFXCmvXVOC5Y/TRBLHSiwtAUDdfk7y04S8FIg90/IDU1wqJgMVMz
b3G0uph97XFsm7c92+Zvg6PSurgkTvngJKYR0Z7nS2eqk/b2Du26Gi/l0pFuJ+O2kdbtmFh08wdgmDYSD/ATDr72tW62xExHUL5Q
n6mjyW2mQ7fRrlrZsXwkazenaMy1M1qOZ3HTN7xYdB0TE6+x8cY0235agBsEKCPMWFaEevrfOhghWggaB1NCuVx+xGJ903JWb1wP
eMQkQu0Kn45qyDsC/Ej4XlVdNs6B0lhRg2C4ImjTqHzwGoRFF3dQdrCNUWHzYfo7flYf0hRbja9zGL6Fr10C5eOQxhcXH5d2vQWQ
8HmLQSA+a7RGBRYWMR3TNgYxXHHxcAjEIxAPTwKOyL+Yw7yd8LWHoQTiglZCkNJIEZglXRY5QZjclciHyr7l9KwZzEpnLbPTM/TS
1nmSlUY9JLLL9c+3X94ME8wpjPf5v3N5gPFI/fV1ltiH948MvvDZ3N03zEvPLH88/npuavNs487IC+r28ged6dU/HJY26vbwPO8H
jf6bXfA/UIDHGoZM97rl9b3aThceefGAG509rqqwFl0GU2vnyW63xdnz/N44OMjHNisubOaG9Zfxofkypm9h64Tu0DnGRPZhQsVt
DGS498DKO8xn0g9EcnQs62ZV3V2z7ZbXZa64IrLVosibwn/9LDSNiR5o8QJRXze+Ofns0tt+eLfj6ccfa7n+qK9bPnaf3u5VOSJ2
iPy06nU9JLqDrHNX0ZFfq8sjCbLlWTG7WVCkkZToIONBFcQOU0TbFZt+7OCXpmUEQTNRJWAF1FVv4Irj53Hr8+fywuMvctQZh8jL
zXfryAbLfHynt7Jn/nzYzvM6vvtmXOWx47+omTyP/1acVUg8O27IhEFvrLttEm50pyv6PJm76KrjramPrePDpe/wBY9y2tHn8PzE
p2TGC6u5/snbdKuulWQ0QUMmnyHJXfjTH3dmz1P6097kYrQqyncLZ6PSpf/TAoFW2D2htKSQMspROoZJHjGVxCQiPtmWfeOn1f7Y
9q6ewhT9j0Hr4w2Y4u1SfsDOCxp/vvqwgiu8E/9wsLHspUA6g7TMT3zCe888jZ00yS1xSAzU6K2B8iokXYGgMz6F/W1e+/gL3pj6
Bt2T3bHtCJtaFnLTtQ/y9FvX0TbTwbQMgq3GDoqu1ythl5IxSNc47HXSLhw+dxYzP/qOHVYMl/FnnRLMrvnZ+rBx0uPvH75s7yM/
HJoZ1nCo0nqyOyx/dG98XVfr1r1+0Ia9B7bTUlVudS/tHvSPlBhVlEdKGRAZ7B095Cj5aukcludWcECf0/A9DyIK0fo3h6pkuIkR
AzNf0B0upXk2M19bzOUTr6Vfsh+rM4vo9DrZObEPTm5HhvhH0EMGkNBRTG0QJ0EppQQo2knTRpvuoJVmGsnRScZvI0MrDikcOvEl
Jb7k8PFIkE8e3XSSUmIUkK/LiOtiYiT0FhbJFmMVX3Q8yfSOzxj23q5sO28ofxh9OLueto+M7T2Yo9cMZv6Hh+qZMxbwLZN54acn
mbb3Z7z+xpt0Xhfo1+/4hDv6v8xZ6w6iPWjGI4urHS0SiJ8DlGCUhqxaAF8LRqUQKwdnk4ZKyH3vhQVNC1PdKRxrnsldbSdBp02+
WSBurautpTGKpI9ExELwddbPosREtEeb10BOpwhwiUgMgySdtGlfOnFJEeAjQeiPbBMNPaW1AWg8cZjaWcZZo65l7w0jWdpQQ5XZ
h8823MXSyE+MvepkAuVBJjRRCYNyu7pgNOJCENNUDjboZ/Smxe+kxCylPmKhcXVtZr3UUk8HzbT6TaTSkIegi0JJULC1jouEGGmB
oLob0BLgJDzeko/o8elwquKV7CTjsNPdmaXf12v5mWY245NDEeCSReOToJBCehEhicIGFBnacMkiaFxcOmgmw0YMYjpCgUQpwsRg
VNUA3A5bum+jMJ0uQpbZlecQBYl0qRq26rJ8QSUCug+vIP+nArJotuhqPSAy3PzcfaupMFY0jwwCN/v/j9nO/zAk7WqHNbkVj+4d
OfLT73Lv33NA5/ZHT7I+yg7LbK/Wma6+rvQu65KGEx4/I/+SvZ5vf7D1b7GizX9kk9wmb48DVqeW3XJG7GZv/B07GuvvRT5bsJAB
l+Y49PpR5DIu+jGb7+9czBXtp7PI/JmIl0eOTqqMQdySuI2DY0cGkzsmc1LL0ZF61rfFzeSDVxbc/cC9TVd3yFzhd8Xpv/kARIff
Z6z5TOMNNXqyHt/95IG3LHdmXHrUgn3N8b1P5eryu+Sz9im6zauRQqMUCsCoMhBPwNNaB102gBqi2wnDKuJUjYnSfUZ/Pv2mgcWt
P+Mb9aTtRto2dnL6KSt44qk7eeG2Nzn3+tN5re5pIlJkPbjDpOxFc0/q91PjVx8f1v2MAz/a8vzCf6wIh8/h5KFX7/DhhkffSuf8
gTf0npQ5/4rD7ZlP1TFr6QK+5gnOPf4yHj73fvnmpcXc/NB9dBjVuCpLPFPJCXufzMln70VxH4UrHrHuQrxPCG+J/EZKFiMswlgB
RVYBFfEqyNoUMZxesQHEs/ngK2k3svofZ3dPUDCRX/bXiTHTCh7ZNjgicfU557iZxSi2ROQD/TKX33oaQ0YNIrfRIVaqQrVdF0lE
tv5LoJECqNuQ4ra776G7Xczm9BqCTs3OBXvT/l2CnyavY7fj++Ku85CoQqtwUxMlv4XleBrtGHi2zw13nMXZi19h/boGdmztqa44
41pn0W2rR178wxFXWWLdCEvtY4+dopZ1zJolomYtzsxi2Phhdq/pvSrq0rV91uqFg1YG8/s2pbYcMsLfc0SioUJPb54mu3cfARWg
DVDu75anhNmzGIKRZ+BHfKLdLNY82cyLV3xFXkKxJP0zyo/TV3ajOaPYJTiQwexOb1VFadQiGdUUlgv5haZOpTX1DXGa2vJozRXR
QSkpP0uGDGnaaGIz7TSR0506RwaFSD5llFJJARXkU0yl6seA/sVU9YL2zQP17C2rJNlZwkpmsEqmsXTDdN7b8AZD3tqR3Xvvz5gx
e+qxx+/MqINGccoPo3hjySHcV3Mpo47YjhuuvlbKTqpCTenG6dHrafWaiEhUu4FLLGnqWDyU84qSX5keggodm1SAUSn4EZ/CSluX
xPMgrfHFoMavpzVoF8f1qKdW05X6BfP/AZHB7+FHCStd158rZRIELuvq1rNrv8XsFDmVaXoq36eeoVdlFWP335XYcAjqQFKyFS+k
y+o8LEaB4EU84hUmvYoryescRs9If+pj3wJITjp0q9RKU1BLzmzDafpt8CdWV0/SJd8THWYhg4cWsHoKKxbOpNOoJciNIhdEGGXs
piuCItbJzgRmGsdoJ6Pq0XjYJCnRfSnUlSSMfAqiCWKGRRaHdqeDbNah00nRFFSzSi/QrTpFHhUabNm3X2+Wtq2gOF1I/pEKHfFQ
GOFSVEEYUNHFBdYioTZahbe1sF+SPIlSozuI6VJKI0XQqVV1sNDsuu//cMDB/wIkDWB8l3t/jUYfE5O8hw53d7voVvtR94jc+Xr7
guOzHznvjHi+7aEbTLEu87Rr/jXiqflf3CT1hQOeL3tm0yUP7+6faV1+26le6w+oSZ8voPCkJk5/YB/85oC2811eeesV7rKvpymo
Aw2eARf2uZHL9OV6eeOS4KDG3e0F/nRsiby5fWL3Wxd2/rT03qarf0ew4n8YfpjmwQQlx+4lttp8fY/I8OTm7NKL3q9+OXdF37vN
VfWrMEjQM7879Ao3ABHARIijiSgIQjE/+T6FPWH/faoYfezRfPrwED746VNSMpeCiMn6mnXcfvlzTHzgIh676Rm58o4r+aD2DT3Q
uN18dp93sud9e2LlN7VTphzV/cx939vy3Ob/Ghw91oQp3h/6/XnUhxuf/yCXlqobB72SuWTC4dbCezr5aeHPvMIlXHDR+XL9H25k
2pvrePSZ9zEMizyvv+wS3Z0zzjic4ZflQUGApz0MWzBUl+lDsHXb+TXZu8v9I6C0IEZxeRJnfYbebEdlrCdWWwQjsPz9o+ODB/8b
0LMpprf/zF5XFzmDdrlo/2ucwWOLjI2fKpkefEfJfq2ceeG1uDkXq8gIRwJq68ajQydnAdf0MeM2E0++G6ezkRazliCAAqOUhe2z
2CF+GDV3x0kfmiM61IRMWACRUHUc7hxdrqoB+GZArGeMGx48nLuOWkH7hipG/rFK/XncVe6NX5x/6Z79x3367dK3Z8RiRRZM0FpP
VbBXsHTKLQ7oTcAmhfrBUjYFdkVHmXTf3konnWbZZKzISyF7XIRu8gk6BUyNGKID1bX1GBrf9rFKDPn8wUV8ff1irOJ2lrfNp9Qf
Qjc1mJJgKKPLtmH/4SPoXjEIKwaxRBisYI9GY4YGHgPrLNIbY2QaCkMLRh98L1xhOS9LOp0l05Il05YRz/NJ5scoSOQRsaOYpgme
ouRwKNwZafgyYPozmzDcFD3pgcgI5uZqyOkUC/hBL9jwA49vEAa8viOj++zJ2B12Yv9j9+SQtml8sOwDXnj4IcbsvQOrRtrsM/sQ
5lvzyBoZHE+oHBClahi4OQ+Vswgc6UIG/FC8C4gveIGmfBQyZGQJ/GDpbeUYekT7IlkPU5sYGJgS3+ofp5UYEtIDgjAvObSu6Goi
FQYmhlhorfG1i6tDJEAROmoEXenDQRAQBK70zOvHwxfdT+Hn+/HxmgX8zIdU9C/hyWtfZudxA3DaXYJ69WvHq7tKOEZ4oA2yATE3
Gur29/aoeztHeWlvxDIBtG0b5NIdup16sS2PpIQ659+u4Ffr2q0L1EBoy/j8uGkTraxns7+CuGmyX5/9dDaYI32dhO6nBhAvjGEV
gmt3YvsGhrawVQIjYhGptMjrY2JYAU7Gx+0oxm1Cd9ZkxWkZoDs6RrKlswZfhFyQZl7TV8xt28IOxh6oIeANCH+1QAfoQCP6LyuO
1qADjU9AvIdBeWEpv7Ssp5TedItHtNUUi9R61TH+OS4/3HYEE+viSrPH6hudP98xz5iRfCj1fOq+koecPTunnRcR/V6T1/SDDn3A
vP9GAZ6oTLG8l2uuuKs8t82Qm465JldWinnf5cuwD6zj8lf3xV0Ma89o5fbZNzLJegrthG+UA7ofyc397wqiDcXBJasvtD9wX0FE
ze1nD71pg7vq0186f9z6u/w/KLx/8RqCkZxtzPGnyc6lQ16tzq4+s581MIKjdDl9xZMMUcOGQhAk9IPYWois8C2vBSQR7gdBuU/e
tnDcXsMYeusgvntmHZtzGwlUmtTqHF/dt4rjnzqS/Io8zrzoLHlqy71cljfRfOawydmzPzxy0Mc1r7w9rvSoo75ofK+m6yf8Z1N7
E6Z5BxSd1vPT6hcmG9mSqjuHvZ499a49rMX35vhywY+8aF7IrY9fJecdcz4/XtPA589uYiC74TCafQYN5qCzexI9AdyEh4oolKFQ
Es6npOuMpn+3cERCJyWNDqU+toeHQ0wSRCIxbGUjaK+X/Q8jFQZM8wYV7rL76o7ll58ZmeAccvtgZTQrWd5az7fJ53jz8TvDGa2W
0Jg/1uWAFPwmIfaCACtqM+OZhaz7cS3RpNDWkabU6EaxqqTGX8PL7rWM9EbQcOPe9HjRBTOMkZOu1yxhMxxubwKGKFzXY/ARlexz
QR2fvbSCYT8Pk1Ou3DuYOuvYxAebHn5k6enBPkNfkBRMAKb6vxfzw1TVk/Xm+j+sd+Udy3ViOfpGB+BpTWeiGWsX6Fys8WtCJyll
ScicFUGUxiPAEkN/+eE82Wz9zFeZN4n6PdlWDqci6MP5I4/Q36lJXDXvcX+AjJaM5xGVPCJBHiWxMp0nZRLmFmRIuylyQRpXcrgq
havaEXKawCGlXTwdYAQGSptiGJY2McJUKq0xdZTo1DhpaZeSPnlctNPZctjg3dUH6z7TG2Kl8qfyP+G0ZFhdvZ5NzSupS1ezifVM
Wv8gk9aHj3ho0RD27n4w52x7KVvWb0GiHn5hmm3cPkz2XpeAnLZKNBRAbk2A5QRgyq/vxa3PRxyQMoXTBma9SULKpVwPotgqJJcO
0xk8EEebWv8lgCZ/pZP5O3wZwvlnf3Mb9hwzVl9xzrnS64e+fLB+PZM4j0F7FvDKY5MpbaqkeaqLVgq6CH34oV3jVhtQy1SUDDX5
4s35bPi2iV8659J7wAEM3cngp7WFWhDiSZt0ez0SeNoqCSS/byhZU5nQenOrlbf+C2cAwYwItZ011AUbyIpBYAd6tvqQh9c/ovMo
CwiUtjclMTCJKosYSQK80DlODFSoIgftktKd+OLrQGlENA4ZNB4JEqpJ18hBhUfqlFcgCQnon+gT7t5pIdA+2tt6g8PDsRb51WpH
vHANR/tA97K+OC3fkSWQsnhZUEBxpNFfm+Sf5woA8bRr1ntbHt3R2HXJe5lJz6xxl/V/M/NF5hbz4diluRPuXDlA7ztotXj/EcTy
9xZgQ1Bet/weZ7a05U67cuB9zu5HlxvPn7Ve2sYs487PDiXzrsHUsxZyf/N1TDM+ARcGFA/l7vKH9e6yv3/fwoesR1pvMXOqpaln
pO9jNxRPueecmlHp3xGs/gfg5v/8mkulL4I+u9cOW35ueqex2OvWK6+tyqtgALVqKYlIASQgcAMkK1qU/M4yoouwQ7ioFOBnBPJ9
tn1U0ee4gay4rz+bvk/Tlm5jw/dppl7XxGFP7MvT9z/OcRf+gbuX+3KX+bj54lHvZk9/f/wuXzV/8MmuJQecMKPpy+X/SSdsAt7u
pScP+rbj1Tdjucq+E7d7I3faFbtbs29P6+9nzZAXrIt56NU7+ONxf+TH01uZ+mIrZXSjrCKfvc4so/8RBsEwD19UmDTT1fEGEvxO
jqF/1fSJ6to1uroPMwalBflofAwEI64IlIdD2h9e9g/BRQL4V4+cXPDI4jMe3ts7PX7uVcc6BcOQBed5PLr5Xi5/bjx9BvXCzboY
Sv029/1dmUOD2ELL5k5evONDVLyNFell9JKdiPkJuksVddRT4Q7j4cyN7LjwU0o+j5M8RELnHqUQHXSd3rsg9xCLxxCFHwSccMcI
Jn4zg+9ebGLPwhJ14+UX51bcuGDUnu9W3RtRsXNzwUSDXz1qt/4Tv5RRYr1t+0gQZGlDK0VvdqQoukFjIdn2AKdWaSPBr/C/dC0D
LwtWFqqKujGl/Uf65Q2jKrkTRelB3DjueFb4c/X1X90mHg32DL77yzubUkQpwSL2q6tYgIuPh0sWn9R/gvD93h/ld5+3BD5Z8g6X
9Lw+N7J4V2NN43peW/0e5aqU3XqMYf+dD6U0ViJN9XXMa53Bss5fWN6xiI0tm3hi8UOARwGV9LH6M9OcyvlFl3NE4b48vOEbYtEo
2NCxVqFbNFFbYfsG6ayLSqiQ0JSDqDZo3pRj9YpGtOHQ5m/Gs3x6FfbElGhXuLwQtxLEjIQ4OAQEWNpElCJixInYMbEMO4RwA619
PxBQ2lAmgdLYKkKxVUJVfi/69e5F997d6FcwjL69CljxpceNb73JF8HjHH/J7jz44CN0vGey5VMXKRaMAg1Gl6ubH25qTrtPXmGU
6A4+99//Gl++9h2rjZm0eC0c1L8H/kENlMxJoGdo7AIDtyFDxLMIynzMvSHXAbR2oRogW/3jwnGR4EfCbOceRcUoIEYR9alGvlv1
NQ7Nuo1OS//OyVYHf1FDEDFCmjYaf2u+o/63bxEhQgE5so6RjcgQa0c69Rbpl6iC+QHeSrCOEYIsiA7n37qrEP+qE+7y6baqoE/v
frDSJ4NHRWVJULyy3Kp2lpfJb9j/P8OlgcDHt+b5M749pfjSsa+0PHj33untTnzReNsbr87YbdBq+aOB8ZLPv09MMv/OI6A/tuIP
h0xv+vzx0+K36VOu2M1494ZNsqpiHrd+fRA1j7s8c8nrPOPdSAObKTSL9RVDbuJPsfOD6Stm6bEdu9jLmV2XZyVePCRx+lPvtb64
4ZyaUVvhZm8KU+R/o/h2dcEaYPuCw7MwIVdu96Tf4L46MrNEMAKKS0vDuVNtKGKny35Ougg6WvgVrtRbHUl9wdOa/BEeO70i9Pk+
zrpXouivG5j1fg1CwJFPHMSrkVfl1PPO0I8sflBuiN5gTTrxk9xpb4zfYX7bT9/tV37cSd80TP5G6/+QNWcC3v7d/7TLj41vTYnm
qnreNeoN59QbxpiL74BvZv8or8Uu4Ym37pEjDzmCqUc189P77RSSz+CD8xh9U5y8HgEeHtKkfvUKFvVbsdH/tjEQHfoNE74+w1Sk
IxDz86lQvSkLeqMKBFPZKCyh8x9aMEo/rVXvKwY+2zM3Ysc/j74qN+LKPLPpvkAefv0RBh0B4884klyng+WYXauyKx1IhZAiIgSi
sfJMfrx+DX5DiqneJ3Tzh1BOf4aX9GdQQS+a1vkcLmcztfVl7u5zOa8vehZnrINp2WGiUEDov4yAsdXdpwtCNMBMGpx891BeOGoV
5S8UyNAri80rR9/oXDbr5HO2795/4ezNPz2h2cv8XRf8l8UsCKxS6UWQsenLCNJWA1hop0VI1YUMaN/VoaexDqFXLyWYeYHYZgGF
lOrFnd/LsIKx+tKRh/BLx1x93fQblakc3d3a/m5DBRuyQYcIyhPEx0BLIJ7ne76BgWAaSplG+GRVqFgKsuJpxwBtdLE1u568KKVM
Q9CGQomHF3g4WgG2GTU35uYee+mmk3Y4of5S9+oBN/g1vdept9a9rp5c8iRWQ47T9z6Zo/c8ikNH7gKDoGZjmubNHXhpl05aqW7a
wMKV81i/bh1vt76gx0YPYrvW3VFaCQLptULQbPPjwjVUDbXZZkRPss2gXA/TB78QXK3xRfDNFNF4A92Tu3BXxduYsTwihoVoyC+M
ErOjuI6H9jSGYxCJRCkoz8fID+0WCUBZSKwAlIl0rEYHDsRLoKgPmHsC82De6y5fzllIY307vxR8pdcPfk/OOu4ULjrpWlbf5NPy
g6tVsRKdBWnQITvf1CgDMo0+5X1jNJdt4IEbPmDZnIUstr9AvGJ253w2rUnpF66Yz4F/2pWqgh7knDTF9KCCAM/0oHtYfP3mALF/
awB+LZASFjyjj0l+shABorpYD05ui6k7g/WpdZSYhV9G7cjUDqc+pdEOhqVNrK5wVgPla4WhwA8IcDUgPv5Wdw1RSquEXRBPZ50D
Mp6/S8YX1TPRw9hIhA31jrZe1tJzfJSgQ/DbumCkrTyN8JDzG8LWBLFeMKhPf4opJ3Dyce2Yroh0Z0nWGPDrF/1zdcIBYL7S/OAW
S+yT6oIt08cH+91yoX1l6SB/uwub/M3vNNHU+W+7YPPvIcdc3u+J8ierr396J+8I+6brz3cXvNFmzE3N5frZB7Ly+jZufeB+3uE+
AI7q9gd9R8mjur1R/JPrTol8y5tg8tIOyV1v/qV11oZ3W1/4j+a8/+tMt5/kR8vAtvskh2IU2xKhhCKzmHikELcTgmZBIjrcjJXu
6n6ly2VdkC7pBLrrhKfBaQhnisXDNfk3KcpGVTD/dYtF76ewEm0cO+kPJEsTXHnMPbw/5weOsPY1nz9+snPGG+O7TW/55P3RpQcc
M6Phiy9+1wkLjDVEvvcO73v6wVNr33lZ5UpLH9h5Su6PF+xsLn8Mvpw7myn5E+SJZ+/n8F0OYtaxGea879GjIp9dLy+k3x8haPXJ
btKhXMcIwOjyuTW6YCHdhWdJaJu3FTBCha/d9wRDG9TWp1ixoolyqy89cn3J7xMjf1FFOCvrR8Dc/xqRTHjbH33TAX9OZfzxZ+Td
kdvnnirTmwlvvPw5vxR/zGePvk7gBqgaFcJ5Soedugp+tU4PPI3RD9re8bE+s/neepts2iePKvbvNo6fjTeZ2/IBlwx5DlnVkz/l
X8/FC8YyadSHnFJ/OE6pg9SGxBHddQ9Efke+7Aq3z+V7DDy0hO3O2SjTnluhh9UP56gbR6qZR54fvNV438RLtnv8gwcXnFcNUxSM
D/6DY6xVYvRjSPcCKmpKWRN2BNK5Ft26OkCSoTWgDrpSdkxEZ4So49O3ohellLCOBDPavubb2jE8uGmirpN5QYKi6zfmFtyj/8Kh
9P/hcnLgysHPPf7y+luufdO978I5S79PXlJ5szOh+y3+lvIm47kNz3PNW/fpq9+6XHYvH80x+xzD7rseQL7Rk/zNMeSXHvTUgxlc
fAidxW36e+8zZlcvojIyQFprXYJayK4Q3EafnmYVb3/0Ecv1fA4+cndKC4thNuSqfTa2NLJJ1zM6/wDWNWzgoY57ya7P/eoiHuCJ
T1b7aDw0CoVFhBhFJCghSSF5JLCJIGgsLCyKSFBOnDgJNAk7irZtcp0mi1iI7tvCZW8czVmj/kwyfYWWhjz5+pQUnb+YuqifAWmt
scJmUlmCH4DbAYMPjjHH/JLnr3qLluY6FpvTKHCHsq3+A92TQ/TlJfsSzXToq544WTrTOZJeET39XWgnQsfatK57ByneT8i2dBXg
35wQdFiItZAS7GSAb5t4OBRTQvf83n6Nt9CKphKLHx392UnHTR/c8Ot+5f0Voc1/iIWE76tmt4Zd8o+avbht9jvNXku0LFqky8WR
H/31zEtt5sru+6NFk2shTOLqeh9qP2RuS5hihfYg1gPyk2UkJEHcL0fsuO5WUo63JdNbZOti/Ke7PMBwtYOB+WSK1KyH3QcejUt0
t3bajwFe/Lek279RgLWAKBPLe7z6ipursjt2f+iM+9y2tY7xzexfOOuLMXx+9QwmvnkLy4zvGWpvyz0979cj3d28O1bdYz/l3G9q
M7NyQGTwLevTa1+b3zpj68/z/9/Oef8+quOC1neSBmZBWUGVTuc0CpPieBE9RnYnl3Fwqg2MZNebQIWHFtkqWVEaQ0Hg+2CFZC3d
lciifU1mU5gk0m2MYt9hpaz9JMnqdxwWXtfBwXccKLykmHDKs7gzAj0+fYDx2H6v5M7+Ynx8ZvNXbw5Ibn/k6s5fpoUz4ZHANHeP
buOP+q7m7UleJi/60Ngpzgkn72yueAi+WT5XXi+4jJc+voc9++7B9BNTLPpa2HGfQkZeahLppknPD435ta1/d3gA6Sq+irCbDHQI
Z2n9u61cdYFWrsZwwEt5ZMwUuWyOkrhFyVCDxBdJ0CJz14ZIyd/b+cKU4IRet1W+X3v3taODy/wzLhirrHkeM6Y08uS6h7jnraup
6FlBeoGLtBhoU4f4sPwlv10VQcfaHB3XJ3jXe4bVbasplYFsY+5KebHmm+UfowPYUrWA88/dkZU/ljDulwu4/INz2e/03akozyeb
1dAYSsm08GsXitborjg/8QXJeBx19TBu/uY7mT+9G2NOK+GiCX/y5t7wfemLa2653BDzMl8/LjA+PL5ys3zM1ttuRTE1/S+Agtsc
XFcLLrTOFamdZ2ijVKMlQLoMQcyoIJ1Kx0Z4MnJoFTFMLPJ0b7UfBdkyX0mbbZrGV2mv5R4d2nh04eZjgWl/K/by710jfw1uk3tX
nNlhiHHd/pVHfzSz9dtLL6495Ij7qofbp+Vfn7tmwFXGH3c8UZ6b9aaeXv8el7x5uew5cywnD7iYvv0HUdWzJ00/wndfrGIxi1jB
dCKYbGa97l/WE7cTSa0RsvUaVWBzWPQoHrzjHmq+qWHvvcdStU8R/fpUMCRbysN7XYRSDi25JgxTcIMMtQ21bN5UTabFI7VRxOmA
gv4eRqGP326gOg1yTS6qGBKlMexsAmIBWimikqQwUiaxWBxteVhxCysS1dISY5e1e9J/eHfqvRreuGEpJdNHUJxSujltEI9r6la5
WBERwxQMO/TyNgyTQWco3m5+nM+f/py0ZFhozKe/vx8D9QHsutMQfcbwvVk+Y2nwx9pj1LrcMilngI6qEhI6D1hA+4YUrT+ji0YL
6U2Cimm0iw783/ZpjcbrEPIqoE3SaDQJ8ombScQQCXAaxu80sPPKH3tFEySCpSz9L42LdmRHaaTR2KA3uL0SvTct6vi+vc6riQex
Dj3C6skKb6184D7Dn4ftTl5+hFwzeOmuNC8/DOPSXQVYGRBkhUgn9BhSimX6WG4beUmktLQbxharG+Lha/4pK/BWqdJgBtnLWT6v
TPc+tJoVTyBMSOrkR510Nv6+C/4bBXgvQxCvJK/y0EyHd+Y9uz/s9uhToT68ZxmHPdmPp295inu+moiy4ObofZyWuND/tPYT85z2
7SPValVDj2SPR8/v8fgT1y0/tamr6sP/nPvTf7cA057dUm6SyKsoLPP9Nh+XDpLJPF02yJbOlhyZBlCdWwtSeK7eWnyVERawvL4m
2Qbw2n1EhY5Gv7JGRKBeo+IefQ+3KRlusPlDlw2Ppjn4wgOQiMt5x98o1QuW66MzR5vP7fO2d/53xxWsTi94b2jeLict65j1Kcxl
TMURe81t/uxlP5eMPjHmI+/gY7Y3ZjyYYfaqWfJJ4YO89u0j7Nh9R344qIOVP/uMuShB7wMsnDaPzCaNRPWv/q0i+ld0x8sFxOM2
XqVHtjlDJBULi5z8rhCHMkt0V+SdqQzsuIlu8ynsJsS7gxWEVhaLGtcbfweJ7NdnoDCDD+vvvCHi9Ot26bHnO4bjGfOeNHl87VMc
cvzuHHLsAXQsdsjNMyAZzme3qoTCMi/4jqZoL+H1i+bTsWITL1lPMUD2pFLvxKEDx/HglvORwMYyhceX3ssRR+wF8UpGyDimN0zm
2ruu5rlznsWryuK0maFvrgrCTtQLoXftdbGtcxqrESqOiXDykyN45tBF9LxlLwYel1DnfXmhd/H3M84qKeo9paHl+xm/h6KTJLfe
dNtXDuwMqns7fqPgLYTmVS61a9ISrTURMbQyBdtSRKKic00+6apO3X3/qLhkAJsRiXH0LqxCGhQRM9La6rSIiPwuonLaX2uB5W9I
Kv6rcJ/yta++qJ4yI2JEZwwsGHrgivaFN93UfvSur83bx79iwO3+4+MvV5vXXSqTv/uWL9dP4Z71D1P0dQG9qwYzaMeejN5lT8Y0
HsiH0/KZ3zEfkwSFw6OYA6B2uY+f02RqPWw3wpGF5/LykhvwFgofPfA2Aw4u5fw//Zm9hozB3QCOgFkKRhLMcmAbIAOp5SEildwe
qOC3PLIcEO/6+FtXGtwVSM1mWLeinU8fW8OC1rW6lCqGDEjSeyehb8wmXQf1y3zSrR4SCfBbFD23sak8q40nvryHGR9Oo8NqYkO2
jeHBsfRmV07bdW92reqmH3jn2eDejhvt/GgsWxkblHMyOmlEYiTMOOKa6EQKlYTMJmhbpVEFEvo4+8GvYxKUItcGRt+A7r2KiakY
dmATJ1/HzSQu6dgva4mUUpo6lEP9rmzyv/s6lENlKUuDndmZHfscXfN5w6S6Je6cHo3JGmdE8TDqWhtobN9EY34ThWW9yDQ5ZBrD
xjxw+HVcF+YKg58Ca5NDxcACIlGTTm8dxYUj6VvVk8SC0sqUbOT/qATp776WstSDkdZG5rUoLX+MqsSViDqCgOe3jnX/VgE2YJp3
RM+z+39Q89KTlxc+aow7dkd/6ks1MuhGj0sfOo0f53/L4Ynx3BE86K/JVcsRHYdaC/iqw7ajL40vOvPBd+peWHfd8lO32kf+nQ5W
/3s12NOZKouYkSxPOO1rDeWRobK8u8RKoGaRJlMbYMTA79raZCsMbWgkgHipxZdzfmJwj/6UehU0rs1iJ02QMNIMQ4ddZosipQPM
mNDraJv0LwE1L2U46LRDeHaKzcknnkfNynVcbl1uvLXfR+7J3x1TuCr9y/vDCnb7c1W898YZTR9OSjmSfGDky+5eO21rvH/XOubU
fMWKvh/x0pcPMDAykO/3TpONKw56IQaOonaOF8aqRTWkNFpplJJQl2dArsOlrFuMTdYG7n7gHq455iaSdXG01WVRvbX4dhVtHQCR
gERxjMruBRg1imRfk2hvCDwXQxnSXGkIG/5e1jN+n+SIo9d3Lj/r2hHXuyNGlhuf3FrNYv9nGges4IWHXyKzyqPxHYU2gtAdSoFW
umvPUbgpn5JhNp+9M5fpHy1mfvQJotkCqhjJIRVHMM15hQVt84iqAnztUK+Xccnz13DdoMd1SqfZ1/qzvPf59Ry9eB47H7490dMC
mr4IY9z8rnzYwJOQdep3HapWKjzLZdjx3el/zGqeeGAupywbKYccvUfw3eI/JV9uuffhKwY/u++9K87shGMVDNPTCL3sdeBGXJ2G
dZBtVKiMwlkOa/yVrKKOEq9AS2AjWSM05jIVzdkWHW1sY8fGQ/HJktOaoVXd6VWah6yyCASxlK356+EWfy/rV/8DnbA/lrHmNH+a
Xtoy//OnR341476Vl921Lvfj6Rev/oO9cPMVwZ+OOca79MT9zIFfb8drdZPYpL5hRfUrTK7eQiwe5Q+jx8vp46/UOy4ZwONzq4kU
RyAPNtTUEgQaXzRZP0OeLmJkcDTvGXfTwypj9QdrOOn9ExgxaDfOGHwlvTfvyPrl7UgSEpWCZSoswyRaIsSqhMQ8TaJCEy8RrEpB
FWtEqV+Vpl6nxncFnYHUamhbqqldlKFhRUBzjUs7OUwU5dKPc4bvIEP2RUeGewRFAaYpmMqgfQuy6Wuh/idN6TFCxRHNPHXPW8xf
OIdNkbX4nYXsyun0tXbklG330b18Kzjzg7Plfe9Vuyoy4Ocz+k+88eVNt1ytM+xdmFfspsyIyqeEdNEK8gdDugGa12jMsiA8IAdd
9KauJZtrFex6j4peeRQYxSSCwtBARUrw8KxOb6OEh8G/+sz/6iFsIhP1BCbwMR8bk6eP6bw3am1J0T6q00zrqu2gam45TlOWmsbN
DDB7kW3RtKwDsboOsl0jYRGNMgW/QzCWa7qNg+LCIlqz1RLvgert96aAbt3G3bNTbMrlUzL8z/rl/28Ts4C5How3AqYE6aDj3l6M
KOpkkfxnELQC+GrkmoJDlm7z6oHeqT2uPfVsZ8VXrUbHtos58+kTSa/xmRT5WI90R/s3uzfab+knNcJr2yd2vmNh59ylU+qe+xVu
nstc9//WvRkrih/J+B09SxlBWb8i3blIicaje0UlRKFpsSbXGmDGuryClQr5SIbGsAQn55JXZvHNj18xcfblvHb/6yQL+lK9PEO8
xECp35isYTELQwJ0IFgJhbdZkVmQY//D9+e9N96QP5xyHA8v8fV5/Nl4ac/3vRN/OMhY2THnmc2ZZX7KcY1bdpjk/mHMAca7zy/h
8443xBu6lClTn6Ui1405p6QpOdigfKxF49Tw4CCxoCvWritwQIUpThIBN+fSfUicee0z+NPVx6P9gInH3UPTWgczCYGWXzNVdVd+
KgKBpSnvY1DaLYKPT7RKiFRCudmTfMq0WZj9T06sWsKiNMUf3+vaYVO23PfMYYVnGxecclzw8ZNr5Yf0F/oz9ZB8+tRk4nkRljyU
I73ZxEiE2snQJhKUGUp0xBBaaeOt22eRisxnWW4RVTKCfDOC5C1n0tpniKgkvg4IAo2lLKbWTWZ0r+1laM8/sGpTikAJD1TfxDXP
PM+Qg2xUfhFN8xwwwfc0gae7RguCIRrBp32KwipxOemSkRz75e2S+NxhXMcoddFRlzqzXvhhp+c33fS0/k6fInsLSxnf1ZFGQJOH
EZCZB6klCaIDDB34sDE6l1nMpMKsgMAM81OVS0baaaZWhlt9mLWmSmdIEyVODJ9CI0Y5PWhVdT50/i9sJP/xNY1pWxEt45y5+7cp
Uecd3O3MZ2e1fXr2o+lrTnhz0ot5p/U42x0/+Bh1eskR8tmKSr2FxdQZc8ilU0z69g35MPqunDjybH3kUTsxakxfnBZY7v5I1shh
6SQ+ObRois1yevjbUFRsMKxqG35e8iU1K1dz3qajOWTPY7jw2BvJzLRpWZ8lVmERL9BEYopIsWAWgVWmsCsEq1jCOaqAn9G49ZBt
0jRtzrJ+VpZ0DcTyAgoGFNBtsEVp4BD1i4laBtFCKN4OEgMRiZsoEyQWYFg+3RKakkOgZVZ4AH/v1mX8uPpzFuuFDOzck8Hsyy6l
u3DYgB1Y1rDU33/Nn+zVMjfXyx5867TRS+4Z+L2djUbyLu3BNhSZZdqUYirpwdKoI4W7Qf00dOsGjZULIOgaiYWOF1r7iNsqmKt8
BvdVJFQRpfSSykRC8ihGYZip+BbpKqZ/7bn+zYI8kYl6JCOxjYiO2Pmb88mnIlpG/h4QX5KntY5KQ3UtZKBjA7QsBasU8NGBhISq
rbaGfgeYSzQcDwMGDqJpS4poOVJldaOQysKfXvqpBNjc5Q3/zwpFd/3eU7YG76iNLGr5j6Qt/+7Ua0vEP3ntPrcOzOy46wPj78sF
EczP1r8jd0+7nCPaT+KqyA3+x+775rnBn4xO1TC3t937hs25LZ//0jl7a4ej/4/AzX9FU2WQyWV6DLJ6kmcX6qinMdH06dUPBDrW
Ctmcj2GBGArD/F1Bswyy7VAyEJrS1SzcOJOzzj+Tyy++hp23259NP+WIJMFXIdkg3MC7bAZ9IfChdbPgWAaVkRy7HroT706ewmnH
nysPLXmQwxpONu4d/Hpw2crjgyanXu7Y9QXvhCF/UG8+/wOvpx4mtnMrH378JsUNpax/MUeP820yrbDscR9UgIpodGYrWzg0rFCG
wtcaZSr67hnno0VTuPXBW8m4aXbpPw6nw6N9o4HK88Nl/Sspq0t2FSgyrT4DDzXoObiE7Mc5rBhYeVAYrUICm9Ws/k/u+s0CU9AT
tJ1/Z+Wjvf1tSu44eaIz6/Nqc9ba2Uzhern+4msYtfN2rH4gS91MC1UUQFtXlJsKfTFNS8ilPAYfEeHBRyeTblnPdOttouShsdim
qDfPbrkXTzxE0gRdrbyvQlL304vv4skdd6a+7mcK/HxmOTP4JJjM8mu255hbRtPyuiC+aN/0Q1JaaOAQGjMpgQzMu1mz54NJLrnp
QK688E6MH26XcYXDjOtG3+acO+PYP+44fr85hpgPrNVrrbGgZ4Q3IBpXMTpXQYsLtsTY1JilI1pLPavRuXY8P0BJGH7hqSwN1NI/
KGZ1aikZMtjEWNG4gh1iZXhY2BL9f1t+/4tzr0AH6uOaZ+YpUecOTuzw+Krcglvu3XzBkR9ufj24fuit3sWjDzC+WtOdz2tNTIRu
xmA9PztZnpx+PzErLpN3/FCn82CtNYcGfw1JqQLxERE2iU3K66QoWoFdYjMjM4tB0cEM9wcx/YvP2FxXw42XXM/O0WG0/eyRLBeM
vBCWtksVkSIwwkAj/HaN06rJ1Ws6aqC12qez2cTvSNBjjEnn6Lk88/aLurG9QSJGFOUaxFUEq9ZCFllI1iRqxKgwe9NbjaAwU0FR
UIJtmIw4LsHaqbU8s+pqVjibOcSfQH92ZLtuPdh/ux76+SWTvYmbL450qLqlg+wdz1mVnfdj32liaq1VPJ50AwxKeiXpmYowa14Z
vg4we4POItn1SmdT0uUF/Rv7GYRcG+jlgjUUTDOCnTPpsRMU1ucjyy1reXa9tRd7BdPCEcU/cviSDBnROgCMjQYGSQrRFQGGHSNB
OQ2zmyEB7W2apoVCXnc0pkZ10ShEh0zoIKMJskAS+o/qyZpv1+DnoPvwwqCcysLltVtKBbVZs7TLDFf+WYvw7++l5u/QARuC+IMr
d9x3Q/Wa817Y7n6326h889ZnHmDW5m94X38bdFo5jssdYi2Xea0Fdt4t31Wue3LvDX2z/KbK9P8P3wiBaYFIhCAtffrkb0thoZK8
BGRpYdghfXFsSC0y8PN/ZbUgRshs7LLnINdikdrZoXFNG0VGKZs7VvLQrQ9z7OlbGD/mNNZ+FJAVHy3SNavpki1tvfeesHKKULdY
Mfz2DKP3G8nTzz7OqaefxfP1dzA+c666afATelPFEo6tOFGmvDKbV/07JTk6zWfvf0Temjw6V3gkDzZY80ZA408Ko7RLxhdIOOsF
AtGYpuCmfZKFMSoOSnPPh3fx+btfkTJbSRp57GafiOFYpJcqKEWLBTrUz6O1QnwIsgqzMAAbCrrlh2b9pg+Ghec7ZOlgwH86dp+q
DDG9gffvcEPa6djnnj2ez2krYU79Zo7+nLtk51135PxDrmLLPR4rH7HQPTS6Sf/ubatRgcJr8akYGeGz5R/z/XdfI9FqmrOtQIzB
jOTT+ldYxSogRjG96Ut/oiSoDmqpYQUtXh13zL+afva2rO1UxMjnQ/sJdp1/Iu57Gxk39ETmPddBpDhEDsQUDBGNaBHRGAK5OsXs
G1xGnD6KbQaX8dnSJ3A/v4gTjhotf1h6qvdKy/03HNvrz5+9teHh5cMYZunwcGLHdIS6X6CdDgwHWVm3AaWi5FGlY1KA3yUQCvAw
xcDDJl+VExgqNLPHYkXnajY1bU8HWXw/w/8huWSX5StGoANZkZq7yBDzqLJo5TWr3ZkTT192lH1c/LzcYf0PU9eVnKm+XruYmdks
nvj0MgeJ77msmb2BAY0uteYa2nLVOMoJ32tiYqsobbRRKUXE7DhJCqn361niLmRYcjsK1ke4/fQXuObx09jnimHk1ugwk9fUaCfM
6HVrNX6rkG2CXIeirQ7a6iDdYRK4YKQ0tZOF8uaR+tB9Wrj2tstYsW45AN3oQRU9sUkQoLo2uUVE+Q4bs8sz2WHoL30ZMKQnrZ7B
yf4LjOg2nP3HVOIsbfLP+O4c3nWeiRSYhZ/9MXrpKW90PtAYjuggouKBlVA6QBEdZFNeDHkLkihtghmgW6Gxrh2jw0Q8HyQIdxSl
MMXSXjomuY0ekTIwE4JOdeqiUUjip6iOEE2ubF+Y9wR3121VtvwjV4yYDgiIGwXVLoFOtWnxXU3ggk0J1QsaaXDALNG0umlyNQ5W
YGGJCSgdEISkT1+kqd6Dtii9tyungZ9oXYjk948EJZTF3JRRESqS64V/retvWlEKoNeODaJ9p0XuuL7kdnOP40Y5Tzw2hf5NvfRx
9oP64czd5uv+C0St2BcHJY+48vOWDxftvaEv/LdCBf7XryA3O2sVjKrs2adkGPkDEWIddJqtVPWppHEhdGzRGG0BfhDg4qJDYFYk
CD1pNTbZdI7Cpu7kGwm25GpQ0RW8/uzbbBlXx6n7XsDaFxO0N6aJJsPNU9HVUXZtmIZoar8SOtYb7PSgw9gjdubVu1/hlAknMbn2
EU6sOY8DC07jlTe/5kP/Kel5oMnktz7F+iaKlAe0apef/+TjbokQ6Rag2/jVtlYIs3ExFLm0S7ehcdzRK7jo2RtZt2AdrdYWUn4L
MVXGtv2GETPitKxLI00ayzJCgYwv+EFI7daOtRVOwo7baAI6NvtQ05WUQcZY0rCpi2g3QeBm/vLUeowBU7z9K84a+1XDS9cdmDzd
2WPn/dTk57/Xc2WStJes0RNOmSSZ5TDt3izpdIZoxkJhhnaCotASpgYpz6Z1u5VMePwSqiLdmZ37kW52T0YV7ssRA4+iqXwHTsme
TVl+T3oU9WPodr2wuwurFzVQ27qBto4acn6a4uZyek7vybfNX7HEmUFl4TQWvPEZOz0yCOPjAWyqqSeuohgolBZBNIH4BNpHGQbN
XyWI9YpxwZGXcfHmc3iv7V7U96epM/v92f1h/idFU6ofvcUSe/xSvRSLCCCWpSDbCVk66WxqoLGljvygglL6kk8SUQamKB2gxcIk
Sg/6GIN1ZaInEfKIE6NUutOqUqRowdeZ/4udwdZRhOFrTzfl6u7au+zwaYs75tz9ZvahPd5Y9ByHlR7rnN/tIqOsIUGQWa77GFXy
lvuMbmtrY0P7Bm2rPClnkI5IHqIltJbUppjkETXz6VfVDx9FjAi7GEcyt/M7aiPVjCzci58vb2aHuCLvFAeFgfIMaDRC4lUDZB1w
NsCydwPS6YBcW4Cb0YhjYHgBBAFLn9VUzN+XD2+ayVufvciTkx+lhrW0mI0Y2iSnM/j/drvTJsXGMFY1tXNZdCcu7fUQvY1t2O+0
CO8895F/w9orrHWykn7xbZ6YdMiiK3abIhnADPkxYw0tGlPZWjCgFOw8MLFDIiABqYzDcuYQ1zYh2xPQKswvkgRQSCQX0FCXIBJE
0HgQRaKm5Wl04abmTaUQrJnAzf9g+Q0JhVoHJM2SmnqqnSUbV8pO6/rrzlQgBjE2N25haXUz0V1MGliHoh0jiGB2CYIDCQ/RYsAm
t5GDZ4+jzCknRSc1y3KUHRrR+YkCiWQKqlxp3Wr1pfkXvn5fgJUhht93upy/b/yEna+97Irc+nda1Z4No/2fol/YB7XtSbPU/TQk
se39a9Mr3v2s5YOthTf4Jyq+AIy5aExZjNLuw7bpo+0BSE63YxWZVBb2YNnsZpayloQjaF/jKS/0okW0iSU5z6eguIIIFfT3t2Gq
epZyoxdNuSZS9iy8z1M0NbZyxskX4b5YxqY1NeTFouCHIW/KMH5bswpapwsNBwj7PwFj+m3HO+d9xMkvn8Arax+Q5qV1+m3/MRl3
/K5MevBV0ncZJEcr5nzcxIy72hFsrKggdSahA64RqiC7DCTanBTbjKmkboevufOhG+lo6qDZ2kzaS9E3MYTqzo2U7pdFeudYwXId
cwQrm9BGaA8EgK1tDD+BromSrrGBgE7a2LwsRW5lAtd2CLSmMlmk/v3Mdyv0PFHrs7WVeLng9iK/t335gRNyP365Uq1rnS/z+IAJ
Z9/FyF7b6E8vqZFfOleTsH3IKkwi2MQxg9A5JE2WXU/ox13TL6ejpZn1VoahxkgGxnei1BvC0lXNFK0fQNStYED/gQzvE8MyQtbr
4MIK8hdUsHFROzWper5q+561biP9rG3R2mdxaialqoqnP76HG258kQfO+4U8I9RFe+KhCXQgPkoEI4iLZSZYNdnkTxN35tLx13Lp
q2fwSm2KUuMK85EeL7njN+97dNSyj2tzG95ybsyaMlGidcEWOnIOKZpY27KajtYOyulBJQ75JFHaCp+fFixlU4zQzetOd7MbtsQJ
tEu5WQYxj4y0IyLaC5yu0LC/ZVzw92xiW79efseM1n/ja//tzztW8WvHMi0AgglMkCl6iv1N/fsz9Kn6gG3fH3Pe2s7Fl33Q+GSP
rxvf8s4rukLOksu4puNM7YvH6k3r6DugliGRMbqzowMbC188QjtEiJDG8ovpXt6HQezAvOBzxlhH0su/nuW5+SxyF9OSdzO73/U8
Ywb3JbcEvvr8F2bFZpOinpTUU922gf22OYSxe53NZ/fWY0sGwSPQmkAcHEnjR1xZNienfzovYNwFR3LoLUdy/QM38lnrG0QNRaHZ
jZyXIQg0fWUklQynQgZSofvrmJUk/Z3HQYOHUT444Mr7rgmebn7M0qa/dGR0r+sWpH78YLcpon5rWrogHm2jtOUYmBha43SA4zv4
OovuCHSb2yJz+Ihyo4LA1ygdWkeaRLAlThaLmDaoXlUMGYM2Wlj5HlhxpZXoZAmVFSB6IuPVP9q8lXOs1kCFWbllo1rWubJ1aUHt
sj1pzrgoDLY01bA+sZmh+d3ZKHMIaEVJAg8HHx8tGhED07DY6Nawau4OVPbqhSbL+tXtbJMto7giH7U20jsU43cK/+KX+VvfpPxI
TG0XzfW88dlxjzuxefiLfvnZvN+aaMxpnb40Zscm2G7k7eWdC7d2y/LPVni7+kNdXV09opLtK4dv29ejD+LnpajoVkZhRUIvXT5P
fuZzClSCkI8ajiGUUohlkupwGNfzUKp6xuk9KEnbnE5KjEJ6GINYkZ1F1p6DOU9xW+PlnH7yBVRNG8xP362iu1kQeg4Hwa9m8C4u
vu3htVm0nhXj6Mf6MHh4H17449uc/drJvLL+Djn19PE88+AztD4GXhQmvbWAea/XEYuqMN3L9xEMYjpGJIhji0XW92ihjdHHVzE/
72XefPxl0kE7NcYaHDdH/+R2+EZAmk7M3i5SkWYl32IojSmRMHxdRbB1nBgJLCtOQ7qNg1oOJNvkkSVFZ3MGry7s5jRaJ+1Bf2Wj
DqHnHpMGXpXLeWPu3OUeJ8gqc+qi6cxmCvsdcACXnvVn5l3fJJ+s+lg70QbRro9WoMQkCBQF+Qnyo/kUFST4QU3ju+++oVe8F9nA
Jyc5fk5/SzRYQYk3mJgkSagifq7vSb9Z/emVKKc4HmdLWzPrc9VsVmuoVatokvW0ygYytFAaK6LM604iGWHGJ3OYs/N7DB3Vk4/m
vE0sYuH5ng4kwMfHEJOYimOJTV1HE+mnV3LHxAtYkb6Qu1+/j9drB8kTo2/i0vSt3NF80ZO7FR5Uzc1MZyJFnqRY66+QdcyWJAUk
zQKilNIdi6iO/FrTQoN3GweLmE5SqMoQW9Oeq6HEKMC3U7TqWnpZBcpSER2u45v98SwVgCkM+zfPYuumOx6YAv/u/0OIWoQIxoRQ
RiHDuFlPDBGNv/j8CdwsWz9/KlPVNKZpEN8QFRrxA8OGjbeXDh/uj5+C9zEfW/KyOMCD40rPfnNO+4cPNrm1xz7ccoc3seApEWIk
pIDldYvkwMgxDFA7UUs1Jqpr83YJ1XCO5EuMUquSbfO3Z07Hp3zpvMrNZa+zXdPBeIEwo2MSp208gcc/uZv+c0cwY9pKFqTXUa9X
sYFFNLCBJe9vpsetAznhnr155pp5rFbfY4km52dwJIUXZLFjltg6yS/3fc0Bx+7C43e9wJuvHsBN08+mya+hzOzDwGBn+klfEkaU
StPUWbWMTq9Tp2jX36bX8eX0z4xPmqdYZdHKp06vuvmau9ee08Zvnve/Jy12xYp4ro+DW++RWQ8ZHDxyNH3o4dZ5bOQXaXUKtHgm
hrKwdQyDKLYRpZlGkukIKW8MrblWmlnD6pUNEulhS0q3q7m1P1ygJ+hvZKKku4I8/+HOclj+ni0zWz9oW+H/WJxuPdVfrhaJRtOe
SVPd2sEQS1OTWE1bdiNmkAwPNTihk522MLTSddSxrm41O4wZSWFeXGoa2/CayigqjpBdm+1liiLQ/QKY+8/KhP6vFeCbxu5hTpz2
w33v9HkjL75EO8euOT3+Pq+34AaPPTzg1fsuXn1Ke9cOsVXD9M94U0RQtKcyAwfQy+i1TdwjhtHoNdJnaBU0wsyNP7KaH+nm9SLQ
ugtqCjC00G7UE5fe9NrhBCrGQ+aHWrxZmhbVQDcZhCUJUk4nc41pDNgwggfuv4WzrvkTu/Ydxfsv/EDE9BAJyEmGAJ9AeWg8JApL
corZZ8U568H92OPsYby1z/O89/Ukrr3pBnCgtrKaOx97iRXzqqmKF6F8E187+OKidAhDmZEI2YxPYUEBB164DdNXTOLbZ7+hXm2i
Wm9CBxpDoghRGlOrMMSi8wdN54g0K2Lfo3J0GbObOkJULImG8zcrwnpZQ03bMFrWhhmh6XbINIG2HAICVZ9a/+9O1mPZy5gm07w9
K4/b7aeaz244tvI8b/89D1HPPDyVVXyLV9nIvVe9K63fBjz+xrt6uT2VhJvA1dlf3fedwCFBkmP2Oog/nn8wS9sXc9glU8kvzMN3
NaYhunV5QMc8JZYrREugcEg+0d4JRAwsMTAtwfXz8aQKrbcjcF0yTZ101nQSOFoT11IyoBQraZB1syyfvo5RFw9l1lndybhtGJj4
XfFvSgwMsbVFjKpYBQtWrJDv3lnMxSdewdezv2TN6qm8ueoAddZhZ/lTX/u06OfOb565f7uvTwDspJ9krbdUNrOQHdmFmJlPodmd
chJihHQxBC2iwRCTLAqbmBQVGjonDj3zS8mP5NG/JCk72bvpJenpO353cH3RHp8UtsDEvyMx++/L1P73EOXErQduGctY+ZhOyZCR
pYz3lPzg2UaUbr27j9yypuHkgljhqpa7NjxnXKJysMSuHzs+OHTaoX4/+sla1lpfND5TY6vo8Umj1C8Iik4oCEpzccoMnxbqMtXa
iBlSqCrxiKHwujbu0MUhi6Mr7UJZ07iBeqcBUWFW8I/mO9x0/ANs+SzCoJbr+CIo5aUvX+THpT9hmkJgB7iBixukqJJeZHSdfunG
KVL4QJzL7t+FSy9dyEzzZeLKJggIWfNuhJhKYEVtHpj8MF8s+YpbTrqFbQZ+y2UfnsHq5mUkzEIGSn/e957Rde4WQnu8LgPbDaDE
dPolh9yxMbXm1rvXnvOrMuQv54ECjEfzPpZE/ACfVE0ab3WSDtJ4WYdl37aKzppESOL7LoYWAg2ODhBc/CAjOdowshKGNkRNGoPV
0pHu1Hv1HqZu7v2Yd/eGK/fr99B2j1linyZaDJigfztY/T2N5s1bP0ldPfji1uc3XlLTwpZ+bg4arGqiWHS6aRpb2xEVEMtP6LrO
TkwFns6g8bs4MFmswEKTZnPjemjcnVhM0dzRQts8KC8qBegV/krDfkcC+dctwEqhvHunzT37isTE/Ro7avQ2zeP9RnPz07sX7P7A
jKYZKy9effLv57z+P/MLVkqR7Qz6VBh9SQ4BpsOq9RvotUcxnd/Chk0Nulz1Jq6LxMMVT3Jo7ZLTndh+AXvpU+i7UwmUw4qlIfNX
+0Kn38lhxllM9z+izl/HallOH0fzxh1TOPaCgBMvHM2Tj75FvVpFVEXROgiDT+myhlMB7UEL513wBQ9Fbma/g3fikurLoBN+nr2M
S665her6TZRZlTTkQuPlQEJzfQKNZ+RoyXSw9/bjOOa8kXzzzpfM/PInNhrLaQqaQENfcxu2NfdiZuobbImTUh00zswSjadpim4k
nsnHllioK+wyRA5TkjStuon21na2bK4jbkRpcR0aVoeuTT6uvaD1IyvssZbKMG5mKUtlCtP0F/vpxOHfF943UO+ZuObM650FH6SN
RmcDKyNf8/CZz1LZ0oMXbpyjl6s5lNCti2xko/HxA4+IxEi1uyz4fg1N6Uc449CrWfRcinVOBss0tG3YxCVCDKXze2nJ76nJU0Is
Y2AVASVdR8bOADrj5NoUqeoAfz1EN2kyjSLZTs1qyZIlTbO00urC6pbl9DGG0ZitxTCEAC/8RlpQ2sTAJuImKLd8fvxgkx6530Bu
OOkOTr/tj6yqX6QXLt1G3bT3rc6hX3075MkNVz2IqCLDFYwgHBfYEqPYLsYwIyQpxED/hvaGTs1oNNEgSXO0QYZUDOWb6o9ZGZvD
4ZXHGZP+/Jh/xIPHDtz7s8pXL9rllktNW7LioVzl6VzE9c2sq4mDckWJJUpclKlM7TqmdnOeDlwdaJMgz7Z8x7C1lYnoPADyyC/p
HfQMSv1hRSP94T3wORw3ul/M06KZwYyuGEjQb9wcKz3jqf2aOhrOaF7TecDZRdfEFrYsIHpxt1OHddvj5pXLZ326bOktNDDUHs94
bxjD/DbaIuv0upxvyFpLR1Da0CZRFBZ+4IipLOJBGYXEMPAJ8JCu92AWjxIrTnuwks3ZesQSqvRgqmtquX7tKUy67h1m3i2MKzuE
b4uXsqljJQhYOoEi9HMfY43nM2eSzIm8z6MT4PiJW7jjhtO49LYOFhsfU2AkUUEEQ0fCdKjAoF9kEJuXNHPurddyy5mX8unV33PN
Wxfy7rw3McTirMhdMtucEnzZ+ZG5W/FB71TaPe5fnVmSLLBK2qY3fTLf174K07Ju9v9jlc8UwMRQhufj0tqWQhozZEnjOz4zVyxi
+22G0NfeXRu+szUlpSuSAREtFKiekPVI9DbZY/fdeenLp6n2a6Rmfl998dl/UtX3rnWebr391KJIRU2703itryfK745af0czNVFP
6eIJDfwMJxYp1oH28YIcu8bH4LR28IvxLfkqH91g0yM2iHZaMcXCD3k0hNpGsCVKnEraalzaOn0s16CatdTM2UmsWAITq8e5I6/L
f3T2xPbfFf5/zQIsiGdFrL5Jp/C+ebnZ3n2pG9+Lx+N3Whlr/o+N/1sxgf97BBEB7EysX0myG7aNVL+OLG1axfii4XSuhsq2nell
5gBFgI8WF0/CjNCdg30Y2287eozw9JY/w5zl8zAl9Jct0hWyNljE/sbRZMThW/dzGnJtYKzkjYffYt/D2rjqT+fy0qufU+2tIG5Z
BAEoMbri1jTlysAx09xy1stk7xIOPXsUX70zjxv//AgRJ8G25j7a9COhHhYRHQQE4uJIhi1uNWfvfRJDxpXw1r1vsm71ChZZv5Dx
XNDCwcZpxI0I9d56dmcc04KPUYaiekMDiVZFifSiQFVgqUhXygoEXSMqQykgH52xqG9swRRFA7VkGgfS4TXikjLKs4V/wahfwhLD
ENM5eWbf661cwa7XHHZ9NpkusloXeqxkqhy612GM630wPz1So+dXL2NgfCi2G0WLaC2BBIEf6mH9HDv36kfJdimu+uhsZm+Zw7Wj
n2H9UxEyOkdRxKOoTFHUwyRRINqr05LRAbQFBOVgpruo+c0BXlNAuhbaNyhaNwiZRiFVL7TVBDQ1OLSQop5WOmgSm6hOqgTdjN5d
Xt8SGiAgiA5n7aa2iRoR7TkWXz9czRH3juGC0y7nlefeYPDPu3HkESPUGX2ucJ9ef9ueggryVL5v67iCKMUlcboXFFKTgQgWZrSL
vC5dygtfE7U0dT/4BAWai649l/LpBo+9ejPJL7tz4aW7q3fufNU/8pZjDnlk1u1jDMxMWKxC/pPCDBSWltBHVQEqwOtKRHJ0aNa7
1UCV3wlblHSZn/tgeCaWZzxoZhUlKYNIBoJ2l1xtBLs5cmzRoX2MXqOP3/50jt3vWG/0DtvnFr7SIC8te3qnDza++UnEyH9+bL/d
r/t27Yf1E5lojGSkKqAg8AJPiuK9bK01vh8QIQ+0QcJIYhsJ4hLFJ8BUXQoU5aNEE3MtkjEfM1ZIKZUgcUyvkMGxgbw0836uTN7A
rX+6TT/zwipeXvsapQyhTA8QkwQZ0hTZMYbGdmW908E6Z5aeZX8ojXdsov6Ueu6/+kLuvrs3tSzEMk0IjDBVidBMosJUdGbaufeR
1zn18MO4+9RHGTpiELe/cgtPZNu4qvQ+6VXYN3i5+ql9e+cNmV2bWfdgZ1s7o3uMjvXcfJkz5T/yB/+LCavGDmJBPRvZ2LCBeNom
Tbs4js/GutWM3mMo/SPbik5nNSpcmz5euEeFgcBk23y+eGsJ4/60F47VxoOfnMWaxdfK+V/+kWsvu9iou3+d837ba9eURKtijcdt
uYaXcLb+fFtFtRNk/6LY2UZUO35WuBlhKTK1HmuvE/D7x3a5VOPvmgpw/EEpc589B7Lm4V7MDDqIRV3s+kJ6JntLNXU6LnYYxkDo
RykYmNogS4ZcrSnt2QBToqxiNpvbD6cwUhpEyK/4dt3bZcDWAvyvC0HvySnR2d47NzXrui+/9T56yBTzx0w605UB839bz/tflyCh
p5z2Xd6pL5w2qKxnMWo5svoHlw2ynp7JcTQvDChjABHJhcbhCKYWXDS9Et3ZY9feDHw6YMuiFJOemM86tYSELqWSEVQxRH8XPC+L
gqn6qsQdwTDzevVe5nPa05uYZ8+V1o/aaB7TxmmXnsDcV3dgTe0aohFBtAlaobX61d2mXbUw+eZFdG72+fSJZYzShxGzYqEjU9fE
3tAGpmEQuAEZPE44voqO0hXcNeFuGpwN1BstlLjbkQf80TpatwZb9HO5O2QbaydGRPYSMxsln0JWNKyl1LEYldiPjhaXqIrga68r
b9TEwsbUNsXkSAYlpHMBjpemnvW0Ne2M56YAbaY8/1d22cd8bCxlqbNr+WGHzq//8dIzK2529t99J3P23bDS+JHo4FYevPYV6iZ7
LPyhkyq7Bzg9wpwejN8lIioCK2CHcUkun3wKRWYhMxdO527jPCZMfIj0F2Vi2L6O9QuIl/skSsAuEiLFglUIRl6X9Et0qOFWYQyj
SmisbgFBAoKERvKEWEGcvM02BdkYacrxcMQLfC0YRIgQNSMQaPFla0p0+JflG0SsKKmlivlPN3LJ9RfLzJU/M/37Kbr31FI596hL
ZfE7C7zpnR+pRCwPK5tHAMTLfHr1TFI/wyGaB6o4POaqIPQWF60QLcR9SH1SRKSihDuvvJ940IvnXnucxHVl/OGkwTx12ivunHnz
CuJmsjDnhiHylhHRSpkiPlp7IXtblGgvcHEdRwLlIaK1DgLRgcZ3w0Jo+ApDTBEEN/BwfC9k75tdloeGxjEdOsxmmlId7L73SA7c
ZpxbmCnWG2aiPv9mi7mqYSlXHnedc1TTwXLf23ef8fXaj/fyo3qC5divzQ3m+hBSwhWis6RwtEOcIgICKqKDScSL8PPBz5kY0VDn
qgnNbWxLSBaZqGScCFFEmySkQMeJo5Ti2a9v1xWNPdghuhvX6vsYlNgOEO0EgbhaiHcPIFMgy1obdb6UsaDjLb2SWUx+OhB1dpJr
nzmJSRf0psHdhGkaiDZCrobKoQOfSkOhsJn5YQPe5gQXXHcDQ4aP4Mo7L+O5xhfkitJb9KDBI0sf2HjtA3jxcRdtf+ONjy245ueZ
HGtMQMvEvygk/17fairbT7MJ1eKyJbuFDupoTTXSQQsJK0ZPozdBVwSbj4svPgG+9vFFMLBNi/RPJgtaPM6/6WJ69OnBtY/diDMt
ymmth8rDpzxuqEmB937LexeXvdlzL/+NXAYC5eMFpqWCiB1BRBmiTQxUEDUjQTKSFN/XKtC+0lobxg+24Yo3qKe/I+dse42MOW6w
XvmiJ3tusxOLV+1K3sgmeu0G3ef3okJaiSkwfPPXAEXpknK6ksNotsk4OZKRQlazmo2pGtm+b54XI1HQ6jT2BllDF6/hX7YAry/4
JZqXLniwwa9eKAie9v5ZCVZ/85rABJnIRP3MjAd2UBgDB2/T3WuYiSxZWw2Rdnr27iMbvnUooURH0BhBV4y2gUT8QnY6tIx+DzuY
9TY/vb6CqfIeaWmjUCrorodRopKkdbuOGnnB3ambrPNjl7mXJi/hw84fZIuzjGprFl/89JGkOlKcfMnp9H/jINYtaCUeUwTaBx98
0WHXSWiDuOGxTvaKHoopJo7O4hvOr29jy7QJOi2SxSb9zgj4bu3bfPXcZ6zTC3GVSXd/D3rJNvqU2CH6Xfcp9V7wgiEot8gqUclY
nMDxsJTNIncOOyf6MKp0b1mzqY64jmpP/2ZeprSNqQxSaGJBEQVGnDSdtNGonY4ceB4gtmkG1lb5yVzmOjfs/Mp2zyy+4aVRHBw5
76wTvRVTDNnUWis/xh/g2SfuokhK+OnVNBEjRlXQv6u79LYycTFUBNc12O7EBPcvPAerLYFnOgRGhoVzFvO6+TU33HkCuVZb4lG0
bYClELMIqALygFjY9wGhS3Wdwm6GaCsUdIKThlwj5BrAawOnLUKmNUFnfQV+GvwAicch2wrNix3sOLieLx6+/lVOpsIiEYlAzYee
1O3l8dh1D/CHBZfL+rb1bPvLaG459h5j3PM/ok1NVPLwReMUtlH4RzCnBETzBFUS6ikVGsMQxATDCvvRPC1s+MIjl/K45aGL6Ta8
jMevu4OFk/aTw/uOVUPt/f2sp/F88PHIGikC09faQDzx8MjhBjk83yUb5HClE09lMLAxtIUfaHTgaB24WBriOo+InyQSGIiA5UVJ
GqU6YceJx6LE7TgDd0+QKEHm35lTc2tXkSPNDkeU8PP6n+TT+783r9r3cv3iOW857/w4qf9jsx6YtIJlp4/otud9qxp+/sxUFoXR
HpZDlqgZkwh5OLgMtkdRFJSQTvhEAo0RDdEA6QpC8aMQzbdwLUHjEcUiIXn4hqODINAiGA/9cr0zJm9fvd5ZgqdDdq6hI2IqRfva
tLqr77PqzPLD5N76Z+kt27O643tWxufob5/sLYWXlHP65AOYfFI56VQnUasrnk/7BBKK40Wb2jJNWuYF8um5rexz5zF88sK23Hzb
rTw49xY5KHuS/+iO7+inN0wc9/Qvt44ZU3XILd9v+eA+EWH8sPEWw8f7w6Ys0RO5Wf/GJD8WeBfBCmyiJDrjbPZzpOkgJxmGRAeQ
9JIU0w0/8MUQQwddcG7QxVRX4bkNZRt0Lsvw/Q1NnPTAaeR1i3D19fciC1wOb95PbjjgAVJfiLuqbe12BUYBvgYXjfIjRMnDJk6U
KDGJkdU5snTQKBvJShuWYYKhCRztn9nvUn35xMPUzFuzLJqb0z9ZP0irlWLE3t0oOcKh71vlbNZDBcno0Fve7bIH7Prb8HE6XMgI
fRPbsIBfdHNnMxGvty6RUmnOtg4S5Nt/QS3wXxbgDW0LWoFWfjPSCP4VW/6JTFWg9fqWnUdZGHZBMplb+TXmKn8N/Qf0ku67VrDi
5k6KSIitTa3x8MXFdhN6wD75MuJZj5b3TeqmuPxQ9z3tehOe1oiOMjw6nM7I8sBvc61iqXoZi8bHMndeNT662Tm3cKL+oWOEWu2O
krXB9/qb+Z9JS0MLp51xNrv1GMGGz30o9hBT/2aXIoJpGUSiZhimFkDgJvH80B5RtJBt13QbbVN4VANvv/02836ZxkLzB4qc7ejO
LgxP9NUHWzsHd3VebU33PmdAZIf71uWWHxzgD+2gNVDalIzO0k4dPi698geQJo84NoH2wxm1AiWhDrcDhaVjVNo9MLHw8NBZExUY
gG36nmsiMEVPQWtt9i4c9HBeemDJhJPvzEWbS8zaOS5f8SjnXjKe7YaOYMkFLmBTXliC9jSBH+D7Ptr3CVRAOqvZdtcy5iVfYdGs
xZjxAEkLSsWoi6yioFARq1Asq1/AmnWrJch6WmcsHbGSEkkUU2JU0F9XkAhsxIec55NVDimVocltpiVoJKVbyAQpXC+Liglm3CRd
maGzTwde1sMyDJxIih0H7kK/d8dS+62DXWrg+l3gUBgbiEHYufq5GMteznHYS+XceePFPH3Fj+y7eDe222sI5+17Je0/ZOgW99Ba
s9PgXaj7KcPQ0xXVH0RorMkhpV3dr9H1fS3964QuVqpoXwsLbnc4/74TiCWjPHvV28zf+K60+m2isLvwCpsgbNK1YIpBDIuoVijC
suXikcMjg0MGh3D2nSMlLhkM0djY2DpKgMYhhcbHIEGSbuTTnXL60md2XxLkM2SX3ux8Zb4MG9eXigH57LjhfB655UVufW2CjPvm
FPPU407yDtpmnH74y8f3eWvTW/sUR3u/fdeuL91269wLVa2zgSq7O90ZwhxxpNAoIkEEO+KhtWDE+DUGVBmCY2qx49AZuKRJY4oQ
V/nYRATIFBvdV7X4tdt80THF/m3lbw2/DlH1i5ad7d/T7YngDwUHqgfa5tBD9WdTeiUzou9S+mAFwakWRz62N99emCHTkSIS00ig
ugINlRYVgvp2zNTSacu8qx0GXdSPZx54hA+//pg77npYrfzhMH39Hi87H1bek3hy9nP39IgPHqLv1peoa1RHf6cjMmzsLj7Tft/g
dBUZZYgiQr6RR89YkvKWvkRUgj1HbU9xeYyY6yEWiBLRoEVU2FEa4dRAS4AEmnwzibMGvr2glRNe/yPRpwyuv+gp0pvSnLDwYE7s
d5YqobdXUl5AsjCOUaAwLBMThV0K0V5hamm2OfxIp3Jo34M0+I4m6+UI4q4sftKldV6UVeZ3TPOf5PEXrmf3I8ew7pEMPYbGGLm0
N2tWt4k202EWeWhjjdZoQ4R0oLFyCbbvPYLJq9NsSlXjdfajW7KENZ0/9wu0LyJ7/cvLkOgqvsG/9CtlmjbF0k1tvYbGzWLyU/ks
XtHOGuaz46Be4IGzyaRAFWgR0GaA6UDfURHZ5lWN+4PJ22cuITNuFYvqp6LFwQiiKKJsm9yW6fKzBtCGO7/GXfdYD3tA55TsqxPr
gkb/rvKXvPV6mLWwdk8W6Q/1zI2TJHMHnHLxCWw7fGfWTDYwSwSJdxU9U6OsAGW4XWBVmCYSBIKfEXJNmr4H2TjD1vPW018xa/0X
LFWL2CFzFr3YliOGjyQ/lfXP3nyivdpb1NzTGnTeBmfpZB+9l6sdKXf6B/m6zGjXG7GwcXyH/MIYUQpJRmxRiFYmiFJaiRYRhe2C
HbMpKywnRh4OWVSnRS5wNXimm+lQBIhShjO8YsQ5ne3e2An9H3B23a2nufBGWMZMqdoryukTT6bheZfmtYrE8EBHMwa+q/GySnzH
QjsQtGsSpSbuwat5/u4XKcsvYk7nNPrJDozkNFbklrBuzQa+e2oeTz7yBhuYi2BRQS96MJQCetKdANuwKcpT+LGAVM6lPZOjNetQ
qxtZzVLqWEc7jaRpJ8ABAjJ00kQNGdIEeDRRz8D+Q/j8kZ9o2xDFCzQS6aq/CpQKDeaV0qKMAD9ts+qTHAedN4qNi9tZ+NJKBm8Y
wrUX/5kPls2kurmeG/a/hI2LNvHeV3MIBgnH3TSS/E9jbJ7jYBcrxAzjLJWpQx9sCWPcivoLulNY/5DDiUcczfbl25BNdKIMhSEK
wzcx0ybKCW1fqPTRxRpTTEEbIYPbUChToZTSfuBJgBceuPwACQQjY+Bs1mQbPHJZlywp8kfYYAmx/BimZWObNtla8B3FsP27/brC
nB98uu9Uxt2TrmLZNSt599qF3PdWswwr6i8XHHKzc1hqvEz8+tpjzp965Oh41O5QYnrJvALVs3lwWN7wSShIFmishCDRLjzEEBED
TAV2HFLZTjKEw/0EBUTE1oAxILn9xFp/Q0lntmkbhZkOAkB1OYcrbRdY8fItmfVHnl97TPy5wg/c05LnGG92vsAQM48V2YV8EHsS
47UYm2qqOemOY5g/oYhsxsMup4sBr8LKpAIMUzBMD/EMVj/tk2tMcPa5JzN655259upn5I4fPjSvHn27v9sfDwqu+ejy0xNXlw4+
ZccLrnhjwdMzl67+3Bg7dqoxbdrUriJcrgXBEF80HmahTVW8jMKaKqxcjJITodtYKLk3SqY5wO4WZg1v5WooS6EFrSwtYoRcJ1Mp
/IzNzAkdnPD0sRQ8VcDdV7zBs5t/kRkdnzOQXWUAuzEiOopiu5w8K06RbVHZTbAikG2xSKU0jZ0d1GQaqNY11Pm1tOlmMqpV13vr
aWYLYzhGFhd8xqQX7mLs4bsy7/I0sx9w2P54xai7LYy7CljzswUqjWKrfhkxxUSwybUF9OrZAwNFJ02ksh5FsTjSYfaOGQkN6f+/
MOL4Fy++oXTqsKrLerxf/eCheyRHBMHGuLEw9Qub1VxO7H0iqRnQ3qiwbYXYAeKZ9NjGYMT7GtYYvH3yej5Jv812eTbV9evxlUfC
L6aUfpiGSUOuTgBdZBVt2pxbJdXu2lv7Rrbd8n3us4dPqj8w+dCOz+X27jbULPvlAnrJYP2987A8dfeLXH1xBdvf3YsNH2qUpzAS
PkKAUmEQgtaC2AKWELgKJ6UZeJZFXfVaXr/9Az5PvUJWxfmD8xC9inowbp++rFi5wDtl/XF2u6qv2c4cfeQCd+ZsPV4b5tsxLxdk
GK53pZQv9Ro9nZ52XykLKrHzID8SJVYJRhQx7dDQQwzBMBTSBEYcrKiJgaKF9bI5u54OOgBfWvx2G0GfOvza7d5d88Sth+mJ/gm3
bausLT6triMbC9Zw03Vn4S6F2k8E1V0TD8DLgedofBft+1rEVWRqFP3PUVw5+RbstLDE+BkV2CTMAgZ5u7NbwfG4VZ9x1CO7Ux4v
h0BrCUwyUkM9K4hKHE/5TNYRVKDJ5VKIBsOyMVQUT6fpoB5P0qH5hQ4jB0UUYhoohEgQwfMt+kUGIxuj3Pr4vdx4/i0seSpLsjj8
EjHCwqgMjVhg2mBGNP4GRecsn9Nu2pPbZ07n5/ndOfSRfA5+dhTvXTSVd5d+xvdbvqZf2fscvPoM7jtjA+c8sB/DB1aw+Vsfu0gg
6mPYYRcsFigbzChE4iAZRWaWyw69BoUMjShQRBivZ3U1fZHf/Te/W+W5ro80QgroBKcRsg2QbYdcBlQWjE6wUhBph85pEDjhVmhY
YCcg0ROKBkLDyz52EZgBdHwtyAcB0R08hu4wiOsfG8S8P2/g7cfnctmkz4x9B4ziqf0n5d5f9kq3icuuqvIQL688pko3VWJgETVt
InEkrwosF7QZztp1lzbaz2gCBRYmFgae9lFYWGG4tdHkbWnblFr2YQgaBL+ZY3ahSq2OMCx/52NWdM599YL2o6PvVn3vJOw/G0+2
3U1fYzibMmt5N/kwI388mGa9ieteupSFdxikWsAqddG+Kxo0XelihqEQwydaKbTMgJ9bsgw7aTDvfHEnD0/4kBdf+EROaT5cvXPW
l9lbvrhqzHvz3v9yRMWYBybXfnuHiDgjOcc6lKf9ib/BrIZLhpRK65ilyWpH98wbKNHCONMnr2XAvWX48/JY/5VDNF8kCMKYLmUE
4aHNQouFKBOUEWBZEHTYzLkmzQHnjqP5UI8Xpz7LqD7bYQcGnlrFIr+OEgbSSw3Cs4sIzDiWaZApTtGc3876YD3NkWpSqpaU24Ln
doKf/v+1d9ZhUl3Z2v/tfaS8q50WXBpoNDQhSBIkQJCQBIsREuLuriTEQ4gSI0qECMSJESW4BHd3aKy9u6rO2Wd/f1STMHNn7szc
b+beCLuf83RXP9Vy9ll7vUvetZbIMhKYOsFW9R6PXPAAXVp2Zt5lcb3kBY1huyx5t1rEisO0v88gNDHCinckDnGkIbGRGNrEw6Z0
p0de01QihCnjAJ4WMtWX5iWIdbml3rMNH9l63pba9pneHxmA/+hLmMLiq30vPpqi6+Tc3nZ0Ys+uGmOZ+J5qbxt5oiEbv/KoEI4O
akkoYZHbXFL4nifEbpNvzyzn471fk5OWhs+O6ZKKEhxZSaZoQY5uhl8Lb0d8u2EK34E6/iarllfO1572rC3x5a+28Xddvzw+57kL
Fgxu/WS3l+MnntjHyPpmoMh2m+h37EvFQ0/ez0MXP0nzi1Iom+ugSySEkqQhYSaHWRumTPZbzYRwS8n8yQuY/MyXfG5O1Jlee05R
N4q2DevTo0e+N+GnF70xm66x/aZY1C084PxZpZ8vLaTQtqZYCYkdj8kaQv4UnVJVFw9FXjBXd+rQVlRucUnN8fA1Tk4akiI5/5ja
0YoybCBtcBMKjwRlYgernaW4hqdB2mXxUkOv0HZut0bjW8b6ZV13y7mJzMHK2DHS4Iey5Zz72rHUb5PDnhcdRKokKDUqIVBu0pNy
lSekkFQWe7S4wM9nG9+jfHkle/2bqIpVk042uAbt05qSfUI1F826j4SsYUf1Vl1HtKKAnmTTiBSRTQY5+HQYn/YRqDmEPBIPE0dK
StjLHr2RYr2Og2IX1ZRQofdQyW5iVGBgUk2cdv6OLC+bRe+sfqz6Yhuzj5tN57O6Ujw7jpmaTE4KIxkClIbG8oEVElgCYls8wh0s
zn60kDdP3kabZ1pRb3g6DY5vyMHXJE3s1uzdt5M3Q/fSMziI0eds4Npx51BwQSMO/OBipkhIURhWMhwtLTB8YAUEpj9J0opXuWBr
DDMJutqsBWO/+KVSBaeW6pMAXSWgCkSlRleALgenVFOzLznurupgbV48pknEIFEJsXKNUwaqWkACfAFBMEsQ36upWgWhbEmkMdg+
TdlOjRImdQtsnI0eIuLRoUUDOnzWgJVzNvLx83N5dvZKc1jmKd78k05UN66/Xe6o3kcKufi8ILlZGaS0hIoKjV0DSku0mwR/7Wqc
AAitCOkQKSIFx0vgSg9L27W8iYQaOmyIMX/yfGsrW93DSmxEbemUWF42Z0rLYFH1+pqlr52+p0/2R01+cErlKPlq6VPkGvXZWbmB
WaH3qfymJ2+Yn3Dx+MGsfizJmCcEnkIcmtssTTBNgWGBZQFKsu2LGvKrTW55aRjzeq0XMy9ZSPGz+da95z0f71Y4JfDM5GdG5xqF
ba/rdf9VT3x/586fmSCaNm1qbN0kXFtYlsYjphI4Byvo0D5f9Op5GXdfM5p6O4qoyFeMeqQfHds1Z+VbMSy/FCLgaSHAsGRyYIwB
pg8MO/l/GTZ4MZPtP9XQa+jxdO3RnVA0QDDbIJSXnJCmtZesdMgCz/L4taC/DnjNk8fHAa8EKrdC+fY4TrUgUeVheIr0ggALHo6x
+lWDgA9cxwDT02t/SIiaPX66PuuR1SnAwjGS0pIEjiG0FEI4aCr2O2SeYxEIm8TcA5QZWpzT8jL32x2z671UfO+zerQ+RdwrPH6/
own/7ADc3ZTMcOtYTa/emdh45jU5Dydy7KOM8dumskFPFpf3v4B2vVrx1ZlVSO2JoOHTjTuZovFLCtuTLBim+HbnUhKiho71Oust
ZV8S1inso4ygzsKPrZ2qmN6T2C0tYa76psGkLWL/OwCuRhvLY7NnDA5desLUmtdfu3zWuQPGHftabMAVJxppb7cRmQffEy/7LuSW
F6/giZUv0uLBIK7joHeb6JBGS/Di4NUI7AIDUmP8eM0y3pj2Kt+YH9M+cRr9uIJO3bN08/SAN+b9O+SL1Q/ZESv8wkOpn9505b6e
lYfmMZtYKBw3U+Ti8/lIM+ugHUXhoFwaHJ+qDxbEhFdqUrNDIFNIsi2l/qXnmfYL/H6PqB1FAjWU65iuri0UNuw+zQbkdD3xxJGy
LHzs9T3uiHe4MWJWPwRzJu+m6KI0jhvVlOrvXDxXEKinsWo0TkwIz0lOiEJInEpJtKvN3rxNLH5kI75IDTsqttJQdCRbN6Oobica
jnS5+puhrNm9AYSkNYMo1CeSQwFRMgjpEFFSyCCF3KBFWi6YQUH8oODgXpd9ToIycnQ+zThAR/boLexigyizNlPBTkq8rZSo/Zwc
HY5heiyNzWLWwZ8Y5buNV8ZNpufHbcmM+VHVgM8TQggthUAaSe/QCoDlB1NpahY7FByXRc/bq/nxoS0c/319+t/SgZq1d/Hp7G8o
FlvYUbWMz+PvcmzmCUy75RhSx6VR/7oI1XOSc48JJIdrCJn0goUNIiAQPo2wdRJsA6DtQ6MnD5Uy1Yq/0mgHRKKWWW4BIVE72jH5
2jM1MiC0nQFONcSrhEhUJ8HYrUn+PAmB54KVIrQ/E4SdzOf5omDV0ehqj8yjbBKF1cxYtgL1Yg4pZVF2JlYTr7uT6uZ7EJmliK4V
jPrgPXlu6AwmdnqXRSVrKQltQFU4Iq+bH7sV2Os9rHQD7YAbA7dao2MCFYRIpqBSGYT9Qby4xoefFDNFAKJSlLlTJk9RyRlWf7NZ
kACs1dU/f9HcatN7rbv8vTPW92/5Rd48x2enGK/FHyHHqM+Bql0s9n9L649784W7nv7jm7DzZZOK9QZGUCNMjTARpiUwbTBsjRXU
WH6BISTxbS5VP3gcM6IZLTtUi5+u28L0Fw6a/YedrTvcVpS4Z+Jdg9/4/u3OvRuMePqbS9566cevfyzrcUqPUMdJx4c0EAj5RNvC
KEt2pOqpE1eIrw+uJBD+nqF7z+Lh4S9zyj0nMPSOfmyfmkCVmsIIg7DQhimQdhJ0jYDGCiUNN2ECjoEv5iM9G4ShMFyN3u8hIgIR
EKgI6Hiy+ExpoEZDBVCm8PZpnAOa2EFNTZmgukRSfVATKxUkaix2fu9Quc0kq6kWNQcFulxqNFi2y8HVLosvNDjmVUnfjwMsuM7U
e9Y41CQ0CVnDvnUuwUA6w04aKt57fzqLNq6nh9nUGFPwcOLitWcOaPdUr9sk8j6PoUbtaL8/1DL+2OA73IAvVLf0IcesrJ77Rn95
mbim/Y3yqwXrxfTEK6JeXphn7hvL9o8cShdJMuvZtDrRFI0f9xBasOhsxdzVOwnaFnk04eiOLcWPmz7HrdDsdYvJ1UU0lo3Jlqb3
vvOq6ZeRL+/cefunSej8hVNlrHEWVs7v4Hw88cBD+Z9s/qhjxM1xh17cToTLUmSHbacxT37LZ1sn0WXOSdQZ7McuUtgZEquexM4Q
+DpqKstifDdiCS/OGccP1vd0ca/Sfe2zOOXkFtjGfu+Cz84xP3Rec7Kt/CvKndIxn1e/lgCM3exWyTSaqZV2zu1s9ml0nP9UNaN8
lsxvmUarnAIqvnTZMVWKthdGCfgFokLgSxXYKeBPEQSjEn9Akno0pCdCvPXjRMqEorHZDOmrEktiM9xG0YK6C3bNHnxW+Hrj0jf6
S2u2YuvEODtzisVpU5rjHVDoZQIiOmmd2wLpA8tO5pwsS2KlCEKDYrx26xdQuZ9P46+SIZrQQg9mWIPzSTRdTWx/NWUzEvSxT+cU
41KG1TmbE1odQ/v6TWjVNJejumeKdj3CtDzJotn5kuxbJZmXSOp0F9TtYOj6XW0adQ7SrHUKhc1zaF+nGe38LURj1ZhCp4g2dKaN
3ZX2viLeL32VCGkc9A4SkyU0qzyGHSWlDLyoFTXFimCmxJeCCKQI4U8RBNIF/jRBIBV86RLpgc7QNDo5ys4Z5cRmWaT4DDrckEv2
3Ka4NRZxJ0hUZ7Osei6kH6DBt8cRSYmSeQ4YPolpgREVGKkCGRXIdIFIBRkViIhABiXCFhiGgZTJwR94SY+RRBJ4cUG4gCtA1TKL
RfL70hPIZFoToXWy17WRBFfD0Jg+MC2wLIEZ1NpO0RipGjsqRCBNEGkMmR1MAlkGS7au4LlHp7Dsk92s2L2MD0om6S/KP+erHTOZ
smQG381dQHBZGqPESFKr6rJ663oqAlXMq14kFsc+4byRZ9K4qDHOJhdfWGL6BJYFtj85lSvURJIzyCCzSYQVOxcyY810jg+eScNg
VH9W+Z5ItdJeq3DLdtQC7d8LWXqAdcDbu7vQ7vTDRnf56dMqvgifY9zgRVR9sUWsI2iGUQ6ssKfTZHVnfMUZtH8qRCBuIsvACmoR
TBcEMgTBjOQz96dBIBUCGRDIMjA9iVetCTb3UXBuFikhTyx9qkTULW0qRl430MXvpn4396feL86b2Ftkhvf3rXds+VMLnh0mkI0v
aHqVytIROee7YioqQqKRPIYt8fX85EyhoS/C/G82UlLt0vfmQnwhiawUBLOEsDO08GcIfJkQyAJ/usBOFfjSBP5MiRHRyCCYqRKR
IpApEkISQgbCSuaT0TpprMU0VCcrcakGXQ1utcCtErjVAhWXSVlSye8btkBYgDbAlUIqQ1jKwGcbxCth7xeCev2h8fWmENsMnD1S
eFoTK4X9q2OcfnEXvpz3Ncv2/ywi+1qJvoUdRcXeKu+Hms+PPSXj/M9XV7+2K6nPV+kjAPy7WFrAaTzVdG7k7eJ7P0hXLes/3v1Z
tW9PQs7eM4vF4h0x5qJHaZqTz44PHAKNPFqdaVLnelDVsPoSyZolLpZPE3KjBFQWR13kp6B3AzZuWsWaPaupq7vRJ9SL8tAW/W31
x2aGmft5pT74Y23OWR8WAjMm7L7XibnVnz732DPWF9s+6l620hanXNhVN+xgiGO3DWJZ9UY+ODCJXm530kYGUWkKGTQhy8X52mLJ
WVsZu+UmFpobOc65iW6RExl1biu2l65xT/98iL1MziorsNqftt3Z8J6HZx72t5MPWhh4qIuaiaPqD2w+yFtfvUJ0bFHE5G8+5Z1t
z1KHZiJlTTOaXysw/GDEwFcH/Bmi9jCDle9xcJbBl0s/ZJfaJdrYPagXDfNTxVdsqV7TqCB2nHH/43eQ294S1RMNpny1WvR9Ip/o
UX68tRrDB0ag1kL3aWx/kulqWALtaLL6mXz43Fwq5uzhA2McPieDxrovI6JXklZ3B/fOv0ZHNmd7OaKO9jylq+R+vVOv8DbHF+t1
lYv02orFennZEm958Upvxebd3prFpd7aLw96ayeXeYu/3+nNXrpKL1y/Qi/dukSv3L5cryle4q0tWag3V6z0SmLbvWpnr+dDadco
F2nRdJHmNMP1HDzhskEtoSDQkM3LXRp1qkfzogx0mcaXAVZY40sR2FGwomCkCWQqGKkCLDDSTPKPDrD504PUPypMeAh4YUXKxkzS
o9lUHUijHu2YWz6H1b5vafxlL2o2h8gcoZGpQBhkWCJCAhEW4KsdKl/rwWqp0dJDe16yhafrgZtsn5oMjye9IGGBNnUyPxwWEBEQ
FJg+geEXwvIjrIgUZljgixpYIQM7bGBHDCyfxJdpiEAdA3+OIUL5klBTTeRoWDFvOx/dvoipb85ie8Vy5ljvUEa1bqCPpTHHcnRG
X85rdy6Xhi7SJ7l9veXuAvGKfkSsjy6gUWVvsaRkExXGGi6pdy65zXORrsYKJg0zOwi2D6wGkN3L5M5Ln2T9xnUEfAZzlq3mWN9Q
soJCf1IxiWx/nYkHE/u3/9X5+3sgbO5TO4tb2UetWestO22u/pKzfZfrY6w+YqO3k3TyKdOlzDYmM7DpQDKaZnDQ2U/DohQyCgzM
FI1lawKpEl+WxM4AX6bEypAYdQQyVyBDAg8PbUD28Snk9bNZ9cFesW+yKU/t10sVHV/ofrf0m7rT1n02cMn+tY2kZ9Urrt6e+cPa
OWLfRs3w3seKNk1zxM5NMSxdDxODBc6PBAMxnKUp7P4+yNHn5RJtL9GlGn+awM7QWOlgRQVmBKw0gRkWEAEjDYx0MFKSUS4dBkKA
Tx9qeZ9MV9TWwQgBBkkCaDLKkwy7CwsMv0gaZ3bys5Qi2ejXkkiZnGMmMTCDEpkqiBtQskCTcyw0uFCKUDXYe038tknZDpeUlpJ+
vXvw4ldPI7wgGTQXQ07o5c1ZvsSe7n3YRHWLvXPv1tOO5IB/R2lfaUhTPbpr5MPaDbW/tuXoeFajNGPeT+vFCr7lvBPPZ9CITpTN
cMkqMgi3FaSPNFClHsXPaar9DrltDMo2p+KrY1I4Apal/UjRcc1Elw0d9Ns/v83xKSfQqXFr8ciu5wBBqp1WstvRf+/ACyEEprDu
aBBptm7C7tHPbLp5Y2Tcw/c6bT7IMsbedhsTZnzOZR/ey+PBm2lxQw6JhgmM8TZr7t/ETeVns4INHONcT9fsLlzWtVC/+c2b3m0b
b/Bhx0t7+Iec8WP5h1/zl/1mf7FGEl5cCCGsoBXQaUdD54qOHPzGFRdbdzK3Zhbja+5j+dZd3PXYFRQ+r/HNFuidBjqSZP1qD+yI
IJ7nQCKdmLmTRdUzREHayTriCwu3xtZX9LlRNG4aJfYYPDF5scg73aTu4FScKhcjJ6nwrUqBEdXY1eBWaby4ILYHMopMlv9cTNkH
CWZG3qSkIkYXeQqFZn8GHV2PQfOH6XK3wnidx341GlUyt0nZf7UrBSmEyCJACgKDOFXEKCVOOVBzmIOk/6uz5KA7VHZWrzb8Tr68
OoxrapQn+CIxifPs+/jpnlV0mlmfAB4iLhGBpCkrDRBBkQQ4U4Bfgw1OjSJc6KPZfRG2PF9B2oY0GlwYZslXe8ncG2V4zy5M/2EL
+bIFXx14gavDJ3LPlLdolHEUodsT6FRRWwqULCbisCwdzmGXAs9J9rdSnktcJEu6tAeaZGMP0xMYCkQERAp4NeApj4StiaUrlPBw
lYurHZxEHPCQwoC4QChBIoHwHE1JuUduMItF11Xyw7vr+Fl/yQz7Qw66JVhuSLemE8cHT6R92wyatA17y1cs05N2PmF/WDWJKrNU
o1zvyuCNIrumHpYIkKfqkh+pC/XBTMik9k+A9mlI01i2yeXDH+SHFZ/jLi7FV+Pj1gZ3s317tdZRS6MxEo73rzgULmCuSiz5rFWg
4zUrY4uevV+NVE/6p3m3ZIyRb+x9nVgiTrWvmD3OBsaNnMnBbQZZLaN07t+C4we1JnpsEK/MQ5VqpD+ZLsAP2Lr2qvXFpcaNK9I7
B+i7MIfvLt7Bk2NXiq6t2hhTzv/SeWvRM77x3z0zLORPKT2+Sb/dG0rWh57cc3nkxy8+4ppj7hBn9GzLu/P2U1yRS5EYyqaan9kf
mkRkW5SvBjSiy7Mp1Ds1QGKdRtoSLBBmrUdqAIauJeclG515v3T2PuwceYc6oIpfaasmeP5kiBoFVm09NoZGWhrDEhimTqYyhEbX
DlGTCJRf4FZ4CFtjhMHwgyM1y+/1aH6eQb07DcLNFMWTBdnVIYqnObS9Op1Hz3uEO1+5l2Zbe9D0hDbGo8PHOWdNHtI7c0nd0YYw
7mqv21vJEY5/jHzwH7TIubsJ09320T7DNlQte7+veZlz/9U3GnNeq2DuvlmipOlsXn3rQYIRE3ehh4gKzF4S6UkqPnYp3e5RtU9Q
uV2T0cjGbX+QR6aO4evPPiM7JUUPHXY2fi+Xde853Nb3LE79vLu3tHKe1TJ0zPBVlXOm8PfnI0tASCFVp8ze/RaXznqnidM1dfzV
Lyd6XlTf2H4pvLdgrpiceIPHzrya4wa34Mfb5nLhnpHsqNlHK28IQ+uey2mNO6hHfr5bvlz1pJFqZ37bN+O8W97fPXZRLfi6f+P5
6iQL2vz5kpzb2vXTF8danZ5p5bYJEN8EYj28NXMht+8+R5zKuTx84S3UecxBb5bgCAgka15JVbiOxbgun3LP7tPwk8c1+WP0zJr3
COlGvHHT01RMhx9/3s3M/GXi+QV9ksogppOhrRqZpNLGkzkmr0IjKiVeQFPteHxz6gHeSdzJh7FX6SRPI8Ur4p5mV/OTGq9v33ST
DBjhKlP75mitawTCVSilUUpIlImtDCzPFpZrSFMDKJkQHp4nwDWwtfCk6aEtDwflaU8mi4lAG9LFNV1qLFObfkfEelWq8uitDR9V
rWv6ywnFL5FupDNdvU2TSD6Pi09pfatN2nUm3rZatrGoVUJ2bV2mAdrSUJuX1R4YtmTVReXImQYtxgeoyktwd4evadYhlQLVnG/m
bSAuqpnBu2yL/sCrDScy8LzjoTewGNRuqNgFWysOsrJsA6Xl+9i3dysHEtuIu3GUNki4HkLZWDpAUGeSqfOp4zUgTebgt8M0tyI0
DwFtgEJgFlQsgY01Dhvc/ez1Ktjn7WWHt55dLKJG7MEhTpwYSnmY2sCUNpFEPmOOG82GdVt5puwe1tWs03VUIdm0oIFZSN+2RQxo
307PX7xEP7B0tDXDmwbS3dEytfX79YJtg9/tePeSp5q9oXy7u8uPKr+gXuoWXvh2DG6Ri9yTRAftAXU08RKXB05+j59Wf0JVdCsr
d6wAZdMvOlwPTb1a+1L3emcu62tlGDk9D7jF02u7+Kl/Uv8ZQki3ib/t5RtqljzVWLY1xmZ/rlKzfXLM8rGcdXov5u14n1dnvkbT
lJZkVReQ4ubRPKU5ffsdS+dbm5FxVAivEqgxwPbQPl1bSmbgeV6SYV9LiJOBpAE1/9WNvHTJYhq6LfQZQ1vrbdZib8yMu+XCnfOr
B7U4syTNnxF4f93z0f3VleZZaVdweeNLWbx3HZO3fyVsYVCsN7LJns6Q0FmcXHYz7e7MovFdBsKRIJP5fS9ZLZwkZyH/q0vgqV9g
zBPUdiEFrbxkmkLX2qrlIKtEMidc5eEkNE6lIF4qcEoNYvugqhRiFbVXmUu8JBnFEIFaBr8Ntl9gBzUBqck7URA5TZJYBuVTNBXb
wchR1D/f5qWx7zPppTWMTLmOU5+O6Pcen+3dvexymZsTPGP5njlTutPdnM509wgA/zbDzhImq7PqX9v48+J3ZrWMn5r91KUPeaXL
bfnzrF0sDn/FI1OH0Kh7Pu5qB1EFoqmB9Emc+Qqn1KOqWOPFTFLbSj6Z+wWjn7qf0j37CUSk3lyxFkmQq46/ieayD0Winb575Rl8
vfdz3Tp8bJ8VlTOn84/rqk3AHV7v8mO/OPDWK+nVLQqeHfxybNBZbcyVo5HjVr3DsrRPuerUwdz43hUcqCnX7fQQrky7hVbpOeq6
Tefac/W06mw7//bi+I6nRbKL8H+rdPRoLSP3p78qJGelO3WtM62rEuddfy4Ft9tG/MsaFl4U4N2qz3lZXC6ut25kzKVXwf0JSJgc
ml7qoRFhzabL4bJXRvFd4n36RM+hTeB4Bh3bhazVzdiws4R31Sc8NOUUGp6QhusoRHIeN0KJJDUtoRGOgErQpRov02Pb2TZfzv6c
q8VJNHE7U5/jGJV6Ibmm9k7Z30UrWWNk+uoN3xPb9KGn9d80gPXf4Nz8Sp3U/+Ag1H4IQYNAy4FbY6un+HSK8V7z+eK7LQtkcXw3
5cY+PleP8XS717lq1Aji18SwElbSSDn0BAzQyT6myZyaSJY4aZVUxBYmM447QE55kGZ3hVhfvI8rbniSo5o34JS6w5k3ezc1NfCz
+Ixv9HjGnvoIbctbU7ziALvi5RyoKWeH3k+x3EHMKqHcKCbuVSIUaM9AConWgqARJCgCBLwwEZFG2MzE8oUJmWHqpGTgy/QjAya6
3KN070H2Vu+iWu0nrmLEvATVqoJqUYpDNXEvQUI5ybpTmfSmUgJpNJOFfLj/PXyJHH0CozCJ6KMymnNSs478HJ/tfrH2K//71a9S
KXfPbhBq8fLFmXd+MWbr+cXhYJ2n7Cp99ZsFPyTWb0kxp8bf5NJLCjnphRNxXAdDG3iuhoDG2w7vn7SA9zdOYnlwKrv3HdANRDvq
iqPY6BUzKGW4vq6om247o5nhan+fhDr4/b8AwIen4lQ9u/mg7c7aN5ro9pE3W0zTNfUSYnzN3Xw0820yZAPheS4xsY+IHaK+aEHT
WDc6ZfRl4IVdaDjKQhQkkNJIynmyBDkZgvYO5eOT8u9JjZlusnthKc9eNp3dC11Oyu9K2w519Oclr4uHF4zRUX+dmp7ZJ3k/H/jR
mn/we7MFXcWzBa8KgwivbZomit2dVIgdLGMq7SKtuE4/Q/cRzYg8nEBXSogd5skKcEmQwE22Y1Ea4UqkMjE9oza0LJKkPyfptWKB
dgRaaDwXvBh4sdpoiqVwPI9E3CNRo6iuriaecHBjmuoKh/xoXeReP9t/cFAxD3+6xA5JLAt8AY0/ogmnQqStwOol8Uog9r1Gl4DZ
wsPXzuLpUR+y7pN0hnY8XhfdJr3bz3tVTomNr+rRpPtJ761+csZwhhuT/wCkrD8YAI+WcK/QP2iROiDzs7yabv3uO/Fp5+iuDYyf
Rlcxja84+85m9L2vLU6Ng5EQaJ8EG8Rm0Ls94hUept+mIlzGU2NfYvrHSzlgbGabWk6FU6lTRQ4Zoj6OFyAzkMVdBfdoGd+rT1nT
02gcbHvypuqln/8jMDz80N/RdkKjp9bf9JysCfW7vXBc/LyBZ5hTX94t3i57AfPoFUz7+UNaqZ76sZRXdaVT6d5QfbZ/m1xa2sxq
M3x9fMW3SaNjtPhn6uSGM9z4ih+PiVll9ziu6tNc9+TGovuds+84hrUvxuV7X69kgfkxM/WL4pGMZ7j0/tOQFyWQro00QTkgLAdv
tiVePPM7feXOPtRXnRg77Dn8G/LYswR+5ANOv7k1p1zTHTfuIgMCnQLCqmXeyuS4N600JCQioqma4rH91kpO2dODWLVJC92T49L6
MDS1px6x+SS1XHxrZxr179jrbn2wdt/0f0iGa/0AmYgY6Y+Uq/03Dw2OcW5ufrMxc8Ui1nmr9TT1pghGqli4dAb+LBOvTEC+h8TE
Vao2dyZ+9TJ08l6FAlcrjIikZJbik5676NG0HvlXW3zwxXRGfzaOc1qczqVdR/D1p1vZs7+cedZH1G3sZ8Ha+WzxLWOP2gIoDERt
KFpgaBsTH4a2sQhj6xC2CCHxY+ogTZAVHwAAOS1JREFUYTIIi0z8OgMfUXwECOlUQoSxCeDiUco+qjgAlFMtKohRTRWllOtiqinG
pRKPBDEqcElg4cPF47SMi1hdvkOHnCYMCJxMr7ottOOWqUd238eU2Cs+0/D2ppo5t+07ectEMVkq0OjR2jbus3/qmTbwmAldPnK/
+Hq7/EY8wWszbiO9Uxaeq2praTzcTYqSsy3unns7r1tjSU80p5lxjA6qOthkUxAtoF92e71Bf+08suvBwIF45cBKVfzF/wCAfzGK
8+3GQ3YmNr3fJzjEfazwWTn74Hx50C1h9r5lrK1ZI6rZQ5xSHCowLEmWV59e6gxG1h1Fp57pmGeB6qnwfBoLiYqBrtEID4QDugao
1DilGjvHwtMeH744l08eX05DChnU+jjtq7eD+5fdJD7ZNcVtEmiXSJWpelH1dNvyosbd2U+KkxsOF1+vX8HPJSuokruY7k0ilOLw
eocpdGzQmnXfVrFL7WaXOognNFoqynQFVaqSNB1BKA1eiKjIopWvDulhH5EUg7BpEEoTmH2AZsAij7I5cKAYKmocymM1VMQr2O/V
sNMrp1jtZw+b2GPOpYIdJFQ1uaIRodQ6nH7pIHocfSxsDFC2DJxqB3+KwA6DL80jVCdJEDPrSnTz2tzzDo2uAt0S2CAY2+t7/HsL
6TcqT6c0R11z22P2wvDHm28/6s5ul8zov+ef1XtHAPh/MfRsyFluvZS2d8cqnHuvT3/WuXRyV2P/WINXPp9H2sC93DB5EI7lYkmJ
VxuVUZUeYg/IGoEMGaz8cSfjH5jAgs2zSUQrWFe2Ukd0FlmyPrYXxiKDRnZzuma0oY7OpCqxNzGm6lpfsbP/DldXP+xp759SALUl
Qs7cTjql36r0p0orK0Zdmnenc2XmXfLpVZ+JbV0+Y+GcL/g8a76eFfvJuankIr9liz0d03ueMXPP1Ol/I+T8z8UJRmvz6PHd+6+L
LbokUWWfeFb0FvOGc69wdq6oka9//zm75EKxgu95+pjnOfP+43F6JZAJM0nU0BrpIVaeW6V7vt+D848/gzva3czIZx5mBdM49YQe
PP7E3SSqFYYLIhd0tLZcxkyGZiXgmWjP1UJOMpBReOKtNxk95QFamR05yhvEzT1P1w/Ofli9VHObXUfW+3Kft2uAhzL4z7dKlQB9
Uy5I/aFq4gJURsN32v3kHe0vkLfPe1lXZKzmmqtPE+b8bOxsjyJfE1wBiSEu0d5JSkU8FsfURrJbh0qWAqGSRDPP8rAzbGY/uJ1F
d1TRq7AFdS6H+x54kjd3v8HdTW/jwt7DmfLJCsqdCtJaV/Ls9AlskSuIqzgWfiTJaUwSiYkfH2FCZJJGA0JkEiKHFOoQJpM0Mkgn
jRA2fiRBFH4UEVviDxl4CUFZlctBBDUo4kmflxL2s5diDrKT/WyhmmJK2Eo5+0klAwOLARmnsf+AX7craMKgOs30J6s/dJ/b/4x/
DfPdOr68d7tFBo35aP+E9cmqgEITVjnntLs9742lDy+9ufXTGfd0vELd9sZnorzRdF5d+BjxlASmY6J9GrkFyi83uH7a9bwhnqWz
OpdWohcBL12npHv0btMKu9jVY7bdoL6s/shni8jeJoHm/VZXL1zCrzPL/2VOjES66WbeazVe6SjLC+rWkSKnX/rJumNKN1leFZM/
7Z/PhqpNJFRcGPiJUc5Oay2ejnOi0YdzGp1O1ytaw6CkHaHqOhiOxNsNXo0GT+BVatxdkDgAZl1BuIXB0rnbGXfnFPT6NPoHBtEi
J03/rL7ikT03qG3uVreV/xhxwN1mbk9slkPC13Bf+/vFxn3lTF27gL1iHft96+hzVHsWrJxJRVUl5cYBYp6LxsYSFpbw15b169pI
TCoBI5uoWYew2YhUfx4pZgYNAmmE/RamnZxgtOdAOcWV5ZR7+zjgraXS20i1riDhuuBoaighIUpBKxzPIcefR4AIuiaFtIaZdBpa
wIl9e9BYNaBiNSSqHIL54MtNkhRlSCLSNTo7qc0MR6BIVgAUTyxn/KhltE9vr4d+EGbRpCp11Us321sjMyfvrVp9muM5Br/zmQV/
IAAebsBk1Tn95N6rqpZ8MTx+Jw98fZ7MyDHFO322sjQ6j/vnnoyVboNLsnmBJ5PaPO4iTEhs0Xx3704+fedzlvo/YJ27gnCioW4o
OxH2MoAA+ZEcuoba09Sqx9fxd72XD46l2N1q+UWQoBG+p8Tdd+8/a4GPZrTczW5jAhNcrbXMjzR+bFfllmuHWOc5lwTvF/NYKFuk
Br2pBz/Wb1SMt6JW2vxe6adf8FHxCyv+p+Bb60F6gPZJP9kp+afsLN32VAHHNXio9yOJrMqmxgvz36eYNWywZopnC5+h/7hjiPdM
YFXZ6EoPQh4130teueZLBl1WxI4n0nm0+E425Exl4cwFWHv9qI1g5WtkpkZEBTog0EEgmDzYVqWpeQaWvL5bTCqfTl6dKCnC4PUl
3/Nk17v0jvIl3mkrBpqm6VWekDKs86cHX1v9D8pL/t3kRDfHbHDZHrX1uc7+U5w3+70jx8wcz9kXdSVrdQvx1kcf8wGvklsnnbOi
55BZ0h5xdClFN+fTrHsexFzcstpyH1X7pJROlgfVkZiVBq+dMx/v22y6dW9I/KhdXP7UtczTX3BV+t1ccerVrFq9kbt+vk1Xqiry
RD1RVzTBlgFsn4+wFcY0LCzDh6VtAgQJ6VQioQjR1FQs6cMyffh9BoGQSSgKAcsgkgYp9cBqArKdB6US1kHlXnTpnmQdcKIcyvdW
i/27q6ipdqnxYtSoCly3moOx3eyvKaUyEaNlbgv6dz6ODZuWe/f8fI342ZlhSGksaWK2vGOTs/YLpZ1DRqZbK3duo+ymfbbt3Tdt
yqAfVGRDgbhg9XmMueRUznnhLKpiMWy/ibET1K2S86dey2flU7mCF8j3WuPi0qJ/UDfNTuPTHz5To7ddbpWzW6SYGR9lBZvdvKl8
7gb9KwNa/w+NLz04/8L8Ofu+eeygu6dzwovXB4SPVHqE+jp9jNN1C6O9CKYF5Mb4fvaXuKKqWrGZpWyUsym1ttI0I5PhHU+ln9mf
zAZRYieBr5ODUS5xtmgSBzSqRuCUC5yDAjNPkX60xYaNe3n82ReY88VKujCITv5OOtUw9Ff2096kytd0tmwg0v2p8ueymaKN7MNT
3V7DdlPFW3O+oc+Axjy36Ta+W/OFBrBrDTA/aYRIESYBHSCFECF8hMmmMQEyCREVGWQSIUQaQbKwiRgm/jRJrMRjn3IpJsZBqnUZ
+yhlLxXsZRdrxR42oamilK0cYDctrHZEvBw2eiupG87QGW428RoPne7R9cRu4sKRZ9M8Pw/ngEK7GiNNItKSLH9qjfTkQ5Aox8Ow
JKue3cXbV25h1A0ddbNHbD4dvF7d+tl1dnnGrkt3HVj8YgeKrJ9ZWKsHhT4CwP83SwLemQWjMz/f9sLMY2JnFjx22xi37aiI8cnJ
+/h409fcNbMnjTvlk6h2MXwymaOrTdV60sP9QTD76i2MW/E08+33sBI5dBZnUlcXahcfgYDN0W2b0NSXzvTl070JpffrdXqODRCW
0fkBI238gLzukydunRj/Fw6/qA0Ny8lM1pa0vbqBxo9urlpzUzezjx6dOjZxa/Vl1qLqOTLXzptwTt3RNz+y6ZKyZPEJ/z9MQHEo
lCsQ6vg6p7aee3Dqh6aT1WxCsw/ijWhhPrthCttZJNbL6XwwaApdxrbEqedibJdQCSrXQ2yVrLyukq9nr2GTtYyzP27PMXU6cuBj
h0BjA1+GhxHRkKWxCkyU7WHMNCEAB/ZU6I9uWsXizbtJxCrFQXbq9jmFHF2vPREq9dkLhrDNWKeP8ncftajqx0m1tdXe/+K5EBfn
jvZPOfD8vIOJva2fLfgkfnLLk82nNzzPaytf4yBbRZbIQGtNpdhP/dRcGlS2JddoQ9+zu9HvzvakNQigdgNVtflpN0lAEx6IZgZV
G2u4Z+A3pOxsyimDW7ChZiGXfnUmB9jCpVn36371+ukN69ZTz2yI9Am0FkgMfEEbf8if7EltAHGhSWghPLQRVMnGHELiSo02lRYB
ExkyhLRM8Ex8OkAw4COYbgGakrJKSqqrdIxKPM9FVyjiBxKoqgROPIarlfA8D9OzEUprzzFFQmnqReqyRa3TN2wbaScoTaTLnAcv
SLv/8ccOXFihwexOd3rQw7uXezVgSAzXtH13NnW73vfJeV85L7w33fg8Po7pX08ku1Um8WwH32IL70G49ccJrNy/lxFpF9BM5+K0
qaFeL1vvWVTpPfDlg+ITd6xpG/7yXKvho9vj6x/wtPsLgP5/RkgEoA1hcFbuTRmzDnzartQ7MKDKKx0UV/ECgFwa0TPQP3FS7um0
zuskHSHFjh3l7NxeRYlbJjaxgKV8hxcs5ljRnW4MoXn3ptQ93ybcxsMqlVSthYpdXlKqKyWu5xFsDOFCk1envcKDTzyKKrF0N2Oo
Pjt0ESJnL48fuI1Vlet0o1AzsbjsOxFVLRjX+mX6ZHfhvZI3mbD0Rer6mpAi8sgy6umgkSr8RhoRmYJPBjG1QUCGdNAIiqAvQHq2
n9SISTRVkl5HEswAK9eDDkBDYJ0HG02qtsGBbR6xCkmiBvbvreLgvhi7q3exu2oX5exhH9sopIiFZcsoE7vYpzexh9XYtke+kYuo
8YucSCtOHXIKg0Z2IrswlOxwpoFUknXJJAlknpfsxuU4GstnMOuBjXx3ZxWjbm2r61+NnnDSTB5bdF+1Va9q4Krts2YW/QLCRwD4
/+oeRPHlOlj0ZsEnKRWNe40bMNE54ao6xtJrHF5f9wlDxzah541HkTjgYAYMvNpZKdrUiJjGfM1k/iObuL34NpZ5y2jl9qY1JxL1
8smx03VB9ygFTVJZPWuNenjFg/yk37QRkGJk/hDUoUd337llWm27tP/p/utDoU8ppFfX1/jsbfENz/ilPzWmYtU5Vr3Lit2db2jt
8U/ml/9lb+/mphPqPr39mhdS4o0GjsuaEMtxmpvPlb4j14nvqLJ28OUZk2nxSCNcv4t+X8AJGivNZOUJFUxZspCB9zejecO6bJ7s
EsgVBPPBCHlkFEmsowSxlS7uEza7Fu3is/BMVm0QtKxsowvtuhwwywk1qyKrg58OoXrq7rce8h4vvd3XMFB42daa1S/qZJPF/23W
owGolqGjT9hYveKbPhn9Rb/IKfHmqceIhCHlrD0z5JIDS/XOmp1IXMrZI8qNYlKsVI6KnUifvKGccH0bGlxlIGskbNHoeHKaFdUC
7df4jrWZ+9Q67r/2S46nNwPOb8Gb373KU9tu1BGZ5oVUlixht07O8XGFIi41HiI5Ibe2T7JGY6Lx8HBrXwssQmgkHooAKUTIJkgm
fqLYhIiSTpgoLglKOcg+dlLCTmKU4hLDpQqHMqgdevA3jhx+svBwkbJqbZ5VcPnm+PLva4lwf6sUTugXtSEuCU27vsEDPS5rc617
xtQbZJdBJs88+gixaBx/uY/lJxbzys6pOG4GZ+ScSpMLIHxcArXW1tMe36Du23qdvVJ8qTOsrHdbR3qO+enAu2tqO1/9O6Mjf/H7
BILLCp8Jf735xV4l7u5+Zaq0j/LcphKbdqKbd1L+ELdfi740yCqQJTHEhgWVbN+xm2X8xFzxKWXmFtHWKaK7eSqFbdqTfVqAhn3D
BIVN+UJNzQao2atxyyFSqGk8zGbZprVc/tBFzFowg1acos+wL6dLbhOm1DzLhH0veC3DHdkeW06Vo8T4vHd0REQ8N5bAEwIRkEQC
IaKhqE4LpSOFQUI4QgoPn+UjlBpAZhgEGpn4/TLZYS2EsIJoKwJGJslSuopkNzTlJI3IeCUkyh2c/R7xUhenVKBKhXANiMWU8OKO
+Hz5DLHwwBJKvb24HGAHS9jDBiJmQOTJhjRKdKZvYBj9T+lK3cv9+I9LzpnCFbXDLviFOY4GVaWwozafXLqSNS8anHN1C517Gd5D
vaeaE3Y9vLuoWYv+H657dVkHLrJ+5iXn91ad9EcAYEMKQ7XO7PLk3gOV19yYNyFx4dgic9f9kikrZ1OvRyWj3uyL63MxHYknNTos
cIMKX4kFY2HqO7MYs/9VAtU5dGYgeWThJ0CzblFadozovVsrvJe+ed17teohX43YTUSmLcy2mjy94ZYFbx/Wp/QQMP7/SoAEvGbh
o1rurdlRmOqLbtpavWHxv8m6/2/BRl+lfekv1nm+JLH/vBui98R7hkZa7+76jkViGhnS4a3TXqH+K2k4PzuoMoPEDqj6yaGyIEaY
KEvucUnpJtBBj8zWBvVHSMpqytn6jEPJtHK+qJnMLLmcjJoihjCMYQPr6lAnFyFNVDneNz8tVM8uesK3mrkirmsmlqp9o5R2/y/A
95dnIRBeupl/U4nac4Wn3QaZ1KWX/3R3ePoFXuP0unKJu0h8tfNH9lVUihARymQxu801ZOsow5wrGDniBLLGgFvHQawSqD1JhrRX
KqA++NNMHrhgMqsWxDk95zQKRsKYx57iU/lUwhZmJWhTa+0p4q5DTGlcT4MypEg2mdRa6STquSDiEhJCCiUxFEhHYiRsAspnpGDi
Q2jT9fAcGwtDmsITSI0yY6pKKmqMJMhr7XoqIbR2PLQQQvtAG1prJ8nnTY4AUHhBqdnaLtDj7pmVk/cdBrz6r3SLBFSnnH6FK/cs
mfFWl8/S7ANt1CXrTxWvPX676D3sOJQBC0/dwOXzr6eNGMwlvnNoNtYj82LJ/tGGfvrhL9XT3GhXmltWNTSa37A5sewrT6v/hEH6
X0h5tV+r5DckZ6VfkTKv5scT93t7zixz9x+vlc6IkMFx4d5q6LGnq2Pb95BqdYpYNH8TK4rXsdJbJlbKbzkoV9HIbUIBvaiffQwn
jehC4eAU0JqyLyz2znOo2AHpLTWtLrJRGR7PvPoU9715L75EDsON6/Xp+b1Z5PtY37HhDq9Z8Bhd7G7hQLzYZ2KjiKFRgMQkgI8I
EdIxMHBQSAwCRAmRhkUQmzBh0omQTgpppJFLHg2JygD108NUlMTZqMrYx0FqKKeUYkrZQym7qWQPHGJVY1KPJrTObZHokd+FiC8q
Zu9cJL7cNZVtiRXCoQIhPFFBCa5RTg55HO8O59ToBRx3cX2il4DbxE3WLDj8Gk32SFZNVIGZMHhh6FyMefXof1k9nX0q7rWDJ/im
OONXX935uhPvmnH+9t8jM/r3DsAGoLpmndJ+edmcuf0So40nH71cqPmId6csZGvTxYz95FxsSyIqJF5AIeoKdATMMpNd9+/l2Ze+
YHb1AYqc4ymyCmnezsRfD3J7+ijd5HoT35iqXjv4qG8bcwgYwZ/qGE0e3zxq2RdignBqD6f8NwHvfwHh/+b1v5++lqyt8/RoLdMf
yHq5xN1/7o0N73C7OBfKR3a+zH5jITeI2+gzvDON77PYfr6mfKVB09uhurHLB8P2EzWDBENBGp1rkn1aXH//4XyWvlDM9oqlzPR/
QNRpIQaom2hptKL/VWGyL5fUfIz75SsLeG7tM/ZcviZmlKxLkZnjzm95/dvjlt1U8x80Ov4FEMY7OXpN6tLEtO67nc0j425siMQW
xzFUXZx5o9uxWTu5Xm03lqzfQUWJTQCTTeZsFptT6eAdxc0trqPw5Gyc0xWGqUnM1+iEQCUEdidBxd4abrjgPZrvOZ6zr27K9vJy
7/bXxxgH0xe/1T936EOL988xbO1VO6bt+A2pXOn3QvGoSjFtZdh5yieDOt2u6zXM6uGOqkRxEop70Jbhq3VI9S8fvxZr6drDL2qL
tcQvrw8FZf7+puvakI3A0x4efzcy88uUH621Mmx7VGNavTr1uMWJsfNeNBalvM9nD35GzjFBMe2spdy05Aoa0ovr7Dvo8oFBsJXJ
jtEJd/Sbr9qfms9jGdXvnptxzZUP77rqAL+y4f830xJ/AcaGNDkh74yma0oW9itVxUPLYyWdAX8j2ZJBTYcmTs09k6yaunLVgT1y
9s4VLInNYKeYQ7W5h6CTTjv6MzB8Gu1OaAw9K8gMZ1Pzs8meuYpQtqbRhRDNM/lq+g9cPHYEKeXNKOQkPaT+CVRlrNHXLblEp1oN
lF+IuaXu7hUCISWmlgjhgdZSKFfHNQJpSb9leLYhhWVLYZk2trSlT3poZehALGykqxDZMiSyLTOR5oR02DEMh2pZIQ6yy3B1jUzo
Kul6NTJGmayh0kAnggkRDwshgpWJyoZxN57rw0drX0f39Izz3CYUyoOqRC50F4hFB5eJKl2BJkGlsYcyuYNUmc4p4RGcX3QWzUdm
QS9I5MWB5FhMqmvNPQtE3KRyWRXPXDWPrI0t9QlXZOMr0M4519zqXx786Id9Z288SUwQcRit/6iTk36LxoOhtRYhX/jz1pymfxpR
Ht95qXbHscLtmXG12rp+j1JLtYq/6irnB6XcRELppVrpF7T6+PxpaqDvHnU1P6pJ1n61sEFCVTztKL1Fq8oXtDvxqMWJdgzWgqAO
2SlbmwWOulIP14c67YgiiqzDQlX/CUOmtvr2ryvo/3P7OZzhBiD1aG2mGXXeAvQt+fc7F0TeVA8XTVEzOxSrx1mmDjzvql2DtZrT
a7c6v9HNatmL29U4/0o1KWWP2jFWqx8mLHYvav6wezLXuC38RW7ErON2YZR7J/PVd22VqnlUqw23VTnjm3zr9Oci7SdNG5a5uklK
m9sfK/g0U/z27MJfOiwZwqQo/fg+Of66nwpDVhlEdG8u1O80WZbYepp2lpzmqHfq7VUvsU+NZb7qY12hmthd1N2hZ9XKthWq5HWl
3DWOir3uqoonlCp9yVG6QqvVU/eo85mm5jRKqPhU7b7eaqnbhgH7eub3bfcPbOVa+Rstk0TE4cYhuRn+S2XyITkaLf/yfb+8X/6N
67Cf+ws5P1wu/xkZFYBhCRvg9eND/fWio3UslZbuDSPvdfXn2v30qIVuHs1UCwarRyIL1M7H4qrkXu1My1jvdmaUTvMV7uuUetIV
lrQPT5v8X+se85BcCARaa9Ezb0DXRtHmrwes0G5A+4joXv5h+ukGnyZmNduTmJK+2r3cfkkdz8WqOcepLCtX1REN1EDOUVfztLq0
6Bk17f5V6sCHWu14ylVb79Rq+1tVKr5Gq1XTNqjmdVsoSVC1Zrj7TMYc971G3ydSZIoXtTMX6OE6IITAEBamPHTZWMLGkj7swy6f
9OEz/LWXD0tamNLENAwMQ2D8MmYzeZmGxDLM2svCljY+6ccnA2itpX5RWxfm31y3YaDNyWE7/R2E4QC6IR30VeFx6tuCbfG5Xcqd
VwrnqUt8b6lhTFAjeU2dIu9Ubc2Bqm2wr7qtzVi17apdSj+ulV6llaMTyj2YUO5SRzmzXZWY5yp9UKvi1aXq4cYz1Xi2u9tu086c
W/bWFDJQpwfzXjCTMmb+nhxL8XsGXykMN9XKusuXqDvmga5vJE4a0NJ4575lvJh4lKc+u0z0Tu9GyYcJIseZiKM11e8Ktjyzi5cP
fsDmMsFJnEa3zBwaXeXq4HGSRJlk8TPl6pnvn7M/40Vi5t5N+WbBy0Oi178yrvicvYcp48O9sj9Kc3BR6wkb05nuruiuwwPmFS7Y
Flvd4qHWb7kXdR0h35+whtajohz3XC57Pq9k1EXX8nXpK7xx/9s0WTCY6spS9tRfwQuvPaeL2cwecyvSTaMToxiYPozeBYVEsz1v
yrKvxfNbHjM3Mhdt13xX32r0ylN1500dvDa74q9yiPo3tj+H0gCeKSz6Z53TdlnZ7GE71fYzfG5asy5mX67seo3ba1AbvW+bJ5d8
sIe9u6rFOpbzg/0uIR+cHD+XoRf3o8GVErHTo2YN6GxFZJjFD3dtYcX9Fmeem094MOr2YROtt+W4BZe1PL/vvUs/riyiuWhMiQdQ
SKFexSpRSKEGOPxrgHu5l78jn/9qDbX+J/SG/gd7pobXvS5/8o4nZo3LfbOer7yte5PZS26ZtUEsv24fZ33TG4ccrsx/kguGH03x
qjL3+WkTfd/ygecFqyf1zht4zxsbHtj4f+D1/rMycShCpQxhcmnBA3nz9n553JbY2v77Y7tPQFO3Hi05NTiEXuFBjvZZ4sfyWWJ6
5Q/sVOtFwjyAXwpEwk9TjuP8Y66k26CWRJoLIsEg1QmXlNYm1aqMq2++hkmfTtRh8jkzfCWnZp/gXb79DGuru+nrs7NvOOfNPY/t
a5XdI5TYF3BstukAAf0zUPR3buBnwro7AD2oZLdIvq+IXAo0wG7WCfiZn2vfHyZXT+fH2lfZGibDYaWBhrDokTqsy+bEiiE7Ypu6
JFRVywh1048Ln8yIwnPdrk2K9L54jVy8ZLfYvGkfNVRSzi42sJiqwC66mR04N3M4RYOawoWg6jsYG5Ids5SlCQwz2b+9ghcGLtLB
9Q05894GelXxWu+C5863i8Mrb3Uqqx5RuP8b5Yp/6mWCoEValzNskeGNzvsgUfqodifX2+m2Zoj7yguTlP5Zq+0jYio2VamKGY5a
0He7upaHVCc5Sp0t3lNvklCLCpSrpyRc/a12tgzXsXv52m3FcJ1mNdxRN9D08qfS56b8LS/ojx1VGC0BY3cfHUr1Z69uanTWa8/a
63yfWqE+Onqz0gml4lu1uqD+oyqfDqqTr78aWjRMrXhpvzqNsWoQVyu/9LlI3HSaOmfJsc6X9XY7q5uWxMeF30sU0kcLgtpn+35q
GG5ySq1ndPge/x6Mwr/w+h5u/H60caDtpaYRXhyhnj7NuFV/OXxtfO8TOrH5dtf9uOFB9Shr1Gk8oxr5eqnTrDvV7JNL1b6vE0pv
clXsTaViPzlKa60+GrZFfSLLVOw1rVZdXRnvyEjdJKXoVUtYJFus/oU3+q9c/0fyVGQZwiDNrHtDhi9bz29ZEevKlc69d93rLn9p
p5tDazddtHSfq7fAKT5dJx5J/yTWjN466q+3unX6MX0McejYdf+tezbisKhBbe5CckXegxn1Q01HWKb5MXDQwq/7hk7Rr9T7JPFV
01XxuzJfdTrZp7rZFLoZNHZTjToqh+bqVK5T10ReUhPPmK42vnZAVXyqlVqplbNKqyfufE5ZflMB7hmBW92lacWJzkZvjcHSAVnD
2wshKWS4fRg57W9c+t98jZZ/HRUwhU2Rr2eTsBW9GOz5Fln6hMCFemLHad72UYn4ust0YnLX7ep+Y5G6k0XqKr5UnblaNZED1KU8
qOY1Wq/2X6FV+fda6RWuir/iqqpnHKXLtNq/vVzd2uor9SCb1PZ7tDv51AVOFoU6LVzn5lqdYtX+T0fWfyA0y9D8y9paVvTgQHmV
u+d6JzG9KO724Hb34VvGu3qXVvtviavqt5Xa/axSH7dbojrSS+XYXdQlTFMTKVUrzoor/b12D9ys46/6Vyb6cqduaPapaB8d8PzN
+RPq/g5B4d+VDTYFkoxAzqVg62eP+8jZ0lWr582V6uCGCqUrtHqh9UzVg0vUNZnPqdPTb1EtjdbqlQffU2c1fkRdH3reDRlhN0yG
O97/qTM3fUf8rrTH4/VpqQFtW/acZuG2Qyzh+2vFJX6nsviLwt3cXfsbphScbhuBmak00YOte/TMc4tj8fe1u/KKmHrD3q3uYIbq
al6mjrZHqPtC09SqW6qV2qyUWuIqd49S1fvi6tW0berb3BpVM0Wr1wuWOHniGG9Q9qg+yb/yvvFPgoH4jYCS1Fdpn0DOGJwxQn+Z
uTfeKusk56e35jj1Ulu4+VaRO63uZndlYWVsqLzKk0R1Wij/1QdbTMz4W8bO7ylC99epi2OjAxtH7OiNwCJAF5pH6XvSn3Y/y14Q
fy3rO+fS4GNuO4a6UZq5tkhVuaKRKqSnujjlcfVEn4/VnBeXKWeBVnq1Vt+/PUs1zGumALe/fam7KFoS72j10Rjs6pM+9Jj/Q6fh
0L3/YjAJBLq79reOHnuqaaRPMsjeVcgJ+taGL+j55+6N77pLJ+afE3Ontj2oJvp2qZv4XnXnLnWMOFeN4i71Ymi2Wn1+QpV/pZX+
0VHO547SB7Xat7lcXdJ4srqT+Wr7Tdp96ZivnCD5ul6k4FpTWLUg/GfS3f9LB3r1yToS9WctbMKxesGw4tjSQTG3D3e7I3tf7eqY
dmOfJNySZ2Jq2onr1flijMry1VHNGKCu5if1TuNiVXyPVsVjtfN6g2XxwTykm4k+8YJAt9eG513eXgp5eJ7pz/bwJCBH5j2YgWBD
j7SzvK3naecVNqo5D+1UWmv19dDV6hGmq3F1flA9gmcrQDUzWqtR3c5Xz1/5ubqC193zU25yJ6V849wXfMbJEDka0Ia0fmocaDH4
sDy6/ANFFQ7lBAGBvlhbTdJbnJNiZW+tTzd9deQlb9PtsXjiHe0sHV6tJlGhzuY11dA6QZ3KneqzEzaomjVa6f2u0pVarXnrgHqA
xeq7onK1/zmdGMwYnWbX/0mP1ubvDIwkQIfosUVAyQcNZru3yPcT15/7gNOhzVFOHoXuqrwq552cObEsmmoE2xumtDy9Vnke8nr/
CLJhHO4Z/tB9s79RuPkwaVqfABVpZOuhwXP06w0+ik9pODt+W+orTlvzRBf8ComKmmmqteilultnq7v7P6WWTFqv9F6tiucdUN1a
9FCA6uwfoGZlbYh3MI7VGOwcWuecVoc/g//je/9Fl9rSx4kNTmkYDeXcECRnbRO66cvTHtffDNrmVjyt4zUTtbPtFq1mtY+rN83N
6iJeUW3FGao/16rnAnPV2vNiKv6jVnqbo3SJVptm71FD055X14jv1IoRrjOm8JWEIM0rTD36XJm8dZMj698X+pMY5EQaPuQnS799
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
)"

; The logo: a custom image picked in Settings -> Appearance, or the built-in
; XZX logo (stored inside this script and written to a temp file once).
CoopLogoPath() {
    static embedded := ""
    if (embedded != "")
        return embedded
    target := A_Temp "\xzx_macro_logo.png"
    if !WriteEmbeddedLogo(COOP_LOGO_B64, target)
        return ""
    embedded := target
    return embedded
}

; Fit an image of iw x ih inside a w x h box without stretching, centred.
FitRect(iw, ih, x, y, w, h) {
    if (iw <= 0 || ih <= 0)
        return [x, y, w, h]
    k := Min(w / iw, h / ih)
    fw := Round(iw * k), fh := Round(ih * k)
    return [x + (w - fw) // 2, y + (h - fh) // 2, fw, fh]
}

LogoImage() {
    static cache := Map()
    path := CoopLogoPath()
    if (path = "")
        return 0
    if cache.Has(path)
        return cache[path]
    ; Loaded from memory, not from the file: GDI+ keeps a file it opened
    ; locked, which would stop a new logo from being saved over it.
    pImg := 0
    try {
        data := FileRead(path, "RAW")
        pStream := DllCall("Shlwapi\SHCreateMemStream", "Ptr", data, "UInt", data.Size, "Ptr")
        if pStream
            DllCall("Gdiplus\GdipCreateBitmapFromStream", "Ptr", pStream, "Ptr*", &pImg)
    }
    if pImg
        cache[path] := pImg   ; the stream stays alive with the image
    return pImg
}

WriteEmbeddedLogo(b64, path) {
    ; CRYPT_STRING_BASE64 = 0x01
    size := 0
    if !DllCall("Crypt32\CryptStringToBinaryW", "WStr", b64, "UInt", 0,
        "UInt", 0x01, "Ptr", 0, "UInt*", &size, "Ptr", 0, "Ptr", 0) || !size
        return false
    buf := Buffer(size, 0)
    if !DllCall("Crypt32\CryptStringToBinaryW", "WStr", b64, "UInt", 0,
        "UInt", 0x01, "Ptr", buf, "UInt*", &size, "Ptr", 0, "Ptr", 0)
        return false
    try {
        if FileExist(path)
            FileSetAttrib("-R", path)
        f := FileOpen(path, "w")
        if !IsObject(f)
            return false
        f.RawWrite(buf, size)
        f.Close()
        try FileSetAttrib("+R", path)
    } catch
        return false
    return FileExist(path) ? true : false
}



ApplyRoundedCorners(hwnd, width, height, radius := 24) {
    if (width <= 0 || height <= 0)
        return
    ; Windows 11 DWM corners are anti-aliased, which keeps the window edge
    ; smooth; the region fallback covers older systems.
    try {
        pref := Buffer(4, 0)
        NumPut("Int", 2, pref)   ; DWMWCP_ROUND
        if DllCall("Dwmapi\DwmSetWindowAttribute",
            "Ptr", hwnd, "UInt", 33, "Ptr", pref, "UInt", 4, "UInt") = 0
            return
    }
    try {
        rgn := DllCall("CreateRoundRectRgn"
            , "Int", 0
            , "Int", 0
            , "Int", width + 1
            , "Int", height + 1
            , "Int", radius
            , "Int", radius
            , "Ptr")
        if rgn {
            DllCall("SetWindowRgn", "Ptr", hwnd, "Ptr", rgn, "Int", true)
        }
    }
}

; Freeze painting while many controls change, then repaint once. This is what
; stops windows flashing to black during tab swaps and bulk restyles.
; WM_SETREDRAW is NOT used here any more. On a top-level window it briefly
; clears WS_VISIBLE, and with stream proof (capture exclusion) on, DWM drops
; and re-adds the whole window - that was the flash on every tab swap.
; Nothing paints while our code runs anyway, so we just repaint once at the end.
SuspendRedraw(hwnd) {
}

ResumeRedraw(hwnd) {
    ; RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
    try DllCall("User32\RedrawWindow", "Ptr", hwnd, "Ptr", 0, "Ptr", 0, "UInt", 0x185)
}

; The window itself paints the backdrop image whenever Windows asks it to clear
; its background. Before, that clear was plain black (or skipped), so any spot
; a control had just left showed black for a moment until the backdrop picture
; caught up - the small black flashes when switching menus.
WM_ERASEBKGND_SKIP(wParam, lParam, msg, hwnd) {
    global LoginGui, LoginBg
    pic := 0
    if (UI.Has("gui") && UI.Has("bg") && hwnd = UI["gui"].Hwnd) {
        pic := UI["bg"].Hwnd
    } else if (IsObject(LoginGui) && IsObject(LoginBg) && hwnd = LoginGui.Hwnd) {
        pic := LoginBg.Hwnd
    } else {
        return
    }
    hb := pic ? SendMessage(0x173, 0, 0, pic) : 0          ; STM_GETIMAGE
    if !hb
        return 1                                          ; nothing yet: don't paint black
    rc := Buffer(16, 0)
    DllCall("GetClientRect", "Ptr", hwnd, "Ptr", rc)
    w := NumGet(rc, 8, "Int"), h := NumGet(rc, 12, "Int")
    mdc := DllCall("Gdi32\CreateCompatibleDC", "Ptr", wParam, "Ptr")
    prev := DllCall("Gdi32\SelectObject", "Ptr", mdc, "Ptr", hb, "Ptr")
    DllCall("Gdi32\BitBlt", "Ptr", wParam, "Int", 0, "Int", 0, "Int", w, "Int", h,
        "Ptr", mdc, "Int", 0, "Int", 0, "UInt", 0x00CC0020)  ; SRCCOPY
    DllCall("Gdi32\SelectObject", "Ptr", mdc, "Ptr", prev)
    DllCall("Gdi32\DeleteDC", "Ptr", mdc)
    return 1
}

; Returns the strip controls it created, so callers that draw a gradient inside
; a page (rather than as the window backdrop) can register them with that page.
; Mirrored three-stop gradient: the top colour fades to the bottom stop by
; the middle of the window and back again, so the default reads pink ->
; black -> pink.
; A single stretched 1px-wide bitmap instead of a stack of coloured strip
; controls: one control paints the whole surface in one blit, so windows
; open and repaint without lag, and the fade is per-pixel-row smooth.
; One GDI+-smoothed picture paints the whole window: a soft violet glow from
; the top-left, a fainter one in the bottom-right, fading into near-black.
; The colour field is computed on a tiny grid and upscaled with bicubic
; filtering, so it is perfectly smooth and costs almost nothing to build.
GradientBackdrop(dlg, x, y, w, h, glowRgb, baseRgb, steps := 0) {
    if (w < 2 || h < 2 || !GdipReady())
        return []
    gw := 40
    gh := Max(8, Round(gw * h / w))
    pSmall := 0, pBig := 0, pG := 0, pAttr := 0, hbm := 0
    try {
        if DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", gw, "Int", gh,
            "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pSmall) || !pSmall
            return []
        Loop gh {
            j := A_Index - 1
            v := j / (gh - 1)
            Loop gw {
                i := A_Index - 1
                uu := i / (gw - 1)
                d1 := Sqrt((uu - 0.15) ** 2 + ((v + 0.05) * 1.3) ** 2)
                d2 := Sqrt((uu - 1.0) ** 2 + ((v - 1.05) * 1.3) ** 2)
                glow := Max(0, 1 - d1 / 1.1) ** 1.6 + 0.4 * Max(0, 1 - d2 / 0.85) ** 2
                col := BlendRgb(baseRgb, glowRgb, Min(1, glow))
                DllCall("Gdiplus\GdipBitmapSetPixel", "Ptr", pSmall,
                    "Int", i, "Int", j, "UInt", 0xFF000000 | col)
            }
        }
        if DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", w, "Int", h,
            "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pBig) || !pBig
            return []
        DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBig, "Ptr*", &pG)
        DllCall("Gdiplus\GdipSetInterpolationMode", "Ptr", pG, "Int", 7)
        DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)
        DllCall("Gdiplus\GdipCreateImageAttributes", "Ptr*", &pAttr)
        ; TileFlipXY: the edges sample mirrored pixels instead of transparent
        ; black, so the border of the window does not darken.
        DllCall("Gdiplus\GdipSetImageAttributesWrapMode", "Ptr", pAttr,
            "Int", 3, "UInt", 0, "Int", 0)
        DllCall("Gdiplus\GdipDrawImageRectRectI", "Ptr", pG, "Ptr", pSmall,
            "Int", 0, "Int", 0, "Int", w, "Int", h,
            "Int", 0, "Int", 0, "Int", gw, "Int", gh,
            "Int", 2, "Ptr", pAttr, "Ptr", 0, "Ptr", 0)
        DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBig,
            "Ptr*", &hbm, "UInt", 0xFF000000)
    } finally {
        if pAttr
            DllCall("Gdiplus\GdipDisposeImageAttributes", "Ptr", pAttr)
        if pG
            DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
        if pBig
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pBig)
        if pSmall
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pSmall)
    }
    if !hbm
        return []
    pic := 0
    try pic := dlg.AddPicture("x" x " y" y " w" w " h" h, "HBITMAP:*" hbm)
    DllCall("Gdi32\DeleteObject", "Ptr", hbm)
    return pic ? [pic] : []
}

GdipReady() {
    static tok := 0
    if tok
        return true
    try {
        if !DllCall("GetModuleHandleW", "Str", "gdiplus", "Ptr")
            DllCall("LoadLibraryW", "Str", "gdiplus", "Ptr")
        si := Buffer(A_PtrSize = 8 ? 24 : 16, 0)
        NumPut("UInt", 1, si, 0)
        t := 0
        if DllCall("Gdiplus\GdiplusStartup", "Ptr*", &t, "Ptr", si.Ptr, "Ptr", 0) = 0
            tok := t
    }
    return tok != 0
}

; Anti-aliased rounded card with an optional 1px border, as one picture.
; Falls back to a plain rounded panel if GDI+ is unavailable.
Card(dlg, x, y, w, h, radius, fillHex, borderHex := "") {
    hbm := CardBitmap(w, h, radius, Integer("0x" fillHex),
        borderHex = "" ? -1 : Integer("0x" borderHex))
    if hbm {
        pic := 0
        try pic := dlg.AddPicture(Box(x, y, w, h) " BackgroundTrans", "HBITMAP:*" hbm)
        DllCall("Gdi32\DeleteObject", "Ptr", hbm)
        if IsObject(pic)
            return pic
    }
    return UiPanel(dlg, x, y, w, h, fillHex, radius * 2)
}

CardBitmap(w, h, radius, fill, border := -1) {
    if (w < 2 || h < 2 || !GdipReady())
        return 0
    pBmp := 0, pG := 0, hbm := 0
    try {
        if DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", w, "Int", h,
            "Int", 0, "UInt", 0xE200B, "Ptr", 0, "Ptr*", &pBmp) || !pBmp
            return 0
        DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp, "Ptr*", &pG)
        DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)
        DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)
        if (border >= 0) {
            FillRoundPath(pG, 0, 0, w, h, radius, border)
            FillRoundPath(pG, 1, 1, w - 2, h - 2, Max(0, radius - 1), fill)
        } else {
            FillRoundPath(pG, 0, 0, w, h, radius, fill)
        }
        DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBmp, "Ptr*", &hbm, "UInt", 0)
    } finally {
        if pG
            DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
        if pBmp
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pBmp)
    }
    return hbm
}

FillRoundPath(pG, x, y, w, h, radius, rgb) {
    FillRoundA(pG, x, y, w, h, radius, 0xFF000000 | (rgb & 0xFFFFFF))
}

; ---- GDI+ drawing helpers (login + cards) ---------------------------------
RoundRectPath(x, y, w, h, radius) {
    pPath := 0
    DllCall("Gdiplus\GdipCreatePath", "Int", 0, "Ptr*", &pPath)
    d := Min(radius * 2, w, h)
    if (d <= 0) {
        DllCall("Gdiplus\GdipAddPathRectangle", "Ptr", pPath,
            "Float", x, "Float", y, "Float", w, "Float", h)
        return pPath
    }
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", x, "Float", y,
        "Float", d, "Float", d, "Float", 180, "Float", 90)
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", x + w - d, "Float", y,
        "Float", d, "Float", d, "Float", 270, "Float", 90)
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", x + w - d, "Float", y + h - d,
        "Float", d, "Float", d, "Float", 0, "Float", 90)
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", x, "Float", y + h - d,
        "Float", d, "Float", d, "Float", 90, "Float", 90)
    DllCall("Gdiplus\GdipClosePathFigure", "Ptr", pPath)
    return pPath
}

FillRoundA(pG, x, y, w, h, radius, argb) {
    pPath := RoundRectPath(x, y, w, h, radius), pBrush := 0
    try {
        DllCall("Gdiplus\GdipCreateSolidFill", "UInt", argb, "Ptr*", &pBrush)
        DllCall("Gdiplus\GdipFillPath", "Ptr", pG, "Ptr", pBrush, "Ptr", pPath)
    } finally {
        if pBrush
            DllCall("Gdiplus\GdipDeleteBrush", "Ptr", pBrush)
        if pPath
            DllCall("Gdiplus\GdipDeletePath", "Ptr", pPath)
    }
}

StrokeRoundA(pG, x, y, w, h, radius, argb, width := 1) {
    pPath := RoundRectPath(x, y, w, h, radius), pPen := 0
    try {
        DllCall("Gdiplus\GdipCreatePen1", "UInt", argb, "Float", width, "Int", 2, "Ptr*", &pPen)
        DllCall("Gdiplus\GdipDrawPath", "Ptr", pG, "Ptr", pPen, "Ptr", pPath)
    } finally {
        if pPen
            DllCall("Gdiplus\GdipDeletePen", "Ptr", pPen)
        if pPath
            DllCall("Gdiplus\GdipDeletePath", "Ptr", pPath)
    }
}

LinearFillRound(pG, x, y, w, h, radius, argb1, argb2) {
    pPath := RoundRectPath(x, y, w, h, radius), pBrush := 0
    p1 := Buffer(8), p2 := Buffer(8)
    NumPut("Float", x, "Float", y, p1)
    NumPut("Float", x + w, "Float", y + h, p2)
    try {
        DllCall("Gdiplus\GdipCreateLineBrush", "Ptr", p1, "Ptr", p2,
            "UInt", argb1, "UInt", argb2, "Int", 1, "Ptr*", &pBrush)
        DllCall("Gdiplus\GdipFillPath", "Ptr", pG, "Ptr", pBrush, "Ptr", pPath)
    } finally {
        if pBrush
            DllCall("Gdiplus\GdipDeleteBrush", "Ptr", pBrush)
        if pPath
            DllCall("Gdiplus\GdipDeletePath", "Ptr", pPath)
    }
}

; Soft radial glow: `argb` at the centre fading to fully transparent.
GlowEllipse(pG, x, y, w, h, argb) {
    pPath := 0, pBrush := 0
    try {
        DllCall("Gdiplus\GdipCreatePath", "Int", 0, "Ptr*", &pPath)
        DllCall("Gdiplus\GdipAddPathEllipse", "Ptr", pPath,
            "Float", x, "Float", y, "Float", w, "Float", h)
        DllCall("Gdiplus\GdipCreatePathGradientFromPath", "Ptr", pPath, "Ptr*", &pBrush)
        DllCall("Gdiplus\GdipSetPathGradientCenterColor", "Ptr", pBrush, "UInt", argb)
        edge := Buffer(4)
        NumPut("UInt", argb & 0x00FFFFFF, edge)
        count := 1
        DllCall("Gdiplus\GdipSetPathGradientSurroundColorsWithCount", "Ptr", pBrush,
            "Ptr", edge, "Int*", &count)
        DllCall("Gdiplus\GdipFillPath", "Ptr", pG, "Ptr", pBrush, "Ptr", pPath)
    } finally {
        if pBrush
            DllCall("Gdiplus\GdipDeleteBrush", "Ptr", pBrush)
        if pPath
            DllCall("Gdiplus\GdipDeletePath", "Ptr", pPath)
    }
}

DrawLogoInto(pG, x, y, w, h) {
    pLogo := LogoImage()
    if !pLogo
        return
    iw := 0, ih := 0
    DllCall("Gdiplus\GdipGetImageWidth", "Ptr", pLogo, "UInt*", &iw)
    DllCall("Gdiplus\GdipGetImageHeight", "Ptr", pLogo, "UInt*", &ih)
    r := FitRect(iw, ih, x, y, w, h)
    DllCall("Gdiplus\GdipSetInterpolationMode", "Ptr", pG, "Int", 7)
    DllCall("Gdiplus\GdipDrawImageRectI", "Ptr", pG, "Ptr", pLogo, "Int", r[1], "Int", r[2], "Int", r[3], "Int", r[4])
}

; The window gradient painted straight into an existing GDI+ surface.
DrawBackdropInto(pG, w, h, glowRgb, baseRgb) {
    gw := 40
    gh := Max(8, Round(gw * h / w))
    pSmall := 0, pAttr := 0
    try {
        DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", gw, "Int", gh,
            "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pSmall)
        if !pSmall
            return
        Loop gh {
            j := A_Index - 1
            v := j / (gh - 1)
            Loop gw {
                i := A_Index - 1
                uu := i / (gw - 1)
                d1 := Sqrt((uu - 0.15) ** 2 + ((v + 0.05) * 1.3) ** 2)
                d2 := Sqrt((uu - 1.0) ** 2 + ((v - 1.05) * 1.3) ** 2)
                glow := Max(0, 1 - d1 / 1.1) ** 1.6 + 0.4 * Max(0, 1 - d2 / 0.85) ** 2
                DllCall("Gdiplus\GdipBitmapSetPixel", "Ptr", pSmall, "Int", i, "Int", j,
                    "UInt", 0xFF000000 | BlendRgb(baseRgb, glowRgb, Min(1, glow)))
            }
        }
        DllCall("Gdiplus\GdipSetInterpolationMode", "Ptr", pG, "Int", 7)
        DllCall("Gdiplus\GdipCreateImageAttributes", "Ptr*", &pAttr)
        DllCall("Gdiplus\GdipSetImageAttributesWrapMode", "Ptr", pAttr, "Int", 3, "UInt", 0, "Int", 0)
        DllCall("Gdiplus\GdipDrawImageRectRectI", "Ptr", pG, "Ptr", pSmall,
            "Int", 0, "Int", 0, "Int", w, "Int", h, "Int", 0, "Int", 0, "Int", gw, "Int", gh,
            "Int", 2, "Ptr", pAttr, "Ptr", 0, "Ptr", 0)
    } finally {
        if pAttr
            DllCall("Gdiplus\GdipDisposeImageAttributes", "Ptr", pAttr)
        if pSmall
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pSmall)
    }
}

BlendRgb(fromRgb, toRgb, amount) {
    amount := Max(0, Min(1, amount))
    fr := (fromRgb >> 16) & 0xFF
    fg := (fromRgb >> 8) & 0xFF
    fb := fromRgb & 0xFF
    tr := (toRgb >> 16) & 0xFF
    tg := (toRgb >> 8) & 0xFF
    tb := toRgb & 0xFF
    return (Round(fr + (tr - fr) * amount) << 16)
        | (Round(fg + (tg - fg) * amount) << 8)
        | Round(fb + (tb - fb) * amount)
}

; The whole UI is derived from ONE accent colour (C.MENU_COLOR), picked in
; Settings -> Appearance from presets or a custom colour.
RefreshThemeColors() {
    global THEME_ACCENT, THEME_SOFT, THEME_MUTED, THEME_GRADIENT, THEME_GRADIENT_END
    global THEME_PANEL, THEME_FIELD, THEME_INPUT, THEME_BORDER, THEME_BUTTON_TEXT
    global BTN_IDLE, BTN_IDLE_HOVER, NAV_IDLE, NAV_IDLE_HOVER, BTN_QUIET, BTN_QUIET_HOVER
    global BRAND_ACCENT, BRAND_ACCENT_OFF, TXT_SUB, TXT_DIM
    global ACC_RGB, ACC2_RGB, ACCL_RGB, SOFT_RGB, PANEL_RGB, FIELD_RGB, INPUT_RGB, BORDER_RGB
    acc := (C.MENU_COLOR & 0xFFFFFF) || 0x9D4EFF
    base := 0x0B0912
    ACC_RGB    := acc
    ACC2_RGB   := BlendRgb(acc, 0x000000, 0.28)
    ACCL_RGB   := BlendRgb(acc, 0xFFFFFF, 0.30)
    SOFT_RGB   := BlendRgb(acc, 0xFFFFFF, 0.72)
    PANEL_RGB  := BlendRgb(base, acc, 0.07)
    FIELD_RGB  := BlendRgb(base, acc, 0.12)
    INPUT_RGB  := BlendRgb(base, acc, 0.20)
    BORDER_RGB := BlendRgb(base, acc, 0.26)
    hex(v) => Format("{:06X}", v)
    THEME_ACCENT := hex(acc)
    BTN_IDLE := hex(acc)
    BTN_IDLE_HOVER := hex(BlendRgb(acc, 0xFFFFFF, 0.15))
    BRAND_ACCENT := hex(ACCL_RGB)
    BRAND_ACCENT_OFF := hex(BlendRgb(0x7A7790, acc, 0.15))
    THEME_SOFT := hex(SOFT_RGB)
    THEME_MUTED := hex(BlendRgb(acc, 0xFFFFFF, 0.48))
    THEME_GRADIENT := BlendRgb(0x000000, acc, 0.34)
    THEME_GRADIENT_END := BlendRgb(0x050407, acc, 0.03)
    THEME_PANEL := hex(PANEL_RGB)
    THEME_FIELD := hex(FIELD_RGB)
    THEME_INPUT := hex(INPUT_RGB)
    THEME_BORDER := hex(BORDER_RGB)
    THEME_BUTTON_TEXT := IsLightColor(acc) ? "15121C" : "FFFFFF"
    NAV_IDLE := THEME_FIELD
    NAV_IDLE_HOVER := THEME_INPUT
    BTN_QUIET := hex(BlendRgb(base, acc, 0.15))
    BTN_QUIET_HOVER := hex(BlendRgb(base, acc, 0.26))
    TXT_SUB := hex(BlendRgb(0xA29FB2, acc, 0.16))
    TXT_DIM := hex(BlendRgb(0x6B6880, acc, 0.16))

    STATE_COLORS["IDLE"] := THEME_SOFT
    STATE_COLORS["SEARCHING DIAL"] := BRAND_ACCENT
    STATE_COLORS["WAITING BAR"] := THEME_MUTED
    STATE_COLORS["ERROR"] := THEME_DANGER
    LED_COLORS["green"] := BRAND_ACCENT
    LED_COLORS["amber"] := THEME_SOFT
    LED_COLORS["red"] := THEME_DANGER
    HIST_COLORS["ok"] := hex(BlendRgb(acc, 0xFFFFFF, 0.15))
    HIST_COLORS[""] := hex(BlendRgb(base, acc, 0.16))
}

OpenDiscordInvite(*) {
    try Run "https://" DISCORD_INVITE
}

IsLightColor(rgb) {
    r := (rgb >> 16) & 0xFF
    g := (rgb >> 8) & 0xFF
    b := rgb & 0xFF
    return (r * 299 + g * 587 + b * 114) >= 160000
}

ShowWelcomeLogin() {
    global ACCESS_CODE, AccessGranted, LoginGui, LoginCodeEdit, LoginStatus, LoginAttempts
    global LoginButton, LoginInputSurface, LoginButtonState, LoginHovering
    global LoginBg, LoginFocused, LoginReveal, LoginRevealBtn, LoginDiscordBtn, LoginDiscordHover

    AccessGranted := false
    LoginAttempts := 0
    LoginButtonState := "default"
    LoginHovering := false
    LoginDiscordHover := false
    LoginFocused := false
    LoginReveal := false
    geo := LOGIN_GEO

    LoginGui := Gui("+AlwaysOnTop +OwnDialogs -MaximizeBox -MinimizeBox -Caption +ToolWindow", "Metal Detector")
    TrackWindow(LoginGui.Hwnd)
    LoginGui.BackColor := Format("{:06X}", THEME_GRADIENT_END)
    LoginGui.MarginX := 0
    LoginGui.MarginY := 0

    ; The whole look - gradient, logo glow, logo, glass card, Discord pill,
    ; key box and button - is ONE rendered image. Only text and the edit sit
    ; on top, so there are no stacked pictures to leave black corners.
    LoginBg := LoginGui.AddPicture("x0 y0 w" geo.w " h" geo.h, "")
    LoginInputSurface := LoginBg

    LoginGui.SetFont("s12 c" TXT_DIM, FONT_UI)
    closeBtn := LoginGui.AddText("x" (geo.w - 40) " y12 w28 h28 0x200 Center BackgroundTrans", "✕")
    closeBtn.OnEvent("Click", LoginExit)

    LoginGui.SetFont("s17 norm c" TXT_MAIN, FONT_SEMI)
    LoginGui.AddText("x20 y234 w" (geo.w - 40) " h34 Center BackgroundTrans", "Welcome back")
    LoginGui.SetFont("s9 norm c" TXT_SUB, FONT_UI)
    LoginGui.AddText("x20 y272 w" (geo.w - 40) " h20 Center BackgroundTrans",
        "Link your Discord, then enter your access key")

    LoginGui.SetFont("s7 bold c" TXT_DIM, FONT_UI)
    LoginGui.AddText("x" geo.dx " y" (geo.dy - 22) " w200 h16 BackgroundTrans", "1   DISCORD ACCOUNT")
    LoginGui.AddText("x" geo.fx " y" (geo.fy - 22) " w200 h16 BackgroundTrans", "2   ACCESS KEY")

    LoginGui.SetFont("s10 norm cFFFFFF", FONT_SEMI)
    LoginDiscordBtn := LoginGui.AddText("x" geo.dx " y" geo.dy " w" geo.dw " h" geo.dh
        " 0x200 Center BackgroundTrans", "Link Discord account")
    LoginDiscordBtn.OnEvent("Click", LoginLinkClick)

    LoginGui.SetFont("s11 c" TXT_MAIN, "Consolas")
    LoginCodeEdit := LoginGui.AddEdit(
        "x" (geo.fx + 18) " y" (geo.fy + 15) " w" (geo.fw - 96) " h20 Password Background" THEME_INPUT
            " -E0x200 -Multi -VScroll -HScroll", "")
    LoginCodeEdit.OnEvent("Change", LoginClearStatus)

    LoginGui.SetFont("s7 bold c" BRAND_ACCENT, FONT_UI)
    LoginRevealBtn := LoginGui.AddText("x" (geo.fx + geo.fw - 66) " y" (geo.fy + 15)
        " w52 h20 0x200 Center BackgroundTrans", "SHOW")
    LoginRevealBtn.OnEvent("Click", LoginToggleReveal)

    LoginGui.SetFont("s10 norm cFFFFFF", FONT_SEMI)
    LoginButton := LoginGui.AddText("x" geo.bx " y" geo.by " w" geo.bw " h" geo.bh
        " 0x200 Center BackgroundTrans", "Unlock")
    LoginButton.OnEvent("Click", TryLogin)

    hiddenDefault := LoginGui.AddButton("x-100 y-100 w1 h1 Hidden Default", "")
    hiddenDefault.OnEvent("Click", TryLogin)

    LoginGui.SetFont("s8 c" TXT_SUB, FONT_UI)
    ; Status lives INSIDE the card, under Unlock, with its own reserved line -
    ; it can never run into the footer whatever it says.
    LoginStatus := LoginGui.AddText("x" geo.cx + 12 " y" (geo.by + geo.bh + 12) " w" (geo.cw - 24)
        " h20 0x200 Center BackgroundTrans", "LINK YOUR DISCORD TO CONTINUE")

    ; Footer: one clickable support line, then the credit - each a single
    ; centred control, so nothing can overlap at any DPI.
    LoginGui.SetFont("s8 c" BRAND_ACCENT, FONT_UI)
    footY := geo.cy + geo.ch + 22
    discordLink := LoginGui.AddText("x20 y" footY " w" (geo.w - 40)
        " h20 0x200 Center BackgroundTrans", "Need help?  Join " DISCORD_INVITE)
    LoginGui.SetFont("s7 c" TXT_DIM, FONT_UI)
    LoginGui.AddText("x20 y" (footY + 28) " w" (geo.w - 40) " h16 0x200 Center BackgroundTrans",
        "XZX Macro  ·  by Coop")
    discordLink.OnEvent("Click", (*) => OpenDiscordInvite())

    LoginGui.OnEvent("Close", LoginExit)
    LoginGui.OnEvent("Escape", LoginExit)

    S.dragMap[LoginGui.Hwnd] := "welcome"
    for interactive in [closeBtn, LoginCodeEdit, LoginButton, LoginRevealBtn, discordLink, LoginDiscordBtn]
        S.noDrag[interactive.Hwnd] := true

    UpdateLoginButtonVisual(true)
    LoginGui.Show("x" Max(0, Round((A_ScreenWidth - geo.w) / 2))
        . " y" Max(0, Round((A_ScreenHeight - geo.h) / 2))
        . " w" geo.w " h" geo.h)
    SetStreamProof(StreamProofEnabled)
    UpdateStreamProofUI(StreamProofEnabled)
    ApplyRoundedCorners(LoginGui.Hwnd, geo.w, geo.h, 40)
    InstallLoginEditCapsLockSuppression()
    SetTimer(LoginHoverTick, 60)
    SetTimer(LoginBalloonSweep, 60)
    ; A link from an earlier launch is remembered - check it once the window is up.
    SetTimer(LoginRestoreLink, -150)

    while !AccessGranted
        Sleep(50)

    SetTimer(LoginHoverTick, 0)
    SetTimer(LoginBalloonSweep, 0)
    SetTimer(LoginAuthPoll, 0)
    RemoveLoginEditCapsLockSuppression()
    try S.dragMap.Delete(LoginGui.Hwnd)
    try LoginGui.Destroy()
    LoginGui := ""
}

; ---- Discord linking -------------------------------------------------------
; state: "none" (not linked) | "pending" (browser open, waiting) | "linked" |
;        "off" (the bot has linking switched off - key only)
LicenseHttp(method, path, body := "", timeoutMs := 6000) {
    global LICENSE_API_URL
    if InStr(LICENSE_API_URL, "PASTE-YOUR-BOT-ADDRESS")
        return "DENIED|KEY SERVER NOT SET UP - RUN /key setup IN DISCORD"
    try {
        http := ComObject("WinHttp.WinHttpRequest.5.1")
        http.Open(method, RTrim(LICENSE_API_URL, "/") path, false)
        http.SetTimeouts(timeoutMs, timeoutMs, timeoutMs, timeoutMs)
        http.SetRequestHeader("Content-Type", "application/x-www-form-urlencoded")
        http.SetRequestHeader("User-Agent", "XZXMacro/2.0")
        http.Send(body)
        if (http.Status != 200)
            return "DENIED|SERVER ERROR " http.Status
        return Trim(http.ResponseText)
    } catch {
        return "DENIED|SERVER UNREACHABLE"
    }
}

LoginSetLink(state, name := "") {
    global LoginLinkState, LoginLinkName, LoginDiscordBtn
    LoginLinkState := state
    LoginLinkName := name
    label := state = "linked" ? "✓   Linked as " name "   ·   change"
        : state = "pending" ? "Waiting for Discord…   (click to reopen)"
        : state = "off" ? "Discord linking is off"
        : "Link Discord account"
    try LoginDiscordBtn.Text := label
    UpdateLoginButtonVisual(true)
}

LoginSetStatus(text, color := "") {
    global LoginStatus
    try LoginStatus.Text := text
    try LoginStatus.SetFont("c" (color = "" ? TXT_SUB : color))
}

LoginRestoreLink() {
    global LoginSession
    saved := ""
    try saved := IniRead(C.INI_FILE, "Auth", "Session", "")
    if (saved = "") {
        r := LicenseHttp("GET", "/api/auth/status?s=none", "", 4000)
        ; OFF = linking switched off; 404 = a bot version without linking.
        if (r = "OFF" || InStr(r, "SERVER ERROR 404")) {
            LoginSetLink("off")
            LoginSetStatus("KEY REQUIRED")
        }
        return
    }
    r := LicenseHttp("GET", "/api/auth/status?s=" saved, "", 4000)
    parts := StrSplit(r, "|")
    if (parts[1] = "LINKED") {
        LoginSession := saved
        LoginSetLink("linked", parts.Length >= 2 ? parts[2] : "")
        LoginSetStatus("KEY REQUIRED")
        try LoginCodeEdit.Focus()
    } else if (parts[1] = "OFF" || InStr(r, "SERVER ERROR 404")) {
        LoginSetLink("off")
        LoginSetStatus("KEY REQUIRED")
    } else {
        try IniDelete(C.INI_FILE, "Auth", "Session")
    }
}

LoginLinkClick(*) {
    global LoginSession, LoginLinkState, LoginLinkUrl, LoginPollStart
    if (LoginLinkState = "off")
        return
    if (LoginLinkState = "pending" && LoginLinkUrl != "") {
        try Run(LoginLinkUrl)
        return
    }
    LoginSetStatus("OPENING DISCORD…")
    r := LicenseHttp("POST", "/api/auth/new")
    parts := StrSplit(r, "|")
    if (parts[1] = "OFF" || InStr(r, "SERVER ERROR 404")) {
        LoginSetLink("off")
        LoginSetStatus("KEY REQUIRED")
        return
    }
    if (parts[1] != "OK" || parts.Length < 3) {
        LoginSetStatus(parts.Length >= 2 ? parts[2] : "COULDN'T REACH THE KEY SERVER", THEME_DANGER)
        return
    }
    LoginSession := parts[2]
    LoginLinkUrl := parts[3]
    LoginPollStart := A_TickCount
    LoginSetLink("pending")
    LoginSetStatus("PRESS AUTHORIZE IN YOUR BROWSER")
    try Run(LoginLinkUrl)
    SetTimer(LoginAuthPoll, 1500)
}

LoginAuthPoll() {
    global LoginGui, LoginSession, LoginPollStart, LoginLinkUrl
    if !IsObject(LoginGui) {
        SetTimer(LoginAuthPoll, 0)
        return
    }
    if (A_TickCount - LoginPollStart > 600000) {
        SetTimer(LoginAuthPoll, 0)
        LoginSetLink("none")
        LoginSetStatus("LINK TIMED OUT - TRY AGAIN", THEME_DANGER)
        return
    }
    r := LicenseHttp("GET", "/api/auth/status?s=" LoginSession, "", 4000)
    parts := StrSplit(r, "|")
    switch parts[1] {
        case "PENDING":
            return
        case "LINKED":
            SetTimer(LoginAuthPoll, 0)
            try IniWrite(LoginSession, C.INI_FILE, "Auth", "Session")
            LoginLinkUrl := ""
            LoginSetLink("linked", parts.Length >= 2 ? parts[2] : "")
            LoginSetStatus("DISCORD LINKED  •  NOW ENTER YOUR KEY", BRAND_ACCENT)
            try WinActivate("ahk_id " LoginGui.Hwnd)
            try LoginCodeEdit.Focus()
        case "ERROR":
            SetTimer(LoginAuthPoll, 0)
            LoginSetLink("none")
            LoginSetStatus(parts.Length >= 2 ? parts[2] : "LINK FAILED - TRY AGAIN", THEME_DANGER)
        case "EXPIRED":
            SetTimer(LoginAuthPoll, 0)
            LoginSetLink("none")
            LoginSetStatus("LINK EXPIRED - TRY AGAIN", THEME_DANGER)
        default:
            return    ; network blip: keep waiting
    }
}

LoginToggleReveal(*) {
    global LoginCodeEdit, LoginReveal, LoginRevealBtn
    LoginReveal := !LoginReveal
    ; EM_SETPASSWORDCHAR: 0 shows the text, 0x25CF masks it again.
    try SendMessage(0xCC, LoginReveal ? 0 : 0x25CF, 0, LoginCodeEdit)
    try LoginRevealBtn.Text := LoginReveal ? "HIDE" : "SHOW"
    try LoginCodeEdit.Redraw()
    try LoginCodeEdit.Focus()
}

; Renders the complete login backdrop for the current state to an HBITMAP.
LoginRender(state, hover, focus, link := "none", discHover := false) {
    geo := LOGIN_GEO
    w := geo.w, h := geo.h
    if !GdipReady()
        return 0
    pBmp := 0, pG := 0, hbm := 0
    ready := (link = "linked" || link = "off")
    try {
        if DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", w, "Int", h, "Int", 0,
            "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pBmp) || !pBmp
            return 0
        DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp, "Ptr*", &pG)
        DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)
        DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)

        DrawBackdropInto(pG, w, h, THEME_GRADIENT, THEME_GRADIENT_END)
        GlowEllipse(pG, 30, 20, w - 60, 220, (0x66 << 24) | ACC_RGB)
        DrawLogoInto(pG, (w - 300) // 2, 24, 300, 200)

        ; frosted card behind the form
        FillRoundA(pG, geo.cx, geo.cy, geo.cw, geo.ch, 22, 0x10FFFFFF)
        StrokeRoundA(pG, geo.cx + 0.5, geo.cy + 0.5, geo.cw - 1, geo.ch - 1, 22, 0x22FFFFFF, 1)

        ; step 1: Discord pill
        if (link = "linked") {
            FillRoundA(pG, geo.dx, geo.dy, geo.dw, geo.dh, geo.r, discHover ? 0xFF1C3A30 : 0xFF132A23)
            StrokeRoundA(pG, geo.dx + 0.5, geo.dy + 0.5, geo.dw - 1, geo.dh - 1, geo.r, 0xFF3CC48F, 1.4)
        } else if (link = "off") {
            FillRoundA(pG, geo.dx, geo.dy, geo.dw, geo.dh, geo.r, 0xFF1E1538)
        } else {
            d1 := 0xFF5865F2, d2 := 0xFF4450C8
            if (discHover || link = "pending") {
                d1 := 0xFF000000 | BlendRgb(0x5865F2, 0xFFFFFF, link = "pending" ? 0.0 : 0.14)
                d2 := 0xFF000000 | BlendRgb(0x4450C8, 0xFFFFFF, link = "pending" ? 0.0 : 0.10)
            }
            if (link != "pending") {
                Loop 5 {
                    k := 6 - A_Index
                    FillRoundA(pG, geo.dx - k * 2, geo.dy - k * 2 + 5, geo.dw + k * 4, geo.dh + k * 4,
                        geo.r + k * 2, ((discHover ? 7 : 5) * A_Index << 24) | 0x5865F2)
                }
            }
            LinearFillRound(pG, geo.dx, geo.dy, geo.dw, geo.dh, geo.r, d1, d2)
            if (link = "pending")
                StrokeRoundA(pG, geo.dx + 0.5, geo.dy + 0.5, geo.dw - 1, geo.dh - 1, geo.r, 0xFFB5BCFF, 1.4)
        }

        ; step 2: key box
        fill := LoginFieldFill(state)
        FillRoundA(pG, geo.fx, geo.fy, geo.fw, geo.fh, geo.r, 0xFF000000 | fill)
        border := state = "error" ? 0xFFF0587E : state = "success" ? 0xFF3CC48F
            : focus ? (0xFF000000 | ACCL_RGB) : (0xFF000000 | BlendRgb(BORDER_RGB, 0xFFFFFF, 0.06))
        StrokeRoundA(pG, geo.fx + 0.5, geo.fy + 0.5, geo.fw - 1, geo.fh - 1, geo.r,
            border, (focus || state != "default") ? 1.6 : 1)

        ; Unlock: muted until Discord is linked, then the glowing purple button
        c1 := 0xFF000000 | ACC_RGB, c2 := 0xFF000000 | ACC2_RGB, gl := ACC_RGB
        if (state = "success")
            c1 := 0xFF34C08C, c2 := 0xFF209268, gl := 0x34C08C
        else if (state = "error")
            c1 := 0xFFE0517F, c2 := 0xFFB02F5C, gl := 0xE0517F
        else if !ready
            c1 := 0xFF000000 | BORDER_RGB, c2 := 0xFF000000 | INPUT_RGB, gl := 0
        if (hover && (ready || state != "default")) {
            c1 := 0xFF000000 | BlendRgb(c1 & 0xFFFFFF, 0xFFFFFF, 0.14)
            c2 := 0xFF000000 | BlendRgb(c2 & 0xFFFFFF, 0xFFFFFF, 0.10)
        }
        if gl {
            Loop 7 {
                k := 8 - A_Index
                a := (hover ? 7 : 5) * A_Index
                FillRoundA(pG, geo.bx - k * 2, geo.by - k * 2 + 6, geo.bw + k * 4, geo.bh + k * 4,
                    geo.r + k * 2, (a << 24) | gl)
            }
        }
        LinearFillRound(pG, geo.bx, geo.by, geo.bw, geo.bh, geo.r, c1, c2)
        FillRoundA(pG, geo.bx + 1, geo.by + 1, geo.bw - 2, geo.bh // 2, geo.r - 1, 0x16FFFFFF)

        DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBmp, "Ptr*", &hbm, "UInt", 0xFF000000)
    } finally {
        if pG
            DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
        if pBmp
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pBmp)
    }
    return hbm
}

LoginFieldFill(state) => state = "error" ? 0x2A0E1C : state = "success" ? 0x0F2A22 : INPUT_RGB

InstallLoginEditCapsLockSuppression() {
    global LoginCodeEdit, LoginEditOriginalProc, LoginEditProcCallback
    if !IsObject(LoginCodeEdit) || LoginEditProcCallback
        return

    LoginEditProcCallback := CallbackCreate(LoginEditWindowProc)
    setWindowLong := A_PtrSize = 8 ? "User32\SetWindowLongPtr" : "User32\SetWindowLong"
    LoginEditOriginalProc := DllCall(setWindowLong,
        "Ptr", LoginCodeEdit.Hwnd, "Int", -4,
        "Ptr", LoginEditProcCallback, "Ptr")

    if !LoginEditOriginalProc {
        CallbackFree(LoginEditProcCallback)
        LoginEditProcCallback := 0
    }
}

RemoveLoginEditCapsLockSuppression() {
    global LoginCodeEdit, LoginEditOriginalProc, LoginEditProcCallback
    if !LoginEditProcCallback
        return

    if IsObject(LoginCodeEdit) && LoginEditOriginalProc {
        setWindowLong := A_PtrSize = 8 ? "User32\SetWindowLongPtr" : "User32\SetWindowLong"
        try DllCall(setWindowLong,
            "Ptr", LoginCodeEdit.Hwnd, "Int", -4,
            "Ptr", LoginEditOriginalProc, "Ptr")
    }

    CallbackFree(LoginEditProcCallback)
    LoginEditProcCallback := 0
    LoginEditOriginalProc := 0
}

LoginEditWindowProc(hwnd, msg, wParam, lParam) {
    global LoginEditOriginalProc
    if (msg = 0x1503)
        return 0
    return DllCall("User32\CallWindowProc",
        "Ptr", LoginEditOriginalProc,
        "Ptr", hwnd, "UInt", msg, "Ptr", wParam, "Ptr", lParam,
        "Ptr")
}

; Second line of defence: dismiss the balloon and destroy any tooltip window
; the edit control manages to spawn regardless of the subclass.
LoginBalloonSweep() {
    global LoginCodeEdit
    if !IsObject(LoginCodeEdit)
        return
    editHwnd := 0
    try editHwnd := LoginCodeEdit.Hwnd
    if !editHwnd
        return
    try PostMessage(0x1504, 0, 0, , "ahk_id " editHwnd)   ; EM_HIDEBALLOONTIP
    pid := DllCall("GetCurrentProcessId", "UInt")
    prev := 0
    Loop {
        tip := DllCall("User32\FindWindowExW",
            "Ptr", 0, "Ptr", prev, "Str", "tooltips_class32", "Ptr", 0, "Ptr")
        if !tip
            break
        prev := tip
        tipPid := 0
        DllCall("User32\GetWindowThreadProcessId",
            "Ptr", tip, "UInt*", &tipPid)
        if (tipPid = pid)
            try DllCall("User32\ShowWindow", "Ptr", tip, "Int", 0)
    }
}

TryLogin(*) {
    global AccessGranted, LoginCodeEdit, LoginStatus, LoginAttempts, LoginButtonState
    global LicenseStatusText, LoginLinkState

    if (LoginLinkState != "linked" && LoginLinkState != "off") {
        LoginSetStatus(LoginLinkState = "pending" ? "FINISH LINKING DISCORD IN YOUR BROWSER FIRST"
            : "LINK YOUR DISCORD ACCOUNT FIRST", THEME_DANGER)
        return
    }

    code := Trim(LoginCodeEdit.Value)
    if (code = "") {
        LoginSetStatus("KEY REQUIRED")
        LoginButtonState := "error"
        UpdateLoginButtonVisual()
        LoginCodeEdit.Focus()
        return
    }

    LoginSetStatus("CHECKING LICENSE...")
    LoginButtonState := "default"
    UpdateLoginButtonVisual()

    result := VerifyLicense(code)
    LicenseStatusText := result

    if (SubStr(result, 1, 3) = "OK|") {
        AccessGranted := true
        LoginSetStatus("ACCESS GRANTED", "3CC48F")
        LoginButtonState := "success"
        UpdateLoginButtonVisual()
        Sleep(250)
        return
    }

    LoginAttempts += 1
    msg := (SubStr(result, 1, 7) = "DENIED|") ? SubStr(result, 8) : result
    if (msg = "")
        msg := "LICENSE CHECK FAILED"
    ; The link ran out on the server - ask for it again instead of eating the key.
    if InStr(msg, "LINK YOUR DISCORD") {
        try IniDelete(C.INI_FILE, "Auth", "Session")
        LoginSetLink("none")
    } else {
        LoginCodeEdit.Value := ""
    }
    LoginSetStatus("ACCESS DENIED  •  " msg, THEME_DANGER)
    LoginButtonState := "error"
    UpdateLoginButtonVisual()
    LoginCodeEdit.Focus()
}

VerifyLicense(code) {
    global LoginSession
    return LicenseHttp("POST", "/api/activate",
        "code=" UrlEncode(code) "&session=" UrlEncode(LoginSession), 8000)
}

UrlEncode(text) {
    size := StrPut(text, "UTF-8") - 1
    if (size <= 0)
        return ""

    buf := Buffer(size, 0)
    StrPut(text, buf, "UTF-8")

    out := ""
    Loop size {
        b := NumGet(buf, A_Index - 1, "UChar")
        if ((b >= 0x30 && b <= 0x39)
            || (b >= 0x41 && b <= 0x5A)
            || (b >= 0x61 && b <= 0x7A)
            || b = 0x2D || b = 0x2E || b = 0x5F || b = 0x7E) {
            out .= Chr(b)
        } else if (b = 0x20) {
            out .= "+"
        } else {
            out .= "%" Format("{:02X}", b)
        }
    }
    return out
}

LoginClearStatus(*) {
    global LoginStatus, LoginButtonState
    if (LoginButtonState != "default") {
        try LoginStatus.Text := "KEY REQUIRED"
        try LoginStatus.SetFont("cA99CC4")
        LoginButtonState := "default"
        UpdateLoginButtonVisual()
    }
}

LoginHoverTick() {
    global LoginGui, LoginButton, LoginHovering, LoginCodeEdit, LoginFocused, LoginDiscordBtn, LoginDiscordHover
    if !IsObject(LoginGui) || !IsObject(LoginButton)
        return
    MouseGetPos(, , &winHwnd, &ctlHwnd, 2)
    onWin := (winHwnd = LoginGui.Hwnd)
    hovering := false        ; no hover redraws - they made the screen flash
    discHover := false
    focused := false
    try focused := (DllCall("GetFocus", "Ptr") = LoginCodeEdit.Hwnd)
    if (hovering = LoginHovering && focused = LoginFocused && discHover = LoginDiscordHover)
        return
    LoginHovering := hovering
    LoginFocused := focused
    LoginDiscordHover := discHover
    UpdateLoginButtonVisual()
}

UpdateLoginButtonVisual(force := false) {
    global LoginGui, LoginBg, LoginCodeEdit, LoginButtonState, LoginHovering, LoginFocused
    global LoginLinkState, LoginDiscordHover
    static lastVisual := ""
    if !IsObject(LoginBg)
        return
    visual := LoginButtonState "|" LoginHovering "|" LoginFocused "|" LoginLinkState "|" LoginDiscordHover
    if (visual = lastVisual && !force)
        return
    lastVisual := visual
    hbm := LoginRender(LoginButtonState, LoginHovering, LoginFocused, LoginLinkState, LoginDiscordHover)
    if !hbm
        return
    SetPictureBitmap(LoginBg, hbm)
    try {
        LoginCodeEdit.Opt("Background" Format("{:06X}", LoginFieldFill(LoginButtonState)))
        DllCall("User32\RedrawWindow", "Ptr", LoginGui.Hwnd, "Ptr", 0, "Ptr", 0, "UInt", 0x01A1)
    }
}

LoginExit(*) {
    global AccessGranted
    if !AccessGranted
        ExitApp
}

; ============================================================================
;  STARTUP
; ============================================================================
S.gameCtx := (*) => GameActive()
S.menuCtx := (*) => MenuActive()

LoadConfig()
RefreshThemeColors()
ShowWelcomeLogin()
RecalcGeometry()
BuildMainGui()
; Built hidden shortly after the dashboard is on screen, so the first
; SETTINGS press still opens instantly without stalling the menu's own
; appearance behind hundreds of hidden controls.
SetTimer((*) => ShowSettings(true), -400)
SetStreamProof(StreamProofEnabled)
RegisterHotkeys()
OnExit(OnShutdown)

LogLine("session start  Metal Detector by Coop  " A_ScreenWidth "x" A_ScreenHeight
    . "  dial " C.DIAL_CX "," C.DIAL_CY " r" C.DIAL_RADIUS
    . "  bar y" C.BAR_Y " probe x" C.BAR_FULL_X)
if (C.CONFIGURED_FOR != A_ScreenWidth "x" A_ScreenHeight)
    LogLine("WARNING config was calibrated for " C.CONFIGURED_FOR
        . " - recalibrate (" C.HK_CALIB ")")
else if (!G.native)
    LogLine("NOTE non-1080p screen: defaults scaled from the 1080p baseline."
        . " Verify with " C.HK_CALIB ".")
; A snapshot at every launch, so macro.log alone is enough to diagnose a
; "pressed start, nothing happened" report without a second round trip.
; Deferred: the pixel-speed benchmark makes ~200 screen reads, which used to
; freeze the dashboard for its first moments on screen.
SetTimer((*) => DumpDiagnostics("startup"), -1500)

SetStatus("IDLE", "PAUSED", "amber")
UpdateStats()
SetTimer(ClockTick, 1000)
SetTimer(HoverTick, 100)

; ============================================================================
;  CONFIG LOAD / SAVE
; ============================================================================
LoadConfig() {
    fresh := !FileExist(C.INI_FILE)
    migrateBackground := IniRead(C.INI_FILE, "UI", "BackgroundColor", "<<UNSET>>") = "<<UNSET>>"
    migrateButtons := IniRead(C.INI_FILE, "UI", "ButtonColor", "<<UNSET>>") = "<<UNSET>>"
    for e in CFG_MAP {
        raw := IniRead(C.INI_FILE, e[1], e[2], "<<UNSET>>")
        if (raw = "<<UNSET>>") {
            SaveCfg(e[3])                 ; materialise the default
            continue
        }
        prop := e[3]
        C.%prop% := ParseVal(raw, e[4], C.%prop%)
    }
    ; One-time migration: menus now default to the compact 75% scale.
    if (IniRead(C.INI_FILE, "UI", "ScaleDefault75", "<<UNSET>>") = "<<UNSET>>") {
        C.UI_SCALE := 75
        SaveCfg("UI_SCALE")
        IniWrite(1, C.INI_FILE, "UI", "ScaleDefault75")
    }
    if (migrateBackground) {
        C.BACKGROUND_COLOR := 0x46102F
        SaveCfg("BACKGROUND_COLOR")
    }
    ; One-time migration: configs saved with the old bright-pink gradient top
    ; move to the dark-pink-to-black look that matches the logo.
    if (C.BACKGROUND_COLOR = 0xFF4FAF) {
        C.BACKGROUND_COLOR := 0x46102F
        SaveCfg("BACKGROUND_COLOR")
    }
    if (migrateButtons) {
        C.BUTTON_COLOR := C.MENU_COLOR
        SaveCfg("BUTTON_COLOR")
    }
    ; One-time migration: F8 collides with the FiveM console, so start/stop
    ; moves to F6.
    if (IniRead(C.INI_FILE, "Hotkeys", "ToggleOffF8", "<<UNSET>>") = "<<UNSET>>") {
        if (C.HK_TOGGLE = "F8" || C.HK_TOGGLE = "") {
            C.HK_TOGGLE := "F6"
            SaveCfg("HK_TOGGLE")
        }
        IniWrite(1, C.INI_FILE, "Hotkeys", "ToggleOffF8")
    }
    ; One-time migration: the gap between rounds (E presses) moves to 3s even
    ; over an older saved value.
    if (IniRead(C.INI_FILE, "Timing", "GapDefault3s", "<<UNSET>>") = "<<UNSET>>") {
        C.ROUND_GAP_MS := 3000
        C.RETRY_GAP_MS := 3000
        SaveCfg("ROUND_GAP_MS")
        SaveCfg("RETRY_GAP_MS")
        IniWrite(1, C.INI_FILE, "Timing", "GapDefault3s")
    }
    ; One-time migration: the minigame changed from the centred blue-arc dial
    ; to the "Skill Check" panel (pink zone, red marker, 1/2 + 2/2 stages).
    ; An existing config.ini still holds the blue-arc colours, which can never
    ; match, so the detection values are reset to the new style once.
    if (IniRead(C.INI_FILE, "Colors", "SkillCheckStyle", "<<UNSET>>") = "<<UNSET>>") {
        C.ARC_COLOR         := 0xC345AF
        C.ARC_TOL           := 30
        C.TICK_COLOR        := 0xCE1313
        C.TICK_TOL          := 45
        C.BLUE_DOMINANCE    := 60
        C.RED_DOMINANCE     := 90
        C.USE_DOMINANCE     := 1
        C.DIAL_RADIUS       := Round(53 * SCL)
        C.ARC_WIDTH_MIN_DEG := 6
        C.ARC_WIDTH_MAX_DEG := 200
        C.REACTION_MIN_MS   := 5
        C.REACTION_MAX_MS   := 25
        C.AUTO_LOCATE_DIAL  := 1
        C.POST_PRESS_SETTLE := 250
        for p in ["ARC_COLOR", "ARC_TOL", "TICK_COLOR", "TICK_TOL",
                  "BLUE_DOMINANCE", "RED_DOMINANCE", "USE_DOMINANCE",
                  "DIAL_RADIUS", "ARC_WIDTH_MIN_DEG", "ARC_WIDTH_MAX_DEG",
                  "REACTION_MIN_MS", "REACTION_MAX_MS", "AUTO_LOCATE_DIAL",
                  "POST_PRESS_SETTLE"]
            SaveCfg(p)
        IniWrite(1, C.INI_FILE, "Colors", "SkillCheckStyle")
        if (!fresh)
            LogLine("config.ini switched to the Skill Check style (pink zone,"
                . " red marker). The dial position is found automatically.")
    }
    ; One-time migration #4: faster restart. The log showed "bar none" on
    ; every round (this server has no progress bar), so skip that 3s wait,
    ; and cut the gaps between rounds.
    if (IniRead(C.INI_FILE, "Timing", "FastRestart4", "<<UNSET>>") = "<<UNSET>>") {
        C.ROUND_GAP_MS := 800, C.RETRY_GAP_MS := 800
        C.STAGE_GAP_MS := 1500, C.WAIT_FOR_BAR := 0
        for p in ["ROUND_GAP_MS", "RETRY_GAP_MS", "STAGE_GAP_MS", "WAIT_FOR_BAR"]
            SaveCfg(p)
        IniWrite(1, C.INI_FILE, "Timing", "FastRestart4")
    }
    ; One-time migration #3: presses landed EARLY - aim for the zone middle.
    if (IniRead(C.INI_FILE, "Timing", "SkillCheckAimMid3", "<<UNSET>>") = "<<UNSET>>") {
        C.TICK_LEAD_MS := 15, C.ZONE_INSET_PCT := 30
        C.REACTION_MIN_MS := 0, C.REACTION_MAX_MS := 10
        for p in ["TICK_LEAD_MS", "ZONE_INSET_PCT", "REACTION_MIN_MS", "REACTION_MAX_MS"]
            SaveCfg(p)
        IniWrite(1, C.INI_FILE, "Timing", "SkillCheckAimMid3")
    }
    ; One-time migration #2 (from the 2026-10-10 log): the skill check takes
    ; 4-8s to appear after the slot key, so the old 3s wait made the macro
    ; think the detector was broken and press the NEXT slot mid-dig. And
    ; stage 2 can take over a second to show after stage 1.
    if (IniRead(C.INI_FILE, "Timing", "SkillCheckWaits2", "<<UNSET>>") = "<<UNSET>>") {
        C.DIAL_APPEAR_TIMEOUT := 12000
        C.STAGE_GAP_MS        := 1500
        SaveCfg("DIAL_APPEAR_TIMEOUT"), SaveCfg("STAGE_GAP_MS")
        IniWrite(1, C.INI_FILE, "Timing", "SkillCheckWaits2")
    }
    ; One-time migration #6: the detection box back to a roomy size, editable
    ; from now on (Settings -> Regions, or drag its corner in Calibrate).
    if (IniRead(C.INI_FILE, "Geometry", "DialBox7", "<<UNSET>>") = "<<UNSET>>") {
        C.DIAL_BOX_PX := 140, C.DETECT_RANGE_PCT := 20
        SaveCfg("DIAL_BOX_PX"), SaveCfg("DETECT_RANGE_PCT")
        IniWrite(1, C.INI_FILE, "Geometry", "DialBox7")
    }
    ; One-time migration #5 (from the 2026-10-11 screenshot + log): the current
    ; Skill Check is a light pink zone (0xFF88F0) with a red line (0xEF4444).
    ; Settings had been switched around while troubleshooting (zone green /
    ; purple, marker blue), which can never match it - put them back once.
    if (IniRead(C.INI_FILE, "Detection", "SkillCheckColours5", "<<UNSET>>") = "<<UNSET>>") {
        C.ZONE_STYLE := "pink", C.ARC_COLOR := 0xFF88F0
        C.TICK_STYLE := "red",  C.TICK_COLOR := 0xEF4444
        C.AUTO_LOCATE_DIAL := 1
        for p in ["ZONE_STYLE", "ARC_COLOR", "TICK_STYLE", "TICK_COLOR", "AUTO_LOCATE_DIAL"]
            SaveCfg(p)
        IniWrite(1, C.INI_FILE, "Detection", "SkillCheckColours5")
    }
    ; Theme lock: the XZX purple look and logo are
    ; fixed, so any saved colour override is ignored.
    ; The menu colour is chosen in Settings -> Appearance; everything else is
    ; derived from it (RefreshThemeColors).
    if !(C.MENU_COLOR & 0xFFFFFF)
        C.MENU_COLOR := 0x9D4EFF
    C.BUTTON_COLOR := C.MENU_COLOR
    if (fresh)
        LogLine("config.ini created with defaults for this screen")
}

ParseVal(raw, type, fallback) {
    raw := Trim(raw)
    try {
        switch type {
            case "s": return raw
            case "i": return Integer(raw)
            case "c": return Integer(raw)          ; accepts 0xRRGGBB
            case "f": return Float(raw)
            case "b": return (raw = "1" || raw = "true") ? 1 : 0
        }
    } catch {
        LogLine("WARNING could not parse '" raw "' as " type " - keeping default")
    }
    return fallback
}

FormatVal(val, type) {
    return (type = "c") ? Format("0x{:06X}", val) : String(val)
}

; Write one property back to config.ini, looked up by property name.
SaveCfg(prop) {
    for e in CFG_MAP {
        if (e[3] != prop)
            continue
        try IniWrite(FormatVal(C.%prop%, e[4]), C.INI_FILE, e[1], e[2])
        catch as err
            LogLine("ERROR writing " prop " to config.ini: " err.Message)
        return
    }
}

; The ini table is the single source of truth for a property's type.
FieldType(prop) {
    for e in CFG_MAP
        if (e[3] = prop)
            return e[4]
    return "s"
}

; ============================================================================
;  MAIN UI
; ============================================================================
; Dark, always on top, no Windows chrome, draggable anywhere on its background.
; Rebuilt from scratch when the UI scale changes.

DragMainWindow(*) {
    global UI
    if !UI.Has("gui")
        return
    try {
        PostMessage(0x00A1, 2, 0, 0, "ahk_id " UI["gui"].Hwnd)
    }
}


UpdateStreamProofUI(enabled) {
    global UI
    if !IsObject(UI) || !UI.Has("streamStatus")
        return
    try {
        UI["streamStatus"].Text := enabled ? "●  ON" : "●  OFF"
        UI["streamStatus"].SetFont("c" (enabled ? BRAND_ACCENT : BRAND_ACCENT_OFF))
        if UI.Has("btnStream")
            UI["btnStream"].Text := "Stream proof  ·  " (enabled ? "ON" : "OFF")
    }
}

; ============================================================================
;  MAIN WINDOW - sidebar + dashboard
; ============================================================================
; Like the login, every shape (gradient, sidebar, cards, buttons, history
; dots, the mini skill-check ring) is ONE rendered image: smooth anti-aliased
; corners, real glows, no stacked pictures. Text and the activity feed sit on
; top as normal controls. Hover/state changes just re-render the image.
;
; Layout, in 100%-scale units (P() scales them):
;   sidebar 0..230   logo, status, uptime, START / STOP, navigation
;   content 250..936 header, status card, 4 stats, recent rounds, activity
; showNow false: build the new window hidden and keep the old one on screen
; until FinishMainShow(), so a colour or size change swaps in one step.
BuildMainGui(showNow := true) {
    global UI, C, S, HIST_CELLS

    oldGui := UI.Has("gui") ? UI["gui"] : 0
    oldX := "", oldY := ""
    if IsObject(oldGui)
        try WinGetPos(&oldX, &oldY, , , "ahk_id " oldGui.Hwnd)
    UI.Clear()
    SET.Clear()          ; settings controls live in this window and die with it
    S.hoverHwnd := 0
    S.mainHover := ""
    S.view := "dashboard"
    S.mainHwndKey := Map()

    u := C.UI_SCALE / 100
    P(v) => Round(v * u)
    W := P(MAIN_W), H := P(MAIN_H)

    dlg := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000", "Metal Detector by Coop")
    TrackWindow(dlg.Hwnd)
    dlg.BackColor := Format("{:06X}", THEME_GRADIENT_END)
    dlg.MarginX := 0
    dlg.MarginY := 0
    UI["gui"] := dlg

    ; 1) the rendered backdrop - must be the first control so everything sits on it
    UI["bg"] := dlg.AddPicture("x0 y0 w" W " h" H, "")
    UI["dragBar"] := dlg.AddText(Box(P(250), 0, P(640), P(84)) " BackgroundTrans", "")
    UI["dragBar"].OnEvent("Click", (*) => DragMainWindow())

    ; ---- sidebar -----------------------------------------------------------
    dlg.SetFont("s" P(8) " bold c" THEME_SOFT, FONT_UI)
    UI["led"] := dlg.AddText(Box(P(38), P(154), P(160), P(22)) " 0x200 BackgroundTrans", "●  READY")

    dlg.SetFont("s" P(7) " bold c" TXT_DIM, FONT_UI)
    dlg.AddText(Box(P(26), P(194), P(180), P(14)) " BackgroundTrans", "SESSION TIME")
    dlg.SetFont("s" P(17) " norm c" TXT_MAIN, "Consolas")
    UI["uptime"] := dlg.AddText(Box(P(24), P(210), P(182), P(30)) " BackgroundTrans", "00:00:00")

    MainLabel(dlg, u, "btnRun", "START", StartRun, "s" P(11) " norm cFFFFFF", FONT_SEMI)
    MainLabel(dlg, u, "btnStop", "STOP", HardStop, "s" P(9) " norm cF8B4C6", FONT_SEMI)

    dlg.SetFont("s" P(7) " bold c" TXT_DIM, FONT_UI)
    dlg.AddText(Box(P(26), P(366), P(180), P(14)) " BackgroundTrans", "MENU")
    handlers := Map("btnDash", ShowDashboard, "btnSet", OpenSettingsView, "btnCal", (*) => ToggleCalibration(),
        "btnDiag", (*) => DumpDiagnostics("manual"), "btnReset", ResetStats, "btnStream", ToggleStreamProof)
    for item in MAIN_NAV {
        key := item[1], r := MAIN_RECTS[key]
        dlg.SetFont("s" P(11) " norm c" THEME_MUTED, "Segoe MDL2 Assets")
        icon := dlg.AddText(Box(P(r[1] + 14), P(r[2]), P(24), P(r[4])) " 0x200 BackgroundTrans", item[2])
        icon.OnEvent("Click", handlers[key])
        S.mainHwndKey[icon.Hwnd] := key
        S.noDrag[icon.Hwnd] := true
        label := item[3] (key = "btnStream" ? (StreamProofEnabled ? "  ·  ON" : "  ·  OFF") : "")
        if (key = "btnSet")
            label := "Settings"
        MainLabel(dlg, u, key, label, handlers[key], "s" P(9) " norm c" TXT_MAIN, FONT_UI,
            [r[1] + 46, r[2], r[3] - 50, r[4]], "Left")
    }
    dlg.SetFont("s" P(7) " norm c" TXT_DIM, FONT_UI)
    dlg.AddText(Box(P(26), P(608), P(180), P(14)) " BackgroundTrans", "XZX Macro  ·  by Coop")

    ; ---- header ------------------------------------------------------------
    dlg.SetFont("s" P(18) " norm c" TXT_MAIN, FONT_SEMI)
    UI["title"] := dlg.AddText(Box(P(250), P(24), P(420), P(34)) " BackgroundTrans", "Dashboard")
    dlg.SetFont("s" P(9) " norm c" TXT_SUB, FONT_UI)
    UI["subtitle"] := dlg.AddText(Box(P(252), P(60), P(440), P(20)) " BackgroundTrans", "Metal Detector  ·  skill check automation")

    dlg.SetFont("s" P(7) " bold c" TXT_DIM, FONT_UI)
    UI["streamLabel"] := dlg.AddText(Box(P(716), P(37), P(100), P(18)) " 0x200 BackgroundTrans", "STREAM PROOF")
    dlg.SetFont("s" P(8) " bold c" (StreamProofEnabled ? BRAND_ACCENT : BRAND_ACCENT_OFF), FONT_UI)
    UI["streamStatus"] := dlg.AddText(Box(P(812), P(37), P(70), P(18)) " 0x200 Right BackgroundTrans",
        StreamProofEnabled ? "●  ON" : "●  OFF")
    MainLabel(dlg, u, "btnClose", Chr(0xE711), (*) => ExitApp(), "s" P(9) " norm c" TXT_SUB, "Segoe MDL2 Assets")

    ; ---- status card -------------------------------------------------------
    ; Everything from here to the activity feed belongs to the Dashboard view
    ; and is hidden while Settings is open.
    ; Keyed on the hwnd from a two-variable loop, so only the controls added
    ; below count as dashboard-only - the backdrop, sidebar and header must
    ; stay visible when Settings is open.
    before := Map()
    for hwnd, ctl in dlg
        before[hwnd] := true
    dlg.SetFont("s" P(7) " bold c" TXT_DIM, FONT_UI)
    dlg.AddText(Box(P(274), P(116), P(200), P(14)) " BackgroundTrans", "STATUS")
    dlg.SetFont("s" P(24) " norm c" TXT_MAIN, FONT_SEMI)
    UI["state"] := dlg.AddText(Box(P(272), P(134), P(480), P(46)) " BackgroundTrans", "IDLE")
    dlg.SetFont("s" P(9) " norm c" TXT_SUB, FONT_UI)
    UI["ledLabel"] := dlg.AddText(Box(P(275), P(184), P(480), P(22)) " 0x200 BackgroundTrans", "PAUSED")

    ; ---- stats -------------------------------------------------------------
    statNames := ["ATTEMPTS", "HITS", "MISSES / TIMEOUTS", "SUCCESS RATE"]
    statKeys  := ["rounds", "hits", "timeouts", "rate"]
    statInit  := ["0", "0", "0 / 0", "0%"]
    Loop 4 {
        x := 250 + (A_Index - 1) * 176
        dlg.SetFont("s" P(7) " bold c" TXT_DIM, FONT_UI)
        dlg.AddText(Box(P(x + 18), P(256), P(140), P(14)) " BackgroundTrans", statNames[A_Index])
        dlg.SetFont("s" P(19) " norm c" TXT_MAIN, FONT_SEMI)
        UI[statKeys[A_Index]] := dlg.AddText(Box(P(x + 16), P(276), P(140), P(40)) " BackgroundTrans", statInit[A_Index])
    }

    ; ---- recent rounds -----------------------------------------------------
    dlg.SetFont("s" P(7) " bold c" TXT_DIM, FONT_UI)
    dlg.AddText(Box(P(274), P(352), P(200), P(14)) " BackgroundTrans", "RECENT ROUNDS")
    for item in [["HIT", 734], ["MISS", 788], ["TIMEOUT", 846]]
        dlg.AddText(Box(P(item[2] + 12), P(352), P(70), P(14)) " BackgroundTrans", item[1])

    ; ---- activity feed -----------------------------------------------------
    dlg.SetFont("s" P(7) " bold c" TXT_DIM, FONT_UI)
    dlg.AddText(Box(P(274), P(430), P(200), P(14)) " BackgroundTrans", "ACTIVITY")
    dlg.SetFont("s" P(8) " norm c" TXT_SUB, "Consolas")
    UI["log"] := dlg.AddEdit(Box(P(272), P(452), P(648), P(118))
        " ReadOnly -E0x200 -VScroll -HScroll Multi Background" THEME_PANEL, "")
    S.noDrag[UI["log"].Hwnd] := true
    UI["dash"] := []
    for hwnd, ctl in dlg
        if !before.Has(hwnd) && hwnd != UI["bg"].Hwnd
            UI["dash"].Push(ctl)

    dlg.SetFont("s" P(7) " norm c" TXT_DIM, FONT_UI)
    UI["hint"] := dlg.AddText(Box(P(250), P(598), P(690), P(16)) " Center BackgroundTrans", "")

    centerX := Max(0, Round((A_ScreenWidth - W) / 2))
    roomX := A_ScreenWidth - W - W
    startX := roomX >= 0 ? Min(centerX, roomX) : centerX
    startY := Max(0, Round((A_ScreenHeight - H) / 2))
    if (oldX != "")
        startX := oldX, startY := oldY          ; stay where the old menu was
    MainRefresh()
    S.mainShowAt := [startX, startY, W, H]
    S.mainOldGui := oldGui
    if showNow
        FinishMainShow()

    ApplyRoundedCorners(dlg.Hwnd, W, H, P(40))
    S.dragMap[dlg.Hwnd] := "main"

    SetStreamProof(StreamProofEnabled)
    try UpdateStreamProofUI(StreamProofEnabled)

    UpdateRunButton()
    UpdateHint()
    UpdateStats()
    RenderLog()
    SetStatus(S.state, S.detail, S.led)
}

; A clickable, transparent label sitting on a button drawn in the backdrop.
; `area` is [x, y, w, h] in 100%-units; defaults to the button's own rect.
MainLabel(dlg, u, key, text, handler, font, face, area := "", align := "Center") {
    r := IsObject(area) ? area : MAIN_RECTS[key]
    dlg.SetFont(font, face)
    ctl := dlg.AddText("x" Round(r[1] * u) " y" Round(r[2] * u) " w" Round(r[3] * u) " h" Round(r[4] * u)
        " 0x200 " align " BackgroundTrans", text)
    ctl.OnEvent("Click", handler)
    UI[key] := ctl
    S.mainHwndKey[ctl.Hwnd] := key
    S.noDrag[ctl.Hwnd] := true
    return ctl
}

; Re-draw the backdrop for the current hover / run state / history.
MainRefresh(redraw := true) {
    if !UI.Has("bg")
        return
    hbm := MainRender()
    if !hbm
        return
    SetPictureBitmap(UI["bg"], hbm)
    if redraw
        try DllCall("User32\RedrawWindow", "Ptr", UI["gui"].Hwnd, "Ptr", 0, "Ptr", 0, "UInt", 0x0185)
}

; Puts a freshly rendered bitmap into a Picture control and frees the old one.
; Takes ownership of hbm. The first time it goes through .Value (which turns
; the control into a bitmap picture); after that it's swapped in place with
; STM_SETIMAGE, so nothing is hidden, resized or erased in between - that
; erase is what made the menu flash on hover.
; Hand cursor over the menu's and login screen's clickable labels.
WM_SETCURSOR_HAND(wParam, lParam, msg, hwnd) {
    global LoginButton, LoginDiscordBtn, LoginRevealBtn
    static hand := DllCall("LoadCursor", "Ptr", 0, "Ptr", 32649, "Ptr")
    if ((lParam & 0xFFFF) != 1)                    ; only over the client area
        return
    clickable := S.HasOwnProp("mainHwndKey") && S.mainHwndKey.Has(wParam)
    if !clickable {
        for ctl in [LoginButton, LoginDiscordBtn, LoginRevealBtn]
            if IsObject(ctl)
                try clickable := clickable || (ctl.Hwnd = wParam)
    }
    if !clickable
        return
    DllCall("SetCursor", "Ptr", hand)
    return true
}

SetPictureBitmap(ctl, hbm) {
    pic := 0
    try pic := ctl.Hwnd
    if !pic {
        DllCall("Gdi32\DeleteObject", "Ptr", hbm)
        return
    }
    style := DllCall("GetWindowLongPtr", "Ptr", pic, "Int", -16, "Ptr")
    if ((style & 0x1F) != 0x0E) {                        ; not SS_BITMAP yet
        try ctl.Value := "HBITMAP:*" hbm
        DllCall("Gdi32\DeleteObject", "Ptr", hbm)
        return
    }
    old := SendMessage(0x172, 0, hbm, pic)               ; STM_SETIMAGE, IMAGE_BITMAP
    if (old && old != hbm)
        DllCall("Gdi32\DeleteObject", "Ptr", old)
    ; 32-bit bitmaps get copied by the control; free ours if it did
    if (SendMessage(0x173, 0, 0, pic) != hbm)            ; STM_GETIMAGE
        DllCall("Gdi32\DeleteObject", "Ptr", hbm)
}

MainRender() {
    u := C.UI_SCALE / 100
    P(v) => Round(v * u)
    W := P(MAIN_W), H := P(MAIN_H)
    pStatic := MainStaticLayer(W, H, u)
    if !pStatic
        return 0
    pBmp := 0, pG := 0, hbm := 0
    try {
        DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", W, "Int", H, "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pBmp)
        if !pBmp
            return 0
        DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp, "Ptr*", &pG)
        DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)
        DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)
        DllCall("Gdiplus\GdipDrawImageRectI", "Ptr", pG, "Ptr", pStatic, "Int", 0, "Int", 0, "Int", W, "Int", H)
        hov := S.HasOwnProp("mainHover") ? S.mainHover : ""

        ; START: purple when idle, green while running; soft glow + sheen
        r := MainR("btnRun", u)
        c1 := S.running ? 0x34C08C : ACC_RGB, c2 := S.running ? 0x209268 : ACC2_RGB
        if (hov = "btnRun")
            c1 := BlendRgb(c1, 0xFFFFFF, 0.14), c2 := BlendRgb(c2, 0xFFFFFF, 0.10)
        Loop 6 {
            k := 7 - A_Index
            FillRoundA(pG, r[1] - k * 2, r[2] - k * 2 + P(5), r[3] + k * 4, r[4] + k * 4, P(14) + k * 2,
                (((hov = "btnRun") ? 7 : 5) * A_Index << 24) | c1)
        }
        LinearFillRound(pG, r[1], r[2], r[3], r[4], P(14), 0xFF000000 | c1, 0xFF000000 | c2)
        FillRoundA(pG, r[1] + 1, r[2] + 1, r[3] - 2, r[4] // 2, P(13), 0x16FFFFFF)

        ; STOP: quiet rose outline, fills in on hover
        r := MainR("btnStop", u)
        FillRoundA(pG, r[1], r[2], r[3], r[4], P(12), hov = "btnStop" ? 0xFF4A1830 : 0xFF2A1022)
        StrokeRoundA(pG, r[1] + 0.5, r[2] + 0.5, r[3] - 1, r[4] - 1, P(12), 0xAAF0587E, 1)

        ; navigation: the open view stays highlighted, hover highlights too
        view := S.HasOwnProp("view") ? S.view : "dashboard"
        for item in MAIN_NAV {
            active := (item[1] = "btnDash" && view = "dashboard") || (item[1] = "btnSet" && view = "settings")
            if (hov != item[1] && !active)
                continue
            r := MainR(item[1], u)
            FillRoundA(pG, r[1], r[2], r[3], r[4], P(10), active ? (0x26 << 24 | ACC_RGB) : 0x16FFFFFF)
            if active
                FillRoundA(pG, r[1], r[2] + P(8), P(3), r[4] - P(16), P(2), 0xFF000000 | ACCL_RGB)
        }
        if (hov = "btnClose") {
            r := MainR("btnClose", u)
            FillRoundA(pG, r[1], r[2], r[3], r[4], P(10), 0x40F0587E)
        }

        ; status pill dot colour follows the LED
        led := S.HasOwnProp("led") ? S.led : "amber"
        dot := led = "green" ? ACCL_RGB : led = "red" ? 0xF0587E : SOFT_RGB
        FillRoundA(pG, P(24), P(150), P(182), P(30), P(15), (led = "green" ? 0x28 : 0x14) << 24 | dot)

        if (view = "settings") {
            ; tabs: the open page filled with the accent, hover lifts the rest
            active := SET.Get("activePage", "interface")
            for i, tab in SET_TABS {
                rr := SettingsTabRect(i)
                x := P(rr[1]), y := P(rr[2]), w := P(rr[3]), h := P(rr[4])
                if (tab[1] = active) {
                    LinearFillRound(pG, x, y, w, h, P(12), 0xFF000000 | ACC_RGB, 0xFF000000 | ACC2_RGB)
                } else {
                    FillRoundA(pG, x, y, w, h, P(12), 0xFF000000 | (hov = "tab:" tab[1] ? INPUT_RGB : PANEL_RGB))
                    StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, P(12), 0xFF000000 | BORDER_RGB, 1)
                }
            }
            SettingsRender(pG, u, hov)
            if (SET.Get("tabFontFor", "") != active) {
                SET["tabFontFor"] := active
                for i, tab in SET_TABS
                    try SET["tab_" tab[1]].SetFont("c" (tab[1] = active ? THEME_BUTTON_TEXT : TXT_SUB))
            }
            DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBmp, "Ptr*", &hbm, "UInt", 0xFF000000)
            return hbm
        }

        ; recent rounds: one rounded dot per round, newest on the right
        n := S.history.Length
        cell := (690 - 48) / HIST_CELLS
        Loop HIST_CELLS {
            idx := n - HIST_CELLS + A_Index
            kind := (idx >= 1) ? S.history[idx] : ""
            col := Integer("0x" HIST_COLORS.Get(kind, HIST_COLORS[""]))
            x := 250 + 24 + (A_Index - 1) * cell
            FillRoundA(pG, P(x), P(382), P(cell - 6), P(16), P(8), 0xFF000000 | col)
        }
        DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBmp, "Ptr*", &hbm, "UInt", 0xFF000000)
    } finally {
        if pG
            DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
        if pBmp
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pBmp)
    }
    return hbm
}

MainR(key, u) {
    r := MAIN_RECTS[key]
    return [Round(r[1] * u), Round(r[2] * u), Round(r[3] * u), Round(r[4] * u)]
}

; Everything that doesn't change on hover, drawn once per size and colour set.
MainStaticLayer(W, H, u) {
    static cache := 0, cacheKey := ""
    view := S.HasOwnProp("view") ? S.view : "dashboard"
    key := W "x" H "|" C.ARC_COLOR "|" C.TICK_COLOR "|" C.MENU_COLOR "|" CoopLogoPath() "|" view
    if (cache && key = cacheKey)
        return cache
    if !GdipReady()
        return 0
    if cache {
        DllCall("Gdiplus\GdipDisposeImage", "Ptr", cache)
        cache := 0
    }
    P(v) => Round(v * u)
    pBmp := 0, pG := 0
    DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", W, "Int", H, "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pBmp)
    if !pBmp
        return 0
    DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp, "Ptr*", &pG)
    try {
        DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)
        DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)
        DrawBackdropInto(pG, W, H, THEME_GRADIENT, THEME_GRADIENT_END)

        ; sidebar: darker glass panel with a hairline edge, logo with a glow
        FillRoundA(pG, 0, 0, P(230), H, 0, 0x99050309)
        FillRoundA(pG, P(230), 0, 1, H, 0, 0x22FFFFFF)
        GlowEllipse(pG, P(-10), P(4), P(250), P(150), (0x55 << 24) | ACC_RGB)
        DrawLogoInto(pG, P(25), P(18), P(180), P(120))
        FillRoundA(pG, P(24), P(382), P(182), 1, 0, 0x1AFFFFFF)

        ; header stream pill
        FillRoundA(pG, P(700), P(30), P(196), P(32), P(16), 0xFF000000 | FIELD_RGB)
        StrokeRoundA(pG, P(700) + 0.5, P(30) + 0.5, P(196) - 1, P(32) - 1, P(16), 0xFF000000 | BORDER_RGB, 1)

        if (view = "settings") {
            MainCard(pG, P(250), P(144), P(690), P(440), P(20))
            DllCall("Gdiplus\GdipFlush", "Ptr", pG, "Int", 1)
            cache := pBmp, cacheKey := key
            return cache
        }

        ; cards
        MainCard(pG, P(250), P(96), P(690), P(132), P(20))
        Loop 4
            MainCard(pG, P(250 + (A_Index - 1) * 176), P(242), P(162), P(88), P(16))
        Loop 4
            FillRoundA(pG, P(250 + (A_Index - 1) * 176 + 18), P(320), P(22), P(3), P(2), 0xFF000000 | ACCL_RGB)
        MainCard(pG, P(250), P(340), P(690), P(72), P(16))
        MainCard(pG, P(250), P(422), P(690), P(160), P(16))

        ; legend dots for recent rounds
        for item in [["ok", 734], ["miss", 788], ["timeout", 846]]
            FillRoundA(pG, P(item[2]), P(356), P(7), P(7), P(4), 0xFF000000 | Integer("0x" HIST_COLORS[item[1]]))

        ; mini skill-check ring in the status card, in the chosen colours
        MainRing(pG, P(868), P(162), P(40), u)
        DllCall("Gdiplus\GdipFlush", "Ptr", pG, "Int", 1)
    } finally {
        DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
    }
    cache := pBmp, cacheKey := key
    return cache
}

MainCard(pG, x, y, w, h, r) {
    FillRoundA(pG, x, y, w, h, r, 0xFF000000 | PANEL_RGB)
    StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, r, 0xFF000000 | BORDER_RGB, 1)
}

; A tiny version of the in-game skill check: grey ring, zone arc, marker.
MainRing(pG, cx, cy, r, u) {
    pen := 0
    width := Max(3, Round(7 * u))
    try {
        DllCall("Gdiplus\GdipCreatePen1", "UInt", 0xFF2A2E3B, "Float", width, "Int", 2, "Ptr*", &pen)
        DllCall("Gdiplus\GdipDrawEllipse", "Ptr", pG, "Ptr", pen, "Float", cx - r, "Float", cy - r, "Float", 2 * r, "Float", 2 * r)
        DllCall("Gdiplus\GdipSetPenColor", "Ptr", pen, "UInt", 0xFF000000 | (C.ARC_COLOR & 0xFFFFFF))
        DllCall("Gdiplus\GdipSetPenStartCap", "Ptr", pen, "Int", 2)
        DllCall("Gdiplus\GdipSetPenEndCap", "Ptr", pen, "Int", 2)
        DllCall("Gdiplus\GdipDrawArc", "Ptr", pG, "Ptr", pen, "Float", cx - r, "Float", cy - r, "Float", 2 * r, "Float", 2 * r,
            "Float", 20, "Float", 70)
        DllCall("Gdiplus\GdipSetPenColor", "Ptr", pen, "UInt", 0xFF000000 | (C.TICK_COLOR & 0xFFFFFF))
        DllCall("Gdiplus\GdipDrawArc", "Ptr", pG, "Ptr", pen, "Float", cx - r, "Float", cy - r, "Float", 2 * r, "Float", 2 * r,
            "Float", 250, "Float", 6)
    } finally {
        if pen
            DllCall("Gdiplus\GdipDeletePen", "Ptr", pen)
    }
}

; Position/size option string helper - keeps the layout lines readable.
Box(x, y, w, h) => "x" x " y" y " w" w " h" h

UiPanel(dlg, x, y, w, h, bg, radius := 28) {
    panel := dlg.AddText(Box(x, y, w, h) " Background" bg, "")
    RoundHwnd(panel.Hwnd, w, h, radius)
    return panel
}

; The XZX logo is 3:2. Pass the width; the height follows unless given.
AddCoopLogo(dlg, x, y, w, h := 0) {
    if (h <= 0)
        h := Round(w * 2 / 3)
    logo := 0
    pngPath := CoopLogoPath()
    if (pngPath = "")
        return 0
    hBitmap := CoopLogoBitmap(pngPath, w, h)
    if hBitmap {
        try logo := dlg.AddPicture(Box(x, y, w, h) " BackgroundTrans", "HBITMAP:*" hBitmap)
        DllCall("Gdi32\DeleteObject", "Ptr", hBitmap)
    }
    if !IsObject(logo)
        try logo := dlg.AddPicture(Box(x, y, w, h) " BackgroundTrans AltSubmit",
            "*w" w " *h" h " " pngPath)
    return IsObject(logo) ? logo : 0
}

; Premultiplied-alpha 32-bit HBITMAP of the logo fitted into w x h, or 0.
CoopLogoBitmap(pngPath, w, h := 0) {
    if (h <= 0)
        h := w
    if !GdipReady()
        return 0
    pSrc := LogoImage()
    if !pSrc
        return 0
    hBitmap := 0, pDest := 0, pGraphics := 0
    try {
        if DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", w, "Int", h, "Int", 0,
            "UInt", 0xE200B, "Ptr", 0, "Ptr*", &pDest) = 0 && pDest {
            if DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pDest, "Ptr*", &pGraphics) = 0 && pGraphics {
                DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pGraphics, "Int", 4)
                DrawLogoInto(pGraphics, 0, 0, w, h)
                DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pDest, "Ptr*", &hBitmap, "UInt", 0)
            }
        }
    } finally {
        if pGraphics
            DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pGraphics)
        if pDest
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pDest)
    }
    return hBitmap
}

; An anti-aliased rounded rectangle rendered by GDI+ into a transparent
; picture control - every corner is smooth and identical.
AddRoundRectPicture(dlg, x, y, w, h, radius, rgb) {
    hbm := RoundRectBitmap(w, h, radius, rgb)
    if !hbm
        return 0
    pic := 0
    try pic := dlg.AddPicture(Box(x, y, w, h) " BackgroundTrans", "HBITMAP:*" hbm)
    DllCall("Gdi32\DeleteObject", "Ptr", hbm)
    return IsObject(pic) ? pic : 0
}

; Premultiplied-alpha HBITMAP of a filled rounded rectangle, or 0.
RoundRectBitmap(w, h, radius, rgb) {
    static gdipToken := 0
    if !gdipToken {
        try {
            if !DllCall("GetModuleHandleW", "Str", "gdiplus", "Ptr")
                DllCall("LoadLibraryW", "Str", "gdiplus", "Ptr")
            si := Buffer(A_PtrSize = 8 ? 24 : 16, 0)
            NumPut("UInt", 1, si, 0)
            tok := 0
            if DllCall("Gdiplus\GdiplusStartup",
                "Ptr*", &tok, "Ptr", si.Ptr, "Ptr", 0) = 0
                gdipToken := tok
        }
        if !gdipToken
            return 0
    }

    d := Min(radius * 2, w, h)
    hBitmap := 0
    pBmp := 0
    pGraphics := 0
    pPath := 0
    pBrush := 0
    try {
        ; 0xE200B = PixelFormat32bppPARGB (premultiplied alpha)
        if DllCall("Gdiplus\GdipCreateBitmapFromScan0",
            "Int", w, "Int", h, "Int", 0,
            "UInt", 0xE200B, "Ptr", 0, "Ptr*", &pBmp) != 0 || !pBmp
            return 0
        if DllCall("Gdiplus\GdipGetImageGraphicsContext",
            "Ptr", pBmp, "Ptr*", &pGraphics) != 0 || !pGraphics
            return 0
        DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pGraphics, "Int", 4)
        if DllCall("Gdiplus\GdipCreatePath", "Int", 0, "Ptr*", &pPath) != 0 || !pPath
            return 0
        DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", 0, "Float", 0,
            "Float", d, "Float", d, "Float", 180, "Float", 90)
        DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", w - d, "Float", 0,
            "Float", d, "Float", d, "Float", 270, "Float", 90)
        DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", w - d, "Float", h - d,
            "Float", d, "Float", d, "Float", 0, "Float", 90)
        DllCall("Gdiplus\GdipAddPathArc", "Ptr", pPath, "Float", 0, "Float", h - d,
            "Float", d, "Float", d, "Float", 90, "Float", 90)
        DllCall("Gdiplus\GdipClosePathFigure", "Ptr", pPath)
        argb := 0xFF000000 | (rgb & 0xFFFFFF)
        if DllCall("Gdiplus\GdipCreateSolidFill", "UInt", argb, "Ptr*", &pBrush) != 0 || !pBrush
            return 0
        DllCall("Gdiplus\GdipFillPath", "Ptr", pGraphics, "Ptr", pBrush, "Ptr", pPath)
        DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap",
            "Ptr", pBmp, "Ptr*", &hBitmap, "UInt", 0)
    } finally {
        if pBrush
            DllCall("Gdiplus\GdipDeleteBrush", "Ptr", pBrush)
        if pPath
            DllCall("Gdiplus\GdipDeletePath", "Ptr", pPath)
        if pGraphics
            DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pGraphics)
        if pBmp
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pBmp)
    }
    return hBitmap
}

FlatButton(dlg, u, x, y, w, h, label, handler) {
    dlg.SetFont("s" Round(9 * u) " norm c" THEME_BUTTON_TEXT, FONT_SEMI)
    ctl := dlg.Add("Text", Box(x, y, w, h) " Center 0x200 Background" BTN_IDLE, label)
    ctl.OnEvent("Click", handler)
    RoundHwnd(ctl.Hwnd, w, h, Round(18 * u))
    S.noDrag[ctl.Hwnd] := true
    return ctl
}

RoundHwnd(hwnd, w, h, radius) {
    try {
        rgn := DllCall("CreateRoundRectRgn", "Int", 0, "Int", 0,
            "Int", w + 1, "Int", h + 1, "Int", radius, "Int", radius, "Ptr")
        DllCall("SetWindowRgn", "Ptr", hwnd, "Ptr", rgn, "Int", true)
    }
}


; START keeps the accent fill, STOP reads as danger, everything else stays a
; quiet dark fill - a clear primary / destructive / utility hierarchy.
BtnBase(key) => key = "btnRun" ? BTN_IDLE
    : key = "btnStop" ? BTN_DANGER
    : BTN_QUIET
BtnHover(key) => key = "btnRun" ? BTN_IDLE_HOVER
    : key = "btnStop" ? BTN_DANGER_HOVER
    : BTN_QUIET_HOVER

UpdateRunButton() {
    if (!UI.Has("btnRun"))
        return
    try UI["btnRun"].Text := S.running ? "RUNNING" : "START"
    MainRefresh()
}

; One cell per round, right-aligned so the newest is always at the same place.
; Recent-round dots are part of the rendered backdrop.
RenderHistory() {
    MainRefresh()
}

RecordOutcome(kind) {
    S.history.Push(kind)
    while (S.history.Length > HIST_CELLS)
        S.history.RemoveAt(1)
    RenderHistory()
}

; Hover highlight for the flat buttons, so they still feel clickable.
HoverTick() {
    if !UI.Has("gui")
        return
    MouseGetPos(, , &winHwnd, &ctlHwnd, 2)
    mainHwnd := 0
    settingsHwnd := 0
    try mainHwnd := UI["gui"].Hwnd
    if SET.Has("gui")
        try settingsHwnd := SET["gui"].Hwnd
    overMain := mainHwnd && (winHwnd = mainHwnd)
    overSettings := settingsHwnd && (winHwnd = settingsHwnd)
    if !overMain && !overSettings
        ctlHwnd := 0

    ; Hovering never redraws the menu (that redraw was the flash). The hand
    ; cursor from WM_SETCURSOR shows what can be clicked instead.
    S.mainHover := ""

    if (ctlHwnd = S.hoverHwnd)
        return
    if SET.Has("buttons") {
        for ctl in SET["buttons"] {
            hwnd := 0
            try hwnd := ctl.Hwnd
            if !hwnd
                continue
            if (hwnd != S.hoverHwnd && hwnd != ctlHwnd)
                continue
            base := BTN_IDLE
            hover := BTN_IDLE_HOVER
            try base := SET["buttonBase"].Get(hwnd, BTN_IDLE)
            try hover := SET["buttonHover"].Get(hwnd, BTN_IDLE_HOVER)
            bg := (hwnd = ctlHwnd) ? hover : base
            try {
                ctl.Opt("Background" bg)
                ctl.Redraw()
            }
        }
    }
    S.hoverHwnd := ctlHwnd
}

; Keep a remembered window position on-screen if the display layout changed.
ClampToScreen(&x, &y, w, h) {
    x := Min(Max(x, 0), Max(0, A_ScreenWidth  - w))
    y := Min(Max(y, 0), Max(0, A_ScreenHeight - h))
}

; ---- drag-anywhere + position persistence ---------------------------------
OnMessage(0x0201, WM_LBUTTONDOWN)      ; start a drag
OnMessage(0x0020, WM_SETCURSOR_HAND)   ; hand cursor over buttons
OnMessage(0x0014, WM_ERASEBKGND_SKIP)  ; clear to the backdrop image, never black
OnMessage(0x0232, WM_EXITSIZEMOVE)     ; drag finished -> persist

WM_LBUTTONDOWN(wParam, lParam, msg, hwnd) {
    root := GuiTopLevel(hwnd)
    ; The docked settings panel and the main menu move as one unit: dragging
    ; the panel's background grabs the main menu instead, and the WM_MOVE
    ; sync below carries the panel along with it. Controls keep their clicks.
    if (hwnd = root && S.dragMap.Get(hwnd, "") = "settings") {
        mainHwnd := 0
        try mainHwnd := UI["gui"].Hwnd
        if mainHwnd {
            try PostMessage(0x00A1, 2, 0, , "ahk_id " mainHwnd)
            return
        }
    }
    if (S.dragMap.Has(hwnd)) {
        ; Tell the window it was clicked on its (non-existent) title bar,
        ; which is what makes a caption-less window draggable.
        try PostMessage(0x00A1, 2, 0, , "ahk_id " root)
        return
    }
    ; The main menu drags from any non-interactive part: labels, panels and
    ; the gradient all start a move, while buttons keep their clicks.
    kind := S.dragMap.Get(root, "")
    if ((kind = "main" || kind = "welcome") && !S.noDrag.Has(hwnd))
        try PostMessage(0x00A1, 2, 0, , "ahk_id " root)
}

; Real-time sync: whenever the main menu moves, the docked settings panel is
; repositioned in the same message, so the pair glides as a single window.
OnMessage(0x0003, WM_MOVE_SYNC)        ; WM_MOVE

WM_MOVE_SYNC(wParam, lParam, msg, hwnd) {
    return   ; settings lives inside the main window, nothing to keep in sync
}

WM_EXITSIZEMOVE(wParam, lParam, msg, hwnd) {
    kind := ""
    for h, k in S.dragMap
        if (GuiTopLevel(h) = hwnd)
            kind := k
    switch kind {
        case "main":
            try {
                UI["gui"].GetPos(&x, &y)
                C.UI_X := x, C.UI_Y := y
                SaveCfg("UI_X"), SaveCfg("UI_Y")
            }
        case "dialbox": CalibDialBoxMoved()
        case "dialsize": CalibDialBoxResized()
        case "barmark": CalibBarMarkMoved()
    }
}

; A control's messages arrive with the control hwnd; walk up to its window.
GuiTopLevel(hwnd) {
    try {
        root := DllCall("GetAncestor", "ptr", hwnd, "uint", 2, "ptr")  ; GA_ROOT
        if (root)
            return root
    }
    return hwnd
}

; ============================================================================
;  UI UPDATES
; ============================================================================
; The big readout is deliberately one of the four canonical states; the finer
; detail (STARTING, WAITING TICK, WAITING FOR GAME...) goes next to the LED and
; into the log, so the panel stays readable at a glance from across the room.
SetStatus(state, detail := "", led := "") {
    ; The loop calls this every poll; skip the repaint entirely when nothing
    ; changed, otherwise the transparent statics flash over the gradient.
    if (state = S.state && (detail = "" || detail = S.detail)
        && (led = "" || led = S.led) && UI.Has("state"))
        return
    S.state := state
    if (detail != "")
        S.detail := detail
    if (led != "")
        S.led := led
    if (!UI.Has("state"))
        return
    try {
        UI["state"].SetFont("c" STATE_COLORS.Get(state, "D8C8F5"))
        UI["state"].Text := state
        UI["ledLabel"].Text := S.detail
        UI["led"].SetFont("c" LED_COLORS.Get(S.led, THEME_SOFT))
        UI["led"].Text := "●  " . (S.led = "green" ? "ACTIVE" : S.led = "red" ? "ATTENTION" : "READY")
        UI["led"].Redraw()
    }
    if (led != "")
        MainRefresh()
}

UpdateStats() {
    if (!UI.Has("rounds"))
        return
    attempts := S.attempts
    rate := attempts ? Round(S.hits * 100 / attempts) : 0
    secs := (A_TickCount - S.sessionT0) // 1000
    try {
        ; Only touch controls whose value actually changed - rewriting the
        ; text of a transparent static forces a repaint of the gradient
        ; behind it, which reads as a flash on the menu every clock tick.
        SetTextIfChanged(UI["rounds"],   "" attempts)
        SetTextIfChanged(UI["hits"],     "" S.hits)
        SetTextIfChanged(UI["timeouts"], S.misses " / " S.timeouts)
        SetTextIfChanged(UI["rate"],     rate "%")
        ; A success rate is only meaningful once something has been attempted,
        ; so stay neutral at zero attempts rather than showing an alarming red.
        rateColor := !attempts ? "F4EEFF"
            : rate >= 90 ? THEME_ACCENT : rate >= 70 ? THEME_SOFT : "F0587E"
        if (S.rateColor != rateColor) {
            S.rateColor := rateColor
            UI["rate"].SetFont("c" rateColor)
            UI["rate"].Redraw()
        }
        SetTextIfChanged(UI["uptime"], Format("{:02}:{:02}:{:02}",
            secs // 3600, Mod(secs // 60, 60), Mod(secs, 60)))
    }
}

SetTextIfChanged(ctl, txt) {
    if (ctl.Text != txt)
        ctl.Text := txt
}

ClockTick() {
    UpdateStats()
}

ToggleMainMenu(*) {
    global UI, SET
    if !UI.Has("gui") || !IsObject(UI["gui"])
        return
    dlg := UI["gui"]
    hwnd := 0
    try hwnd := dlg.Hwnd
    if !hwnd
        return

    ; The menus are linked: hiding closes the settings window too, and showing
    ; brings back only the main menu.
    if (DllCall("User32\IsWindowVisible", "Ptr", hwnd) || SettingsVisible()) {
        SetTimer(SettingsFollowTick, 0)
        try dlg.Hide()
        HideSettingsWindow()
    } else {
        try dlg.Show("NoActivate")
        SetStreamProof(StreamProofEnabled)
    }
}

; The settings panel docks to the main menu's edge, so the menu has to be on
; screen for the panel to have an edge to pop off (F5 hides both).
EnsureMainMenuVisible() {
    global UI
    if !IsObject(UI) || !UI.Has("gui") || !IsObject(UI["gui"])
        return
    hwnd := 0
    try hwnd := UI["gui"].Hwnd
    if (!hwnd || DllCall("User32\IsWindowVisible", "Ptr", hwnd))
        return
    try UI["gui"].Show("NoActivate")
    SetStreamProof(StreamProofEnabled)
}

; The panel's own footprint. WinGetPos can't measure a hidden window, and the
; resulting 0x0 used to dock and shape the panel down to an invisible sliver -
; which is why the settings tab appeared not to open on the second press.
SettingsSize() {
    global SET
    w := h := 0
    hwnd := 0
    try hwnd := SET["gui"].Hwnd
    if hwnd {
        prevDetect := A_DetectHiddenWindows
        DetectHiddenWindows true
        try WinGetPos(, , &w, &h, "ahk_id " hwnd)
        DetectHiddenWindows prevDetect
    }
    if (w <= 0 || h <= 0) {
        w := SET.Get("w", 0)
        h := SET.Get("h", 0)
    }
    return {w: w, h: h}
}

; True while one of the script's own menu windows is the active window.
MenuActive() {
    global UI, SET, LoginGui
    active := WinExist("A")
    if !active
        return false
    for owner in [UI, SET] {
        if (IsObject(owner) && owner.Has("gui") && IsObject(owner["gui"])) {
            hwnd := 0
            try hwnd := owner["gui"].Hwnd
            if (hwnd && hwnd = active)
                return true
        }
    }
    try {
        if (IsObject(LoginGui) && LoginGui.Hwnd = active)
            return true
    }
    return false
}

SettingsVisible() {
    if !(S.HasOwnProp("view") && S.view = "settings") || !UI.Has("gui")
        return false
    hwnd := 0
    try hwnd := UI["gui"].Hwnd
    return hwnd && DllCall("User32\IsWindowVisible", "Ptr", hwnd, "Int") ? true : false
}

HideSettingsWindow() {
    if (S.HasOwnProp("view") && S.view = "settings")
        ShowView("dashboard")
}

UpdateHint() {
    if (!UI.Has("hint"))
        return
    try {
        UI["hint"].Text := C.HK_TOGGLE " start  ·  " C.HK_STOP " stop  ·  "
            . C.HK_MENU " open/close menu  ·  F4 stream proof on/off  ·  "
            . C.HK_CALIB " calibrate  ·  "
            . C.HK_SETTINGS " settings  ·  " C.HK_DIAG " diagnostics"

    }
}

ResetStats(*) {
    S.rounds := 0, S.hits := 0, S.attempts := 0
    S.misses := 0, S.timeouts := 0
    S.history := []
    S.sessionT0 := A_TickCount
    UpdateStats()
    RenderHistory()
    LogLine("stats reset")
}

; ============================================================================
;  LOGGING  - one sink, mirrored to macro.log and the panel's log pane
; ============================================================================
; Levels: 1 = normal, 2 = verbose (phase timings, scan results),
;         3 = trace (every probe decision). Set LogLevel in config.ini or
;         from the Settings window. Higher levels also write everything below.
LogLine(msg, level := 1) {
    if (level > C.LOG_LEVEL)
        return
    tag := (level = 2) ? "· " : (level >= 3) ? "» " : ""
    try FileAppend(FormatTime(A_Now, "yyyy-MM-dd HH:mm:ss") "." A_MSec
        . " | " tag msg "`n", C.LOG_FILE)
    ; The on-screen pane only ever shows normal-level lines; verbose and trace
    ; would scroll anything useful away within seconds.
    if (level <= 1) {
        S.logLines.Push(FormatTime(A_Now, "HH:mm:ss") " " msg)
        while (S.logLines.Length > 200)
            S.logLines.RemoveAt(1)
        RenderLog()
    }
}

LogV(msg) => LogLine(msg, 2)      ; verbose: phase timings, scan outcomes
LogT(msg) => LogLine(msg, 3)      ; trace: individual probe decisions

RenderLog() {
    if (!UI.Has("log"))
        return
    text := ""
    for line in S.logLines
        text .= line "`n"
    try {
        UI["log"].Value := text
        ; WM_VSCROLL / SB_BOTTOM - keeps the newest line visible.
        PostMessage(0x0115, 7, 0, , "ahk_id " UI["log"].Hwnd)
    }
}

Dbg(msg) {
    if (C.DEBUG)
        LogLine("dbg | " msg, 2)
}

; ============================================================================
;  DIAGNOSTICS  - one command that captures everything needed to explain
;  "I pressed start and nothing happened". Written to macro.log so the whole
;  file can just be sent as-is.
; ============================================================================
DumpDiagnostics(why := "manual") {
    LogLine("---------- DIAGNOSTICS (" why ") ----------")
    LogLine("Metal Detector by Coop")
    LogLine(Format("ahk {}  {}-bit  admin={}  script={}",
        A_AhkVersion, A_PtrSize * 8, A_IsAdmin, A_ScriptName))
    LogLine(Format("screen {}x{}  dpi {}  configured for {}",
        A_ScreenWidth, A_ScreenHeight, A_ScreenDPI, C.CONFIGURED_FOR))

    ; ---- window gate: the single most common reason nothing happens --------
    exe := "?", ttl := "?", cls := "?"
    try exe := WinGetProcessName("A")
    try ttl := WinGetTitle("A")
    try cls := WinGetClass("A")
    LogLine("gate: WIN_MATCH=" C.WIN_MATCH "  requireActive=" C.REQUIRE_GAME_ACTIVE)
    LogLine(Format("gate: active exe='{}' class='{}' title='{}'",
        exe, cls, SubStr(ttl, 1, 48)))
    LogLine("gate: MATCHES=" (GameActive() ? "YES" : "NO")
        . (GameActive() ? "" : "   <-- macro will idle until this is YES"))

    ; ---- geometry ----------------------------------------------------------
    LogLine(Format("dial: centre {},{}  radius {}  band {}..{}",
        C.DIAL_CX, C.DIAL_CY, C.DIAL_RADIUS, G.rMin, G.rMax))
    LogLine(Format("bar: y{}  track {}..{}  fullProbe {}",
        C.BAR_Y, C.BAR_X_LEFT, C.BAR_X_RIGHT, C.BAR_FULL_X))
    LogLine(Format("keys: start='{}' hit='{}'  hotkeys toggle={} stop={} "
        . "calib={} settings={} diag={}",
        C.START_KEY, C.CONFIRM_KEY, C.HK_TOGGLE, C.HK_STOP,
        C.HK_CALIB, C.HK_SETTINGS, C.HK_DIAG))
    LogLine(Format("slots: autoSwap={} list='{}' active='{}' proven={} walked={}",
        C.AUTO_SWAP_SLOTS ? "YES" : "NO", C.SLOT_KEYS, ActiveStartKey(),
        S.slotProven ? "YES" : "NO", S.slotProbes))
    ; Same 0x{:06X} form used everywhere else in this file - a plain width or
    ; alternate-form spec is not worth the risk for a log line.
    LogLine(Format("colours: arc=0x{:06X}/{} tick=0x{:06X}/{} bar=0x{:06X}/{}"
        . "  dominance red{} blue{} teal{}",
        C.ARC_COLOR, C.ARC_TOL, C.TICK_COLOR, C.TICK_TOL, C.BAR_COLOR, C.BAR_TOL,
        C.RED_DOMINANCE, C.BLUE_DOMINANCE, C.TEAL_DOMINANCE))

    ; ---- live pixel read-back ---------------------------------------------
    ; Proves whether PixelGetColor works at all here, and shows what is really
    ; under each probe rather than what the config hopes is there.
    black := 0, reads := 0
    centre := PixelAt(C.DIAL_CX, C.DIAL_CY)
    LogLine(Format("probe centre ({},{}) = {}", C.DIAL_CX, C.DIAL_CY, HexOrFail(centre)))
    for deg in [0, 45, 90, 135, 180, 225, 270, 315] {
        pt := ProbeAt(deg, C.DIAL_RADIUS)
        v := PixelAt(pt.x, pt.y)
        reads++, black += ((v = 0) ? 1 : 0)
        LogLine(Format("probe dial {}deg ({},{}) = {}  arcBlue={} tickRed={}",
            deg, pt.x, pt.y, HexOrFail(v), IsArcBlue(v), IsTickRed(v)), 2)
    }
    bl := PixelAt(C.BAR_X_LEFT + 6, C.BAR_Y)
    bf := PixelAt(C.BAR_FULL_X, C.BAR_Y)
    reads += 2, black += (bl = 0) + (bf = 0)
    LogLine(Format("probe bar left ({},{}) = {}  teal={}",
        C.BAR_X_LEFT + 6, C.BAR_Y, HexOrFail(bl), IsBarTeal(bl)))
    LogLine(Format("probe bar full ({},{}) = {}  teal={}",
        C.BAR_FULL_X, C.BAR_Y, HexOrFail(bf), IsBarTeal(bf)))
    if (black = reads)
        LogLine("!! every probe read pure black - PixelGetColor cannot see the"
            . " game. Switch FiveM to BORDERLESS WINDOWED.")

    ; ---- pixel-read speed --------------------------------------------------
    ; This is the number that decides whether the macro can work at all. A full
    ; annulus sweep is 180 reads and the dial only exists for ~880 ms, so at
    ; 5 ms per read the scan cannot finish in time. Measured, not assumed.
    t := A_TickCount
    Loop 20
        PixelGetColor(C.DIAL_CX, C.DIAL_CY)
    perCall := (A_TickCount - t) / 20
    t := A_TickCount
    okGrab := GrabDial()
    Loop 180
        GrabAt(C.DIAL_CX, C.DIAL_CY)
    grabSweep := A_TickCount - t
    GrabEnd()
    LogLine(Format("pixel speed: PixelGetColor {:.2f} ms/call -> a 180-probe "
        . "sweep would cost {:.0f} ms", perCall, perCall * 180))
    LogLine(Format("pixel speed: screen grab {} -> capture + 180 reads = {} ms",
        okGrab ? "ACTIVE" : "UNAVAILABLE", grabSweep))
    if (perCall * 180 > 500 && !okGrab)
        LogLine("!! per-pixel reads are far too slow here and the screen grab is"
            . " unavailable - the dial will vanish before a scan completes")
    LogLine("---------- END DIAGNOSTICS ----------")
}

HexOrFail(v) => (v < 0) ? "READ-FAILED" : Format("0x{:06X}", v)

; ============================================================================
;  HOTKEYS  - registered dynamically so rebinds apply without a restart
; ============================================================================
; Every hotkey is global so the panel stays fully usable while tabbed out of
; the game: start/stop, calibrate, settings, menu and diagnostics all fire
; from anywhere. The macro loop itself still waits for the game window (per
; REQUIRE_GAME_ACTIVE) before pressing anything, so starting it while tabbed
; out simply arms it - it engages the moment the game has focus again.
RegisterHotkeys() {
    UnregisterHotkeys()
    ; Works while tabbed into the game. FiveM reads the keyboard through raw
    ; input, so a hotkey on F8 always reaches its console no matter how it is
    ; registered - start/stop therefore defaults to a key the game ignores.
    AddHotkey(C.HK_TOGGLE,   (*) => ToggleRun(),         "")
    AddHotkey(C.HK_CALIB,    (*) => ToggleCalibration(), "")
    AddHotkey(C.HK_STOP,     (*) => HardStop(),          "")
    AddHotkey(C.HK_SETTINGS, (*) => ShowSettings(),      "")
    AddHotkey(C.HK_MENU,     (*) => ToggleMainMenu(),    "")
    ; Global, not gated on the game window: you need this most when the gate
    ; itself is the thing that is broken.
    AddHotkey(C.HK_DIAG,     (*) => DumpDiagnostics("hotkey"), "")
    UpdateHint()
}

AddHotkey(key, fn, ctx) {
    if (key = "")
        return
    ; Keyboard-hook variant: the key is swallowed and the script's own Send
    ; calls can never retrigger it.
    if (!InStr(key, "$") && !InStr(key, "~"))
        key := "$" key
    try {
        if (IsObject(ctx))
            HotIf ctx
        else
            HotIf()
        Hotkey key, fn, "On"
        S.hotkeys.Push({key: key, ctx: ctx})
    } catch as e {
        LogLine("ERROR could not register hotkey '" key "': " e.Message)
        SetStatus("ERROR", "BAD HOTKEY", "red")
    } finally {
        HotIf()
    }
}

UnregisterHotkeys() {
    for h in S.hotkeys {
        try {
            if (IsObject(h.ctx))
                HotIf h.ctx
            else
                HotIf()
            Hotkey h.key, "Off"
        }
    }
    HotIf()
    S.hotkeys := []
}

; Ctrl+Click sampling only exists while calibration mode is on. The ~ prefix
; lets the click through, so it never eats a real click.
CalibHotkeys(on) {
    mode := on ? "On" : "Off"
    try {
        HotIf()
        Hotkey "~^LButton", (*) => CalibrationSample(), mode
    }
}

; ============================================================================
;  RUN CONTROL
; ============================================================================
StartRun(*) {
    if !S.running
        ToggleRun()
}

ToggleRun(*) {
    if (S.running) {
        S.running := false            ; MainLoop unwinds on its next check
        UpdateRunButton()             ; feedback now, not when the loop notices
        LogLine("stop requested after " S.rounds " rounds")
        return
    }
    ; Guard against a second MainLoop stacking on top of one that is still
    ; unwinding after a rapid stop/start.
    if (S.loopActive) {
        Dbg("start ignored - previous loop still unwinding")
        return
    }
    S.running  := true
    S.blackRun := 0
    ResetSlots()
    UpdateRunButton()
    LogLine("started")
    if (SlotRotationOn())
        LogLine("detector slots: " C.SLOT_KEYS "  (starting on "
            . ActiveStartKey() ", swapping when one breaks)")
    MainLoop()
}

HardStop(*) {
    S.running := false
    ReleaseKeys()
    LogLine("EMERGENCY STOP - rounds " S.rounds
        . " miss " S.misses " timeout " S.timeouts)
    SetStatus("IDLE", "STOPPED", "amber")
    UpdateRunButton()
}

ReleaseKeys() {
    try SendInput "{Blind}{" StripMods(C.START_KEY) " up}"
    try SendInput "{Blind}{" StripMods(ActiveStartKey()) " up}"
    try SendInput "{Blind}{" StripMods(C.CONFIRM_KEY) " up}"
}

; ============================================================================
;  DETECTOR SLOT ROTATION
; ============================================================================
; A detector is consumed as it is used, and once it breaks its hotbar key opens
; nothing. Rather than hard-stopping on that, the macro moves to the next
; configured slot. A slot that has never opened the minigame is only probed
; once - walking a bar of empty slots must not cost the full retry budget each -
; while the slot currently producing rounds keeps the whole budget before it is
; written off as broken.
SlotKeys() {
    out := []
    for part in StrSplit(C.SLOT_KEYS, ",") {
        key := Trim(part)
        if (key != "")
            out.Push(key)
    }
    return out
}

SlotRotationOn() {
    return C.AUTO_SWAP_SLOTS && SlotKeys().Length > 1
}

; The key the current round should press to open the minigame.
ActiveStartKey() {
    if (!C.AUTO_SWAP_SLOTS)
        return C.START_KEY
    keys := SlotKeys()
    if (keys.Length = 0)
        return C.START_KEY
    if (S.slotIdx < 1 || S.slotIdx > keys.Length)
        S.slotIdx := 1
    return keys[S.slotIdx]
}

SlotRetryBudget() {
    if (!SlotRotationOn() || S.slotProven)
        return C.MAX_START_RETRIES
    return 1
}

ResetSlots() {
    S.slotIdx    := 1
    S.slotProven := false
    S.slotProbes := 0
}

; Steps to the next slot. Returns its key, or "" when every configured slot has
; been tried since the last time a minigame actually opened - the point at which
; the problem is no longer one broken detector.
AdvanceSlot() {
    keys := SlotKeys()
    if (!SlotRotationOn())
        return ""
    S.slotProbes++
    if (S.slotProbes >= keys.Length)
        return ""
    S.slotIdx    := (S.slotIdx >= keys.Length) ? 1 : S.slotIdx + 1
    S.slotProven := false
    return keys[S.slotIdx]
}

OnShutdown(reason, code) {
    ReleaseKeys()
    try {
        UI["gui"].GetPos(&x, &y)
        C.UI_X := x, C.UI_Y := y
        SaveCfg("UI_X"), SaveCfg("UI_Y")
    }
    LogLine("session end (" reason ") rounds " S.rounds
        . " miss " S.misses " timeout " S.timeouts)
    GrabFree()                       ; release the DC/bitmap held for capture
}

; ============================================================================
;  MAIN LOOP
; ============================================================================
MainLoop() {
    S.loopActive := true
    startFails := 0
    hardError := false
    try {
        while (S.running) {
            ; The window gate is by far the most common reason "nothing
            ; happens", and it used to be completely silent - the loop just
            ; span here forever. Log the transition with the window that is
            ; actually focused, then a heartbeat so the log shows it is still
            ; waiting rather than dead.
            if (C.REQUIRE_GAME_ACTIVE && !GameActive()) {
                SetStatus("IDLE", "WAITING FOR GAME", "amber")
                if (!S.gateWasBlocked) {
                    S.gateWasBlocked := true
                    S.gateSince := A_TickCount
                    S.gateLogged := A_TickCount
                    exe := "?", ttl := "?"
                    try exe := WinGetProcessName("A")
                    try ttl := WinGetTitle("A")
                    LogLine(Format("WAITING: '{}' is not the game. WIN_MATCH='{}'"
                        . "  active exe='{}' title='{}'",
                        C.WIN_MATCH, C.WIN_MATCH, exe, SubStr(ttl, 1, 40)))
                    LogLine("  -> click into FiveM. If it is already focused,"
                        . " WIN_MATCH is wrong: press " C.HK_CALIB
                        . " to see the real exe name.")
                } else if (A_TickCount - S.gateLogged > 15000) {
                    S.gateLogged := A_TickCount
                    LogLine(Format("still waiting for the game window ({}s)",
                        (A_TickCount - S.gateSince) // 1000))
                }
                Sleep 200
                continue
            }
            if (S.gateWasBlocked) {
                S.gateWasBlocked := false
                LogLine(Format("game window active after {}s - resuming",
                    (A_TickCount - S.gateSince) // 1000))
            }

            result := PlayRound()
            if (!S.running)
                break

            ; Only "no-dial" - the minigame genuinely not opening - counts
            ; toward the hard stop. A mistimed hit or a slow bar is a normal
            ; failed round: log it, restart, and do not escalate.
            if (result = "no-dial") {
                startFails++
                deadKey := ActiveStartKey()
                budget  := SlotRetryBudget()
                LogLine("no dial after pressing " deadKey
                    . " (attempt " startFails "/" budget ")")
                if (S.lastScanMiss != "")
                    LogLine("  " S.lastScanMiss)
                if (startFails >= budget) {
                    nextKey := AdvanceSlot()
                    if (nextKey = "") {
                        hardError := true
                        S.running := false
                        break
                    }
                    startFails := 0
                    LogLine("slot " deadKey " gave no minigame (empty slot or"
                        . " the detector broke) - switching to slot " nextKey)
                    SetStatus("SEARCHING DIAL", "SLOT " nextKey, "amber")
                }
                Sleep C.RETRY_GAP_MS
                continue
            }

            ; The minigame opened, so this slot holds a working detector: give
            ; it the full retry budget from here on, and clear the walk counter
            ; so a later break can walk the whole bar again.
            startFails   := 0
            S.slotProven := true
            S.slotProbes := 0
            Sleep((result = "ok") ? C.ROUND_GAP_MS : C.RETRY_GAP_MS)
        }
    } finally {
        S.loopActive := false
        UpdateRunButton()
    }

    if (hardError) {
        SetStatus("ERROR", "NO MINIGAME", "red")
        if (SlotRotationOn()) {
            LogLine("HARD STOP - tried every detector slot (" C.SLOT_KEYS
                . "), none opened the minigame")
            LogLine("check: a detector is actually in one of those slots,"
                . " borderless windowed, dial coords (" C.HK_CALIB ")")
        } else {
            LogLine("HARD STOP - pressed " C.START_KEY " " C.MAX_START_RETRIES
                . "x, never found the Skill Check zone")
            LogLine("check: detector equipped, '" C.START_KEY "' is the right"
                . " key, borderless windowed, dial coords (" C.HK_CALIB ")")
        }
        DumpDiagnostics("hard stop - no minigame")
    } else {
        SetStatus("IDLE", "PAUSED", "amber")
    }
}

; Returns "ok" | "no-dial" | "tick-timeout" | "miss" | "bar-timeout" | "aborted"
PlayRound() {
    ; ---- 0. make sure the previous round has actually finished ------------
    ; A mistimed press leaves the dial up until its tick completes the lap.
    ; Pressing the start key into an already-running minigame is undefined, and
    ; worse, the next scan would lock onto that stale arc whose tick has already
    ; gone past - burning the full 8s tick timeout. Let it close first.
    if (FindArc()) {
        Dbg("dial still up from the previous round - waiting for it to clear")
        if (!WaitForArcGone(C.DIAL_CLEAR_TIMEOUT))
            LogLine("WARNING dial still present after " C.DIAL_CLEAR_TIMEOUT
                . "ms - starting anyway")
    }

    ; ---- 1. open the minigame --------------------------------------------
    SetStatus("SEARCHING DIAL", "STARTING", "green")
    if (!SendGameKey(ActiveStartKey()))
        return "aborted"

    ; ---- 2. find the blue arc --------------------------------------------
    SetStatus("SEARCHING DIAL", "SCANNING ARC", "green")
    S.lastScanMiss := ""
    scanT0 := A_TickCount
    arc := WaitForArc(C.DIAL_APPEAR_TIMEOUT)
    if (!S.running)
        return "aborted"
    if (!arc) {
        LogV(Format("arc scan gave up after {}ms", A_TickCount - scanT0))
        return "no-dial"
    }

    ; Acquisition latency is the number that decides whether a fast round is
    ; winnable at all - the tick can reach the arc in as little as ~25ms.
    LogLine(Format("skill check found in {}ms: zone mid {:.1f}deg width {:.1f}deg radius {}",
        A_TickCount - scanT0, arc.mid, arc.width, arc.radius))
    Dbg(Format("arc found: mid {:.1f}deg width {:.1f}deg radius {}",
        arc.mid, arc.width, arc.radius))

    ; ---- 3. wait for the tick, then press HIT - once per stage -----------
    ; The Skill Check runs in stages ("1/2", "2/2"). A good hit either closes
    ; the panel (last stage) or moves the pink zone somewhere new (next
    ; stage). A zone still sitting in the same place means we mistimed it.
    stage := 0
    Loop {
        stage++
        SetStatus("SEARCHING DIAL", "STAGE " stage " - WAIT TICK", "green")
        tickStart := A_TickCount
        hit := WaitForTick(arc, C.DIAL_HIT_TIMEOUT)
        if (hit = "") {
            if (!S.running)
                return "aborted"
            S.attempts++
            S.timeouts++
            RecordOutcome("timeout")
            UpdateStats()
            LogLine(Format("TIMEOUT waiting for tick on stage {} after {}ms "
                . "(zone mid {:.0f}deg width {:.0f}deg) - restarting",
                stage, A_TickCount - tickStart, arc.mid, arc.width))
            return "tick-timeout"
        }

        ; A tick that was already inside the zone has less of the window
        ; left, so keep the delay short. It stays randomised.
        ; "predict" already aimed ahead of the marker - press right away.
        delay := (hit = "predict") ? 0
            : (hit = "immediate")
            ? Random(C.REACTION_MIN_MS, C.REACTION_MIN_MS + 5)
            : Random(C.REACTION_MIN_MS, C.REACTION_MAX_MS)
        LogLine(Format("stage {}: marker {} after {}ms - pressing in {}ms",
            stage, hit, A_TickCount - tickStart, delay))
        Sleep(delay)
        if (!SendGameKey(C.CONFIRM_KEY))
            return "aborted"

        Sleep C.POST_PRESS_SETTLE
        next := FindArc()
        if (!next && stage < C.MAX_STAGES)
            next := WaitForArc(C.STAGE_GAP_MS)   ; next stage may take a moment
        if (!S.running)
            return "aborted"
        if (!next)
            break                                 ; panel closed - all stages done
        ; Dial still up: either the next stage (1/2 -> 2/2) or a mistimed
        ; press. Either way the right move is the same - hit it again, using
        ; the zone where it is NOW.
        if (stage < C.MAX_STAGES) {
            LogLine(Format("hit {} done - dial still up, zone now at {:.0f}deg"
                . " width {:.0f}deg ({}) - going again", stage, next.mid,
                next.width, ArcMoved(arc, next) ? "next stage" : "same spot"))
            arc := next
            continue
        }
        S.attempts++
        S.misses++
        RecordOutcome("miss")
        UpdateStats()
        LogLine(Format("MISS - dial still up after {} presses of {}",
            stage, C.CONFIRM_KEY))
        return "miss"
    }

    ; The dial cleared, so the press landed - counted here rather than after
    ; the bar stage, so a bar timeout can't hide a successful hit. The attempt
    ; is counted once, in exactly one of hit / miss / timeout.
    S.attempts++
    S.hits++
    UpdateStats()

    ; ---- 4. wait for the progress bar to fill ----------------------------
    SetStatus("WAITING BAR", "FILLING", "green")
    barResult := WaitForBar()
    if (!S.running)
        return "aborted"
    if (barResult = "timeout") {
        ; The hit already landed, so this is only a bar-read failure - it must
        ; not be counted as a failed attempt on top of the hit.
        UpdateStats()
        LogLine("TIMEOUT waiting for progress bar - restarting")
        return "bar-timeout"
    }

    S.rounds++
    RecordOutcome("ok")
    UpdateStats()
    ; hit=immediate means the arc spanned 12 o'clock. Logging it makes the real
    ; frequency of that case measurable rather than assumed at ~19%.
    LogLine(Format("round {} ok  ({} stage{}, last zone mid {:.0f}deg width {:.0f}deg,"
        . " hit {}, bar {})", S.rounds, stage, stage = 1 ? "" : "s",
        arc.mid, arc.width, hit, barResult))
    return "ok"
}

; ============================================================================
;  DIAL DETECTION
; ============================================================================
; Acquisition speed matters more than it looks. The tick always starts at 12
; o'clock and sweeps once at ~401 deg/s, so a randomly-placed arc is reached
; after 0..0.90s. Any millisecond spent finding the arc is a millisecond the
; tick might already have used up, so the fast path below runs first and the
; exhaustive sweep is only the fallback.
FindArcSeed() {
    ; Fast path: one native region search over the dial's bounding box (~2ms).
    ; The hit is validated against the annulus because that box also contains
    ; sand, characters and - near the coast - blue water.
    x1 := Max(0, G.boxL), y1 := Max(0, G.boxT)
    x2 := Min(A_ScreenWidth - 1,  G.boxL + G.boxW)
    y2 := Min(A_ScreenHeight - 1, G.boxT + G.boxH)
    try {
        if (PixelSearch(&fx, &fy, x1, y1, x2, y2, C.ARC_COLOR, C.ARC_TOL)) {
            dx := fx - C.DIAL_CX, dy := C.DIAL_CY - fy
            r := Sqrt(dx * dx + dy * dy)
            if (r >= G.rMin && r <= G.rMax)
                return Mod(ATan2(dy, dx) * G.DEG + 360, 360)
            Dbg(Format("PixelSearch hit off-annulus (r={:.1f}) - sweeping", r))
        }
    } catch as e {
        Dbg("PixelSearch failed (" e.Message ") - sweeping")
    }

    ; Fallback: sweep the annulus itself. Immune to anything off-radius,
    ; costs ~180 probes at 2 deg steps.
    ang := 0
    bestScore := -999, bestAng := 0, bestCol := -1, blackHits := 0, n := 0
    ringHits := 0
    while (ang < 360) {
        ; three rings, not one: the saved radius and 6px either side of it
        for dr in [-6, 6] {
            ptx := ProbeAt(ang, C.DIAL_RADIUS + dr)
            if (IsArcBlue(PixelAt(ptx.x, ptx.y)))
                return ang
        }
        pt := ProbeAt(ang, C.DIAL_RADIUS)
        col := PixelAt(pt.x, pt.y)
        if (IsArcBlue(col))
            return ang
        ; Remember how close the annulus ever got to the arc colour. A failed
        ; scan can then say what was actually on screen instead of only "not
        ; found", which is the difference between a 5-second fix and guessing.
        n++
        if (col = 0)
            blackHits++
        if (col >= 0) {
            ; pink-ness: how far red AND blue both sit above green
            score := Min(((col >> 16) & 0xFF) - ((col >> 8) & 0xFF),
                         (col & 0xFF) - ((col >> 8) & 0xFF))
            if (score > bestScore)
                bestScore := score, bestAng := ang, bestCol := col
            ; Count how much of the ring looks like the dial's own dark grey
            ; outline. This is what separates the two very different causes of
            ; a failed scan: a ring that is mostly there means the minigame IS
            ; open and the ARC is what we are failing to see (wrong colour or
            ; radius); a ring that is absent means the minigame never opened.
            if (IsRingPixel(col))
                ringHits++
        }
        ang += C.SCAN_STEP_DEG
    }
    if (ringHits * 100 // Max(1, n) >= 50)
        verdict := "  -> DIAL RING IS PRESENT: the minigame is open but the arc"
            . " is not being recognised. Check ArcColor / DialRadius ("
            . C.HK_CALIB ")"
    else
        verdict := "  -> no dial ring either: the minigame never opened. Check"
            . " the detector is equipped and '" ActiveStartKey()
            . "' is the right key"

    S.lastScanMiss := Format("no pink zone on the ring (r={}); closest was {} at "
        . "{}deg with zone score {} (need > {}); ring-like {}/{}; black {}/{}",
        C.DIAL_RADIUS, HexOrFail(bestCol), bestAng, bestScore,
        C.BLUE_DOMINANCE, ringHits, n, blackHits, n)
        . "`n" . verdict
    LogT(S.lastScanMiss)
    return -1
}

; At a known-good angle, find the middle of the arc's radial band, so every
; later probe sits in the centre of the stroke rather than on its edge.
RefineRadius(ang) {
    best := -1, bestLen := 0, curStart := -1, curLen := 0
    r := G.rMin
    while (r <= G.rMax) {
        if (IsArcBlue(ProbeAt(ang, r))) {
            if (curStart < 0)
                curStart := r
            curLen++
        } else {
            if (curLen > bestLen) {
                bestLen := curLen
                best := curStart + (curLen - 1) // 2
            }
            curStart := -1, curLen := 0
        }
        r++
    }
    if (curLen > bestLen) {
        bestLen := curLen
        best := curStart + (curLen - 1) // 2
    }
    return bestLen ? best : -1
}

FindArc() {
    ; One capture serves the seed search, the radial refine and the full
    ; annulus sweep below - hundreds of probes for the cost of a single BitBlt.
    GrabDial()
    try
        return FindArcInGrab()
    finally
        GrabEnd()
}

FindArcInGrab() {
    seed := FindArcSeed()
    if (seed < 0)
        return false

    radius := RefineRadius(seed)
    if (radius < 0)
        return false

    ; Sample the WHOLE annulus rather than walking outward from the seed.
    ;
    ; This matters more than it looks. The red tick renders ON TOP of the arc -
    ; that overlap is the entire basis of the single-pixel trigger - so a walk
    ; outward from a seed stops dead the instant it reaches the tick and
    ; reports a truncated arc with a shifted midpoint. Observed live: a fixed
    ; 68.8 deg arc measured 26 deg and 42 deg on exactly the rounds where the
    ; tick was sitting on it, silently moving the trigger probes off the real
    ; hit zone.
    step := C.SCAN_STEP_DEG
    n := 360 // step
    blue := []
    Loop n
        blue.Push(IsArcBlue(ProbeAt((A_Index - 1) * step, radius)) ? 1 : 0)

    ; Longest circular run of blue, tolerating gaps up to ARC_GAP_TOL_DEG, so a
    ; tick lying across the arc bridges the gap instead of splitting the arc.
    gapTol := Max(1, C.ARC_GAP_TOL_DEG // step)
    bestLen := 0, bestStart := -1
    Loop n {
        s := A_Index - 1
        if (!blue[s + 1])
            continue
        if (blue[Mod(s - 1 + n, n) + 1])      ; only start at a rising edge
            continue
        len := 0, gap := 0, k := 0
        while (k < n) {
            if (blue[Mod(s + k, n) + 1])
                len := k + 1, gap := 0
            else if (++gap > gapTol)
                break
            k++
        }
        if (len > bestLen)
            bestLen := len, bestStart := s
    }
    if (bestStart < 0)
        return false

    width := bestLen * step
    if (width < C.ARC_WIDTH_MIN_DEG || width > C.ARC_WIDTH_MAX_DEG) {
        Dbg(Format("arc rejected: width {:.0f}deg outside {}..{}",
            width, C.ARC_WIDTH_MIN_DEG, C.ARC_WIDTH_MAX_DEG))
        return false
    }

    mid := Mod((bestStart + (bestLen - 1) / 2) * step + 360, 360)
    return {mid: mid, width: width, radius: radius,
            lo: Mod(mid - width / 2 + 360, 360),
            hi: Mod(mid + width / 2 + 360, 360)}
}

WaitForArc(timeoutMs) {
    deadline := A_TickCount + timeoutMs
    t0 := A_TickCount, lastLocate := 0
    while (A_TickCount < deadline) {
        if (!S.running || (C.REQUIRE_GAME_ACTIVE && !GameActive()))
            return false
        if (arc := FindArc())
            return arc
        ; Not where we expected it: the Skill Check panel is not centred, and
        ; its position can differ per server / resolution. Look for it on the
        ; whole screen - aggressively, because a round is short. Try almost
        ; immediately (40ms, just enough for the dial to have drawn) and keep
        ; retrying every 150ms until it is found, so an off-centre panel is
        ; caught within the same round instead of after it has moved on.
        if (C.AUTO_LOCATE_DIAL && A_TickCount - t0 > 40
                && A_TickCount - lastLocate > 150) {
            lastLocate := A_TickCount
            if (AutoLocateDial() && (arc := FindArc()))
                return arc
            ; Preset colours did not match on this server. Find the dial by its
            ; colour-independent grey ring and learn the real zone colour off
            ; the ring, then try once more.
            if (C.AUTO_LEARN_COLOUR && AutoLearnDial() && (arc := FindArc()))
                return arc
        }
        Sleep 15
    }
    return false
}

; ============================================================================
;  AUTO-LOCATE  - find the Skill Check dial anywhere on screen
; ============================================================================
; 1. PixelSearch the whole screen for the pink zone colour.
; 2. A zone pixel lies ON the ring, so the centre is DIAL_RADIUS away from it in
;    some direction. Try every direction and keep the centre whose circle is
;    covered by ring pixels (grey stroke / pink zone / red marker).
; 3. Nudge that centre a few pixels to sit dead on the ring, then save it.
; Sand, characters or menus that happen to be pink fail step 2 and the search
; carries on below them.
AutoLocateDial() {
    if (!C.AUTO_LOCATE_DIAL)
        return false
    EnsureSaneRadius()
    R := C.DIAL_RADIUS
    W := A_ScreenWidth, H := A_ScreenHeight
    ; Presets are a typical shade, so allow more leeway; a sampled custom
    ; colour is exact. The ring check below rejects anything that isn't a dial.
    tol := C.ZONE_STYLE = "custom" ? Min(C.ARC_TOL, 25) : 45
    ; Search the screen in strips, top to bottom. Anything pink that isn't a
    ; dial (a server logo, HUD icons, this macro's own window) is stepped past
    ; to the right, so it can't use up the search before the dial is reached.
    ; The old search restarted 8px lower each time and gave up after 30 tries -
    ; a pink logo in the top corner (2026-10-11 screenshot) ate all of them.
    own := OwnWindowRects()
    band := 40, tries := 0
    ; The zone's exact shade differs between servers and Skill Check versions
    ; (0xC345AF on the old one, 0xFF88F0 on the current one), and PixelSearch
    ; only finds colours within `tol` of the one it's given. So try the saved
    ; colour first, then the other known shades of the same family.
    for searchCol in ZoneSearchColours() {
        by := 0
        while (by < H) {
            sx := 0
            while (sx < W) {
                found := false
                try found := PixelSearch(&fx, &fy, sx, by, W - 1, Min(H - 1, by + band - 1), searchCol, tol)
                if (!found)
                    break
                if (r := RectAt(own, fx, fy)) {      ; our own menu / overlays
                    sx := r[3] + 1
                    continue
                }
                if (++tries > 150)
                    return false
                if (loc := DialCentreFrom(fx, fy, R)) {
                    if (!ValidDial(loc)) {               ; degenerate (edge / tiny) - keep looking
                        sx := fx + 2 * R + 20
                        continue
                    }
                    moved := Abs(loc.x - C.DIAL_CX) + Abs(loc.y - C.DIAL_CY)
                    if (moved > 2 || Abs(loc.r - C.DIAL_RADIUS) > 2) {
                        C.DIAL_CX := loc.x, C.DIAL_CY := loc.y
                        if (Abs(loc.r - C.DIAL_RADIUS) > 2)
                            C.DIAL_RADIUS := loc.r
                        SaveCfg("DIAL_CX"), SaveCfg("DIAL_CY"), SaveCfg("DIAL_RADIUS")
                        RecalcGeometry()
                        try PositionCalibOverlays()
                        LogLine(Format("dial auto-located at {},{} radius {} (ring match {}%)",
                            loc.x, loc.y, C.DIAL_RADIUS, loc.pct))
                    }
                    return true
                }
                sx := fx + 2 * R + 20                 ; not a dial - look further right
            }
            by += band
        }
    }
    return false
}

; Screen rectangles of this macro's own visible windows (menu, calibration
; overlays) as [x1, y1, x2, y2] - the dial search skips them.
OwnWindowRects() {
    out := []
    try {
        for hwnd in WinGetList("ahk_pid " DllCall("GetCurrentProcessId")) {
            wx := wy := ww := wh := 0
            try WinGetPos(&wx, &wy, &ww, &wh, "ahk_id " hwnd)
            if (ww > 0 && wh > 0)
                out.Push([wx, wy, wx + ww - 1, wy + wh - 1])
        }
    }
    return out
}

RectAt(rects, x, y) {
    for rc in rects
        if (x >= rc[1] && x <= rc[3] && y >= rc[2] && y <= rc[4])
            return rc
    return 0
}

; Colours AutoLocateDial looks for: the saved zone colour, then every known
; shade of its family (pink and purple are one family - see ColourFamily).
ZoneSearchColours() {
    out := [C.ARC_COLOR]
    extra := Map(
        "pink",   [0xFF88F0, 0xC345AF, 0xF0479C, 0xE07CD7, 0x9D4EFF, 0x8B5CF6],
        "purple", [0xFF88F0, 0xC345AF, 0x9D4EFF, 0x8B5CF6, 0xF0479C, 0xE07CD7],
        "green",  [0x3CC48F, 0x22C55E], "blue", [0x3B82F6, 0x2F7BFF], "yellow", [0xF2C14E, 0xF5D90A])
    for col in extra.Get(C.ZONE_STYLE, [])
        if (col != C.ARC_COLOR)
            out.Push(col)
    return out
}

; Given one zone pixel, return {x, y, r, pct} of the ring it sits on, or false.
DialCentreFrom(px, py, ringR) {
    if (ringR < 20)                           ; a point-sized ring matches any grey pixel
        return false
    span := 2 * ringR + 16
    if (!GrabRegion(px - span, py - span, 2 * span + 1, 2 * span + 1))
        return false                          ; no fast capture - too slow to try
    try {
        N := 60
        best := 0, bestX := 0, bestY := 0
        ang := 0
        while (ang < 360) {
            a := ang * G.RAD
            cx := px + Round(ringR * Cos(a)), cy := py - Round(ringR * Sin(a))
            sc := RingScore(cx, cy, ringR, N)
            if (sc > best)
                best := sc, bestX := cx, bestY := cy
            ang += 3
        }
        if (best < N * 0.6)
            return false
        ; fine-tune the centre
        cx0 := bestX, cy0 := bestY
        Loop 9 {
            dx := A_Index - 5
            Loop 9 {
                dy := A_Index - 5
                sc := RingScore(cx0 + dx, cy0 + dy, ringR, N)
                if (sc > best)
                    best := sc, bestX := cx0 + dx, bestY := cy0 + dy
            }
        }
        ; the radius is the middle of the band where the ring is solid
        scores := Map(), top := 0
        rad := ringR - 8
        while (rad <= ringR + 8) {
            scores[rad] := RingScore(bestX, bestY, rad, N)
            top := Max(top, scores[rad])
            rad++
        }
        lo := 0, hi := 0
        for rad, sc in scores {
            if (sc >= top * 0.9) {
                if (!lo)
                    lo := rad
                hi := rad
            }
        }
        pct := Round(best * 100 / N)
        if (pct < 80)
            return false
        return {x: bestX, y: bestY, r: lo ? Round((lo + hi) / 2) : ringR, pct: pct}
    } finally {
        GrabEnd()
    }
}

RingScore(cx, cy, r, n) {
    hits := 0
    Loop n {
        a := (A_Index - 1) * 360 / n * G.RAD
        if (IsRingPixel(GrabAt(cx + Round(r * Cos(a)), cy - Round(r * Sin(a)))))
            hits++
    }
    return hits
}

; ============================================================================
;  AUTO-LEARN  - find the dial by its RING and read the zone colour off it
; ============================================================================
; AutoLocateDial only works if the zone is a shade near one of the presets. On
; a server whose zone is some other colour, that fails and nothing is ever
; found. This path does not care what colour the zone is: the dial's grey ring
; is always the same, so it finds the dial by the ring, then samples whatever
; coloured arc is sitting on that ring and adopts it as the zone colour. That
; is the "just watch the box and get the colour for me" behaviour.
AutoLearnDial() {
    if (!C.AUTO_LEARN_COLOUR)
        return false
    EnsureSaneRadius()
    R := C.DIAL_RADIUS
    W := A_ScreenWidth, H := A_ScreenHeight
    own := OwnWindowRects()
    ; The grey ring stroke. One mid-grey with a wide per-channel tolerance
    ; covers every shade IsRingPixel accepts (0x23..0x48).
    greyCol := 0x363940, greyTol := 20
    band := 40, tries := 0
    by := 0
    while (by < H) {
        sx := 0
        while (sx < W) {
            found := false
            try found := PixelSearch(&fx, &fy, sx, by, W - 1, Min(H - 1, by + band - 1), greyCol, greyTol)
            if (!found)
                break
            if (r := RectAt(own, fx, fy)) {
                sx := r[3] + 1
                continue
            }
            ; Grey is everywhere in a dark UI, so cap the attempts high and
            ; always skip well past a non-ring hit.
            if (++tries > 600)
                return false
            if (loc := DialCentreFrom(fx, fy, R)) {
                if (!ValidDial(loc)) {               ; degenerate (edge / tiny) - keep looking
                    sx := fx + 2 * R + 20
                    continue
                }
                ; We have a real dial. Move onto it and learn its colour.
                C.DIAL_CX := loc.x, C.DIAL_CY := loc.y
                if (Abs(loc.r - C.DIAL_RADIUS) > 2)
                    C.DIAL_RADIUS := loc.r
                SaveCfg("DIAL_CX"), SaveCfg("DIAL_CY"), SaveCfg("DIAL_RADIUS")
                RecalcGeometry()
                try PositionCalibOverlays()
                learned := LearnZoneColour(loc.x, loc.y, C.DIAL_RADIUS)
                if (learned >= 0) {
                    C.ZONE_STYLE := "custom"
                    C.ARC_COLOR  := learned
                    C.ARC_TOL    := 48
                    SaveCfg("ZONE_STYLE"), SaveCfg("ARC_COLOR"), SaveCfg("ARC_TOL")
                    LogLine(Format("dial found by ring at {},{} r{} - learned zone colour {}",
                        loc.x, loc.y, C.DIAL_RADIUS, HexOrFail(learned)))
                } else {
                    LogLine(Format("dial found by ring at {},{} r{} - no zone arc visible yet",
                        loc.x, loc.y, C.DIAL_RADIUS))
                }
                return true
            }
            sx := fx + 2 * R + 20
        }
        by += band
    }
    return false
}

; Sweep the ring at a known dial centre and return the average colour of the
; coloured arc sitting on it (the zone), ignoring the grey ring, the red marker
; and the dark background. Returns -1 if no clearly-coloured arc is present.
LearnZoneColour(cx, cy, radius) {
    span := radius + 10
    if (!GrabRegion(cx - span, cy - span, 2 * span + 1, 2 * span + 1))
        return -1
    try {
        sumR := 0, sumG := 0, sumB := 0, cnt := 0
        ang := 0
        while (ang < 360) {
            a := ang * G.RAD
            dr := -4
            while (dr <= 4) {
                col := GrabAt(cx + Round((radius + dr) * Cos(a)),
                              cy - Round((radius + dr) * Sin(a)))
                dr += 2
                if (col < 0)
                    continue
                rr := (col >> 16) & 0xFF, gg := (col >> 8) & 0xFF, bb := col & 0xFF
                hi := Max(rr, gg, bb), lo := Min(rr, gg, bb)
                if (hi - lo < 50)                 ; grey ring / dark background
                    continue
                if (IsTickRed(col))               ; the moving marker, not the zone
                    continue
                sumR += rr, sumG += gg, sumB += bb, cnt++
            }
            ang += 2
        }
        if (cnt < 14)                             ; no real arc on the ring
            return -1
        return ((sumR // cnt) << 16) | ((sumG // cnt) << 8) | (sumB // cnt)
    } finally {
        GrabEnd()
    }
}

; Did the zone jump to a new spot? That is how the next stage (2/2) shows.
ArcMoved(a, b) {
    d := Abs(Mod(a.mid - b.mid + 540, 360) - 180)
    return d > Max(8, Min(a.width, b.width) / 3) || Abs(a.width - b.width) > 10
}

; True once no arc is on screen, i.e. the previous round has closed.
WaitForArcGone(timeoutMs) {
    deadline := A_TickCount + timeoutMs
    while (A_TickCount < deadline) {
        if (!S.running)
            return false
        if (!FindArc())
            return true
        Sleep 30
    }
    return false
}

; Watches the WHOLE ring every frame, not just the zone.
;   - finds the red marker wherever it is on the ring and measures its speed
;   - fires when the marker is inside the pink zone, OR when its predicted
;     position (TICK_LEAD_MS ahead) is inside it, so frame steps, input lag and
;     a zone drawn on top of the marker cannot make it miss the window
;   - works for either rotation direction
; Returns "" (nothing) | "poll" (seen in zone) | "predict" (fired on the
; prediction) | "immediate" (already inside on the first frame).
WaitForTick(arc, timeoutMs) {
    step := C.SCAN_STEP_DEG
    n := 360 // step
    radii := [arc.radius - 4, arc.radius, arc.radius + 4]   ; wider band to catch the line
    ; precompute probe points once: pts[i] = 3 points across the stroke
    pts := []
    Loop n {
        ang := (A_Index - 1) * step
        row := []
        for rr in radii
            row.Push(ProbeAt(ang, rr))
        pts.Push(row)
    }
    ; Fire only in the MIDDLE of the zone: the zone edges are soft (glow /
    ; antialiasing) and the game judges the press a frame or two after we
    ; send it, so aiming at an edge lands early.
    margin := Max(C.TICK_EDGE_MARGIN_DEG, arc.width * C.ZONE_INSET_PCT / 100)

    t0 := A_TickCount
    deadline := t0 + timeoutMs
    lastAlive := t0
    first := true
    lastAng := "", lastT := 0, vel := 0
    cycles := 0, seenTick := 0
    while (A_TickCount < deadline) {
        if (!S.running || (C.REQUIRE_GAME_ACTIVE && !GameActive()))
            return ""
        GrabDial()
        now := A_TickCount
        cycles++
        zoneCount := 0
        redStart := -1, redLen := 0, bestStart := -1, bestLen := 0
        Loop n {
            i := A_Index
            red := false, zone := false
            for pt in pts[i] {
                col := PixelAt(pt.x, pt.y)
                if (IsTickRed(col))
                    red := true
                else if (IsArcBlue(col))
                    zone := true
            }
            zoneCount += zone
            if (red) {
                if (redStart < 0)
                    redStart := i - 1
                redLen++
            } else if (redLen) {
                if (redLen > bestLen)
                    bestLen := redLen, bestStart := redStart
                redStart := -1, redLen := 0
            }
        }
        if (redLen > bestLen)
            bestLen := redLen, bestStart := redStart
        GrabEnd()

        if (zoneCount)
            lastAlive := now
        else if (now - lastAlive > C.DIAL_GONE_MS) {
            LogLine(Format("dial vanished after {}ms ({} sweeps, marker seen in {})",
                now - t0, cycles, seenTick))
            return ""
        }

        if (bestLen && bestLen < n // 4) {
            seenTick++
            tick := Mod((bestStart + (bestLen - 1) / 2) * step, 360)
            ; marker speed in deg/ms, signed (direction-agnostic)
            if (lastAng != "" && now > lastT) {
                d := tick - lastAng
                d := d > 180 ? d - 360 : (d < -180 ? d + 360 : d)
                v := d / (now - lastT)
                if (Abs(v) < 3)                       ; ignore nonsense jumps
                    vel := (vel = 0) ? v : vel * 0.5 + v * 0.5
            }
            lastAng := tick, lastT := now

            lead := vel * C.TICK_LEAD_MS
            why := ""
            if (InArc(tick, arc, margin))
                why := first ? "immediate" : "poll"
            else if (vel != 0 && Abs(lead) < 90
                    && InArc(Mod(tick + lead + 720, 360), arc, margin))
                why := "predict"
            if (why != "") {
                LogLine(Format("fire ({}): marker {:.0f}deg, speed {:.0f}deg/s, zone"
                    . " {:.0f}..{:.0f}deg", why, tick, vel * 1000, arc.lo,
                    Mod(arc.lo + arc.width, 360)))
                return why
            }
        }
        first := false
        Sleep C.POLL_SLEEP_MS
    }
    LogLine(Format("tick never reached the zone in {}ms ({} sweeps, marker seen in {})",
        timeoutMs, cycles, seenTick))
    return ""
}

; Is angle `ang` inside the zone, at least `margin` degrees in from its edges?
InArc(ang, arc, margin) {
    if (arc.width < 2 * margin + 2)
        margin := 0
    d := Mod(ang - arc.lo + 720, 360)
    return d >= margin && d <= arc.width - margin
}

; ============================================================================
;  PROGRESS BAR
; ============================================================================
; Two completion signals, because the bar is removed within ~100ms of filling
; and a probe that only watches one pixel can be skipped past entirely:
;   1. the probe pixel near the right end turns teal   (normal case)
;   2. the bar was present and has now disappeared     (we were too slow, but
;      the round still completed - counting it is correct)
WaitForBar() {
    if (!C.WAIT_FOR_BAR)
        return "skipped"
    appearDeadline := A_TickCount + C.BAR_APPEAR_TIMEOUT
    seen := false
    while (A_TickCount < appearDeadline) {
        if (!S.running || (C.REQUIRE_GAME_ACTIVE && !GameActive()))
            return "timeout"
        if (BarPresent()) {
            seen := true
            break
        }
        Sleep 20
    }
    ; The skill check already passed. Some servers show no progress bar after
    ; it at all, so a bar that never appears is NOT a failed round.
    if (!seen) {
        Dbg("progress bar never appeared - treating the round as done")
        return "none"
    }

    fullDeadline := A_TickCount + C.BAR_FULL_TIMEOUT
    while (A_TickCount < fullDeadline) {
        if (!S.running || (C.REQUIRE_GAME_ACTIVE && !GameActive()))
            return "timeout"
        if (IsBarTeal(PixelAt(C.BAR_FULL_X, C.BAR_Y)))
            return "full"
        if (!BarPresent()) {
            Sleep 40                       ; debounce a single-frame dropout
            if (!BarPresent())
                return "vanished"
        }
        Sleep 15
    }
    return "timeout"
}

; Sample across the track's left half, which fills first and stays filled.
; Probes start 2% in, NOT at the track's left edge: a probe at exactly the left
; edge lands on the track's own antialiased border and never reads teal at all -
; measured across the full 4.95s fill, it never once matched, which delayed
; "bar present" by 434ms every round. 2% puts the first probe at x~826, inside
; the fill from the moment it appears.
BarPresent() {
    ; One thin strip covers every bar probe, same reasoning as the dial.
    GrabRegion(C.BAR_X_LEFT - 2, C.BAR_Y - 2,
               C.BAR_X_RIGHT - C.BAR_X_LEFT + 6, 5)
    span := C.BAR_X_RIGHT - C.BAR_X_LEFT
    found := false
    Loop 6 {
        x := C.BAR_X_LEFT + Round(span * (2 + (A_Index - 1) * 8) / 100)
        if (IsBarTeal(PixelAt(x, C.BAR_Y))) {
            found := true
            break
        }
    }
    GrabEnd()
    return found
}

; ============================================================================
;  COLOUR PREDICATES
; ============================================================================
; Each accepts either an exact match within tolerance OR a channel-dominance
; test. The dominance path is what catches antialiased blend pixels, which a
; tolerance ball around a single RGB triple would reject.
; Name kept so every caller stays untouched - it means "is this pixel the
; Skill Check TARGET ZONE". Servers colour the zone differently, so the colour
; family is picked in Settings -> Detection (C.ZONE_STYLE). "custom" matches
; only the sampled ZONE colour (ARC_COLOR) within the tolerance.

IsArcBlue(p) {
    col := IsObject(p) ? PixelAt(p.x, p.y) : p
    if (col < 0)
        return false
    if IsTickRed(col)                          ; the marker never counts as zone
        return false
    if (Near(col, C.ARC_COLOR, C.ARC_TOL))
        return true
    if (!C.USE_DOMINANCE)
        return false
    return ColourFamily(col, C.ZONE_STYLE, C.BLUE_DOMINANCE)
}

; Shape test for a colour family - catches antialiased edge pixels that a
; single-colour tolerance would miss. d = how strongly the colour must win.
ColourFamily(col, family, d := 60) {
    rr := (col >> 16) & 0xFF, gg := (col >> 8) & 0xFF, bb := col & 0xFF
    hi := Max(rr, gg, bb), lo := Min(rr, gg, bb)
    switch family {
        ; Pink and purple are one family: the zone is a magenta that sits right on the line between them
        ; (0xC345AF has more red than blue), so the old split made "Purple" miss it completely.
        ; Anything from hot pink to violet counts: red AND blue both clearly above green.
        case "pink", "purple":
            return bb > 100 && rr > 80 && bb - gg > d * 3 // 4 && rr - gg > d // 3
        case "green":  return gg > 120 && gg - rr > d * 2 // 3 && gg - bb > d // 3
        case "blue":   return bb > 130 && bb - rr > d && bb - gg > d // 3
        case "yellow": return rr > 170 && gg > 120 && rr - bb > d && gg - bb > d * 2 // 3
        case "red":    return rr > 100 && rr - gg > 90 && rr - bb > 90
        case "white":  return lo > 205 && hi - lo < 30
        default:       return false            ; custom: exact colour only
    }
}

; Grey ring stroke (0x2A2E3B), or anything else that belongs on the ring.
IsRingPixel(col) {
    if (col < 0)
        return false
    if (IsArcBlue(col) || IsTickRed(col))
        return true
    rr := (col >> 16) & 0xFF, gg := (col >> 8) & 0xFF, bb := col & 0xFF
    ; The ring is a flat dark grey. Its exact shade changed with the new Skill
    ; Check look (0x2A2E3B before, 0x2A2B31 now), and the old +/-7 check missed
    ; the new one - so the dial was never found. Accept any neutral grey a step
    ; brighter than the panel background (0x1C1D21 / 0x1D232F).
    hi := Max(rr, gg, bb), lo := Min(rr, gg, bb)
    return lo >= 0x23 && hi <= 0x48 && hi - lo <= 18
}

; "Is this pixel the moving MARKER" - colour picked in Settings (C.TICK_STYLE).
IsTickRed(col) {
    if (col < 0)
        return false
    if (Near(col, C.TICK_COLOR, C.TICK_TOL))
        return true
    if (!C.USE_DOMINANCE || C.TICK_STYLE = "custom")
        return false
    if (C.TICK_STYLE = "red") {
        rr := (col >> 16) & 0xFF, gg := (col >> 8) & 0xFF, bb := col & 0xFF
        return (rr > 100) && (rr - gg > C.RED_DOMINANCE) && (rr - bb > C.RED_DOMINANCE)
    }
    return ColourFamily(col, C.TICK_STYLE, 70)
}

IsBarTeal(col) {
    if (col < 0)
        return false
    if (Near(col, C.BAR_COLOR, C.BAR_TOL))
        return true
    if (!C.USE_DOMINANCE)
        return false
    rr := (col >> 16) & 0xFF, gg := (col >> 8) & 0xFF, bb := col & 0xFF
    return (gg > 120) && (bb > 120) && (gg - rr > C.TEAL_DOMINANCE)
        && (bb - rr > C.TEAL_DOMINANCE)
}

Near(c1, c2, tol) {
    return Abs(((c1 >> 16) & 0xFF) - ((c2 >> 16) & 0xFF)) <= tol
        && Abs(((c1 >>  8) & 0xFF) - ((c2 >>  8) & 0xFF)) <= tol
        && Abs(( c1        & 0xFF) - ( c2        & 0xFF)) <= tol
}

; ============================================================================
;  PIXEL ACCESS
; ============================================================================
; Screen-space polar helper. Screen Y grows downward, so the sine term is
; subtracted to keep 0deg = east / 90deg = north, matching the measurements
; in the header.
ProbeAt(deg, radius) {
    rad := Mod(deg + 360, 360) * G.RAD
    return {x: C.DIAL_CX + Round(radius * Cos(rad)),
            y: C.DIAL_CY - Round(radius * Sin(rad))}
}

; ============================================================================
;  SCREEN GRAB  - the difference between this macro working and not
; ============================================================================
; PixelGetColor costs ~5 ms per call on this machine (measured straight from
; macro.log: 41 ms for the 8 dial probes in the startup dump, twice). At that
; price a 180-probe annulus sweep costs ~900 ms while the dial only exists for
; ~880 ms, so the arc could never be measured before it vanished - visible in
; the log as a 3000 ms scan deadline overshooting to 3656 ms.
;
; So: BitBlt the dial's bounding box into memory ONCE per sweep, then every
; probe is an array read. One ~1-2 ms capture replaces hundreds of GDI
; round-trips. PixelAt automatically serves from the grab while one is active.
GrabFree() {
    try {
        if (GRAB.oldBm)
            DllCall("gdi32\SelectObject", "ptr", GRAB.hdcMem, "ptr", GRAB.oldBm)
        if (GRAB.hbm)
            DllCall("gdi32\DeleteObject", "ptr", GRAB.hbm)
        if (GRAB.hdcMem)
            DllCall("gdi32\DeleteDC", "ptr", GRAB.hdcMem)
        if (GRAB.hdcSrc)
            DllCall("ReleaseDC", "ptr", 0, "ptr", GRAB.hdcSrc)
    }
    GRAB.hdcSrc := 0, GRAB.hdcMem := 0, GRAB.hbm := 0, GRAB.oldBm := 0
    GRAB.w := 0, GRAB.h := 0, GRAB.ok := false
}

; Capture x,y,w,h of the screen. Returns false if GDI refuses, in which case
; the caller simply keeps using PixelGetColor - correct, just slow.
GrabRegion(x, y, w, h) {
    GRAB.ok := false
    if (GRAB.broken || !C.USE_SCREEN_GRAB || w < 1 || h < 1)
        return false
    try {
        if (GRAB.w != w || GRAB.h != h) {
            GrabFree()
            GRAB.hdcSrc := DllCall("GetDC", "ptr", 0, "ptr")
            GRAB.hdcMem := DllCall("gdi32\CreateCompatibleDC", "ptr", GRAB.hdcSrc, "ptr")
            GRAB.hbm    := DllCall("gdi32\CreateCompatibleBitmap", "ptr", GRAB.hdcSrc,
                                   "int", w, "int", h, "ptr")
            GRAB.oldBm  := DllCall("gdi32\SelectObject", "ptr", GRAB.hdcMem,
                                   "ptr", GRAB.hbm, "ptr")
            GRAB.buf    := Buffer(w * h * 4, 0)
            GRAB.w := w, GRAB.h := h
        }
        if (!DllCall("gdi32\BitBlt", "ptr", GRAB.hdcMem, "int", 0, "int", 0,
                     "int", w, "int", h, "ptr", GRAB.hdcSrc, "int", x, "int", y,
                     "uint", 0x00CC0020))                       ; SRCCOPY
            throw Error("BitBlt failed")
        bi := Buffer(40, 0)
        NumPut("uint", 40, bi, 0)
        NumPut("int", w, bi, 4)
        NumPut("int", -h, bi, 8)                                ; top-down rows
        NumPut("ushort", 1, bi, 12)
        NumPut("ushort", 32, bi, 14)
        NumPut("uint", 0, bi, 16)                               ; BI_RGB
        if (!DllCall("gdi32\GetDIBits", "ptr", GRAB.hdcMem, "ptr", GRAB.hbm,
                     "uint", 0, "uint", h, "ptr", GRAB.buf, "ptr", bi, "uint", 0))
            throw Error("GetDIBits failed")
    } catch Error as e {
        GRAB.broken := true
        LogLine("WARNING screen grab unavailable (" e.Message ") - falling back"
            . " to PixelGetColor, which is far slower on this machine")
        return false
    }
    GRAB.x := x, GRAB.y := y, GRAB.ok := true
    return true
}

GrabEnd() => GRAB.ok := false

; Capture the whole dial bounding box - one call covers every annulus probe.
GrabDial() => GrabRegion(G.boxL, G.boxT, G.boxW, G.boxH)

; 32bpp BI_RGB rows are B,G,R,A, so a little-endian uint read is 0xAARRGGBB;
; masking off alpha yields exactly the 0xRRGGBB PixelGetColor returns.
GrabAt(sx, sy) {
    px := sx - GRAB.x, py := sy - GRAB.y
    if (px < 0 || py < 0 || px >= GRAB.w || py >= GRAB.h)
        return -1
    return NumGet(GRAB.buf, (py * GRAB.w + px) * 4, "uint") & 0xFFFFFF
}

; Returns -1 on failure. Tracks consecutive pure-black reads: in exclusive
; fullscreen the desktop compositor hands back an all-black buffer, so every
; probe silently reads 0x000000 and the macro would just sit there.
PixelAt(x, y) {
    ; Served from the active capture when one is open; identical value, ~5 ms
    ; cheaper. Falls through to the live call for anything outside the grab
    ; (calibration, diagnostics) or if GDI capture is unavailable.
    if (GRAB.ok) {
        col := GrabAt(x, y)
        if (col >= 0) {
            if (col = 0x000000) {
                S.blackRun++
                CheckBlackout()
            } else if (S.blackRun) {
                S.blackRun := 0
                if (S.blackWarn) {
                    S.blackWarn := false
                    try SET["warn"].Text := ""
                }
            }
            return col
        }
    }
    try {
        col := Integer(PixelGetColor(x, y))
    } catch {
        S.blackRun++
        CheckBlackout()
        return -1
    }
    if (col = 0x000000) {
        S.blackRun++
        CheckBlackout()
    } else if (S.blackRun) {
        S.blackRun := 0
        if (S.blackWarn) {
            S.blackWarn := false
            LogLine("pixel reads recovered")
            if (S.state = "ERROR")
                SetStatus(S.running ? "SEARCHING DIAL" : "IDLE",
                    S.running ? "RUNNING" : "PAUSED",
                    S.running ? "green" : "amber")
        }
    }
    return col
}

CheckBlackout() {
    if (S.blackRun >= C.BLACK_READ_LIMIT && !S.blackWarn) {
        S.blackWarn := true
        SetStatus("ERROR", "BLACK PIXELS", "red")
        LogLine("Switch FiveM to borderless windowed - " S.blackRun
            . " consecutive black reads; pixel colour cannot be read from an"
            . " exclusive-fullscreen surface")
    }
}

; ============================================================================
;  INPUT
; ============================================================================
GameActive() {
    if (!C.REQUIRE_GAME_ACTIVE)
        return true
    try return WinActive(C.WIN_MATCH) ? true : false
    catch
        return false
}

; A binding may carry AHK modifier prefixes (^!+#); the bare key name is what
; gets sent down/up, with the modifiers wrapped around it.
StripMods(binding) {
    key := binding
    while (StrLen(key) > 1 && InStr("^!+#", SubStr(key, 1, 1)))
        key := SubStr(key, 2)
    return key
}

; Explicit down / hold / up. GTA V drops instantaneous key events, so a plain
; Send of the character does nothing in-game. {Blind} keeps whatever modifiers
; are physically held out of the picture.
SendGameKey(binding) {
    if (C.REQUIRE_GAME_ACTIVE && !GameActive()) {
        Dbg("send suppressed - game window not active")
        return false
    }
    mods := "", key := binding
    while (StrLen(key) > 1 && InStr("^!+#", SubStr(key, 1, 1))) {
        mods .= SubStr(key, 1, 1)
        key := SubStr(key, 2)
    }
    modName := Map("^", "LCtrl", "!", "LAlt", "+", "LShift", "#", "LWin")
    chars := StrSplit(mods, "")

    for m in chars
        SendInput "{Blind}{" modName[m] " down}"
    SendInput "{Blind}{" key " down}"
    Sleep C.KEY_HOLD_MS
    SendInput "{Blind}{" key " up}"
    Loop chars.Length                              ; release in reverse order
        SendInput "{Blind}{" modName[chars[chars.Length - A_Index + 1]] " up}"

    LogLine("pressed '" binding "' (held " C.KEY_HOLD_MS "ms, admin=" A_IsAdmin ")")
    return true
}

; AHK v2 has ATan but no two-argument arctangent.
ATan2(y, x) {
    if (x > 0)
        return ATan(y / x)
    if (x < 0)
        return ATan(y / x) + (y >= 0 ? 3.14159265358979 : -3.14159265358979)
    return (y > 0) ? 1.5707963267949 : (y < 0 ? -1.5707963267949 : 0)
}

; ============================================================================
;  SETTINGS WINDOW
; ============================================================================
DragSettingsWindow(*) {
    ; Dragging the panel's header moves the whole unit via the main menu.
    mainHwnd := 0
    try mainHwnd := UI["gui"].Hwnd
    if mainHwnd {
        try PostMessage(0x00A1, 2, 0, , "ahk_id " mainHwnd)
        return
    }
    if SET.Has("gui")
        try PostMessage(0x00A1, 2, 0, , "ahk_id " SET["gui"].Hwnd)
}

SettingsDock(w, h) {
    return {x: "", y: "", fromX: "", side: ""}   ; no separate window to dock
}

; Rounds only the outward corners of the settings panel; the side that meets
; the main menu stays square so the two windows join seamlessly.
ApplySeamRoundedCorners(hwnd, w, h, radius, side) {
    if (w <= 0 || h <= 0)
        return
    if (side = "") {
        ApplyRoundedCorners(hwnd, w, h, radius)
        return
    }
    try {
        ; DWM rounding is all-or-nothing, so switch it off and shape the
        ; window with a region: rounded rect + a square strip on the seam.
        pref := Buffer(4, 0)
        NumPut("Int", 1, pref)   ; DWMWCP_DONOTROUND
        DllCall("Dwmapi\DwmSetWindowAttribute",
            "Ptr", hwnd, "UInt", 33, "Ptr", pref, "UInt", 4, "UInt")
    }
    try {
        rounded := DllCall("CreateRoundRectRgn", "Int", 0, "Int", 0,
            "Int", w + 1, "Int", h + 1, "Int", radius, "Int", radius, "Ptr")
        stripX := side = "right" ? 0 : w - radius
        strip := DllCall("CreateRectRgn", "Int", stripX, "Int", 0,
            "Int", stripX + radius, "Int", h + 1, "Ptr")
        if (rounded && strip) {
            DllCall("CombineRgn", "Ptr", rounded, "Ptr", rounded,
                "Ptr", strip, "Int", 2)   ; RGN_OR
            DllCall("SetWindowRgn", "Ptr", hwnd, "Ptr", rounded, "Int", true)
        }
        if strip
            DllCall("DeleteObject", "Ptr", strip)
    }
}

SlideSettingsOut() {
    ShowView("settings")
}

SlideSettingsIn() {
    ShowView("dashboard")
}

SettingsFollowTick() {
    SetTimer(SettingsFollowTick, 0)   ; settings is part of the main window now
}

; ============================================================================
;  SETTINGS - a page inside the main window (no second window)
; ============================================================================
; Settings used to be a separate window that slid out from behind the menu,
; with docking, follow-timers and cross-fades to keep the two glued together.
; Now it is simply the other view of the main window: the sidebar stays, the
; content area swaps between Dashboard and Settings instantly.
ShowSettings(prebuild := false, *) {
    if !UI.Has("gui")
        return
    if !SET.Has("built")
        BuildSettingsPages()
    if prebuild
        return
    EnsureMainMenuVisible()
    if (S.HasOwnProp("view") && S.view = "settings") {
        ShowView("dashboard")
        return
    }
    RefreshSettingsFields()
    ShowView("settings")
}

ShowDashboard(*) {
    ShowView("dashboard")
}

OpenSettingsView(*) {
    if !SET.Has("built")
        BuildSettingsPages()
    RefreshSettingsFields()
    ShowView("settings")
}

; Switch the content area between "dashboard" and "settings".
ShowView(view) {
    if !UI.Has("gui")
        return
    if (view = "settings" && !SET.Has("built"))
        BuildSettingsPages()
    hwnd := UI["gui"].Hwnd
    S.view := view
    SuspendRedraw(hwnd)
    try {
        for ctl in UI.Get("dash", [])
            SetCtlVisible(ctl, view = "dashboard")
        for ctl in SET.Get("chrome", [])
            SetCtlVisible(ctl, view = "settings")
        active := SET.Get("activePage", "interface")
        for pageName, controls in SET.Get("pages", Map())
            for ctl in controls
                SetCtlVisible(ctl, view = "settings" && pageName = active)
        try UI["title"].Text := view = "settings" ? "Settings" : "Dashboard"
        try UI["subtitle"].Text := view = "settings"
            ? "Everything saves automatically as you change it"
            : "Metal Detector  ·  skill check automation"
        S.hoverHwnd := 0
        MainRefresh(false)
    } finally {
        ResumeRedraw(hwnd)
    }
}

SetCtlVisible(ctl, on) {
    try ctl.Visible := on
    try ctl.Enabled := on
}

; ============================================================================
;  SETTINGS PAGES - every row, box, button and switch is drawn into the main
;  window's rendered backdrop (SettingsRender); the controls on top are only
;  transparent labels and borderless edit boxes. Everything saves by itself.
; ============================================================================
BuildSettingsPages() {
    if !UI.Has("gui")
        return
    dlg := UI["gui"]
    u := C.UI_SCALE / 100
    P(v) => Round(v * u)
    SET["gui"] := dlg
    SET["pages"] := Map()
    SET["pageOfHwnd"] := Map()
    SET["rows"] := Map()
    SET["nextY"] := Map()
    SET["items"] := Map()
    SET["buttonBase"] := Map()
    SET["buttonHover"] := Map()
    SET["chrome"] := []
    SET["dirty"] := false
    SET["u"] := u

    ; ---- tabs + saved indicator (drawn in the backdrop; these are labels) --
    for i, tab in SET_TABS {
        r := SettingsTabRect(i)
        dlg.SetFont("s" P(8) " bold c" TXT_SUB, FONT_UI)
        ctl := dlg.AddText(Box(P(r[1]), P(r[2]), P(r[3]), P(r[4])) " 0x200 Center BackgroundTrans Hidden", tab[2])
        ctl.OnEvent("Click", SettingsTabHandler(tab[1]))
        S.mainHwndKey[ctl.Hwnd] := "tab:" tab[1]
        S.noDrag[ctl.Hwnd] := true
        SET["tab_" tab[1]] := ctl
        SET["chrome"].Push(ctl)
    }
    dlg.SetFont("s" P(8) " bold c6EE7B7", FONT_UI)
    SET["saveLabel"] := dlg.AddText(Box(P(SET_SAVE[1]), P(SET_SAVE[2]), P(SET_SAVE[3]), P(SET_SAVE[4]))
        " 0x200 Center BackgroundTrans Hidden", "✓  SAVED")
    SET["chrome"].Push(SET["saveLabel"])
    dlg.SetFont("s" P(8) " bold c" BRAND_ACCENT, FONT_UI)
    SET["warn"] := dlg.AddText(Box(P(520), P(160), P(396), P(24)) " 0x200 Right BackgroundTrans Hidden", "")
    SET["chrome"].Push(SET["warn"])

    ; ---- keys ----------------------------------------------------------------
    SPage(dlg, u, "bindings", "Key binds", "Click REBIND, then press the new key. Esc cancels.")
    for a in ACTIONS {
        row := SRow(dlg, u, "bindings", a.label, "", true)
        SBind(dlg, u, row, a.prop)
    }

    ; ---- general -------------------------------------------------------------
    SPage(dlg, u, "general", "General", "How the macro runs")
    SInput(dlg, u, SRow(dlg, u, "general", "Detector slots", "Hotbar keys holding a detector, tried in order"), ["SLOT_KEYS"])
    SSwitch(dlg, u, SRow(dlg, u, "general", "Swap broken detectors", "Use the next slot when one stops working"), "autoSwap", C.AUTO_SWAP_SLOTS)
    SSwitch(dlg, u, SRow(dlg, u, "general", "Pause when not in game", "Only press keys while FiveM is focused"), "reqActive", C.REQUIRE_GAME_ACTIVE)
    SInput(dlg, u, SRow(dlg, u, "general", "Game window", "Which window counts as FiveM"), ["WIN_MATCH"])
    SChoice(dlg, u, SRow(dlg, u, "general", "Log detail", "How much goes into macro.log"), "logLevel",
        [[1, "NORMAL"], [2, "VERBOSE"], [3, "TRACE"]], Max(1, Min(3, C.LOG_LEVEL)))
    SSwitch(dlg, u, SRow(dlg, u, "general", "Debug logging", "Extra detail for troubleshooting"), "debug", C.DEBUG)

    ; ---- detection -----------------------------------------------------------
    SPage(dlg, u, "colours", "Detection", "Match these to the skill check on your server")
    SChoice(dlg, u, SRow(dlg, u, "colours", "Zone colour", "The target area on the ring"), "zoneStyle",
        [["pink", "PINK"], ["green", "GREEN"], ["blue", "BLUE"], ["yellow", "YELLOW"], ["purple", "PURPLE"], ["custom", "CUSTOM"]], C.ZONE_STYLE)
    SChoice(dlg, u, SRow(dlg, u, "colours", "Marker colour", "The line that moves round"), "tickStyle",
        [["red", "RED"], ["white", "WHITE"], ["yellow", "YELLOW"], ["blue", "BLUE"], ["custom", "CUSTOM"]], C.TICK_STYLE)
    SInput(dlg, u, SRow(dlg, u, "colours", "Custom colours", "Zone / marker hex, for Custom"), ["ARC_COLOR", "TICK_COLOR"])
    SInput(dlg, u, SRow(dlg, u, "colours", "Colour tolerance", "Zone / marker - higher matches looser"), ["ARC_TOL", "TICK_TOL"])

    ; ---- timing --------------------------------------------------------------
    SPage(dlg, u, "timing", "Timing", "Every box saves as you type and works straight away")
    SInput(dlg, u, SRow(dlg, u, "timing", "Press lead (ms)", "Higher = presses E earlier, before the line arrives"), ["TICK_LEAD_MS"])
    SInput(dlg, u, SRow(dlg, u, "timing", "Zone edge skip (%)", "Lower = presses as soon as it's in the zone"), ["ZONE_INSET_PCT"])
    SInput(dlg, u, SRow(dlg, u, "timing", "Reaction delay (ms)", "Random wait before each press, min / max"), ["REACTION_MIN_MS", "REACTION_MAX_MS"])
    SInput(dlg, u, SRow(dlg, u, "timing", "After pressing E (ms)", "Pause before checking for the next stage"), ["POST_PRESS_SETTLE"])
    SInput(dlg, u, SRow(dlg, u, "timing", "Wait for next hit (ms)", "How long to look for stage 2/2"), ["STAGE_GAP_MS"])
    SInput(dlg, u, SRow(dlg, u, "timing", "Gap between digs (ms)", "Pause after a good / a failed dig"), ["ROUND_GAP_MS", "RETRY_GAP_MS"])
    SInput(dlg, u, SRow(dlg, u, "timing", "Give up after (ms)", "Skill check not showing / line not reaching the zone"), ["DIAL_APPEAR_TIMEOUT", "DIAL_HIT_TIMEOUT"])
    row := SRow(dlg, u, "timing", "Defaults", "Put every timing value back to how it shipped")
    SButton(dlg, u, row, "RESET TIMING", (*) => ResetTiming(), "quiet", 770, 130)

    ; ---- regions -------------------------------------------------------------
    SPage(dlg, u, "regions", "Screen regions", "Found automatically - only change these if you need to")
    SSwitch(dlg, u, SRow(dlg, u, "regions", "Find the skill check by itself", "Searches the whole screen when it isn't where it was"), "autoLocate", C.AUTO_LOCATE_DIAL)
    SInput(dlg, u, SRow(dlg, u, "regions", "Detection box size (px)", "The pink square in Calibrate - drag its corner too"), ["DIAL_BOX_PX"])
    SInput(dlg, u, SRow(dlg, u, "regions", "Dial centre", "X / Y in screen pixels"), ["DIAL_CX", "DIAL_CY"])
    SInput(dlg, u, SRow(dlg, u, "regions", "Dial radius", "Ring size in pixels"), ["DIAL_RADIUS"])
    SInput(dlg, u, SRow(dlg, u, "regions", "Bar row", "Progress bar height on screen (Y)"), ["BAR_Y"])
    SInput(dlg, u, SRow(dlg, u, "regions", "Bar left / right", "Where the progress bar starts and ends (X)"), ["BAR_X_LEFT", "BAR_X_RIGHT"])
    SInput(dlg, u, SRow(dlg, u, "regions", "Bar full point", "Turns colour when the bar is full (X)"), ["BAR_FULL_X"])

    ; ---- theme ---------------------------------------------------------------
    SPage(dlg, u, "interface", "Theme", "Colours save the moment you pick them")
    themeOpts := []
    for name in THEME_ORDER
        themeOpts.Push([name, THEME_PRESETS[name]])
    themeOpts.Push(["custom", -1])
    SSwatches(dlg, u, SRow(dlg, u, "interface", "Menu colour", "Recolours the whole menu"), "theme", themeOpts, CurrentThemeName())
    row := SRow(dlg, u, "interface", "Custom colour", "Any hex colour, or pick one")
    SInput(dlg, u, row, ["MENU_COLOR"], 560, 200)
    SButton(dlg, u, row, "PICK…", (*) => PickMenuColour(), "accent", 770, 130)
    SChoice(dlg, u, SRow(dlg, u, "interface", "Menu size", "Scale of the whole menu"), "scale",
        [[75, "75%"], [100, "100%"], [125, "125%"], [150, "150%"]], C.UI_SCALE)

    if !SET["pages"].Has(SET.Get("activePage", ""))
        SET["activePage"] := "general"
    SET["built"] := true
}

; Page title block.
SPage(dlg, u, page, title, note) {
    P(v) => Round(v * u)
    SET["rows"][page] := []
    SET["nextY"][page] := 230
    dlg.SetFont("s" P(14) " norm c" TXT_MAIN, FONT_SEMI)
    heading := dlg.AddText(Box(P(274), P(158), P(620), P(30)) " BackgroundTrans", title)
    dlg.SetFont("s" P(8) " norm c" TXT_SUB, FONT_UI)
    detail := dlg.AddText(Box(P(276), P(190), P(620), P(18)) " BackgroundTrans", note)
    SettingsTrack(page, heading, detail)
}

; One row: drawn background, a label and a one-line description.
SRow(dlg, u, page, label, desc, compact := false) {
    P(v) => Round(v * u)
    y := SET["nextY"][page]
    tight := (page = "timing" || page = "regions")   ; pages with more rows sit a little closer
    h := compact ? 34 : tight ? 39 : 44
    row := {page: page, y: y, h: h, items: []}
    SET["rows"][page].Push(row)
    SET["nextY"][page] := y + h + (compact ? 4 : tight ? 4 : 6)
    if compact {
        dlg.SetFont("s" P(9) " norm c" TXT_MAIN, FONT_UI)
        lbl := dlg.AddText(Box(P(292), P(y), P(250), P(h)) " 0x200 BackgroundTrans", label)
        SettingsTrack(page, lbl)
        row.labels := [lbl]
    } else {
        dlg.SetFont("s" P(9) " norm c" TXT_MAIN, FONT_SEMI)
        lbl := dlg.AddText(Box(P(292), P(y + (tight ? 3 : 5)), P(240), P(18)) " BackgroundTrans", label)
        dlg.SetFont("s" P(7) " norm c" TXT_DIM, FONT_UI)
        sub := dlg.AddText(Box(P(292), P(y + (tight ? 21 : 24)), P(250), P(15)) " BackgroundTrans", desc)
        SettingsTrack(page, lbl, sub)
        row.labels := [lbl, sub]
    }
    return row
}

; Register an interactive element: hover id, optional click, page tracking.
SItem(row, item, ctl := "", handler := "") {
    static n := 0
    n++
    item.id := "set:" n
    row.items.Push(item)
    SET["items"][item.id] := item
    if IsObject(ctl) {
        S.mainHwndKey[ctl.Hwnd] := item.id
        S.noDrag[ctl.Hwnd] := true
        if (handler != "")
            ctl.OnEvent("Click", handler)
        SettingsTrack(row.page, ctl)
    }
    return item
}

; Edit boxes sitting inside drawn input fields. Saves itself shortly after typing.
SInput(dlg, u, row, props, x := 560, areaW := 340) {
    P(v) => Round(v * u)
    gap := 8
    w := Floor((areaW - gap * (props.Length - 1)) / props.Length)
    for pn in props {
        bx := x, by := row.y + (row.h - 30) // 2
        dlg.SetFont("s" P(9) " norm c" TXT_MAIN, "Consolas")
        ctl := dlg.Add("Edit", "x" P(bx + 10) " y" P(by + 6) " w" P(w - 20) " h" P(18)
            . " Center Background" THEME_INPUT " c" TXT_MAIN " -E0x200 -Multi -VScroll -HScroll",
            FormatVal(C.%pn%, FieldType(pn)))
        ctl.OnEvent("Change", SettingsChanged)
        SET["fld_" pn] := ctl
        SItem(row, {kind: "input", x: bx, y: by, w: w, h: 30, ctl: ctl, prop: pn}, ctl)
        S.mainHwndKey.Delete(ctl.Hwnd)          ; typing box: normal I-beam cursor
        x += w + gap
    }
}

; A row of option buttons; the selected one is filled with the accent.
SChoice(dlg, u, row, key, options, initialValue) {
    P(v) => Round(v * u)
    SET[key "Value"] := initialValue
    SET[key "Buttons"] := Map()
    n := options.Length
    areaW := n > 4 ? 360 : n > 3 ? 340 : 300
    gap := 5
    w := Floor((areaW - gap * (n - 1)) / n)
    x := 900 - areaW
    for option in options {
        value := option[1]
        dlg.SetFont("s" P(7) " bold c" TXT_MAIN, FONT_UI)
        by := row.y + (row.h - 28) // 2
        ctl := dlg.AddText(Box(P(x), P(by), P(w), P(28)) " 0x200 Center BackgroundTrans", option[2])
        SET[key "Buttons"][value] := ctl
        SItem(row, {kind: "choice", x: x, y: by, w: w, h: 28, key: key, value: value, ctl: ctl}, ctl,
            SettingsChoiceHandler(key, value))
        x += w + gap
    }
    RefreshSettingsChoice(key)
}

; Round colour swatches; the picked one gets a white ring. "+" is a custom colour.
SSwatches(dlg, u, row, key, options, initialValue) {
    P(v) => Round(v * u)
    SET[key "Value"] := initialValue
    SET[key "Buttons"] := Map()
    d := 26, gap := 10
    x := 900 - (options.Length * d + (options.Length - 1) * gap)
    by := row.y + (row.h - d) // 2
    for option in options {
        value := option[1]
        dlg.SetFont("s" P(10) " bold c" TXT_MAIN, FONT_UI)
        ctl := dlg.AddText(Box(P(x), P(by), P(d), P(d)) " 0x200 Center BackgroundTrans", value = "custom" ? "+" : "")
        SET[key "Buttons"][value] := ctl
        SItem(row, {kind: "swatch", x: x, y: by, w: d, h: d, key: key, value: value, rgb: option[2], ctl: ctl}, ctl,
            SettingsChoiceHandler(key, value))
        x += d + gap
    }
}

SButton(dlg, u, row, text, handler, style, x, w) {
    P(v) => Round(v * u)
    by := row.y + (row.h - 30) // 2
    dlg.SetFont("s" P(8) " bold c" (style = "accent" ? THEME_BUTTON_TEXT : TXT_MAIN), FONT_UI)
    ctl := dlg.AddText(Box(P(x), P(by), P(w), P(30)) " 0x200 Center BackgroundTrans", text)
    return SItem(row, {kind: "button", x: x, y: by, w: w, h: 30, style: style, ctl: ctl}, ctl, handler)
}

; A modern on/off switch at the end of the row; the whole row toggles it.
SSwitch(dlg, u, row, key, initialValue) {
    P(v) => Round(v * u)
    SET[key "Value"] := initialValue ? 1 : 0
    by := row.y + (row.h - 24) // 2
    ctl := dlg.AddText(Box(P(846), P(by - 4), P(60), P(32)) " BackgroundTrans", "")
    item := SItem(row, {kind: "switch", x: 856, y: by, w: 44, h: 24, key: key, ctl: ctl}, ctl, (*) => ToggleSettingsOption(key))
    for lbl in row.labels {
        lbl.OnEvent("Click", (*) => ToggleSettingsOption(key))
        S.mainHwndKey[lbl.Hwnd] := item.id
        S.noDrag[lbl.Hwnd] := true
    }
}

; Key binding: the current key in a drawn box, plus REBIND.
SBind(dlg, u, row, prop) {
    P(v) => Round(v * u)
    by := row.y + (row.h - 26) // 2
    dlg.SetFont("s" P(9) " norm c" TXT_MAIN, FONT_SEMI)
    SET["bind_" prop] := dlg.AddText(Box(P(560), P(by), P(190), P(26)) " 0x200 Center BackgroundTrans", C.%prop%)
    SItem(row, {kind: "bindbox", x: 560, y: by, w: 190, h: 26, prop: prop}, SET["bind_" prop])
    S.mainHwndKey.Delete(SET["bind_" prop].Hwnd)
    dlg.SetFont("s" P(8) " bold c" THEME_BUTTON_TEXT, FONT_UI)
    ctl := dlg.AddText(Box(P(760), P(by), P(140), P(26)) " 0x200 Center BackgroundTrans", "REBIND")
    SItem(row, {kind: "button", x: 760, y: by, w: 140, h: 26, style: "accent", ctl: ctl}, ctl, RebindHandler(prop))
}

; Draws the open settings page into the backdrop.
SettingsRender(pG, u, hov) {
    P(v) => Round(v * u)
    page := SET.Get("activePage", "general")
    if !SET.Has("rows") || !SET["rows"].Has(page)
        return
    focus := SET.Get("focusHwnd", 0)
    FillRoundA(pG, P(276), P(212), P(32), P(3), P(2), 0xFF000000 | ACCL_RGB)
    for row in SET["rows"][page] {
        FillRoundA(pG, P(274), P(row.y), P(642), P(row.h), P(12), 0xFF000000 | FIELD_RGB)
        for item in row.items {
            x := P(item.x), y := P(item.y), w := P(item.w), h := P(item.h)
            hot := (hov = item.id)
            switch item.kind {
                case "input":
                    focused := focus && item.ctl.Hwnd = focus
                    FillRoundA(pG, x, y, w, h, P(9), 0xFF000000 | INPUT_RGB)
                    StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, P(9),
                        focused ? (0xFF000000 | ACCL_RGB) : (0xFF000000 | (hot ? BlendRgb(BORDER_RGB, 0xFFFFFF, 0.12) : BORDER_RGB)),
                        focused ? 1.6 : 1)
                case "bindbox":
                    FillRoundA(pG, x, y, w, h, P(9), 0xFF000000 | INPUT_RGB)
                    StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, P(9), 0xFF000000 | BORDER_RGB, 1)
                case "choice":
                    if (SET.Get(item.key "Value", "") = item.value)
                        LinearFillRound(pG, x, y, w, h, P(9), 0xFF000000 | ACC_RGB, 0xFF000000 | ACC2_RGB)
                    else
                        FillRoundA(pG, x, y, w, h, P(9), 0xFF000000 | (hot ? BlendRgb(INPUT_RGB, 0xFFFFFF, 0.06) : INPUT_RGB))
                case "button":
                    if (item.style = "accent") {
                        c1 := hot ? BlendRgb(ACC_RGB, 0xFFFFFF, 0.14) : ACC_RGB
                        LinearFillRound(pG, x, y, w, h, P(9), 0xFF000000 | c1, 0xFF000000 | ACC2_RGB)
                    } else {
                        FillRoundA(pG, x, y, w, h, P(9), 0xFF000000 | (hot ? BlendRgb(INPUT_RGB, 0xFFFFFF, 0.08) : INPUT_RGB))
                        StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, P(9), 0xFF000000 | BORDER_RGB, 1)
                    }
                case "swatch":
                    sel := (SET.Get(item.key "Value", "") = item.value)
                    col := item.rgb >= 0 ? item.rgb : (C.MENU_COLOR & 0xFFFFFF)
                    if sel
                        StrokeRoundA(pG, x - P(4) + 0.5, y - P(4) + 0.5, w + P(8) - 1, h + P(8) - 1, (w + P(8)) // 2, 0xFFFFFFFF, 2)
                    else if hot
                        StrokeRoundA(pG, x - P(4) + 0.5, y - P(4) + 0.5, w + P(8) - 1, h + P(8) - 1, (w + P(8)) // 2, 0x66FFFFFF, 1.5)
                    if (item.rgb >= 0 || sel)
                        LinearFillRound(pG, x, y, w, h, w // 2, 0xFF000000 | BlendRgb(col, 0xFFFFFF, 0.12), 0xFF000000 | BlendRgb(col, 0x000000, 0.22))
                    else {
                        FillRoundA(pG, x, y, w, h, w // 2, 0xFF000000 | INPUT_RGB)
                        StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, w // 2, 0xFF000000 | BORDER_RGB, 1)
                    }
                case "switch":
                    on := SET.Get(item.key "Value", 0)
                    if on
                        LinearFillRound(pG, x, y, w, h, h // 2, 0xFF000000 | ACC_RGB, 0xFF000000 | ACC2_RGB)
                    else {
                        FillRoundA(pG, x, y, w, h, h // 2, 0xFF000000 | INPUT_RGB)
                        StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, h // 2,
                            0xFF000000 | (hot ? BlendRgb(BORDER_RGB, 0xFFFFFF, 0.15) : BORDER_RGB), 1)
                    }
                    k := h - P(6)
                    kx := on ? x + w - k - P(3) : x + P(3)
                    FillRoundA(pG, kx, y + P(3), k, k, k // 2, on ? 0xFFFFFFFF : 0xFF9A96AA)
            }
        }
    }
    ; saved / saving pill
    x := P(SET_SAVE[1]), y := P(SET_SAVE[2]), w := P(SET_SAVE[3]), h := P(SET_SAVE[4])
    dirty := SET.Get("dirty", false)
    FillRoundA(pG, x, y, w, h, P(12), dirty ? 0x33F2C14E : 0x2634C08C)
    StrokeRoundA(pG, x + 0.5, y + 0.5, w - 1, h - 1, P(12), dirty ? 0xCCF2C14E : 0xAA34C08C, 1)
}

; Settings -> Timing -> RESET TIMING: every timing value back to its default.
ResetTiming() {
    static defaults := Map("TICK_LEAD_MS", 15, "ZONE_INSET_PCT", 30, "REACTION_MIN_MS", 0, "REACTION_MAX_MS", 10,
        "POST_PRESS_SETTLE", 250, "STAGE_GAP_MS", 1500, "ROUND_GAP_MS", 800, "RETRY_GAP_MS", 800,
        "DIAL_APPEAR_TIMEOUT", 12000, "DIAL_HIT_TIMEOUT", 8000)
    SetTimer(SettingsAutoSave, 0)                 ; drop any half-typed change first
    for prop, value in defaults {
        C.%prop% := value
        SaveCfg(prop)
        if SET.Has("fld_" prop)
            try SET["fld_" prop].Value := FormatVal(value, FieldType(prop))
    }
    SET["dirty"] := false
    try SET["saveLabel"].Text := "✓  SAVED"
    try SET["warn"].Text := "TIMING RESET TO DEFAULTS"
    LogLine("timing reset to defaults")
    MainRefresh()
}

; Typing in any box: show "saving", then save once typing pauses.
SettingsChanged(*) {
    if !SET.Get("dirty", false) {
        SET["dirty"] := true
        try SET["saveLabel"].Text := "●  SAVING"
        try SET["saveLabel"].SetFont("cF2C14E")
        MainRefresh()
    }
    SetTimer(SettingsAutoSave, -800)
}

SettingsAutoSave() {
    if !SET.Has("built")
        return
    changed := 0, themeChanged := false
    for key, ctl in SET {
        if (SubStr(key, 1, 4) != "fld_")
            continue
        prop := SubStr(key, 5)
        type := FieldType(prop)
        old := C.%prop%
        val := ParseVal(ctl.Value, type, old)
        if (val = old)
            continue
        C.%prop% := val
        SaveCfg(prop)
        changed++
        ; typing a colour by hand means "use exactly this colour"
        if (prop = "ARC_COLOR" && C.ZONE_STYLE != "custom") {
            C.ZONE_STYLE := "custom", SaveCfg("ZONE_STYLE"), SET["zoneStyleValue"] := "custom"
        } else if (prop = "TICK_COLOR" && C.TICK_STYLE != "custom") {
            C.TICK_STYLE := "custom", SaveCfg("TICK_STYLE"), SET["tickStyleValue"] := "custom"
        } else if (prop = "MENU_COLOR") {
            themeChanged := true
        }
    }
    if changed {
        RecalcGeometry()
        if S.calib
            try PositionCalibOverlays()
        LogLine("settings saved (" changed " changed)")
    }
    SET["dirty"] := false
    try SET["saveLabel"].Text := "✓  SAVED"
    try SET["saveLabel"].SetFont("c6EE7B7")
    if themeChanged {
        ApplyThemeNow("CUSTOM COLOUR SAVED")
        return
    }
    for k in ["zoneStyle", "tickStyle"]
        try RefreshSettingsChoice(k)
    MainRefresh()
}

SettingsTabHandler(page) {
    return (*) => ShowSettingsPage(page)
}

SettingsTabRect(i) {
    return [250 + (i - 1) * 96, 96, 90, 36]
}

CurrentThemeName() {
    for name in THEME_ORDER
        if ((THEME_PRESETS[name] & 0xFFFFFF) = (C.MENU_COLOR & 0xFFFFFF))
            return name
    return "custom"
}

; Nudges the scaled settings layout into the middle of the window. Gradient
; strips span the full width and are left alone so the backdrop stays seamless.
CenterSettingsContent(dlg, pad, W) {
    if (pad <= 0)
        return
    prevDetect := A_DetectHiddenWindows
    DetectHiddenWindows true
    try {
        for hwnd in WinGetControlsHwnd("ahk_id " dlg.Hwnd) {
            try {
                ControlGetPos &cx, &cy, &cw, &ch, hwnd, "ahk_id " dlg.Hwnd
                if (cw >= W)
                    continue
                ControlMove cx + pad, cy, , , hwnd
            }
        }
    }
    DetectHiddenWindows prevDetect
}

SettingsButton(dlg, u, x, y, w, h, label, handler, base := "", hover := "") {
    baseColor := base = "" ? BTN_IDLE : base
    hoverColor := hover = "" ? BTN_IDLE_HOVER : hover
    dlg.SetFont("s" Round(8 * u) " norm c" THEME_BUTTON_TEXT, FONT_SEMI)
    ctl := dlg.AddText(Box(x, y, w, h) " Center 0x200 Background" baseColor, label)
    ctl.OnEvent("Click", handler)
    RoundHwnd(ctl.Hwnd, w, h, Min(Round(16 * u), h))
    if !SET.Has("buttons")
        SET["buttons"] := []
    SET["buttons"].Push(ctl)
    SET["buttonBase"][ctl.Hwnd] := baseColor
    SET["buttonHover"][ctl.Hwnd] := hoverColor
    return ctl
}

AddSettingsToggle(dlg, u, y, label, key, initialValue) {
    P(v) => Round(v * u)
    SET[key "Value"] := initialValue ? 1 : 0
    SET[key "Label"] := label
    SET[key] := SettingsButton(dlg, u, P(274), P(y), P(642), P(32), "",
        (*) => ToggleSettingsOption(key))
    RefreshSettingsToggle(key)
    SettingsTrack("behaviour", SET[key])
}

; Switches save straight away too.
ToggleSettingsOption(key) {
    SET[key "Value"] := SET.Get(key "Value", 0) ? 0 : 1
    on := SET[key "Value"]
    switch key {
        case "autoSwap":
            C.AUTO_SWAP_SLOTS := on, SaveCfg("AUTO_SWAP_SLOTS")
            if !S.running
                ResetSlots()
        case "reqActive":
            C.REQUIRE_GAME_ACTIVE := on, SaveCfg("REQUIRE_GAME_ACTIVE")
            RegisterHotkeys()
        case "debug":
            C.DEBUG := on, SaveCfg("DEBUG")
        case "autoLocate":
            C.AUTO_LOCATE_DIAL := on, SaveCfg("AUTO_LOCATE_DIAL")
    }
    LogLine(key " " (on ? "on" : "off"))
    MainRefresh()
}

RefreshSettingsToggle(key) {
    ; switches are drawn in the backdrop; the next redraw shows them
}

AddSettingsChoiceRow(dlg, u, y, label, key, options, initialValue, page := "interface") {
    P(v) => Round(v * u)
    rowPanel := Card(dlg, P(274), P(y - 4), P(642), P(38), P(10), THEME_FIELD)
    dlg.SetFont("s" P(9) " norm c" TXT_SUB, FONT_UI)
    caption := dlg.AddText(Box(P(292), P(y), P(150), P(30)) " 0x200 BackgroundTrans", label)
    SET[key "Value"] := initialValue
    SET[key "Buttons"] := Map()
    SettingsTrack(page, rowPanel, caption)

    gap := P(5)
    areaW := options.Length > 6 ? P(452) : options.Length > 4 ? P(420) : P(340)
    buttonW := Floor((areaW - gap * (options.Length - 1)) / options.Length)
    x := P(900) - areaW
    for option in options {
        value := option[1]
        ctl := SettingsButton(dlg, u, x, P(y + 1), buttonW, P(28), option[2],
            SettingsChoiceHandler(key, value), NAV_IDLE, NAV_IDLE_HOVER)
        ctl.SetFont("c" TXT_MAIN)
        SET[key "Buttons"][value] := ctl
        SettingsTrack(page, ctl)
        x += buttonW + gap
    }
    RefreshSettingsChoice(key)
}

SettingsChoiceHandler(key, value) {
    return (*) => SelectSettingsChoice(key, value)
}

; Every choice applies and saves the moment it's clicked.
SelectSettingsChoice(key, value) {
    SET[key "Value"] := value
    hwnd := UI["gui"].Hwnd
    SuspendRedraw(hwnd)
    try {
        RefreshSettingsChoice(key)
        MainRefresh(false)
    } finally {
        ResumeRedraw(hwnd)
    }
    switch key {
        case "theme":
            if (value = "custom") {
                try SET["warn"].Text := "TYPE A HEX COLOUR OR PRESS PICK…"
                return
            }
            C.MENU_COLOR := THEME_PRESETS[value]
            SaveCfg("MENU_COLOR")
            LogLine("menu colour now " value)
            SetTimer(() => ApplyThemeNow(StrUpper(value) " THEME SAVED"), -30)
        case "zoneStyle", "tickStyle":
            styleProp := key = "zoneStyle" ? "ZONE_STYLE" : "TICK_STYLE"
            colourProp := key = "zoneStyle" ? "ARC_COLOR" : "TICK_COLOR"
            presets := key = "zoneStyle" ? ZONE_PRESETS : TICK_PRESETS
            C.%styleProp% := value
            SaveCfg(styleProp)
            if presets.Has(value) {
                C.%colourProp% := presets[value]
                SaveCfg(colourProp)
                if SET.Has("fld_" colourProp)
                    try SET["fld_" colourProp].Value := Format("0x{:06X}", C.%colourProp%)
            }
            LogLine(styleProp " now " value)
            try SET["warn"].Text := (C.ZONE_STYLE = C.TICK_STYLE && C.ZONE_STYLE != "custom")
                ? "⚠ ZONE AND MARKER ARE THE SAME COLOUR - PICK DIFFERENT ONES" : ""
        case "logLevel":
            C.LOG_LEVEL := value
            SaveCfg("LOG_LEVEL")
        case "scale":
            if (value != C.UI_SCALE) {
                C.UI_SCALE := value
                SaveCfg("UI_SCALE")
                SetTimer(() => ApplyThemeNow("MENU SIZE " value "%"), -30)
            }
    }
}

ApplyThemeNow(statusText := "SAVED") {
    RefreshThemeColors()
    RebuildThemedGuis("interface", statusText)
}

PickMenuColour() {
    current := C.MENU_COLOR
    if SET.Has("fld_MENU_COLOR")
        try current := Integer(SET["fld_MENU_COLOR"].Value)
    selected := ShowColorPicker(current, UI["gui"].Hwnd)
    if (selected < 0)
        return
    C.MENU_COLOR := selected & 0xFFFFFF
    SaveCfg("MENU_COLOR")
    LogLine("menu colour now " Format("0x{:06X}", C.MENU_COLOR))
    ApplyThemeNow("CUSTOM COLOUR SAVED")
}

ChooseLogo(*) {
    try UI["gui"].Opt("+OwnDialogs")
    picked := FileSelect(1, , "Choose a logo image", "Images (*.png; *.jpg; *.jpeg; *.bmp; *.gif)")
    if (picked = "")
        return
    SplitPath(picked, , , &ext)
    ; Keep our own copy, so moving or deleting the original doesn't break the menu.
    dest := A_ScriptDir "\custom_logo_" A_Now "." StrLower(ext)
    try {
        FileCopy(picked, dest, true)
    } catch {
        try SET["warn"].Text := "COULDN'T COPY THAT IMAGE"
        return
    }
    old := C.LOGO_FILE
    C.LOGO_FILE := dest
    SaveCfg("LOGO_FILE")
    if (old != "" && old != dest && InStr(old, A_ScriptDir "\custom_logo"))
        try FileDelete(old)
    LogLine("logo now " dest)
    ApplyThemeNow("LOGO SAVED")
}

ResetLogo(*) {
    C.LOGO_FILE := ""
    SaveCfg("LOGO_FILE")
    LogLine("logo reset to the XZX logo")
    ApplyThemeNow("LOGO RESET")
}

RefreshSettingsChoice(key) {
    if !SET.Has(key "Buttons") || !SET.Has(key "Value")
        return
    for value, ctl in SET[key "Buttons"]
        try ctl.SetFont("c" (value = SET[key "Value"] ? THEME_BUTTON_TEXT : TXT_MAIN))
}

SettingsNavButton(dlg, u, x, y, w, h, label, page) {
    ctl := SettingsButton(
        dlg, u, x, y, w, h, label, (*) => ShowSettingsPage(page),
        NAV_IDLE, NAV_IDLE_HOVER
    )
    ctl.SetFont("cF4EEFF")
    SET["navButtons"][page] := ctl
    return ctl
}

SettingsTrack(page, controls*) {
    if !SET["pages"].Has(page)
        SET["pages"][page] := []
    for ctl in controls {
        SET["pages"][page].Push(ctl)
        hwnd := 0
        try hwnd := ctl.Hwnd
        if hwnd
            SET["pageOfHwnd"][hwnd] := page
        try ctl.Opt("+Hidden +Disabled")
        try ctl.Visible := false
        try ctl.Enabled := false
    }
}

AddSettingsPageHeader(dlg, u, page, title, note) {
    P(v) => Round(v * u)
    dlg.SetFont("s" P(14) " norm c" TXT_MAIN, FONT_SEMI)
    heading := dlg.AddText(Box(P(274), P(160), P(620), P(30)) " BackgroundTrans", title)
    dlg.SetFont("s" P(8) " norm c" TXT_SUB, FONT_UI)
    detail := dlg.AddText(Box(P(276), P(190), P(620), P(18)) " BackgroundTrans", note)
    accentLine := dlg.AddText(Box(P(276), P(212), P(32), P(3)) " Background" BRAND_ACCENT, "")
    RoundHwnd(accentLine.Hwnd, P(32), P(3), P(3))
    SettingsTrack(page, heading, detail, accentLine)
}

ShowSettingsPage(page) {
    if !SET.Has("pages") || !SET["pages"].Has(page)
        return
    SET["activePage"] := page
    if !(S.HasOwnProp("view") && S.view = "settings")
        return
    hwnd := UI["gui"].Hwnd
    SuspendRedraw(hwnd)
    try {
        for pageName, controls in SET["pages"]
            for ctl in controls
                SetCtlVisible(ctl, pageName = page)
        S.hoverHwnd := 0
        MainRefresh(false)
    } finally {
        ResumeRedraw(hwnd)
    }
}

; Steps a window's layered alpha from one value to another. The window is left
; layered at full opacity so no style change (and no repaint) happens later.
FadeWindowAlpha(hwnd, from, to, steps := 5, delay := 12) {
    if !hwnd || steps < 1
        return
    Loop steps {
        alpha := Round(from + (to - from) * (A_Index / steps))
        try WinSetTransparent alpha, "ahk_id " hwnd
        Sleep delay
    }
    try WinSetTransparent to, "ahk_id " hwnd
}

AddThemeColorRow(dlg, u, y, label, kind, value) {
    P(v) => Round(v * u)
    rowPanel := UiPanel(dlg, P(258), P(y - 4), P(650), P(38), THEME_FIELD, P(19))
    dlg.SetFont("s" P(8) " cA99CC4", "Bahnschrift")
    caption := dlg.AddText(
        Box(P(276), P(y), P(148), P(30)) " 0x200 BackgroundTrans",
        label
    )
    valueKey := kind "ColorValue"
    swatchKey := kind "ColorSwatch"
    hexKey := kind "ColorHex"
    SET[valueKey] := value
    SET[swatchKey] := dlg.AddText(
        Box(P(430), P(y + 6), P(28), P(18)) " Background" Format("{:06X}", value),
        ""
    )
    RoundHwnd(SET[swatchKey].Hwnd, P(28), P(18), P(9))
    SET[hexKey] := dlg.AddText(
        Box(P(464), P(y), P(94), P(30)) " 0x200 Center Background" THEME_INPUT,
        Format("0x{:06X}", value)
    )
    RoundHwnd(SET[hexKey].Hwnd, P(94), P(30), P(15))
    ; The hex readout doubles as a shortcut to the full picker.
    SET[hexKey].OnEvent("Click", (*) => PickThemeColor(kind))

    ; One-click palette: instant swatches beside the full Windows picker.
    ; Gradient stops get darker tones; buttons get vivid accents.
    palette := kind = "button"
        ? [0xFF4FAF, 0xA855F7, 0x3B82F6, 0xF43F5E, 0x10B981,
            0xF59E0B, 0x22D3EE, 0xE879F9]
        : [0xFF4FAF, 0x3A0B2A, 0x52142E, 0x1B0B3A, 0x0B1F3A,
            0x0B2A1C, 0x26051A, 0x010102]
    mini := []
    sx := 568
    for tone in palette {
        chip := dlg.AddText(
            Box(P(sx), P(y + 4), P(22), P(22)) " Background" Format("{:06X}", tone),
            ""
        )
        RoundHwnd(chip.Hwnd, P(22), P(22), P(11))
        chip.OnEvent("Click", ThemeChipHandler(kind, tone))
        mini.Push(chip)
        sx += 26
    }

    picker := SettingsButton(
        dlg, u, P(784), P(y), P(112), P(30), "CUSTOM",
        (*) => PickThemeColor(kind)
    )
    SettingsTrack(
        "interface",
        rowPanel, caption, SET[swatchKey], SET[hexKey], picker
    )
    SettingsTrack("interface", mini*)
}

; Bound per chip, so each swatch applies its own colour without shared state.
ThemeChipHandler(kind, tone) {
    return (*) => ApplyThemeColor(kind, tone)
}

AddThemePresetButton(dlg, u, x, y, label, backgroundColor, buttonColor) {
    P(v) => Round(v * u)
    light := IsLightColor(buttonColor)
    hover := Format(
        "{:06X}",
        BlendRgb(buttonColor, light ? 0x010102 : 0xFFFFFF, light ? 0.12 : 0.18)
    )
    ctl := SettingsButton(
        dlg, u, P(x), P(y), P(88), P(34), label,
        (*) => ApplyThemePreset(backgroundColor, buttonColor, label),
        Format("{:06X}", buttonColor), hover
    )
    ctl.SetFont("c" (light ? "180812" : "F4EEFF"))
    SettingsTrack("interface", ctl)
}

PickThemeColor(kind) {
    if !SET.Has("gui")
        return
    valueKey := kind "ColorValue"
    selected := ShowColorPicker(SET[valueKey], SET["gui"].Hwnd)
    if (selected < 0)
        return
    ApplyThemeColor(kind, selected)
}

ApplyThemeColor(kind, selected) {
    selected &= 0xFFFFFF
    backgroundColor := kind = "background" ? selected : C.BACKGROUND_COLOR
    gradientColor := kind = "gradient" ? selected : C.GRADIENT_COLOR
    buttonColor := kind = "button" ? selected : C.BUTTON_COLOR
    ApplyThemeValues(backgroundColor, gradientColor, buttonColor,
        StrUpper(kind) " COLOUR APPLIED")
}

ApplyThemePreset(backgroundColor, buttonColor, name) {
    ; Presets keep the near-black bottom stop of the gradient.
    ApplyThemeValues(backgroundColor, BlendRgb(backgroundColor, 0x010102, 0.86),
        buttonColor, name " THEME APPLIED")
}

ApplyThemeValues(backgroundColor, gradientColor, buttonColor, statusText) {
    backgroundColor &= 0xFFFFFF
    gradientColor &= 0xFFFFFF
    buttonColor &= 0xFFFFFF

    activePage := SET.Get("activePage", "interface")
    SaveSettings(false)
    if (backgroundColor != C.BACKGROUND_COLOR) {
        C.BACKGROUND_COLOR := backgroundColor
        SaveCfg("BACKGROUND_COLOR")
    }
    if (gradientColor != C.GRADIENT_COLOR) {
        C.GRADIENT_COLOR := gradientColor
        SaveCfg("GRADIENT_COLOR")
    }
    if (buttonColor != C.BUTTON_COLOR) {
        C.BUTTON_COLOR := buttonColor
        SaveCfg("BUTTON_COLOR")
    }

    RebuildThemedGuis(activePage, statusText)
}

FinishMainShow() {
    at := S.HasOwnProp("mainShowAt") ? S.mainShowAt : 0
    if IsObject(at) && UI.Has("gui")
        UI["gui"].Show("x" at[1] " y" at[2] " w" at[3] " h" at[4])
    if IsObject(at)
        try SetStreamProof(StreamProofEnabled)   ; re-apply now the window is visible
    S.mainShowAt := 0
    if S.HasOwnProp("mainOldGui") && IsObject(S.mainOldGui)
        try S.mainOldGui.Destroy()
    S.mainOldGui := 0
}

RebuildThemedGuis(activePage := "interface", statusText := "") {
    RefreshThemeColors()
    SetTimer(HoverTick, 0)
    S.hoverHwnd := 0
    wasSettings := (S.HasOwnProp("view") && S.view = "settings") || statusText != ""
    try {
        SET.Clear()
        BuildMainGui(false)
        BuildSettingsPages()
        if wasSettings {
            SET["activePage"] := activePage
            RefreshSettingsFields()
            ShowView("settings")
            if (statusText != "")
                try SET["warn"].Text := statusText
        }
        FinishMainShow()
    } finally {
        SetTimer(HoverTick, 100)
    }
}

ShowColorPicker(initialRgb, ownerHwnd := 0) {
    static customColors := Buffer(64, 0)
    size := A_PtrSize = 8 ? 72 : 36
    ownerOffset := A_PtrSize = 8 ? 8 : 4
    instanceOffset := A_PtrSize = 8 ? 16 : 8
    resultOffset := A_PtrSize = 8 ? 24 : 12
    customOffset := A_PtrSize = 8 ? 32 : 16
    flagsOffset := A_PtrSize = 8 ? 40 : 20

    cc := Buffer(size, 0)
    NumPut("UInt", size, cc, 0)
    NumPut("Ptr", ownerHwnd, cc, ownerOffset)
    NumPut("Ptr", 0, cc, instanceOffset)
    NumPut("UInt", RgbToColorRef(initialRgb), cc, resultOffset)
    NumPut("Ptr", customColors.Ptr, cc, customOffset)
    NumPut("UInt", 0x00000003, cc, flagsOffset)

    if !DllCall("Comdlg32\ChooseColorW", "Ptr", cc.Ptr, "Int")
        return -1
    return ColorRefToRgb(NumGet(cc, resultOffset, "UInt"))
}

RgbToColorRef(rgb) {
    return ((rgb & 0xFF) << 16) | (rgb & 0x00FF00) | ((rgb >> 16) & 0xFF)
}

ColorRefToRgb(colorRef) {
    return ((colorRef & 0xFF) << 16) | (colorRef & 0x00FF00)
        | ((colorRef >> 16) & 0xFF)
}

; One labelled row: a caption plus one or two Edit boxes bound to properties.
; `extras` is "" for a single field, or an array of further property names.
; `baseX` is kept for compatibility and ignored; `step` is the row pitch.
AddField(dlg, u, y, label, prop, extras, baseX, page, step := 44) {
    P(v) => Round(v * u)
    rowPanel := Card(dlg, P(274), P(y - 2), P(642), P(34), P(10), THEME_FIELD)
    dlg.SetFont("s" P(9) " norm c" TXT_SUB, FONT_UI)
    caption := dlg.AddText(Box(P(292), P(y), P(262), P(30)) " 0x200 BackgroundTrans", label)
    SettingsTrack(page, rowPanel, caption)
    dlg.SetFont("s" P(9) " norm c" TXT_MAIN, "Consolas")

    ; Loop variable is `pn`, NOT `p`: identifiers are case-insensitive, so `p`
    ; would rebind the local P() closure above.
    props := [prop]
    if (IsObject(extras))
        for pn in extras
            props.Push(pn)

    gap := P(6)
    fieldAreaW := P(340)
    w := Floor((fieldAreaW - gap * (props.Length - 1)) / props.Length)
    x := P(560)
    for pn in props {
        SET["fld_" pn] := dlg.Add("Edit",
            "x" x " y" P(y + 2) " w" w " h" P(26)
            . " Center Background" THEME_INPUT
            . " c" TXT_MAIN " -E0x200 -Multi -VScroll -HScroll",
            FormatVal(C.%pn%, FieldType(pn)))
        RoundHwnd(SET["fld_" pn].Hwnd, w, P(26), P(10))
        SettingsTrack(page, SET["fld_" pn])
        x += w + gap
    }
    return y + step
}

RefreshSettingsFields() {
    for key, ctl in SET {
        try {
            if (SubStr(key, 1, 4) = "fld_") {
                prop := SubStr(key, 5)
                ctl.Value := FormatVal(C.%prop%, FieldType(prop))
            } else if (SubStr(key, 1, 5) = "bind_") {
                prop := SubStr(key, 6)
                ctl.Text := C.%prop%
            }
        }
    }
    try {
        SET["autoSwapValue"] := C.AUTO_SWAP_SLOTS ? 1 : 0
        SET["reqActiveValue"] := C.REQUIRE_GAME_ACTIVE ? 1 : 0
        SET["debugValue"] := C.DEBUG ? 1 : 0
        SET["autoLocateValue"] := C.AUTO_LOCATE_DIAL ? 1 : 0
        SET["scaleValue"] := C.UI_SCALE
        SET["logLevelValue"] := Max(1, Min(3, C.LOG_LEVEL))
        SET["zoneStyleValue"] := C.ZONE_STYLE
        SET["tickStyleValue"] := C.TICK_STYLE
        SET["themeValue"] := CurrentThemeName()
        RefreshSettingsToggle("autoSwap")
        RefreshSettingsToggle("reqActive")
        RefreshSettingsToggle("debug")
        for k in ["scale", "logLevel", "zoneStyle", "tickStyle", "theme"]
            RefreshSettingsChoice(k)
    }
    try SET["warn"].Text := ""
}

SaveSettings(rebuildUi := true) {
    activePage := SET.Get("activePage", "interface")
    oldMenuColour := C.MENU_COLOR
    changed := 0
    for key, ctl in SET {
        if (SubStr(key, 1, 4) != "fld_")
            continue
        prop := SubStr(key, 5)
        type := FieldType(prop)
        old  := C.%prop%
        val  := ParseVal(ctl.Value, type, old)
        if (val != old) {
            C.%prop% := val
            SaveCfg(prop)
            changed++
        }
    }

    ; Skill check colours: picking a preset also points the exact-match colour
    ; at that preset, so the screen search finds the dial straight away.
    for pair in [["zoneStyle", "ZONE_STYLE", "ARC_COLOR", ZONE_PRESETS],
                 ["tickStyle", "TICK_STYLE", "TICK_COLOR", TICK_PRESETS]] {
        newStyle := SET.Get(pair[1] "Value", C.%pair[2]%)
        if (newStyle != C.%pair[2]%) {
            C.%pair[2]% := newStyle
            SaveCfg(pair[2])
            if pair[4].Has(newStyle) {
                C.%pair[3]% := pair[4][newStyle]
                SaveCfg(pair[3])
                if SET.Has("fld_" pair[3])
                    try SET["fld_" pair[3]].Value := Format("0x{:06X}", C.%pair[3]%)
            }
            LogLine(pair[2] " now " newStyle)
            changed++
            SetTimer(MainRefresh, -50)
        }
    }

    newScale := SET.Get("scaleValue", C.UI_SCALE)
    scaleChanged := (newScale != C.UI_SCALE)
    if (scaleChanged) {
        C.UI_SCALE := newScale
        SaveCfg("UI_SCALE")
        changed++
    }

    newBackgroundColor := SET.Has("backgroundColorValue")
        ? SET["backgroundColorValue"] & 0xFFFFFF
        : C.BACKGROUND_COLOR
    backgroundColorChanged := (newBackgroundColor != C.BACKGROUND_COLOR)
    if (backgroundColorChanged) {
        C.BACKGROUND_COLOR := newBackgroundColor
        SaveCfg("BACKGROUND_COLOR")
        changed++
    }

    newGradientColor := SET.Has("gradientColorValue")
        ? SET["gradientColorValue"] & 0xFFFFFF
        : C.GRADIENT_COLOR
    gradientColorChanged := (newGradientColor != C.GRADIENT_COLOR)
    if (gradientColorChanged) {
        C.GRADIENT_COLOR := newGradientColor
        SaveCfg("GRADIENT_COLOR")
        changed++
    }

    newButtonColor := SET.Has("buttonColorValue")
        ? SET["buttonColorValue"] & 0xFFFFFF
        : C.BUTTON_COLOR
    buttonColorChanged := (newButtonColor != C.BUTTON_COLOR)
    if (buttonColorChanged) {
        C.BUTTON_COLOR := newButtonColor
        SaveCfg("BUTTON_COLOR")
        changed++
    }

    reqOld := C.REQUIRE_GAME_ACTIVE
    C.REQUIRE_GAME_ACTIVE := SET["reqActiveValue"] ? 1 : 0
    C.AUTO_SWAP_SLOTS := SET.Get("autoSwapValue", C.AUTO_SWAP_SLOTS) ? 1 : 0
    C.DEBUG := SET["debugValue"] ? 1 : 0
    C.LOG_LEVEL := Max(1, Min(3, SET.Get("logLevelValue", C.LOG_LEVEL)))
    SaveCfg("REQUIRE_GAME_ACTIVE"), SaveCfg("AUTO_SWAP_SLOTS")
    SaveCfg("DEBUG"), SaveCfg("LOG_LEVEL")

    ; A changed slot list or a flipped swap toggle must not leave the loop
    ; pointing at an index from the old list.
    if (!S.running)
        ResetSlots()
    else if (S.slotIdx > SlotKeys().Length)
        ResetSlots()

    ; Stamp the screen these numbers belong to, so a later resolution change is
    ; detectable rather than silently wrong.
    C.CONFIGURED_FOR := A_ScreenWidth "x" A_ScreenHeight
    SaveCfg("CONFIGURED_FOR")

    RecalcGeometry()
    if (reqOld != C.REQUIRE_GAME_ACTIVE)
        RegisterHotkeys()
    if (S.calib)
        PositionCalibOverlays()

    LogLine("settings saved (" changed " changed)")
    themeChanged := backgroundColorChanged || gradientColorChanged
        || buttonColorChanged || (C.MENU_COLOR != oldMenuColour)
    if (themeChanged)
        RefreshThemeColors()
    if ((scaleChanged || themeChanged) && rebuildUi) {
        RebuildThemedGuis(activePage, "SETTINGS SAVED")
        if scaleChanged
            LogLine("UI scale now " C.UI_SCALE "%")
        if backgroundColorChanged
            LogLine("background colour now " Format("0x{:06X}", C.BACKGROUND_COLOR))
        if gradientColorChanged
            LogLine("gradient colour now " Format("0x{:06X}", C.GRADIENT_COLOR))
        if buttonColorChanged
            LogLine("button colour now " Format("0x{:06X}", C.BUTTON_COLOR))
    }
    UpdateHint()
    try SET["warn"].Text := "SETTINGS SAVED"
    if (C.ZONE_STYLE = C.TICK_STYLE && C.ZONE_STYLE != "custom")
        try SET["warn"].Text := "⚠ ZONE AND MARKER ARE THE SAME COLOUR - PICK DIFFERENT ONES"
}

; ============================================================================
;  REBINDING
; ============================================================================
; Returns a per-action click handler, so each Rebind button knows its property
; without any shared "currently rebinding" state to get out of sync.
RebindHandler(prop) {
    return (*) => CaptureBinding(prop)
}

CaptureBinding(prop) {
    if (S.capturing)
        return
    S.capturing := true
    lbl := SET["bind_" prop]
    prev := lbl.Text
    lbl.Text := "Press any key..."
    SET["warn"].Text := ""

    try {
        key := ReadKeypress()
    } finally {
        S.capturing := false
    }

    if (key = "") {                       ; Esc, timeout, or capture failed
        lbl.Text := prev
        LogLine("rebind cancelled for " prop)
        return
    }

    ; Reject a binding already in use by another action.
    for a in ACTIONS {
        other := a.prop
        if (other = prop)
            continue
        if (StrLower(C.%other%) = StrLower(key)) {
            lbl.Text := prev
            SET["warn"].Text := "'" key "' is already bound to: " a.label
            LogLine("rebind rejected - '" key "' already bound to " a.label)
            return
        }
    }

    C.%prop% := key
    SaveCfg(prop)
    lbl.Text := key
    RegisterHotkeys()                     ; applies immediately, no restart
    LogLine("rebound " prop " -> " key)
}

; Grab the next keypress, including modifiers and F-keys. Modifier keys are
; excluded as end-keys so that holding Ctrl and pressing E yields "^e" rather
; than ending the capture the moment Ctrl goes down.
ReadKeypress() {
    ih := InputHook("L0")
    ih.VisibleNonText := false
    ih.KeyOpt("{All}", "E")
    ih.KeyOpt("{LCtrl}{RCtrl}{LAlt}{RAlt}{LShift}{RShift}{LWin}{RWin}", "-E")
    ih.Start()
    ih.Wait(15)                           ; give up after 15s rather than hang
    ih.Stop()

    key := ih.EndKey
    if (key = "" || key = "Escape")
        return ""

    mods := ""
    if (GetKeyState("Ctrl",  "P"))
        mods .= "^"
    if (GetKeyState("Alt",   "P"))
        mods .= "!"
    if (GetKeyState("Shift", "P"))
        mods .= "+"
    if (GetKeyState("LWin",  "P") || GetKeyState("RWin", "P"))
        mods .= "#"
    return mods . key
}

; ============================================================================
;  CALIBRATION MODE
; ============================================================================
; Live readout of cursor position and the colour under it, plus overlays that
; show exactly where the macro is looking. Ctrl+Click samples a pixel: it is
; appended to samples.log AND offered to one of the three colour slots, which
; writes straight into config.ini so values never need hand-editing.
ToggleCalibration(*) {
    S.calib := !S.calib
    if (S.calib) {
        BuildCalibOverlays()
        CalibHotkeys(true)
        SetTimer(CalibrationTick, 100)
        SetStatus(S.state, "CALIBRATING", "amber")
        LogLine("calibration ON - Ctrl+Click samples a pixel; drag the orange"
            . " and red handles to move the regions, the orange corner to resize the box; " C.HK_CALIB " to finish")
    } else {
        SetTimer(CalibrationTick, 0)
        CalibHotkeys(false)
        DestroyCalibOverlays()
        SetStatus(S.state, S.running ? "RUNNING" : "PAUSED",
            S.running ? "green" : "amber")
        LogLine("calibration OFF")
    }
}

; The HUD is click-through (WS_EX_TRANSPARENT, 0x20) so it never blocks a
; sample. The region outlines are click-through too; each carries a small
; opaque handle, which is what you actually drag.
BuildCalibOverlays() {
    DestroyCalibOverlays()

    hud := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x20 +E0x08000000", "cal-hud")
    TrackWindow(hud.Hwnd)
    hud.BackColor := "101014"
    hud.SetFont("s11 bold cE8E8EE", "Consolas")
    CAL["hudText"] := hud.Add("Text", "x10 y8 w300 h96", "")
    CAL["hud"] := hud
    hud.Show("w320 h112 NoActivate")
    HideFromCapture(hud.Hwnd)
    box := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x20 +E0x08000000", "cal-box")
    TrackWindow(box.Hwnd)
    box.BackColor := "C345AF"
    CAL["box"] := box
    box.Show("NoActivate")
    HideFromCapture(box.Hwnd)
    WinSetTransparent(70, box)

    track := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x20 +E0x08000000", "cal-track")
    TrackWindow(track.Hwnd)
    track.BackColor := "00B7C8"
    CAL["track"] := track
    track.Show("NoActivate")
    HideFromCapture(track.Hwnd)
    WinSetTransparent(70, track)

    ; Draggable handles - deliberately NOT click-through.
    boxH := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000", "cal-boxh")
    TrackWindow(boxH.Hwnd)
    boxH.BackColor := "C9A3FF"
    CAL["boxHandle"] := boxH
    S.dragMap[boxH.Hwnd] := "dialbox"
    boxH.Show("NoActivate")
    HideFromCapture(boxH.Hwnd)
    ; Corner handle: drag it to make the detection box bigger or smaller.
    sizeH := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000", "cal-sizeh")
    TrackWindow(sizeH.Hwnd)
    sizeH.BackColor := "FFB347"
    CAL["sizeHandle"] := sizeH
    S.dragMap[sizeH.Hwnd] := "dialsize"
    sizeH.Show("NoActivate")
    HideFromCapture(sizeH.Hwnd)
    barH := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000", "cal-barh")
    TrackWindow(barH.Hwnd)
    barH.BackColor := "F0587E"
    CAL["barHandle"] := barH
    S.dragMap[barH.Hwnd] := "barmark"
    barH.Show("NoActivate")
    HideFromCapture(barH.Hwnd)
    PositionCalibOverlays()
}

PositionCalibOverlays() {
    if (!CAL.Has("box"))
        return
    ; Computed into plain variables first: a string literal followed directly by
    ; a parenthesised expression is ambiguous with a function call in v2.
    hx    := G.boxL - 16
    hy    := G.boxT - 16
    trkY  := C.BAR_Y - 2
    trkW  := C.BAR_X_RIGHT - C.BAR_X_LEFT
    markX := C.BAR_FULL_X - 7
    markY := C.BAR_Y - 7
    try {
        CAL["box"].Show(Box(G.boxL, G.boxT, G.boxW, G.boxH) " NoActivate")
        CAL["boxHandle"].Show(Box(hx, hy, 14, 14) " NoActivate")
        if CAL.Has("sizeHandle")
            CAL["sizeHandle"].Show(Box(G.boxL + G.boxW + 2, G.boxT + G.boxH + 2, 14, 14) " NoActivate")
        CAL["track"].Show(Box(C.BAR_X_LEFT, trkY, trkW, 5) " NoActivate")
        CAL["barHandle"].Show(Box(markX, markY, 14, 14) " NoActivate")
    }
}

DestroyCalibOverlays() {
    ; "assign" is included so an open colour prompt cannot outlive calibration
    ; mode as a stray always-on-top window.
    for key in ["hud", "box", "track", "boxHandle", "sizeHandle", "barHandle", "assign"] {
        if (!CAL.Has(key))
            continue
        try S.dragMap.Delete(CAL[key].Hwnd)
        try CAL[key].Destroy()
    }
    CAL.Clear()
}

; The handle sits 16px above-left of the box corner; recover the centre from it.
CalibDialBoxMoved() {
    try {
        CAL["boxHandle"].GetPos(&hx, &hy)
        C.DIAL_CX := hx + 16 + G.boxW // 2
        C.DIAL_CY := hy + 16 + G.boxH // 2
        SaveCfg("DIAL_CX"), SaveCfg("DIAL_CY")
        RecalcGeometry()
        PositionCalibOverlays()
        LogLine("dial centre -> " C.DIAL_CX "," C.DIAL_CY)
    }
}

; The corner handle sits 2px outside the bottom-right of the box. The box stays
; centred on the dial, so its new size is twice the handle's distance from it.
CalibDialBoxResized() {
    try {
        CAL["sizeHandle"].GetPos(&sx, &sy)
        half := Max(sx - 2 - C.DIAL_CX, sy - 2 - C.DIAL_CY)
        C.DIAL_BOX_PX := Max(60, Min(900, half * 2))
        SaveCfg("DIAL_BOX_PX")
        RecalcGeometry()
        PositionCalibOverlays()
        if SET.Has("fld_DIAL_BOX_PX")
            try SET["fld_DIAL_BOX_PX"].Value := C.DIAL_BOX_PX
        LogLine("detection box size -> " G.boxW "px")
    }
}

; Moving the probe marker carries the whole bar track with it, so the track and
; its probe can never drift apart.
CalibBarMarkMoved() {
    try {
        CAL["barHandle"].GetPos(&hx, &hy)
        newX := hx + 7, newY := hy + 7
        dx := newX - C.BAR_FULL_X
        C.BAR_FULL_X   := newX
        C.BAR_Y        := newY
        C.BAR_X_LEFT  += dx
        C.BAR_X_RIGHT += dx
        SaveCfg("BAR_FULL_X"), SaveCfg("BAR_Y")
        SaveCfg("BAR_X_LEFT"), SaveCfg("BAR_X_RIGHT")
        PositionCalibOverlays()
        LogLine("bar probe -> " C.BAR_FULL_X "," C.BAR_Y
            . "  track " C.BAR_X_LEFT ".." C.BAR_X_RIGHT)
    }
}

CalibrationTick() {
    MouseGetPos &mx, &my
    col := PixelAt(mx, my)
    hex := (col < 0) ? "READ FAILED" : Format("0x{:06X}", col)
    ; Polar position relative to the dial centre, so arc/tick samples can be
    ; read straight off as radius + angle.
    dx := mx - C.DIAL_CX, dy := C.DIAL_CY - my
    rad := Sqrt(dx * dx + dy * dy)
    ang := Mod(ATan2(dy, dx) * G.DEG + 360, 360)
    try {
        CAL["hudText"].Text := Format("x {}  y {}`n{}`nr {:.0f}   ang {:.0f}deg"
            . "`nCtrl+Click = sample", mx, my, hex, rad, ang)
        WinMove(Min(mx + 24, A_ScreenWidth - 330),
                Min(my + 24, A_ScreenHeight - 120), , , CAL["hud"])
    }
}

CalibrationSample(*) {
    if (!S.calib)
        return
    MouseGetPos &mx, &my
    col := PixelAt(mx, my)
    if (col < 0) {
        LogLine("sample failed - pixel unreadable at " mx "," my)
        return
    }
    hex := Format("0x{:06X}", col)
    dx := mx - C.DIAL_CX, dy := C.DIAL_CY - my
    rad := Sqrt(dx * dx + dy * dy)
    ang := Mod(ATan2(dy, dx) * G.DEG + 360, 360)
    line := Format("{} | SAMPLE | x={} y={} colour={} | dial r={:.1f} ang={:.1f}deg"
        . " | screen {}x{}",
        FormatTime(A_Now, "yyyy-MM-dd HH:mm:ss"), mx, my, hex, rad, ang,
        A_ScreenWidth, A_ScreenHeight)
    try FileAppend(line "`n", C.SAMPLE_FILE)
    LogLine("sampled " hex " at " mx "," my " (r " Round(rad) ")")
    AskAssignColor(hex, col)
}

; Small prompt: assign the sampled colour straight into a slot in config.ini.
AskAssignColor(hex, value) {
    if (CAL.Has("assign")) {
        try CAL["assign"].Destroy()
        CAL.Delete("assign")
    }
    dlg := Gui("+AlwaysOnTop -Caption +ToolWindow", "assign")
    TrackWindow(dlg.Hwnd)
    dlg.BackColor := "130C24"
    dlg.SetFont("s10 bold cE8E8EE", "Segoe UI")
    dlg.Add("Text", "x12 y10 w236", "Assign " hex " to:")
    dlg.SetFont("s9 norm", "Segoe UI")

    ; Same factory pattern as the rebind buttons: each handler must capture its
    ; own slot, not the loop variable.
    slots := [["Skill check zone colour", "ARC_COLOR", 38],
              ["Moving marker colour", "TICK_COLOR", 68],
              ["Bar filled colour", "BAR_COLOR", 98]]
    for slot in slots {
        b := dlg.Add("Button", "x12 y" slot[3] " w236 h26", slot[1])
        b.OnEvent("Click", AssignHandler(slot[2], value, dlg))
    }

    cancel := dlg.Add("Button", "x12 y132 w236 h24", "Cancel (keep in samples.log)")
    cancel.OnEvent("Click", (*) => dlg.Destroy())

    MouseGetPos &mx, &my
    CAL["assign"] := dlg
    dlg.Show("x" Min(mx + 24, A_ScreenWidth - 280)
        . " y" Min(my + 130, A_ScreenHeight - 180) " w260 h166 NoActivate")
    HideFromCapture(dlg.Hwnd)
}

AssignHandler(prop, value, dlg) {
    return (*) => AssignColor(prop, value, dlg)
}

AssignColor(prop, value, dlg) {
    C.%prop% := value
    SaveCfg(prop)
    ; A colour sampled from the screen is exact, so match it as "custom".
    styleProp := prop = "ARC_COLOR" ? "ZONE_STYLE" : prop = "TICK_COLOR" ? "TICK_STYLE" : ""
    if (styleProp != "") {
        C.%styleProp% := "custom"
        SaveCfg(styleProp)
        key := styleProp = "ZONE_STYLE" ? "zoneStyle" : "tickStyle"
        if SET.Has(key "Value") {
            SET[key "Value"] := "custom"
            try RefreshSettingsChoice(key)
        }
    }
    LogLine(prop " := " Format("0x{:06X}", value) " (saved to config.ini)")
    warn := ColorSanity(prop, value)
    if (warn != "") {
        LogLine("WARNING " warn)
        ; The settings window owns the warning line, and it may not be open -
        ; colours are usually assigned straight from calibration mode.
        if (SET.Has("warn"))
            try SET["warn"].Text := warn
    }
    if (SET.Has("fld_" prop))
        try SET["fld_" prop].Value := Format("0x{:06X}", value)
    try dlg.Destroy()
}

; A sample is only useful if it is actually the thing it is being assigned to.
; It is very easy to click a partially-covered edge pixel: sampling the tick
; where it overlapped the ring once produced 0x65364E - a muddy purple whose
; blue channel beat its green - which widened the exact-match window across a
; large swathe of dark browns. The dominance tests still carry detection, so
; this warns rather than refuses, but it says plainly what went wrong.
ColorSanity(prop, v) {
    rr := (v >> 16) & 0xFF, gg := (v >> 8) & 0xFF, bb := v & 0xFF
    hex := Format("0x{:06X}", v)
    if (prop = "TICK_COLOR" && C.TICK_STYLE = "red" && !(rr > 150 && rr - gg > 90 && rr - bb > 90))
        return hex " is a weak TICK sample (R" rr " G" gg " B" bb ")."
            . " Click the BRIGHTEST part of the red marker."
    if (prop = "ARC_COLOR" && C.ZONE_STYLE = "pink" && !(rr > 140 && bb > 120 && rr - gg > 70 && bb - gg > 60))
        return hex " is a weak ZONE sample (R" rr " G" gg " B" bb ")."
            . " Click the middle of the pink zone, not its glow or edge."
    if (prop = "BAR_COLOR" && !(gg > 120 && bb > 120 && gg - rr > 70 && bb - rr > 70))
        return hex " is a weak BAR sample (R" rr " G" gg " B" bb ")."
            . " Click the filled part of the bar, on its centre row."
    return ""
}


; NOTE:
; The toggle uses Windows SetWindowDisplayAffinity.
; It affects supported Windows capture APIs; no application can guarantee
; invisibility against every possible capture/driver method.

; F4 - Toggle Stream Proof ON/OFF
F4::ToggleStreamProof()

; ============================================================================
;  XZX FISHING  -  auto cast on the green, track the fish, collect   (AutoHotkey v2)
;  Taps 1 to cast, holds E and releases on the green, taps Q/E to keep the box
;  on the fish, then taps E to collect. Loops until you stop.
;
;  F1  = start / stop        (change it in the menu)
;  F3  = calibrate to your screen
;  F2  = show / hide the menu
;  F10 = close the script
;  Settings save automatically to fishing.ini next to this file.
; ============================================================================
#Requires AutoHotkey v2.0
;@Ahk2Exe-SetName XZX Fishing
;@Ahk2Exe-SetDescription XZX Fishing Macro
;@Ahk2Exe-SetProductName XZX Fishing
;@Ahk2Exe-SetCopyright XZX
;@Ahk2Exe-SetVersion 1.0.0
;@Ahk2Exe-SetMainIcon xzx_fishing.ico
;@Ahk2Exe-UpdateManifest 1
#SingleInstance Force
Persistent

; Run as admin so games that run as admin still receive the clicks.
if (!A_IsAdmin && !InStr(DllCall("GetCommandLine", "str"), "/restart")) {
    try {
        if A_IsCompiled
            Run('*RunAs "' A_ScriptFullPath '" /restart')
        else
            Run('*RunAs "' A_AhkPath '" /restart "' A_ScriptFullPath '"')
        ExitApp
    }
}
try DllCall("SetProcessDpiAwarenessContext", "ptr", -4)

SendMode "Event"
SetKeyDelay 10, 10
SetMouseDelay 10
CoordMode "Mouse", "Screen"
CoordMode "Pixel", "Screen"

; ---------------------------------------------------------------------------
;  SETTINGS
; ---------------------------------------------------------------------------
global INI := A_ScriptDir "\fishing.ini"
global Cfg := {
    ; cast bar region + colours (0 = auto-place from the screen; set by calibration)
    castL: ReadNum("castL", 0),  castT: ReadNum("castT", 0),  castR: ReadNum("castR", 0),  castB: ReadNum("castB", 0),
    greenCol:  ReadNum("greenCol", 0x2DA059),
    markerCol: ReadNum("markerCol", 0x8A5A12),
    ; fishing bar region + colours
    trackL: ReadNum("trackL", 0), trackT: ReadNum("trackT", 0), trackR: ReadNum("trackR", 0), trackB: ReadNum("trackB", 0),
    fishCol: ReadNum("fishCol", 0x9A4803),
    boxCol:  ReadNum("boxCol", 0x015692),
    ; tuning shown in the menu
    castHoldMs:  ReadNum("castHoldMs", 700),
    castDelayMs: ReadNum("castDelayMs", 350),
    deadzone:    ReadNum("deadzone", 14),
    tapMs:       ReadNum("tapMs", 35),
    colVar:      ReadNum("colVar", 60),
    recastMs:    ReadNum("recastMs", 1200),
    ; tuning in fishing.ini only
    releaseTol:  ReadNum("releaseTol", 10),
    trackMinMs:  ReadNum("trackMinMs", 12),   ; shortest reel press (fish nearly centred)
    trackMaxMs:  ReadNum("trackMaxMs", 60),   ; longest reel press (fish far from the box)
    waitFishMs:  ReadNum("waitFishMs", 4500),
    endHoldMs:   ReadNum("endHoldMs", 180),
    drag:        ReadNum("drag", 0),          ; reused as "flip fish direction"
    stream:      ReadNum("stream", 1),
    ; keys sent into the game
    castKey:    IniRead(INI, "Macro", "castKey", "e"),
    leftKey:    IniRead(INI, "Macro", "leftKey", "q"),
    rightKey:   IniRead(INI, "Macro", "rightKey", "e"),
    collectKey: IniRead(INI, "Macro", "collectKey", "e"),
    startKey:   IniRead(INI, "Macro", "startKey", "1"),
    runKey:    IniRead(INI, "Macro", "RunKey", "F3"),
    toggleKey: IniRead(INI, "Macro", "ToggleKey", "F1")
}
ReadNum(key, def, isFloat := false) {
    v := IniRead(INI, "Macro", key, def)
    try return isFloat ? Float(v) : Integer(v)
    return def
}
; Stream proof is always on when the macro loads (you can still turn it off for the session).
Cfg.stream := 1
InitRegions()

SaveCfg() {
    for k in ["castL","castT","castR","castB","greenCol","markerCol","trackL","trackT","trackR","trackB","fishCol","boxCol","castHoldMs","castDelayMs","deadzone","tapMs","colVar","recastMs","releaseTol","trackMinMs","trackMaxMs","waitFishMs","endHoldMs","drag","stream","castKey","leftKey","rightKey","collectKey","startKey","runKey","toggleKey"]
        IniWrite(Cfg.%k%, INI, "Macro", k)
}

; ---------------------------------------------------------------------------
;  THEME  (matches the XZX macro)
; ---------------------------------------------------------------------------
global C_ACC := 0x9D4EFF, C_ACC2 := 0x6A2FD6, C_ACCL := 0xB57BFF
global C_GREEN := 0x34C08C, C_GREEN2 := 0x1F8F66, C_ROSE := 0xE0517F, C_ROSE2 := 0xA82D58
global C_BASE := 0x09060F, C_GLOW := 0x3A1474, C_PANEL := 0x130C24, C_FIELD := 0x1A1230
global C_INPUT := 0x241940, C_BORDER := 0x2E2250, C_QUIET := 0x1E1538
global PANEL_HEX := "130C24", INPUT_HEX := "241940"
global TXT_MAIN := "F4EEFF", TXT_SUB := "A99CC4", TXT_DIM := "6F6489"
global ACCENT_HEX := "B57BFF", GREEN_HEX := "6EE7B7", DANGER_HEX := "F0587E"
global FONT := "Segoe UI", FONT_SEMI := "Segoe UI Semibold"
global LOGO := A_Temp "\xzx_fishing_logo.png"   ; built into this file, written out at startup

global WIN_W := 600, WIN_H := 756
global UI := Map(), NoDrag := Map(), Clickable := Map()
global Running := false, Looping := false, NextAt := 0, CycleMs := 1
global Runs := 0, LastRun := 0, T0 := A_TickCount, CurKeys := []
global Casts := 0, Catches := 0, Phase := "idle", PhaseAt := 0
global CastHeld := false, WasTracking := false, LostAt := 0
global LastTapAt := 0, LastStartAt := 0
global Debug := false

; License server = your Discord bot on Railway (same as the XZX Metal Detector)
global LICENSE_API_URL := "https://verification-bot-production-2ea4.up.railway.app"
global DISCORD_INVITE := "discord.gg/xzxx"
global LG := Map(), AccessGranted := false, LoginMark := "default"
global LinkState := "none", LinkName := "", AuthSession := "", LinkUrl := "", PollStart := 0
global LOGIN_W := 420, LOGIN_H := 520
global LicenseCode := ""   ; the key they logged in with (re-checked every minute)
global LastCopySig := ""   ; so the same set of copies is only reported to the bot once
global CopyScanAt := 0, CopyScanPending := false   ; background all-drive copy scan state
global HWID := ""          ; this device's hardware ID (filled in on first use)

WriteLogo(LogoData(), LOGO)
OnMessage(0x0201, WM_LBUTTONDOWN)
OnMessage(0x0020, WM_SETCURSOR)
StartupGuard()              ; a shared copy self-destructs here, before any login
ShowLogin()                 ; Discord link + access key, same as the XZX Metal Detector
BuildGui()
RegisterKeys()
SetTimer(LoopTick, 25)
SetTimer(SecondTick, 1000)
Hotkey "$F2", (*) => ToggleMenu()
Hotkey "$F9", (*) => ToggleDebug()
Hotkey "$F10", (*) => ExitApp()
SetTimer(DebugTick, 120)
SetTimer(LicenseHeartbeat, 15000)   ; checks the key every 15s and logs them out fast if it is disabled
SetTimer(StreamGuard, 4000)         ; keeps the window hidden from capture even if something resets it

; ---------------------------------------------------------------------------
;  THE MACRO
; ---------------------------------------------------------------------------
LoopTick() {
    global Phase, PhaseAt, CastHeld, WasTracking, LostAt, LastTapAt, LastStartAt, Casts, Catches
    if !Looping
        return
    t := A_TickCount

    ; FISHING wins: while the fish is visible, chase it with the two keys
    if (Cfg.trackR > Cfg.trackL && TrackFish()) {
        EnsureCastUp()
        WasTracking := true
        Phase := "fishing", PhaseAt := t
        SetHero("FISHING", "Tracking the fish - keeping it centred")
        return
    }

    ; the fishing bar vanished after tracking -> fish caught: collect, then recast
    if WasTracking {
        EnsureCastUp()
        if (LostAt = 0)
            LostAt := t
        if (t - LostAt >= Cfg.endHoldMs) {
            Tap(Cfg.collectKey)
            Catches++
            UI["catches"].Text := Catches
            WasTracking := false
            LostAt := 0
            Phase := "idle", PhaseAt := t
        }
        return
    }

    switch Phase {
        case "idle":
            SetHero("RUNNING", "Waiting to cast...")
            if (t - LastStartAt > Cfg.recastMs) {
                Tap(Cfg.startKey)
                Phase := "precast", PhaseAt := t, LastStartAt := t
            }
        case "precast":
            if (t - PhaseAt >= Cfg.castDelayMs) {
                Send "{Blind}{" Cfg.castKey " down}"
                CastHeld := true
                Phase := "charging", PhaseAt := t
                SetHero("CASTING", "Charging - releasing on the green")
            }
        case "charging":
            ; Always cast at the tuned hold time (reliable). The green auto-release can
            ; only fire it EARLIER, and never in the first 200ms, so it can't misfire to
            ; an instant release or hang holding the key.
            held := t - PhaseAt
            greenHit := false
            if (held >= 200)
                try greenHit := DetectGreenHit()
            if (greenHit || held >= Cfg.castHoldMs) {
                Send "{Blind}{" Cfg.castKey " up}"
                CastHeld := false
                Casts++
                UI["casts"].Text := Casts
                Phase := "waitfish", PhaseAt := t
                SetHero("CAST", "Waiting for the fish...")
            }
        case "waitfish":
            if (t - PhaseAt > Cfg.waitFishMs)
                Phase := "idle", PhaseAt := t
        default:
            Phase := "idle", PhaseAt := t
    }
}

; Updates the big status text without flicker.
SetHero(state, detail) {
    if (UI["state"].Text != state) {
        UI["state"].SetFont("c" (state = "READY" ? TXT_MAIN : GREEN_HEX))
        UI["state"].Text := state
        UI["count"].Text := state
    }
    if (UI["detail"].Text != detail)
        UI["detail"].Text := detail
}

StartLoop(*) {
    global Looping, Phase, PhaseAt, WasTracking, LostAt, LastStartAt, CastHeld
    if Looping
        return
    Looping := true
    Phase := "idle", PhaseAt := A_TickCount
    WasTracking := false, LostAt := 0, LastStartAt := 0, CastHeld := false
    PaintState()
    if (Cfg.castR <= Cfg.castL || Cfg.trackR <= Cfg.trackL)
        UI["detail"].Text := "Tip: press " Cfg.runKey " (or SET UP) to calibrate to your screen"
}

StopLoop(*) {
    global Looping
    Looping := false
    EnsureCastUp()
    PaintState()
}

ToggleLoop(*) {
    if Looping
        StopLoop()
    else
        StartLoop()
}

RunOnceKey(*) {
    Calibrate()
}

TestRun(*) {
    Calibrate()
}

Tap(key) {
    Send "{Blind}{" key " down}"
    Sleep 15
    Send "{Blind}{" key " up}"
}

EnsureCastUp() {
    global CastHeld
    if CastHeld {
        Send "{Blind}{" Cfg.castKey " up}"
        CastHeld := false
    }
}

; Scan the fishing row for the fish + the box, then tap to centre the fish.
TrackFish() {
    global LastTapAt
    ; sample a few rows across the bar (not just one) so the fish is found reliably
    fsum := 0, fn := 0, bsum := 0, bn := 0
    ystep := Max(3, (Cfg.trackB - Cfg.trackT) // 3)
    y := Cfg.trackT
    while (y <= Cfg.trackB) {
        x := Cfg.trackL
        while (x <= Cfg.trackR) {
            c := PixelGetColor(x, y)
            if ColorNear(c, Cfg.fishCol, Cfg.colVar)
                fsum += x, fn += 1
            else if ColorNear(c, Cfg.boxCol, Cfg.colVar)
                bsum += x, bn += 1
            x += 3
        }
        y += ystep
    }
    if (fn < 2)                         ; need a couple of pixels so a stray dot isn't "the fish"
        return false
    fishC := fsum / fn
    target := (bn > 2) ? (bsum / bn) : ((Cfg.trackL + Cfg.trackR) / 2)
    width := Max(1, Cfg.trackR - Cfg.trackL)
    diff := fishC - target
    ; proportional correction: far = longer press, close = tiny nudge (less overshoot)
    if (A_TickCount - LastTapAt >= Cfg.tapMs && Abs(diff) > Cfg.deadzone) {
        frac := Min(1.0, Abs(diff) / width)
        hold := Round(Cfg.trackMinMs + frac * (Cfg.trackMaxMs - Cfg.trackMinMs))
        goRight := (diff > 0)
        if Cfg.drag
            goRight := !goRight
        key := goRight ? Cfg.rightKey : Cfg.leftKey
        Send "{Blind}{" key " down}"
        Sleep hold
        Send "{Blind}{" key " up}"
        LastTapAt := A_TickCount
    }
    return true
}

ColorNear(c, ref, tol) {
    return Abs(((c >> 16) & 0xFF) - ((ref >> 16) & 0xFF)) <= tol
        && Abs(((c >> 8)  & 0xFF) - ((ref >> 8)  & 0xFF)) <= tol
        && Abs((c & 0xFF) - (ref & 0xFF)) <= tol
}

FindColor(x1, y1, x2, y2, color, &ox, &oy) {
    if (x2 <= x1 || y2 <= y1)
        return false
    return PixelSearch(&ox, &oy, x1, y1, x2, y2, color, Cfg.colVar)
}

; The green target sits at a random spot on the cast bar each time. Find its left
; edge, then say "release now" the moment the gold charge fill arrives just before
; it (releaseTol = px of lead so the release lands on the green despite key latency).
; The cast bar uses a strong BLUE charge that grows left->right and a strong GREEN
; target. We detect them by colour character (not an exact calibrated colour), so it
; works without fiddly colour calibration.
ColorIsGreen(c) {
    r := (c >> 16) & 0xFF, g := (c >> 8) & 0xFF, b := c & 0xFF
    return (g > 90 && g > r + 35 && g > b + 25)
}
ColorIsFill(c) {
    r := (c >> 16) & 0xFF, g := (c >> 8) & 0xFF, b := c & 0xFF
    return (b > 90 && b > r + 45 && b > g + 20)   ; the blue charge
}

; The Cast Power bar is a fixed UI element at the bottom-centre of the screen.
; We scan just its row-band (by screen fraction) so detection never depends on
; calibrating the cast region, and it dodges the blue HOLD button above the bar,
; the macro window (top-right), the minimap and the hotbar.
CastBounds(&L, &T, &R, &B) {
    W := A_ScreenWidth, H := A_ScreenHeight
    L := Round(W * 0.372), R := Round(W * 0.628)   ; inside the cast panel (avoids scene water on the sides)
    T := Round(H * 0.835), B := Round(H * 0.868)
}

; Returns the green target's left edge x on the cast bar, or -1 if not seen.
GreenLeft() {
    CastBounds(&L, &T, &R, &B)
    ystep := Max(2, (B - T) // 3)
    y := T
    while (y <= B) {
        x := L
        while (x <= R) {
            if ColorIsGreen(PixelGetColor(x, y))
                return x
            x += 2
        }
        y += ystep
    }
    return -1
}

; Returns the right edge x of the growing blue charge (rightmost blue from the left,
; tolerating small gaps), or -1 if no charge is seen.
FillEdge(stopX) {
    CastBounds(&L, &T, &R, &B)
    yc := (T + B) // 2
    edge := -1, gap := 0
    x := L
    while (x < stopX) {
        if ColorIsFill(PixelGetColor(x, yc))
            edge := x, gap := 0
        else {
            gap += 2
            if (edge >= 0 && gap > 16)      ; after the charge, a real gap = the leading edge
                break
        }
        x += 2
    }
    return edge
}

; The green target is at a random spot each cast. Release once the gold fill's
; leading edge reaches it (releaseTol = px of lead to beat key latency).
DetectGreenHit() {
    gL := GreenLeft()
    if (gL < 0)
        return false
    edge := FillEdge(gL)
    return (edge >= 0 && edge >= gL - Cfg.releaseTol)
}

ToggleDebug(*) {
    global Debug
    Debug := !Debug
    if !Debug
        ToolTip
}

; F9: live readout so you can tell if the cast bar colours/region are calibrated.
; Hover the green, then the gold fill, and watch the numbers follow your cursor.
DebugTick() {
    if !Debug
        return
    MouseGetPos(&mx, &my)
    cur := PixelGetColor(mx, my)
    gL := GreenLeft()
    edge := (gL >= 0) ? FillEdge(gL) : -1
    gtxt := (gL >= 0) ? gL : "none"
    ftxt := (edge >= 0) ? edge : "none"
    cc := PixelGetColor(mx, my)
    kind := ColorIsFill(cc) ? "BLUE charge" : ColorIsGreen(cc) ? "GREEN target" : "-"
    CastBounds(&L, &T, &R, &B)
    ToolTip "XZX detect  (F9 to hide)`n"
        . "cursor colour: " Format("0x{:06X}", cur) "  (" kind ")  @ " mx "," my "`n"
        . "green left x = " gtxt "    blue charge edge x = " ftxt "   (bar only shows while casting)`n"
        . "screen " A_ScreenWidth "x" A_ScreenHeight "   scan band = " L "," T " .. " R "," B "`n"
        . "releases when blue edge >= green - " Cfg.releaseTol
}

; If never calibrated, drop the detection boxes where the game usually draws the bars.
InitRegions() {
    W := A_ScreenWidth, H := A_ScreenHeight
    if (Cfg.castR <= Cfg.castL) {
        Cfg.castL := Round(W * 0.40), Cfg.castR := Round(W * 0.70)
        Cfg.castT := Round(H * 0.820), Cfg.castB := Round(H * 0.858)
    }
    if (Cfg.trackR <= Cfg.trackL) {
        Cfg.trackL := Round(W * 0.42), Cfg.trackR := Round(W * 0.58)
        Cfg.trackT := Round(H * 0.818), Cfg.trackB := Round(H * 0.866)
    }
}

; Calibration: hover each spot, press SPACE (Esc cancels).
Calibrate(*) {
    StopLoop()
    try UI["gui"].Hide()
    steps := [
        ["Hover the LEFT end of the CAST POWER bar", "castLpt"],
        ["Hover the RIGHT end of the CAST POWER bar", "castRpt"],
        ["Hover the LEFT end of the FISHING bar", "trackLpt"],
        ["Hover the RIGHT end of the FISHING bar", "trackRpt"],
        ["Hover the FISH", "fish"],
        ["Hover YOUR box / line that chases the fish", "box"]
    ]
    got := Map()
    for s in steps {
        r := CalibStep(s[1])
        if (r = "") {
            ToolTip
            try UI["gui"].Show()
            return
        }
        got[s[2]] := r
    }
    ToolTip
    cl := got["castLpt"], cr := got["castRpt"]
    Cfg.castL := Min(cl.x, cr.x) - 4, Cfg.castR := Max(cl.x, cr.x) + 4
    Cfg.castT := Min(cl.y, cr.y) - 12, Cfg.castB := Max(cl.y, cr.y) + 12
    tl := got["trackLpt"], tr := got["trackRpt"]
    Cfg.trackL := Min(tl.x, tr.x) - 4, Cfg.trackR := Max(tl.x, tr.x) + 4
    Cfg.trackT := Min(tl.y, tr.y) - 16, Cfg.trackB := Max(tl.y, tr.y) + 16
    Cfg.fishCol := got["fish"].c, Cfg.boxCol := got["box"].c
    SaveCfg()
    try UI["gui"].Show()
    try PaintState()
    UI["detail"].Text := "Calibrated - press START over the game"
}

CalibStep(prompt) {
    Loop {
        MouseGetPos(&mx, &my)
        col := PixelGetColor(mx, my)
        ToolTip prompt "`n`nColour here: " Format("0x{:06X}", col)
            . "`n`nHover the spot, press SPACE to lock it in  -  Esc to cancel"
        if GetKeyState("Escape", "P")
            return ""
        if GetKeyState("Space", "P") {
            KeyWait "Space"
            MouseGetPos(&mx, &my)
            return {x: mx, y: my, c: PixelGetColor(mx, my)}
        }
        Sleep 30
    }
}

RegisterKeys() {
    global CurKeys
    for k in CurKeys
        try Hotkey "$" k, "Off"
    CurKeys := []
    ok := true
    for pair in [[Cfg.toggleKey, ToggleLoop], [Cfg.runKey, RunOnceKey]] {
        try {
            Hotkey "$" pair[1], pair[2], "On"
            CurKeys.Push(pair[1])
        } catch {
            ok := false
        }
    }
    return ok
}

; Click a key box, then press the new key. Esc cancels.
CaptureKey(which) {
    chip := UI["chip_" which]
    old := Cfg.%which%
    chip.Text := "Press…"
    Suspend(true)
    ih := InputHook("L0 T10")
    ih.VisibleNonText := false
    ih.KeyOpt("{All}", "E")
    ih.KeyOpt("{LCtrl}{RCtrl}{LAlt}{RAlt}{LShift}{RShift}{LWin}{RWin}", "-E")
    ih.Start()
    ih.Wait()
    ih.Stop()
    Suspend(false)
    key := ih.EndKey
    if (key = "" || key = "Escape") {
        chip.Text := old
        return
    }
    mods := ""
    if GetKeyState("Ctrl", "P")
        mods .= "^"
    if GetKeyState("Alt", "P")
        mods .= "!"
    if GetKeyState("Shift", "P")
        mods .= "+"
    key := mods key
    other := (which = "runKey") ? Cfg.toggleKey : Cfg.runKey
    if (key = other || key = "F2" || key = "F10") {
        chip.Text := old
        UI["saved"].Text := "KEY ALREADY USED"
        return
    }
    Cfg.%which% := key
    if !RegisterKeys() {
        Cfg.%which% := old
        RegisterKeys()
        chip.Text := old
        UI["saved"].Text := "CAN'T USE THAT KEY"
        return
    }
    SaveCfg()
    chip.Text := key
    PaintState()
    FlashSaved()
}

; ---------------------------------------------------------------------------
;  GUI
; ---------------------------------------------------------------------------
BuildGui() {
    g := Gui("+AlwaysOnTop -Caption +ToolWindow", "XZX Fishing")
    g.BackColor := Format("{:06X}", C_BASE)
    g.MarginX := 0, g.MarginY := 0
    UI["gui"] := g

    hbm := Render()
    UI["bg"] := g.AddPicture("x0 y0 w" WIN_W " h" WIN_H, hbm ? "HBITMAP:*" hbm : "")
    if hbm
        DllCall("Gdi32\DeleteObject", "Ptr", hbm)

    ; ---- header
    if !FileExist(LOGO)
        Lbl(g, "x28 y22 w100 h50", "XZX", "s26 bold", ACCENT_HEX, FONT_SEMI)
    Lbl(g, "x140 y24 w280 h34", "Fishing Macro", "s17", TXT_MAIN, FONT_SEMI)
    Lbl(g, "x141 y58 w280 h20", "Auto cast  ·  track the fish  ·  collect", "s9", TXT_SUB)
    UI["pill"] := Lbl(g, "x420 y32 w124 h28 0x200 Center", "", "s8 bold", TXT_MAIN)
    Btn(g, "x560 y12 w28 h28", Chr(0xE711), (*) => ExitApp(), "s10", TXT_SUB, "Segoe MDL2 Assets")

    ; ---- hero / status card
    Lbl(g, "x46 y116 w200 h14", "STATUS", "s7 bold", TXT_DIM)
    UI["state"] := Lbl(g, "x44 y132 w320 h44", "", "s24", TXT_MAIN, FONT_SEMI, PANEL_HEX)
    UI["detail"] := Lbl(g, "x46 y178 w330 h20", "", "s9", TXT_SUB, FONT, PANEL_HEX)
    Lbl(g, "x400 y116 w154 h14 Right", "PHASE", "s7 bold", TXT_DIM)
    UI["count"] := Lbl(g, "x380 y130 w174 h44 Right", "--", "s24", TXT_MAIN, "Consolas", PANEL_HEX)
    UI["every"] := Lbl(g, "x380 y178 w174 h20 Right", "", "s9", TXT_SUB, FONT, PANEL_HEX)
    bar := g.AddProgress("x46 y218 w508 h8 -Theme Background" INPUT_HEX " c" ACCENT_HEX " Range0-1000", 0)
    RoundCtl(bar, 508, 8, 8)
    UI["bar"] := bar

    ; ---- stats
    for i, s in [["CASTS", "casts", "0"], ["CATCHES", "catches", "0"], ["SESSION TIME", "time", "00:00:00"]] {
        x := 24 + (i - 1) * 188
        Lbl(g, "x" (x + 20) " y274 w150 h14", s[1], "s7 bold", TXT_DIM)
        UI[s[2]] := Lbl(g, "x" (x + 18) " y290 w150 h30", s[3], "s16", TXT_MAIN,
            s[2] = "time" ? "Consolas" : FONT_SEMI, PANEL_HEX)
    }
    ; zero the counters mid-session (e.g. a fresh inventory of boxes) without restarting
    UI["reset"] := Btn(g, "x500 y270 w56 h20", "⟲ RESET", ResetStats, "s7 bold", ACCENT_HEX, FONT_SEMI, INPUT_HEX)

    ; ---- start / stop
    UI["startLbl"] := Btn(g, "x24 y346 w270 h54", "", StartLoop, "s12", "FFFFFF", FONT_SEMI)
    UI["stopLbl"] := Btn(g, "x306 y346 w270 h54", "", StopLoop, "s12", "FFFFFF", FONT_SEMI)

    ; ---- settings
    Lbl(g, "x46 y428 w200 h14", "SETTINGS", "s7 bold", TXT_DIM)
    UI["saved"] := Lbl(g, "x356 y428 w198 h14 Right", "✓ SAVES AUTOMATICALLY", "s7 bold", ACCENT_HEX, FONT, PANEL_HEX)
    for c in Cells() {
        Lbl(g, "x" (c.x + 14) " y" c.y " w180 h36 0x200", c.label, "s9", TXT_MAIN)
        switch c.kind {
            case "num":
                e := g.AddEdit("x" (c.x + 181) " y" (c.y + 9) " w54 h18 Center -E0x200 Limit6 Background" INPUT_HEX, Cfg.%c.key%)
                e.SetFont("s10 c" TXT_MAIN, "Consolas")
                e.OnEvent("Change", (*) => SetTimer(SaveFields, -500))
                UI["f_" c.key] := e
                NoDrag[e.Hwnd] := 1
            case "switch":
                Btn(g, "x" c.x " y" c.y " w256 h36", "", c.key = "drag" ? ToggleDrag : ToggleStream, "s9", TXT_MAIN)
            case "test":
                Btn(g, "x" (c.x + 166) " y" (c.y + 5) " w74 h26 Center", "SET UP", TestRun, "s8 bold", "FFFFFF", FONT_SEMI)
        }
    }

    ; ---- keybinds
    Lbl(g, "x46 y652 w400 h14", "KEYBINDS   ·   click a purple one to change it", "s7 bold", TXT_DIM)
    for i, k in KeybindSlots() {
        x := 46 + (i - 1) * 130
        if (k.key != "") {
            chip := Btn(g, "x" (x + 2) " y678 w54 h26", Cfg.%k.key%, CaptureHandler(k.key), "s9 bold", TXT_MAIN, FONT_SEMI, INPUT_HEX)
            UI["chip_" k.key] := chip
        } else {
            Lbl(g, "x" (x + 2) " y678 w54 h26 0x200 Center", k.fixed, "s9 bold", TXT_SUB, FONT_SEMI, INPUT_HEX)
        }
        Lbl(g, "x" (x + 66) " y676 w64 h30 0x200", k.label, "s8", TXT_SUB)
    }
    Lbl(g, "x24 y734 w552 h14 Center", "XZX Macro  ·  by Coop", "s7", TXT_DIM)

    g.Show("w" WIN_W " h" WIN_H)
    try {
        pref := Buffer(4, 0), NumPut("Int", 2, pref)
        DllCall("Dwmapi\DwmSetWindowAttribute", "Ptr", g.Hwnd, "UInt", 33, "Ptr", pref, "UInt", 4)
    }
    ApplyStream()
    ; reapply a few times so Nvidia / OBS capture hooks that attach as the window appears still get blocked
    SetTimer((*) => ApplyStream(), -400)
    SetTimer((*) => ApplyStream(), -1200)
    SetTimer((*) => ApplyStream(), -3000)
    PaintState()
}

Cells() => [
    {x: 40,  y: 452, label: "Hold cast (ms)",        kind: "num", key: "castHoldMs"},
    {x: 304, y: 452, label: "Wait before hold (ms)", kind: "num", key: "castDelayMs"},
    {x: 40,  y: 494, label: "Fish dead-zone (px)",   kind: "num", key: "deadzone"},
    {x: 304, y: 494, label: "Tap speed (ms)",        kind: "num", key: "tapMs"},
    {x: 40,  y: 536, label: "Colour tolerance",      kind: "num", key: "colVar"},
    {x: 304, y: 536, label: "Flip fish direction",   kind: "switch", key: "drag"},
    {x: 40,  y: 578, label: "Stream proof",          kind: "switch", key: "stream"},
    {x: 304, y: 578, label: "Set up (calibrate)",    kind: "test", key: ""}
]

KeybindSlots() => [
    {key: "toggleKey", fixed: "", label: "Start / Stop"},
    {key: "runKey", fixed: "", label: "Calibrate"},
    {key: "", fixed: "F2", label: "Show / hide"},
    {key: "", fixed: "F10", label: "Close"}
]

CaptureHandler(which) => (*) => CaptureKey(which)

; Text label. bg "" = transparent over the backdrop, otherwise a solid colour
; (used for text that changes, so old text never ghosts).
Lbl(g, opts, text, size, color, face := "", bg := "") {
    g.SetFont(size " c" color, face = "" ? FONT : face)
    return g.AddText(opts (bg = "" ? " BackgroundTrans" : " Background" bg), text)
}

; Clickable label sitting on a shape drawn in the backdrop.
Btn(g, opts, text, fn, size, color, face := "", bg := "") {
    c := Lbl(g, opts (InStr(opts, "Center") ? "" : " 0x200 Center"), text, size, color, face, bg)
    if InStr(opts, "Center")
        c.Opt("+0x200")
    c.OnEvent("Click", fn)
    NoDrag[c.Hwnd] := 1
    Clickable[c.Hwnd] := 1
    return c
}

RoundCtl(c, w, h, r) {
    rgn := DllCall("CreateRoundRectRgn", "Int", 0, "Int", 0, "Int", w + 1, "Int", h + 1, "Int", r, "Int", r, "Ptr")
    DllCall("SetWindowRgn", "Ptr", c.Hwnd, "Ptr", rgn, "Int", true)
}

; ---------------------------------------------------------------------------
;  STATE / ACTIONS
; ---------------------------------------------------------------------------
PaintState() {
    if Looping {
        UI["state"].SetFont("c" GREEN_HEX)
        UI["state"].Text := "RUNNING"
        UI["detail"].Text := "Casting and watching the bars  ·  " Cfg.toggleKey " or STOP to stop"
        UI["pill"].Text := "●  RUNNING"
        UI["startLbl"].Text := "●  RUNNING"
        UI["stopLbl"].Text := "■   STOP"
        UI["count"].Text := "RUNNING"
        UI["bar"].Value := 1000
    } else {
        UI["state"].SetFont("c" TXT_MAIN)
        UI["state"].Text := "READY"
        UI["detail"].Text := "Press START or " Cfg.toggleKey " to begin  ·  " Cfg.runKey " calibrates"
        UI["pill"].Text := "●  IDLE"
        UI["startLbl"].Text := "▶   START"
        UI["stopLbl"].Text := "STOP"
        UI["count"].Text := "--"
        UI["bar"].Value := 0
    }
    UI["every"].Text := Looping ? "cast · track · collect" : "ready when you are"
    RefreshBackdrop()
}

SaveFields() {
    for k in ["castHoldMs", "castDelayMs", "deadzone", "tapMs", "colVar"] {
        v := Trim(UI["f_" k].Value)
        if !IsNumber(v)
            continue
        Cfg.%k% := Max(0, Round(Float(v)))
    }
    SaveCfg()
    FlashSaved()
}

FlashSaved() {
    UI["saved"].Text := "✓ SAVED"
    SetTimer(() => UI["saved"].Text := "✓ SAVES AUTOMATICALLY", -1500)
}

ToggleDrag(*) {
    Cfg.drag := !Cfg.drag
    SaveCfg()
    RefreshBackdrop()
    FlashSaved()
}

ToggleStream(*) {
    Cfg.stream := !Cfg.stream
    SaveCfg()
    ApplyStream()
    RefreshBackdrop()
    FlashSaved()
}

ApplyStream() {
    if (UI.Has("gui"))
        SetAffinity(UI["gui"].Hwnd, Cfg.stream)
}

;  Hides the window from screen capture (OBS, Discord, NVIDIA ShadowPlay / GeForce Experience /
;  the NVIDIA app, Xbox Game Bar) while you still see it. 0x11 = WDA_EXCLUDEFROMCAPTURE
;  (Windows 10 2004+); if the system is too old for that it falls back to 0x01 = WDA_MONITOR,
;  which shows a black box in the capture instead.
SetAffinity(hwnd, on) {
    if (!hwnd)
        return
    if (!on) {
        try DllCall("SetWindowDisplayAffinity", "Ptr", hwnd, "UInt", 0)
        return
    }
    if (!DllCall("SetWindowDisplayAffinity", "Ptr", hwnd, "UInt", 0x11))   ; EXCLUDEFROMCAPTURE
        try DllCall("SetWindowDisplayAffinity", "Ptr", hwnd, "UInt", 0x01) ; MONITOR (older Windows)
}

;  NVIDIA/OBS can reset the flag when they start capturing, so keep re-asserting it while on.
StreamGuard() {
    if (Cfg.stream && UI.Has("gui"))
        SetAffinity(UI["gui"].Hwnd, true)
}

ToggleMenu() {
    win := UI["gui"]
    if DllCall("IsWindowVisible", "Ptr", win.Hwnd)
        win.Hide()
    else
        win.Show("NoActivate")
}

ResetStats(*) {
    global Casts, Catches, T0
    Casts := 0, Catches := 0
    T0 := A_TickCount
    UI["casts"].Text := "0"
    UI["catches"].Text := "0"
    UI["time"].Text := "00:00:00"
}

SecondTick() {
    s := (A_TickCount - T0) // 1000
    UI["time"].Text := Format("{:02}:{:02}:{:02}", s // 3600, Mod(s // 60, 60), Mod(s, 60))
}

WM_LBUTTONDOWN(wParam, lParam, msg, hwnd) {
    if NoDrag.Has(hwnd)
        return
    root := DllCall("GetAncestor", "Ptr", hwnd, "UInt", 2, "Ptr")
    if (UI.Has("gui") && root = UI["gui"].Hwnd) || (LG.Has("gui") && root = LG["gui"].Hwnd)
        PostMessage(0xA1, 2, 0, , "ahk_id " root)
}

WM_SETCURSOR(wParam, lParam, msg, hwnd) {
    static hand := DllCall("LoadCursor", "Ptr", 0, "Ptr", 32649, "Ptr")
    if ((lParam & 0xFFFF) = 1 && Clickable.Has(wParam)) {
        DllCall("SetCursor", "Ptr", hand)
        return true
    }
}

; ---------------------------------------------------------------------------
;  DRAWING  (everything is one smooth anti-aliased image)
; ---------------------------------------------------------------------------
RefreshBackdrop() {
    if !UI.Has("bg")
        return
    hbm := Render()
    if !hbm
        return
    SetPic(UI["bg"], hbm, UI["gui"].Hwnd)
}

; Swap a picture control's bitmap in place (no flicker), then redraw the window.
SetPic(ctl, hbm, winHwnd) {
    pic := ctl.Hwnd
    old := SendMessage(0x172, 0, hbm, pic)                ; STM_SETIMAGE
    if (old && old != hbm)
        DllCall("Gdi32\DeleteObject", "Ptr", old)
    if (SendMessage(0x173, 0, 0, pic) != hbm)              ; control kept a copy
        DllCall("Gdi32\DeleteObject", "Ptr", hbm)
    DllCall("RedrawWindow", "Ptr", winHwnd, "Ptr", 0, "Ptr", 0, "UInt", 0x181)
}

GdipStart() {
    static tok := 0
    if !tok {
        DllCall("LoadLibrary", "Str", "gdiplus")
        si := Buffer(A_PtrSize = 8 ? 24 : 16, 0), NumPut("UInt", 1, si)
        DllCall("Gdiplus\GdiplusStartup", "Ptr*", &tok, "Ptr", si, "Ptr", 0)
    }
    return tok
}

; Full image = cached static layer + the parts that change with state.
Render() {
    static pStatic := 0
    if !GdipStart()
        return 0
    if !pStatic
        pStatic := RenderStatic()
    pBmp := 0, pG := 0, hbm := 0
    DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", WIN_W, "Int", WIN_H, "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pBmp)
    DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp, "Ptr*", &pG)
    DllCall("Gdiplus\GdipDrawImageRectI", "Ptr", pG, "Ptr", pStatic, "Int", 0, "Int", 0, "Int", WIN_W, "Int", WIN_H)
    DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)
    DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)

    ; status pill
    pc := Looping ? C_GREEN : 0x8A86A0
    RoundFill(pG, 420, 32, 124, 28, 14, (Looping ? 0x30 : 0x18) << 24 | pc)
    RoundStroke(pG, 420.5, 32.5, 123, 27, 14, 0xAA000000 | pc)

    ; START button (bright when stopped)
    if !Looping {
        Glow(pG, 24, 346, 270, 54, 16, C_ACC)
        LinearFill(pG, 24, 346, 270, 54, 16, C_ACCL, C_ACC2)
        RoundFill(pG, 25, 347, 268, 26, 15, 0x18FFFFFF)
    } else {
        RoundFill(pG, 24, 346, 270, 54, 16, 0xFF000000 | C_QUIET)
        RoundStroke(pG, 24.5, 346.5, 269, 53, 16, 0xFF000000 | C_GREEN, 1.5)
    }
    ; STOP button (bright while running)
    if Looping {
        Glow(pG, 306, 346, 270, 54, 16, C_ROSE)
        LinearFill(pG, 306, 346, 270, 54, 16, C_ROSE, C_ROSE2)
        RoundFill(pG, 307, 347, 268, 26, 15, 0x18FFFFFF)
    } else {
        RoundFill(pG, 306, 346, 270, 54, 16, 0xFF000000 | C_QUIET)
        RoundStroke(pG, 306.5, 346.5, 269, 53, 16, 0xFF000000 | C_BORDER)
    }

    ; switches
    DrawSwitch(pG, 304 + 196, 536 + 7, Cfg.drag)
    DrawSwitch(pG, 40 + 196, 578 + 7, Cfg.stream)

    DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBmp, "Ptr*", &hbm, "UInt", 0xFF000000)
    DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
    DllCall("Gdiplus\GdipDisposeImage", "Ptr", pBmp)
    return hbm
}

RenderStatic() {
    pBmp := 0, pG := 0
    DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", WIN_W, "Int", WIN_H, "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pBmp)
    DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp, "Ptr*", &pG)
    DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)
    DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)

    DrawGradient(pG, WIN_W, WIN_H)

    ; logo glow + logo (from your XZX macro, if it has been run on this PC)
    if FileExist(LOGO) {
        pLogo := 0
        DllCall("Gdiplus\GdipCreateBitmapFromFile", "WStr", LOGO, "Ptr*", &pLogo)
        if pLogo {
            DllCall("Gdiplus\GdipDrawImageRectI", "Ptr", pG, "Ptr", pLogo, "Int", 18, "Int", 12, "Int", 112, "Int", 75)
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pLogo)
        }
    }

    ; cards
    Card(pG, 24, 100, 552, 150, 20)                       ; status
    Loop 3 {
        x := 24 + (A_Index - 1) * 188
        Card(pG, x, 262, 176, 72, 16)                     ; stats
        RoundFill(pG, x + 20, 322, 22, 3, 1, 0xFF000000 | C_ACCL)
    }
    Card(pG, 24, 414, 552, 214, 20)                       ; settings
    Card(pG, 24, 640, 552, 84, 18)                        ; keybinds

    ; setting rows
    for c in Cells() {
        RoundFill(pG, c.x, c.y, 256, 36, 10, 0xFF000000 | C_FIELD)
        if (c.kind = "num") {
            RoundFill(pG, c.x + 176, c.y + 5, 64, 26, 8, 0xFF000000 | C_INPUT)
            RoundStroke(pG, c.x + 176.5, c.y + 5.5, 63, 25, 8, 0xFF000000 | C_BORDER)
        } else if (c.kind = "test") {
            LinearFill(pG, c.x + 166, c.y + 5, 74, 26, 8, C_ACCL, C_ACC2)
        }
    }

    ; keybind chips (purple outline = click to change)
    for i, k in KeybindSlots() {
        x := 46 + (i - 1) * 130
        RoundFill(pG, x, 676, 58, 30, 9, 0xFF000000 | C_INPUT)
        RoundStroke(pG, x + 0.5, 676.5, 57, 29, 9, 0xFF000000 | (k.key != "" ? C_ACC : C_BORDER), k.key != "" ? 1.4 : 1)
    }

    DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
    return pBmp
}

Card(pG, x, y, w, h, r) {
    RoundFill(pG, x, y, w, h, r, 0xFF000000 | C_PANEL)
    RoundStroke(pG, x + 0.5, y + 0.5, w - 1, h - 1, r, 0xFF000000 | C_BORDER)
}

DrawSwitch(pG, x, y, on) {
    if on
        LinearFill(pG, x, y, 44, 22, 11, C_ACCL, C_ACC2)
    else {
        RoundFill(pG, x, y, 44, 22, 11, 0xFF000000 | C_INPUT)
        RoundStroke(pG, x + 0.5, y + 0.5, 43, 21, 11, 0xFF000000 | C_BORDER)
    }
    RoundFill(pG, on ? x + 25 : x + 3, y + 3, 16, 16, 8, on ? 0xFFFFFFFF : 0xFF8A86A0)
}

Glow(pG, x, y, w, h, r, rgb) {
    Loop 6 {
        k := 7 - A_Index
        RoundFill(pG, x - k * 2, y - k * 2 + 5, w + k * 4, h + k * 4, r + k * 2, (5 * A_Index) << 24 | rgb)
    }
}

RoundPath(x, y, w, h, r) {
    p := 0, d := r * 2
    DllCall("Gdiplus\GdipCreatePath", "Int", 0, "Ptr*", &p)
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", p, "Float", x, "Float", y, "Float", d, "Float", d, "Float", 180, "Float", 90)
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", p, "Float", x + w - d, "Float", y, "Float", d, "Float", d, "Float", 270, "Float", 90)
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", p, "Float", x + w - d, "Float", y + h - d, "Float", d, "Float", d, "Float", 0, "Float", 90)
    DllCall("Gdiplus\GdipAddPathArc", "Ptr", p, "Float", x, "Float", y + h - d, "Float", d, "Float", d, "Float", 90, "Float", 90)
    DllCall("Gdiplus\GdipClosePathFigure", "Ptr", p)
    return p
}

RoundFill(pG, x, y, w, h, r, argb) {
    p := RoundPath(x, y, w, h, r), br := 0
    DllCall("Gdiplus\GdipCreateSolidFill", "UInt", argb, "Ptr*", &br)
    DllCall("Gdiplus\GdipFillPath", "Ptr", pG, "Ptr", br, "Ptr", p)
    DllCall("Gdiplus\GdipDeleteBrush", "Ptr", br)
    DllCall("Gdiplus\GdipDeletePath", "Ptr", p)
}

RoundStroke(pG, x, y, w, h, r, argb, width := 1) {
    p := RoundPath(x, y, w, h, r), pen := 0
    DllCall("Gdiplus\GdipCreatePen1", "UInt", argb, "Float", width, "Int", 2, "Ptr*", &pen)
    DllCall("Gdiplus\GdipDrawPath", "Ptr", pG, "Ptr", pen, "Ptr", p)
    DllCall("Gdiplus\GdipDeletePen", "Ptr", pen)
    DllCall("Gdiplus\GdipDeletePath", "Ptr", p)
}

LinearFill(pG, x, y, w, h, r, rgb1, rgb2) {
    p := RoundPath(x, y, w, h, r), br := 0
    p1 := Buffer(8), p2 := Buffer(8)
    NumPut("Float", x, "Float", y, p1)
    NumPut("Float", x + w, "Float", y + h, p2)
    DllCall("Gdiplus\GdipCreateLineBrush", "Ptr", p1, "Ptr", p2,
        "UInt", 0xFF000000 | rgb1, "UInt", 0xFF000000 | rgb2, "Int", 0, "Ptr*", &br)
    DllCall("Gdiplus\GdipFillPath", "Ptr", pG, "Ptr", br, "Ptr", p)
    DllCall("Gdiplus\GdipDeleteBrush", "Ptr", br)
    DllCall("Gdiplus\GdipDeletePath", "Ptr", p)
}

Blend(a, b, t) {
    rr := Round(((a >> 16) & 0xFF) + ((((b >> 16) & 0xFF) - ((a >> 16) & 0xFF)) * t))
    gg := Round(((a >> 8) & 0xFF) + ((((b >> 8) & 0xFF) - ((a >> 8) & 0xFF)) * t))
    bb := Round((a & 0xFF) + (((b & 0xFF) - (a & 0xFF)) * t))
    return (rr << 16) | (gg << 8) | bb
}

; Violet glow fading into near-black, drawn into any w x h surface.
DrawGradient(pG, w, h) {
    gw := 40, gh := Round(40 * h / w), pSmall := 0, pAttr := 0
    DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", gw, "Int", gh, "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pSmall)
    Loop gh {
        j := A_Index - 1, v := j / (gh - 1)
        Loop gw {
            i := A_Index - 1, u := i / (gw - 1)
            d1 := Sqrt((u - 0.15) ** 2 + ((v + 0.05) * 1.3) ** 2)
            d2 := Sqrt((u - 1.0) ** 2 + ((v - 1.05) * 1.3) ** 2)
            amt := Max(0, 1 - d1 / 1.1) ** 1.6 + 0.4 * Max(0, 1 - d2 / 0.85) ** 2
            DllCall("Gdiplus\GdipBitmapSetPixel", "Ptr", pSmall, "Int", i, "Int", j,
                "UInt", 0xFF000000 | Blend(C_BASE, C_GLOW, Min(1, amt)))
        }
    }
    DllCall("Gdiplus\GdipSetInterpolationMode", "Ptr", pG, "Int", 7)
    DllCall("Gdiplus\GdipCreateImageAttributes", "Ptr*", &pAttr)
    DllCall("Gdiplus\GdipSetImageAttributesWrapMode", "Ptr", pAttr, "Int", 3, "UInt", 0, "Int", 0)
    DllCall("Gdiplus\GdipDrawImageRectRectI", "Ptr", pG, "Ptr", pSmall, "Int", 0, "Int", 0, "Int", w, "Int", h,
        "Int", 0, "Int", 0, "Int", gw, "Int", gh, "Int", 2, "Ptr", pAttr, "Ptr", 0, "Ptr", 0)
    DllCall("Gdiplus\GdipDisposeImageAttributes", "Ptr", pAttr)
    DllCall("Gdiplus\GdipDisposeImage", "Ptr", pSmall)
}

; ===========================================================================
;  LICENSE / LOGIN  -  same Discord bot + key system as the XZX Metal Detector
; ===========================================================================

ShowLogin() {
    g := Gui("+AlwaysOnTop -Caption +ToolWindow", "XZX Fishing - Login")
    g.BackColor := Format("{:06X}", C_BASE)
    g.MarginX := 0, g.MarginY := 0
    LG["gui"] := g
    hbm := LoginRender()
    LG["bg"] := g.AddPicture("x0 y0 w" LOGIN_W " h" LOGIN_H, hbm ? "HBITMAP:*" hbm : "")
    if hbm
        DllCall("Gdi32\DeleteObject", "Ptr", hbm)

    if !FileExist(LOGO)
        Lbl(g, "x20 y34 w380 h60 Center", "XZX", "s34 bold", ACCENT_HEX, FONT_SEMI)
    Btn(g, "x382 y12 w28 h28", Chr(0xE711), (*) => ExitApp(), "s10", TXT_SUB, "Segoe MDL2 Assets")
    Lbl(g, "x20 y112 w380 h32 Center", "Fishing Macro", "s16", TXT_MAIN, FONT_SEMI)
    Lbl(g, "x20 y146 w380 h20 Center", "Link your Discord, then enter your access key", "s9", TXT_SUB)

    Lbl(g, "x44 y196 w300 h16", "1   DISCORD ACCOUNT", "s7 bold", TXT_DIM)
    LG["discord"] := Btn(g, "x44 y216 w332 h44", "Link Discord account", LoginLinkClick, "s10", "FFFFFF", FONT_SEMI)
    Lbl(g, "x44 y276 w300 h16", "2   ACCESS KEY", "s7 bold", TXT_DIM)
    e := g.AddEdit("x60 y308 w246 h22 Password -E0x200 Background" INPUT_HEX, "")
    e.SetFont("s11 c" TXT_MAIN, "Consolas")
    e.OnEvent("Change", LoginClearMark)
    NoDrag[e.Hwnd] := 1
    LG["key"] := e
    LG["show"] := Btn(g, "x316 y306 w50 h24", "SHOW", LoginToggleReveal, "s7 bold", ACCENT_HEX, FONT, INPUT_HEX)
    LG["unlock"] := Btn(g, "x44 y356 w332 h44", "Unlock", TryLogin, "s10", "FFFFFF", FONT_SEMI)
    hidden := g.AddButton("x-100 y-100 w1 h1 Default", "")
    hidden.OnEvent("Click", TryLogin)
    LG["status"] := Lbl(g, "x44 y410 w332 h20 0x200 Center", "LINK YOUR DISCORD TO CONTINUE", "s8", TXT_SUB, FONT, PANEL_HEX)
    Btn(g, "x20 y458 w380 h20", "Need help?  Join " DISCORD_INVITE, (*) => Run("https://" DISCORD_INVITE), "s8", ACCENT_HEX)
    Lbl(g, "x20 y484 w380 h16 Center", "XZX Macro  ·  by Coop", "s7", TXT_DIM)

    g.OnEvent("Close", (*) => ExitApp())
    g.OnEvent("Escape", (*) => ExitApp())
    g.Show("w" LOGIN_W " h" LOGIN_H)
    RoundWindow(g)
    SetAffinity(g.Hwnd, Cfg.stream)
    SetTimer(LoginRestoreLink, -150)

    while !AccessGranted
        Sleep 50

    SetTimer(LoginAuthPoll, 0)
    g.Destroy()
    LG.Clear()
}

RoundWindow(g) {
    try {
        pref := Buffer(4, 0), NumPut("Int", 2, pref)
        DllCall("Dwmapi\DwmSetWindowAttribute", "Ptr", g.Hwnd, "UInt", 33, "Ptr", pref, "UInt", 4)
    }
}

LoginRefresh() {
    if !LG.Has("bg")
        return
    if (hbm := LoginRender())
        SetPic(LG["bg"], hbm, LG["gui"].Hwnd)
}

; The whole login look (gradient, logo, card, buttons) as one smooth image.
LoginRender() {
    if !GdipStart()
        return 0
    pBmp := 0, pG := 0, hbm := 0
    DllCall("Gdiplus\GdipCreateBitmapFromScan0", "Int", LOGIN_W, "Int", LOGIN_H, "Int", 0, "UInt", 0x26200A, "Ptr", 0, "Ptr*", &pBmp)
    DllCall("Gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp, "Ptr*", &pG)
    DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)
    DllCall("Gdiplus\GdipSetPixelOffsetMode", "Ptr", pG, "Int", 4)
    DrawGradient(pG, LOGIN_W, LOGIN_H)
    DllCall("Gdiplus\GdipSetSmoothingMode", "Ptr", pG, "Int", 4)

    if FileExist(LOGO) {
        pLogo := 0
        DllCall("Gdiplus\GdipCreateBitmapFromFile", "WStr", LOGO, "Ptr*", &pLogo)
        if pLogo {
            DllCall("Gdiplus\GdipDrawImageRectI", "Ptr", pG, "Ptr", pLogo, "Int", 150, "Int", 22, "Int", 120, "Int", 80)
            DllCall("Gdiplus\GdipDisposeImage", "Ptr", pLogo)
        }
    }

    Card(pG, 24, 180, 372, 260, 22)

    ; step 1: Discord button
    if (LinkState = "linked") {
        RoundFill(pG, 44, 216, 332, 44, 12, 0xFF132A23)
        RoundStroke(pG, 44.5, 216.5, 331, 43, 12, 0xFF3CC48F, 1.4)
    } else if (LinkState = "off") {
        RoundFill(pG, 44, 216, 332, 44, 12, 0xFF000000 | C_QUIET)
    } else {
        if (LinkState != "pending")
            Glow(pG, 44, 216, 332, 44, 12, 0x5865F2)
        LinearFill(pG, 44, 216, 332, 44, 12, 0x5865F2, 0x4450C8)
        if (LinkState = "pending")
            RoundStroke(pG, 44.5, 216.5, 331, 43, 12, 0xFFB5BCFF, 1.4)
    }

    ; step 2: key box
    RoundFill(pG, 44, 296, 332, 44, 12, 0xFF000000 | C_INPUT)
    border := LoginMark = "error" ? 0xFFF0587E : LoginMark = "ok" ? 0xFF3CC48F : 0xFF000000 | C_BORDER
    RoundStroke(pG, 44.5, 296.5, 331, 43, 12, border, LoginMark = "default" ? 1 : 1.6)

    ; Unlock: muted until Discord is linked
    if (LinkState = "linked" || LinkState = "off") {
        Glow(pG, 44, 356, 332, 44, 12, LoginMark = "ok" ? C_GREEN : C_ACC)
        if (LoginMark = "ok")
            LinearFill(pG, 44, 356, 332, 44, 12, C_GREEN, C_GREEN2)
        else
            LinearFill(pG, 44, 356, 332, 44, 12, C_ACCL, C_ACC2)
    } else {
        RoundFill(pG, 44, 356, 332, 44, 12, 0xFF000000 | C_QUIET)
        RoundStroke(pG, 44.5, 356.5, 331, 43, 12, 0xFF000000 | C_BORDER)
    }

    DllCall("Gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBmp, "Ptr*", &hbm, "UInt", 0xFF000000)
    DllCall("Gdiplus\GdipDeleteGraphics", "Ptr", pG)
    DllCall("Gdiplus\GdipDisposeImage", "Ptr", pBmp)
    return hbm
}

LicenseHttp(method, path, body := "", timeoutMs := 6000) {
    try {
        http := ComObject("WinHttp.WinHttpRequest.5.1")
        http.Open(method, RTrim(LICENSE_API_URL, "/") path, false)
        http.SetTimeouts(timeoutMs, timeoutMs, timeoutMs, timeoutMs)
        http.SetRequestHeader("Content-Type", "application/x-www-form-urlencoded")
        http.SetRequestHeader("User-Agent", "XZXAltClick/1.0")
        http.Send(body)
        if (http.Status != 200)
            return "DENIED|SERVER ERROR " http.Status
        return Trim(http.ResponseText)
    } catch {
        return "DENIED|SERVER UNREACHABLE"
    }
}

LoginSetLink(state, name := "") {
    global LinkState, LinkName
    LinkState := state
    LinkName := name
    LG["discord"].Text := state = "linked" ? "✓   Linked as " name "   ·   change"
        : state = "pending" ? "Waiting for Discord…   (click to reopen)"
        : state = "off" ? "Discord linking is off"
        : "Link Discord account"
    LoginRefresh()
}

LoginSetStatus(text, color := "") {
    LG["status"].SetFont("c" (color = "" ? TXT_SUB : color))
    LG["status"].Text := text
}

; A link from an earlier launch is remembered - check it's still good.
LoginRestoreLink() {
    global AuthSession
    saved := IniRead(INI, "Auth", "Session", "")
    if (saved = "") {
        r := LicenseHttp("GET", "/api/auth/status?s=none", "", 4000)
        if (r = "OFF" || InStr(r, "SERVER ERROR 404")) {
            LoginSetLink("off")
            LoginSetStatus("KEY REQUIRED")
        }
        return
    }
    r := LicenseHttp("GET", "/api/auth/status?s=" saved, "", 4000)
    parts := StrSplit(r, "|")
    if (parts[1] = "LINKED") {
        AuthSession := saved
        LoginSetLink("linked", parts.Length >= 2 ? parts[2] : "")
        LoginSetStatus("KEY REQUIRED")
        try LG["key"].Focus()
    } else if (parts[1] = "OFF" || InStr(r, "SERVER ERROR 404")) {
        LoginSetLink("off")
        LoginSetStatus("KEY REQUIRED")
    } else {
        try IniDelete(INI, "Auth", "Session")
    }
}

LoginLinkClick(*) {
    global AuthSession, LinkUrl, PollStart
    if (LinkState = "off")
        return
    if (LinkState = "pending" && LinkUrl != "") {
        try Run(LinkUrl)
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
        LoginSetStatus(parts.Length >= 2 ? parts[2] : "COULDN'T REACH THE KEY SERVER", DANGER_HEX)
        return
    }
    AuthSession := parts[2]
    LinkUrl := parts[3]
    PollStart := A_TickCount
    LoginSetLink("pending")
    LoginSetStatus("PRESS AUTHORIZE IN YOUR BROWSER")
    try Run(LinkUrl)
    SetTimer(LoginAuthPoll, 1500)
}

LoginAuthPoll() {
    global LinkUrl
    if !LG.Has("gui") {
        SetTimer(LoginAuthPoll, 0)
        return
    }
    if (A_TickCount - PollStart > 600000) {
        SetTimer(LoginAuthPoll, 0)
        LoginSetLink("none")
        LoginSetStatus("LINK TIMED OUT - TRY AGAIN", DANGER_HEX)
        return
    }
    r := LicenseHttp("GET", "/api/auth/status?s=" AuthSession, "", 4000)
    parts := StrSplit(r, "|")
    switch parts[1] {
        case "PENDING":
            return
        case "LINKED":
            SetTimer(LoginAuthPoll, 0)
            try IniWrite(AuthSession, INI, "Auth", "Session")
            LinkUrl := ""
            LoginSetLink("linked", parts.Length >= 2 ? parts[2] : "")
            LoginSetStatus("DISCORD LINKED  •  NOW ENTER YOUR KEY", ACCENT_HEX)
            try WinActivate("ahk_id " LG["gui"].Hwnd)
            try LG["key"].Focus()
        case "ERROR":
            SetTimer(LoginAuthPoll, 0)
            LoginSetLink("none")
            LoginSetStatus(parts.Length >= 2 ? parts[2] : "LINK FAILED - TRY AGAIN", DANGER_HEX)
        case "EXPIRED":
            SetTimer(LoginAuthPoll, 0)
            LoginSetLink("none")
            LoginSetStatus("LINK EXPIRED - TRY AGAIN", DANGER_HEX)
        default:
            return    ; network blip: keep waiting
    }
}

LoginToggleReveal(*) {
    static shown := false
    shown := !shown
    SendMessage(0xCC, shown ? 0 : 0x25CF, 0, LG["key"])   ; EM_SETPASSWORDCHAR
    LG["show"].Text := shown ? "HIDE" : "SHOW"
    LG["key"].Redraw()
    LG["key"].Focus()
}

LoginClearMark(*) {
    global LoginMark
    if (LoginMark = "error") {
        LoginMark := "default"
        LoginRefresh()
        LoginSetStatus("KEY REQUIRED")
    }
}

TryLogin(*) {
    global AccessGranted, LoginMark, LicenseCode
    if (LinkState != "linked" && LinkState != "off") {
        LoginSetStatus(LinkState = "pending" ? "FINISH LINKING DISCORD IN YOUR BROWSER FIRST"
            : "LINK YOUR DISCORD ACCOUNT FIRST", DANGER_HEX)
        return
    }
    code := Trim(LG["key"].Value)
    if (code = "") {
        LoginSetStatus("ENTER YOUR ACCESS KEY", DANGER_HEX)
        LG["key"].Focus()
        return
    }
    LoginSetStatus("CHECKING LICENSE…")
    r := LicenseHttp("POST", "/api/activate", "code=" UrlEncode(code) "&session=" UrlEncode(AuthSession)
        . "&product=fishing&hwid=" UrlEncode(GetHWID()), 8000)
    if (SubStr(r, 1, 3) = "OK|") {
        LicenseCode := code
        ; remember the key and this PC, so a shared copy self-destructs on another PC before any login
        try IniWrite(code, INI, "Auth", "Key")
        try IniWrite(GetHWID(), INI, "Auth", "HWID")
        LoginMark := "ok"
        LoginRefresh()
        LoginSetStatus("ACCESS GRANTED", GREEN_HEX)
        Sleep 300
        AccessGranted := true
        return
    }
    msg := (SubStr(r, 1, 7) = "DENIED|") ? SubStr(r, 8) : r
    if (msg = "")
        msg := "LICENSE CHECK FAILED"
    ; This key is locked to someone else's PC: this copy was shared, so remove it.
    if InStr(msg, "LOCKED TO ANOTHER DEVICE") {
        LoginSetStatus("THIS KEY IS LOCKED TO ANOTHER PC", DANGER_HEX)
        SelfDestruct()
        MsgTimeout("This key is locked to a different computer, so this copy can't be used here."
            . "`n`nIt will now remove itself.`n`nGot a new PC? Open a ticket in " DISCORD_INVITE " and ask staff to reset it.",
            "XZX Fishing", 6000)
        ExitApp
    }
    if InStr(msg, "LINK YOUR DISCORD") {      ; link ran out on the server
        try IniDelete(INI, "Auth", "Session")
        LoginSetLink("none")
    } else {
        LG["key"].Value := ""
    }
    LoginMark := "error"
    LoginRefresh()
    LoginSetStatus("ACCESS DENIED  •  " msg, DANGER_HEX)
    LG["key"].Focus()
}

;  Every minute: is the key still allowed? If staff disabled it (or it expired),
;  stop the macro and send them back to the login screen.
;  Only a clear "REVOKED" answer logs out. No internet / server down = keep going.
LicenseHeartbeat() {
    static busy := false, tick := 0
    if (busy || LicenseCode = "")
        return
    busy := true
    r := LicenseHttp("POST", "/api/check", "code=" UrlEncode(LicenseCode) "&session=" UrlEncode(AuthSession)
        . "&product=fishing&hwid=" UrlEncode(GetHWID()), 8000)
    busy := false
    if (SubStr(r, 1, 8) = "REVOKED|") {
        ForceLogout(SubStr(r, 9))
        return
    }
    tick++
    PollCopies()                       ; pick up finished scans fast, kick a new one every ~60s
}

;  Looks for other copies of this .exe in the user's own folders and tells the bot
;  about them (it logs a warning in your key-logs channel). Only reports a given set
;  of copies once. Does nothing for the uncompiled .ahk while testing.
;  Runs every heartbeat (15s): reads a finished background scan and reports copies to the bot,
;  and starts a fresh all-drive scan about once a minute. The scan runs in its own hidden
;  process so it never freezes the macro.
PollCopies() {
    global LastCopySig, LicenseCode, CopyScanAt, CopyScanPending
    if (LicenseCode = "")
        return
    scan := A_Temp "\xzx_fishing_scan.txt"
    done := scan ".done"
    running := StrLower(A_ScriptFullPath)
    ; a scan has finished -> read it and report
    if (CopyScanPending && FileExist(done)) {
        CopyScanPending := false
        found := Map()
        try {
            for line in StrSplit(FileRead(scan, "UTF-8"), "`n", "`r") {
                p := Trim(line)
                if (p != "" && StrLower(p) != running && FileExist(p))
                    found[StrLower(p)] := p
            }
        }
        try FileDelete(scan)
        try FileDelete(done)
        SplitPath(A_ScriptFullPath, &fn, , , &nameNoExt)
        bin := RecycleScan(nameNoExt)          ; copies sitting in the Recycle Bin
        if (found.Count || bin.count) {
            sig := "bin" bin.count "|"
            for key, p in found
                sig .= key "|"
            if (sig != LastCopySig) {          ; only report a new set of copies once
                LastCopySig := sig
                list := ""
                for key, p in found           ; every copy's full path
                    list .= (list = "" ? "" : "`n") p
                for n in bin.names
                    list .= (list = "" ? "" : "`n") "[Recycle Bin] " n
                LicenseHttp("POST", "/api/report", "code=" UrlEncode(LicenseCode) "&session=" UrlEncode(AuthSession)
                    . "&product=fishing&count=" found.Count "&bin=" bin.count "&hwid=" UrlEncode(GetHWID())
                    . "&paths=" UrlEncode(SubStr(list, 1, 1500)), 8000)
            }
        } else {
            LastCopySig := ""
        }
    }
    ; no scan running and it's been ~60s (or this is the first one) -> start a fresh all-drive scan
    if (!CopyScanPending && (CopyScanAt = 0 || A_TickCount - CopyScanAt >= 60000)) {
        SplitPath(A_ScriptFullPath, &fn)
        StartCopyScan(fn, scan, done)
        CopyScanPending := true
        CopyScanAt := A_TickCount
    }
}

;  Runs at launch, before the login screen. If this copy was first set up on another PC
;  (its saved HWID doesn't match this one), it isn't allowed here, so it removes itself -
;  no login needed. Also asks the server once, so a disabled/expired key removes the file too.
;  A missing saved HWID (fresh copy) or no internet just carries on to the login screen.
StartupGuard() {
    savedHwid := "", savedKey := ""
    try savedHwid := IniRead(INI, "Auth", "HWID", "")
    try savedKey := IniRead(INI, "Auth", "Key", "")
    if (savedHwid != "" && savedHwid != GetHWID()) {
        SelfDestruct()
        MsgTimeout("This copy of the macro belongs to a different computer, so it can't run here."
            . "`n`nIt will now remove itself.`n`nGot a new PC? Open a ticket in " DISCORD_INVITE " and ask staff to reset your key.",
            "XZX Fishing", 6000)
        ExitApp
    }
    if (savedKey != "") {              ; backstop: server says the key is gone/disabled/on another device
        sess := ""
        try sess := IniRead(INI, "Auth", "Session", "")
        r := LicenseHttp("POST", "/api/check", "code=" UrlEncode(savedKey) "&session=" UrlEncode(sess)
            . "&product=fishing&hwid=" UrlEncode(GetHWID()), 6000)
        if (SubStr(r, 1, 8) = "REVOKED|") {
            reason := SubStr(r, 9)
            if (InStr(reason, "DISABLED") || InStr(reason, "DELETED") || InStr(reason, "EXPIRED") || InStr(reason, "DEVICE")) {
                SelfDestruct()
                MsgTimeout("This copy of the macro can't be used on this PC.`n`n" reason
                    . "`n`nIt will now remove itself.`n`nOpen a ticket in " DISCORD_INVITE " if this is a mistake.",
                    "XZX Fishing", 6000)
                ExitApp
            }
        }
    }
}

;  A stable ID for this PC (Windows' own MachineGuid). Lets you spot the same machine
;  across different keys. Falls back to the computer name + C: drive serial.
GetHWID() {
    global HWID
    if (HWID != "")
        return HWID
    try {
        g := RegRead("HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography", "MachineGuid")
        if (g != "")
            return HWID := g
    }
    serial := ""
    try serial := DriveGetSerial("C:\")
    HWID := A_ComputerName (serial != "" ? "-" serial : "")
    return HWID
}

;  Counts copies of the macro sitting in the Recycle Bin and returns their original names.
;  ssfBITBUCKET = 10 is the shell's Recycle Bin folder.
RecycleScan(nameNoExt) {
    out := {count: 0, names: []}
    if (nameNoExt = "")
        return out
    try {
        bin := ComObject("Shell.Application").Namespace(10)
        for item in bin.Items() {
            if InStr(item.Name, nameNoExt) {
                out.count += 1
                if (out.names.Length < 20)
                    out.names.Push(item.Name)
            }
        }
    }
    return out
}

StartCopyScan(fn, scan, done) {
    drives := ""
    try drives := DriveGetList("FIXED")
    if (drives = "")
        drives := "C"
    bat := A_Temp "\xzx_fishing_find.cmd"
    body := "@echo off`r`n"
    body .= 'if exist "' scan '" del /q "' scan '" 2>nul`r`n'
    body .= 'if exist "' done '" del /q "' done '" 2>nul`r`n'
    Loop Parse drives
        body .= 'dir /s /b "' A_LoopField ':\' fn '" >> "' scan '" 2>nul`r`n'
    body .= 'echo done> "' done '"`r`n'      ; marker so the macro knows the scan finished
    try FileDelete(bat)
    try FileAppend(body, bat)
    try Run('cmd.exe /c "' bat '"', , "Hide")
}

ForceLogout(reason) {
    global Running, Looping, LicenseCode
    SetTimer(LicenseHeartbeat, 0)
    SetTimer(LoopTick, 0)
    Looping := false, Running := false, LicenseCode := ""
    try Send "{Alt up}"
    try Click "Up"
    try UI["gui"].Hide()
    ; A key that was turned off, deleted, or ran out removes this copy from the PC.
    ; A link/account/network hiccup only logs out, so nobody loses their copy by mistake.
    remove := InStr(reason, "DISABLED") || InStr(reason, "DELETED") || InStr(reason, "EXPIRED") || InStr(reason, "DEVICE")
    if (remove) {
        SelfDestruct()                ; copies start deleting right now; the running .exe goes the moment we close
        MsgTimeout("You've been logged out.`n`n" (reason != "" ? reason : "Your key no longer works.")
            . "`n`nThis copy of the macro is being removed."
            . "`n`nOpen a ticket in " DISCORD_INVITE " if you think this is a mistake.", "XZX Fishing", 5000)
        ExitApp
    }
    MsgBox("You've been logged out.`n`n" (reason != "" ? reason : "Your key no longer works.")
        . "`n`nOpen a ticket in " DISCORD_INVITE " if you think this is a mistake.", "XZX Fishing", "Iconx 0x40000")
    Reload()
}

;  A message box that closes itself after `ms` so removal isn't stuck waiting for a click.
MsgTimeout(text, title, ms) {
    try DllCall("user32\MessageBoxTimeoutW", "Ptr", 0, "WStr", text, "WStr", title, "UInt", 0x40040, "UShort", 0, "UInt", ms)
}

;  Deletes ONLY this macro's own files: the running .exe, its settings/session
;  file, the recorded delivery file, AND any copies of those exact files found in
;  this user's own folders (its folder, Desktop, Downloads, Documents). It matches
;  the exact file name, so nothing else on the PC is touched. A tiny batch deletes
;  the copies immediately, waits for this program to close to delete the running
;  .exe, then deletes itself.
SelfDestruct() {
    global INI
    targets := Map()                  ; lowercased path -> real path (dedupes the running exe vs. its folder copy)
    names := Map()                    ; exact file names to hunt copies of
    Add(p) {
        if (p != "" && FileExist(p))
            targets[StrLower(p)] := p
    }
    if (A_IsCompiled) {               ; only ever self-delete the built .exe, never the .ahk while testing
        Add(A_ScriptFullPath)
        SplitPath(A_ScriptFullPath, &n), names[n] := 1
    }
    Add(INI)
    try {
        delivery := IniRead(INI, "Macro", "DeliveryFile", "")
        if (delivery != "") {
            Add(delivery)
            SplitPath(delivery, &dn)
            if (dn != "")
                names[dn] := 1
        }
    }
    if (!targets.Count && !names.Count)
        return
    running := A_IsCompiled ? A_ScriptFullPath : ""
    drives := ""
    try drives := DriveGetList("FIXED")
    if (drives = "")
        drives := "C"
    bat := A_Temp "\xzx_fishing_cleanup.cmd"
    body := "@echo off`r`n"
    for _, f in targets                           ; delete the known files first (settings, delivery, running copy)
        if (f != running)
            body .= 'del /f /q "' f '" 2>nul`r`n'
    ; then sweep EVERY fixed drive for copies with the exact file name and delete them
    for nm, _ in names
        Loop Parse drives
            body .= 'for /f "delims=" %%F in (' "'" 'dir /s /b "' A_LoopField ':\' nm '" 2^>nul' "'" ') do del /f /q "%%F" 2>nul' "`r`n"
    if (running != "") {                          ; finally wait for this program to exit and delete the running .exe
        body .= ':w`r`ndel /f /q "' running '" 2>nul`r`n'
        body .= 'if exist "' running '" ( ping 127.0.0.1 -n 2 >nul & goto w )`r`n'
    }
    body .= '(goto) 2>nul & del "%~f0"`r`n'
    try FileDelete(bat)
    try FileAppend(body, bat)
    try Run('cmd.exe /c "' bat '"', , "Hide")
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
        if ((b >= 0x30 && b <= 0x39) || (b >= 0x41 && b <= 0x5A) || (b >= 0x61 && b <= 0x7A)
            || b = 0x2D || b = 0x2E || b = 0x5F || b = 0x7E)
            out .= Chr(b)
        else if (b = 0x20)
            out .= "+"
        else
            out .= "%" Format("{:02X}", b)
    }
    return out
}

; ---- built-in logo ------------------------------------------------------------
WriteLogo(b64, path) {
    ; turns the text below back into the logo picture (CRYPT_STRING_BASE64 = 0x01)
    size := 0
    if !DllCall("Crypt32\CryptStringToBinaryW", "WStr", b64, "UInt", 0, "UInt", 0x01, "Ptr", 0, "UInt*", &size, "Ptr", 0, "Ptr", 0) || !size
        return false
    buf := Buffer(size, 0)
    if !DllCall("Crypt32\CryptStringToBinaryW", "WStr", b64, "UInt", 0, "UInt", 0x01, "Ptr", buf, "UInt*", &size, "Ptr", 0, "Ptr", 0)
        return false
    try {
        f := FileOpen(path, "w")
        f.RawWrite(buf, size)
        f.Close()
        return true
    }
    return false
}

LogoData() {
    return "
(Join
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
)"
}

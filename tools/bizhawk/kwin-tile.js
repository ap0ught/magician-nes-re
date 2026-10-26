// The KWin half of the emulator window wall: enumerate and report, never place.
//
//   qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript \
//       tools/bizhawk/kwin-tile.js magicianTile
//   qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start
//
// Loaded and unloaded by tools/bizhawk/tile.sh. Nothing here runs on its own.
//
// WHY THE DEFAULT IS INVISIBLE, AND WHY THIS FILE EXISTS AT ALL
// --------------------------------------------------------------
// The maintainer asked for the emulator to open "on a dedicated window that is
// tiled ... so we can still see it but it does not clutter the desktop". The
// neighbouring project (aibeatszelda) does NOT tile: `src/zelda/emulator.py`
// captures frames through BizHawk's own screenshot API and never needs a visible
// window -- a visible one appears only when filming video.
//
// So the default here is the same, and deliberately so:
//
//   * A measurement run needs `client.screenshot()`, which reads the emulated
//     framebuffer and does not care whether a window exists. This is not an
//     assumption; tools/bizhawk/display.sh's header records that BizHawk runs
//     with NO window manager at all on the nested display and the work-RAM
//     fingerprint is byte-identical to the one taken with a decorated window on
//     :0. A window changes nothing any number in this project depends on.
//   * A window that is not needed, and is opened by default, lands on somebody's
//     desktop every single run. That is the clutter. On a machine shared with
//     other projects it is worse than clutter: it is a window that has to be
//     closed by hand.
//
// Hence the wall is OPT-IN. Set MAGICIAN_WALL=1 to have the play harness say
// that windows are visible and how to arrange them; leave it unset and every
// window EmuHawk opens stays off the desktop, because none is opened for you.
//
// WHAT KWIN 6.7.5 CAN AND CANNOT DO  (measured 2026-10-06, this machine)
// ---------------------------------------------------------------------
// The brief for this file asserted that per-`Client` there are `.move()`,
// `.resize()`, `.desktops` and `.noBorder`. Loaded through
// `qdbus6 org.kde.KWin /Scripting` on kwin 6.7.5 / Plasma 6 / Wayland and read
// out of the journal, the truth is:
//
//   * `Client.move` and `Client.resize` are BOOLEAN PROPERTIES, both false. Not
//     methods. No `doMove`, `doResize`, `setGeometry`, `interactiveMove` or
//     `setQuickTileMode` exists either.
//   * `Client.x/.y/.width/.height` are read-only numbers. Assigning throws
//     `Cannot assign to read-only property "x"`.
//   * `Client.frameGeometry = {x:40,y:40,width:640,height:480}` does NOT throw,
//     does NOT move the window, and then READS BACK AS THE VALUE JUST WRITTEN.
//     The X server, asked independently, still said x=840 y=425 w=320 h=240.
//     That is a perfect lie, and it is exactly the shape of failure this
//     project has been bitten by repeatedly.
//   * `Client.noBorder = true` behaves the same way: accepted, reported true,
//     `_NET_FRAME_EXTENTS` unchanged at "0, 0, 28, 0".
//
// The brief also gave the service as `org.kde.kwin.Scripting`; the real service
// is `org.kde.KWin` with the adaptor on the object path `/Scripting`, and
// `org.kde.kwin.Scripting` is not registered at all.
//
// So this script does the only two things KWin scripting is actually good for
// here, and neither of them is placement:
//
//   1. It enumerates the session's windows through `workspace.windowList()` and
//      reports the ones that belong to this project, with their geometry.
//   2. It reports the output geometry, so the usable area can be taken from the
//      compositor rather than guessed.
//
// Placement is done by tools/bizhawk/x11_tile.py over X11, which is measured to
// work, and which tools/side_by_side.py has always used. Keeping a second,
// independent enumeration is the point: X11 and KWin count windows through
// different machinery, so agreement between them is evidence, and disagreement
// is worth being told about.
//
// OUTPUT
// ------
// Lines to the kwin journal, prefixed `magician-tile-v1`, which tools/bizhawk/
// tile.sh reads back:
//
//     magician-tile-v1 screen name=HDMI-A-2 x=0 y=0 w=1920 h=1080
//     magician-tile-v1 window x=691 y=288 w=537 h=503 caption=...
//     magician-tile-v1 done matched=1 considered=6
//
// `considered` is every window KWin knows about and `matched` is how many passed
// the identity test, so a matching rule that silently matches nothing cannot be
// mistaken for "there were no windows".
//
// NO ARROWS, NO `const`, NO `let`, NO JSON
// --------------------------------------
// This runs in KWin's QJSEngine, which is not Node. `JSON` is not relied on and
// neither is template-literal syntax. `print()` is available and goes to
// `journalctl --user -u plasma-kwin_wayland`, which is how tile.sh reads this.
//
// IT CHANGES NOTHING
// ------------------
// No `move`, no `resize`, no `desktops`, no `noBorder`, no
// `setCurrentDesktop`, no configuration, no layout rule. If this file ever grows
// a call that alters the user's session, that is the bug -- the brief was
// explicit that the session must be left alone apart from loading and unloading
// this script.

var PREFIX = "magician-tile-v1";

// Must match `DEFAULT_MATCH` in tools/bizhawk/x11_tile.py. All of them, case
// insensitively, in WM_CLASS / WM_NAME / _NET_WM_NAME. "bizhawk" alone would
// match the other project's windows, and moving those is the incident
// tools/isolation.sh exists to prevent.
var MATCH = ["bizhawk", "magician"];

// The primary output on this machine, measured: `screens[0]` is HDMI-A-2 at
// 1920x1080. Chosen by name so a second monitor cannot silently become the
// wall's canvas; if the name is absent the first output is used and the name is
// reported, so the substitution is visible rather than silent.
var PREFERRED_OUTPUT = "HDMI-A-2";

function quote(s) {
    // A caption is free text from another process and may contain quotes,
    // newlines or NULs. Escaping keeps one window's caption from being able to
    // forge additional `magician-tile-v1` lines in the journal -- tile.sh parses
    // this output, so an unescaped caption is an injection point.
    var out = "";
    s = String(s);
    for (var i = 0; i < s.length; i++) {
        var ch = s.charAt(i);
        var code = s.charCodeAt(i);
        if (ch === "\\") { out += "\\\\"; }
        else if (ch === "\"") { out += "\\\""; }
        else if (ch === "\n") { out += "\\n"; }
        else if (ch === "\r") { out += "\\r"; }
        else if (ch === "\t") { out += "\\t"; }
        else if (code < 32 || code === 127) {
            out += "\\u" + ("0000" + code.toString(16)).slice(-4);
        } else { out += ch; }
    }
    return "\"" + out + "\"";
}

function geomText(c) {
    // `frameGeometry` is a KWin::RectF and stringifies as RectF(x, y, w, h).
    // It is a READ, and the reads have been consistent with X every time; it is
    // only the writes that are swallowed. Parsing it with a regex rather than
    // assuming numeric properties is deliberate: `c.geometry` is undefined on
    // this KWin, and an earlier probe that read `c.geometry` got `undefined` for
    // every window without raising.
    var g = String(c.frameGeometry);
    var m = g.match(/-?\d+(\.\d+)?/g);
    if (!m || m.length < 4) {
        return "x=? y=? w=? h=?";
    }
    return "x=" + Math.round(parseFloat(m[0]))
        + " y=" + Math.round(parseFloat(m[1]))
        + " w=" + Math.round(parseFloat(m[2]))
        + " h=" + Math.round(parseFloat(m[3]));
}

function identity(c) {
    var bits = [];
    try { if (c.resourceClass) bits.push(c.resourceClass); } catch (e) {}
    try { if (c.resourceName) bits.push(c.resourceName); } catch (e) {}
    try { if (c.caption) bits.push(c.caption); } catch (e) {}
    var joined = bits.join(" ").toLowerCase();
    for (var i = 0; i < MATCH.length; i++) {
        if (joined.indexOf(MATCH[i]) < 0) return false;
    }
    return true;
}

function reportScreen() {
    var chosen = null;
    var others = [];
    try {
        var screens = workspace.screens;
        for (var i = 0; i < screens.length; i++) {
            var s = screens[i];
            if (!s) continue;
            if (s.name === PREFERRED_OUTPUT) chosen = s;
            else others.push(s);
        }
        if (!chosen && others.length > 0) chosen = others[0];
    } catch (e) {
        print(PREFIX + " screen-error " + quote(String(e)));
    }
    if (!chosen) {
        try {
            chosen = workspace.activeScreen;
        } catch (e2) {
            print(PREFIX + " screen-name=unknown x=0 y=0 w=0 h=0");
            return;
        }
    }
    var g = String(chosen.geometry);
    var m = g.match(/-?\d+/g);
    var x = m ? m[0] : "0", y = m ? m[1] : "0";
    var w = m ? m[2] : "0", h = m ? m[3] : "0";
    print(PREFIX + " screen name=" + chosen.name + " x=" + x + " y=" + y
          + " w=" + w + " h=" + h
          + (chosen.name === PREFERRED_OUTPUT ? "" : " substituted-for=" + PREFERRED_OUTPUT));
}

function main() {
    reportScreen();
    var list;
    try {
        list = workspace.windowList();
    } catch (e) {
        print(PREFIX + " error windowList " + quote(String(e)));
        return;
    }
    var matched = 0;
    for (var i = 0; i < list.length; i++) {
        var c = list[i];
        var isSpecial = false;
        try { isSpecial = c.specialWindow; } catch (e) {}
        if (isSpecial) continue;                 // plasmashell's own panels/docks
        if (!identity(c)) continue;
        matched++;
        var caption = "";
        try { caption = c.caption || ""; } catch (e) {}
        print(PREFIX + " window " + geomText(c) + " caption=" + quote(caption));
    }
    print(PREFIX + " done matched=" + matched + " considered=" + list.length);
}

// MEASURED: `start()` IS NOT CALLED FOR A SCRIPT LOADED THIS WAY
// --------------------------------------------------------------
// The first version of this file defined `start()` and relied on
// `org.kde.kwin.Scripting.start()` to invoke it. It never ran: loading it,
// calling `start()`, and reading the journal produced no output at all, on
// kwin 6.7.5. Six earlier probe scripts had worked, and the difference was that
// each of them ended with a top-level call to its own entry point -- so the
// top-level call is what executes, and `Scripting.start()` did nothing
// observable.
//
// So `main()` is called at the bottom, as the probes did, AND `start()` is still
// defined and calls `main()`. Both paths are kept deliberately:
//
//   * the top-level call is what is measured to work;
//   * `start()` is what a future KWin, or a declarative script, would use, and
//     leaving it defined costs nothing.
//
// The consequence is that on a KWin that calls `start()` as well, the report is
// printed twice. `tile.sh` reads the LAST `magician-tile-v1 screen` line, so a
// duplicate is inert; and a duplicate is a far better failure than a report
// that never appears, which is what the first version did silently.
function start() {
    main();
}

main();

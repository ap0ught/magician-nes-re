-- Generic capture: dumps CHR RAM, CIRAM, PALRAM, OAM, screenshot at a target frame,
-- with optional button presses along the way.
--
-- Env:
--   MAGICIAN_CAPTURE_OUT   output dir
--   MAGICIAN_CAPTURE_FRAMES target frame count (default 60)
--   MAGICIAN_CAPTURE_PRESSES  comma-separated "frame:button" e.g. "30:Start,90:A"
--                            button pressed for 6 frames starting at frame
local OUT = os.getenv("MAGICIAN_CAPTURE_OUT") or "/tmp/opencode/capture"
local TARGET = tonumber(os.getenv("MAGICIAN_CAPTURE_FRAMES") or "60")
local PRESSES = os.getenv("MAGICIAN_CAPTURE_PRESSES") or ""

local f = io.open(OUT .. "/capture.txt", "w")
local function w(s) f:write(s .. "\n"); f:flush() end

-- BizHawk's joypad keys. Same names as src/play/bridge.lua. "START" is not one
-- of them; the key is "Start", and a scheduled press of the wrong case never
-- lands.
local CANON = {
  power="Power", up="Up", down="Down", left="Left", right="Right",
  a="A", b="B", select="Select", start="Start", eject="Eject",
  stretch="Stretch", l="L", r="R",
}
local function canon(name)
  return CANON[(name or ""):lower()]
end

-- parse presses
local presses = {}  -- frame -> {btn, dur}
for entry in PRESSES:gmatch("[^,]+") do
  local fr, btn, dur = entry:match("(%d+):(%w+):?(%d*)")
  if fr and btn then
    local key = canon(btn)
    if key then
      presses[tonumber(fr)] = { btn=key, dur= (dur=="" and 1 or tonumber(dur)) }
    else
      w("FAIL unknown button " .. btn)
    end
  end
end

local guard = 0
local active_btn = nil
local active_until = 0

while emu.framecount() < TARGET and guard < TARGET * 20 + 5000 do
  local fc = emu.framecount()
  if presses[fc] then
    active_btn = presses[fc].btn
    active_until = fc + presses[fc].dur
    w("press " .. active_btn .. " dur=" .. presses[fc].dur .. " at frame " .. fc)
  end
  if active_btn and fc < active_until then
    joypad.set({ [active_btn] = true }, 1)
  else
    if active_btn and fc >= active_until then active_btn = nil end
  end
  emu.frameadvance()
  guard = guard + 1
end
w(string.format("reached_frame %d steps %d", emu.framecount(), guard))

-- screenshot
pcall(function() client.screenshot(OUT .. "/frame.png") end)

-- dump domains
local function grab(name, start, len)
  local ok = pcall(memory.usememorydomain, name)
  if not ok then return nil end
  local got, s = pcall(memory.read_bytes_as_binary_string, start, len)
  if not (got and type(s)=="string" and #s==len) then return nil end
  return s
end
local function wr(name, s)
  if not s then return end
  local h = io.open(OUT .. "/" .. name, "wb"); h:write(s); h:close()
end

local ciram = grab("CIRAM (nametables)", 0, 4096)
wr("ciram.bin", ciram)
local nt = ciram and ciram:sub(1,1024) or nil
-- Nametable 0 is 960 tile bytes then 64 attribute bytes. Lua indexes are
-- 1-based, so the attributes are 961-1024. 1025-1088 is the next nametable.
local attr = ciram and ciram:sub(961,1024) or nil
wr("nt0.bin", nt)
wr("nt0attr.bin", attr)

local pal = grab("PALRAM", 0, 32)
wr("pal.bin", pal)

local chr = grab("CHR", 0, 0x10000)
wr("chrfull.bin", chr)

local oam = grab("OAM", 0, 256)
wr("oam.bin", oam)

-- log RAM state
pcall(memory.usememorydomain, "System Bus")
local function rb(a) return memory.readbyte(a) end
w(string.format("RAM phase=$%02X fadevec=$%02X second=$%02X curlev=$%02X", rb(0x005F), rb(0x0043), rb(0x0056), rb(0x0061)))
w("done")
f:close()
pcall(function() client.sleep(300) end)
pcall(function() client.exit() end)

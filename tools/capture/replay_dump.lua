-- Replay an input log file and dump CHR RAM / CIRAM / PALRAM / OAM at the end.
-- Env:
--   MAGICIAN_REPLAY_INPUT   path to inputs file
--   MAGICIAN_CAPTURE_OUT    output dir
local OUT = os.getenv("MAGICIAN_CAPTURE_OUT") or "/tmp/opencode/replay"
local INPUT = os.getenv("MAGICIAN_REPLAY_INPUT") or ""
local f = io.open(OUT .. "/replay.txt", "w")
local function w(s) f:write(s .. "\n"); f:flush() end

-- read input file
local lines = {}
local fh = io.open(INPUT, "r")
if not fh then w("FAIL no input file"); f:close(); return end
local nframes = 0
for line in fh:lines() do
  if line:match("^# frames=") then
    nframes = tonumber(line:match("frames=(%d+)"))
  elseif not line:match("^#") then
    lines[#lines+1] = line
  end
end
fh:close()
w("frames=" .. tostring(nframes) .. " input_lines=" .. #lines)

-- pad to nframes
while #lines < nframes do lines[#lines+1] = "-" end

-- BizHawk's joypad keys, same list as src/play/bridge.lua. save_inputs writes
-- simultaneous presses as one line, "Right,A", and "-" for nothing.
local CANON = {
  power="Power", up="Up", down="Down", left="Left", right="Right",
  a="A", b="B", select="Select", start="Start", eject="Eject",
  stretch="Stretch", l="L", r="R",
}
local function canon(name)
  return CANON[(name or ""):lower()]
end
local function buttons_from(line)
  local t = {}
  local any = false
  for raw in line:gmatch("[^,]+") do
    local name = canon((raw:match("^%s*(.-)%s*$")))
    if name then
      t[name] = true
      any = true
    else
      w("FAIL unknown button " .. raw)
    end
  end
  if any then return t end
  return nil
end

local guard = 0
local idx = 1
while emu.framecount() < nframes and guard < nframes*20 + 5000 do
  local line = lines[idx] or "-"
  if line ~= "-" and line ~= "" then
    local buttons = buttons_from(line)
    if buttons then joypad.set(buttons, 1) end
  end
  emu.frameadvance()
  idx = idx + 1
  guard = guard + 1
end
w(string.format("reached_frame %d", emu.framecount()))

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
wr("nt0.bin", ciram and ciram:sub(1,1024))
wr("nt0attr.bin", ciram and ciram:sub(961,1024))
wr("pal.bin", grab("PALRAM", 0, 32))
wr("chrfull.bin", grab("CHR", 0, 0x10000))
wr("oam.bin", grab("OAM", 0, 256))

-- RAM state
pcall(memory.usememorydomain, "System Bus")
local function rb(a) return memory.readbyte(a) end
w(string.format("RAM phase=$%02X fadevec=$%02X second=$%02X curlev=$%02X", rb(0x005F), rb(0x0043), rb(0x0056), rb(0x0061)))
w("done")
f:close()
pcall(function() client.sleep(300) end)
pcall(function() client.exit() end)

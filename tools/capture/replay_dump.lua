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

local guard = 0
local idx = 1
while emu.framecount() < nframes and guard < nframes*20 + 5000 do
  local btn = lines[idx] or "-"
  if btn ~= "-" and btn ~= "" then
    joypad.set({ [btn] = true }, 1)
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
wr("nt0attr.bin", ciram and ciram:sub(1025,1088))
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

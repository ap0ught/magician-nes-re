-- Run to an exact frame, screenshot the emulator's own video buffer, and record the
-- PPU/vram state around that frame.
--
--   MAGICIAN_LUA_OUT=<report> MAGICIAN_PNG=<path> MAGICIAN_FRAMES=<n> \
--     tools/bizhawk/run.sh tools/bizhawk/frame.lua <rom>
--
-- Why this is the control and not tools/nestrace.py: the pixels come out of
-- BizHawk's quickerNES PPU, so "does it show a picture" is answered by the same
-- emulator this project has always compared against, rather than by a second PPU
-- written to match this project's own assumptions.
--
-- emu.frameadvance() is exact -- emu.framecount() returned 10 after 10 calls --
-- so "the same moment" is reproducible without depending on wall clock.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/frame_report.txt"
local PNG = os.getenv("MAGICIAN_PNG") or "/tmp/opencode/frame.png"
local TARGET = tonumber(os.getenv("MAGICIAN_FRAMES") or "60")

local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local function hex(v)
  if type(v) ~= "number" then return "?" end
  return string.format("%02X", v % 256)
end

-- Report a failed access rather than aborting: a nil read here means a core
-- domain problem, and must not be mistaken for a fault in the ROM.
local function rd(a)
  local ok, v = pcall(memory.read_u8, a)
  if not ok or type(v) ~= "number" then return nil end
  return v
end

-- Poking registers is how VRAM gets read, and memory.read_u8 cannot do it: these
-- are writes to $2006/$2007. Getting this wrong yields a plausible-looking but
-- entirely fabricated nametable dump.
local function wr(a, v)
  local ok = pcall(memory.write_u8, a, v)
  return ok
end

w("target_frames " .. TARGET)

local guard = 0
while emu.framecount() < TARGET and guard < TARGET * 20 + 1000 do
  emu.frameadvance()
  guard = guard + 1
end
w("reached_frame " .. tostring(emu.framecount()) .. " steps " .. guard)

local function try(name, fn, ...)
  local ok, v = pcall(fn, ...)
  w(string.format("%s %s %s", name, tostring(ok), ok and tostring(v) or "-"))
end
try("screenwidth", function() return client.screenwidth end)
try("screenheight", function() return client.screenheight end)
try("bufferwidth", function() return client.bufferwidth end)
try("bufferheight", function() return client.bufferheight end)
try("gameinfo", function()
  local ks = {}
  for k in pairs(gameinfo) do ks[#ks + 1] = tostring(k) end
  table.sort(ks)
  return table.concat(ks, ",")
end)

-- Screenshot FIRST, before any register poking. Writing $2006 mid-frame moves the
-- PPU's VRAM address, and a probe that lands mid-scanline would contaminate the
-- very frame the run is supposed to document.
local shot_ok, shot_err = pcall(function() return client.screenshot(PNG) end)
w("screenshot_ok " .. tostring(shot_ok) .. " " .. tostring(shot_err))
local chk = io.open(PNG, "rb")
w("screenshot_exists " .. tostring(chk ~= nil))
if chk then chk:close() end

-- $2000-$2007 through both mirrors. PPUMASK decides whether anything can appear
-- at all, so it is recorded for both images.
local names = { "PPUCTRL", "PPUMASK", "PPUSTATUS", "OAMADDR", "PPUSCROLL", "PPUADDR", "PPUDATA", "PPUMASK7" }
for i = 0, 7 do
  w(string.format("reg_%s_2000=%s 4000=%s", names[i + 1],
                  hex(rd(0x2000 + i)), hex(rd(0x4000 + i))))
end

-- Reading through $2007 needs the address latched and the buffer drained: the
-- first read after a $2006 poke returns the stale byte, the second the real one.
local function vram_read(addr, count)
  local bytes, nz = {}, 0
  wr(0x2006, (addr >> 8) & 0xFF)
  wr(0x2006, addr & 0xFF)
  for _ = 1, count do
    rd(0x2007)                   -- discard stale
    local v = rd(0x2007)         -- real byte
    bytes[#bytes + 1] = v
    if v ~= 0 then nz = nz + 1 end
  end
  return bytes, nz
end

local pal, palnz = vram_read(0x3F00, 0x21)
w("palette " .. table.concat(pal, " "))
w("palette_nonzero " .. palnz)

local nt, nt_nz = vram_read(0x2000, 0x0400)
w(string.format("nametable_2000_nonzero %d of %d", nt_nz, #nt))
local _, nt2_nz = vram_read(0x2400, 0x0400)
w(string.format("nametable_2400_nonzero %d of %d", nt2_nz, 0x0400))
local _, chr_nz = vram_read(0x0000, 0x0800)
w(string.format("chr_0000_nonzero %d of %d", chr_nz, 0x0800))
local _, chr2_nz = vram_read(0x1000, 0x1000)
w(string.format("chr_1000_nonzero %d of %d", chr2_nz, 0x1000))

local function checksum(from, to, label)
  local s, n, nz = 0, 0, 0
  for a = from, to do
    local v = rd(a)
    if v then
      s = (s + v) % 65536
      n = n + 1
      if v ~= 0 then nz = nz + 1 end
    end
  end
  w(string.format("%s_sum %d bytes %d nonzero %d", label, s, n, nz))
end
checksum(0x0000, 0x07FF, "ram")
checksum(0x6000, 0x7FFF, "prgram")
checksum(0x0700, 0x07FF, "oam")

w("final_frame " .. tostring(emu.framecount()))
f:close()

-- Close ourselves: a process left behind holds the single-instance pipe and
-- diverts the next launch into the dead one, which is why past sessions saw
-- "no window".
pcall(function() client.sleep(500) end)
pcall(function() client.exit() end)

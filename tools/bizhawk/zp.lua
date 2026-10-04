-- Zero page and PPU registers at a set of frames, so "the stream was mis-addressed"
-- can be tested instead of argued.
--
-- The game's own software VRAM pointer is not a single zero-page cell -- it is
-- `t0/t1` for the address and `t2` for the increment in the code that writes it
-- -- so the whole of zero page $00-$FF is dumped alongside the PPU registers and
-- the caller diffs the two images cell by cell. A pointer that differs is the
-- answer; a pointer that agrees with a nametable that does not is not.
--
-- PPU registers are read from the "System Bus" domain, which is the only domain
-- that covers $2000-$2007. Note that reading $2002 clears the vblank flag and
-- resets the PPU address latch, so $2000/$2001/$2005 are read first and $2002
-- last: this probe perturbs the machine it is measuring and says so.
--
--   MAGICIAN_LUA_OUT=<report> MAGICIAN_FRAMES=<n> tools/bizhawk/run.sh zp.lua <rom>

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/zp.txt"
local MARKS = {}
for w in string.gmatch(os.getenv("MAGICIAN_MARKS") or "2,3,4,8,16,32,60,90", "[^,]+") do
  MARKS[#MARKS + 1] = tonumber(w)
end

local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

-- Only a select confirmed by getcurrentmemorydomain() is believed: an unknown
-- name does not fail, it leaves the previous domain in place.
local function select(d)
  if not pcall(memory.usememorydomain, d) then return false end
  local cur
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  return cur == d
end

if not select("System Bus") then
  w("FATAL no System Bus domain")
  f:close()
  pcall(function() client.exit() end)
  return
end

local function rd(a)
  local ok, v = pcall(memory.read_u8, a)
  if ok and type(v) == "number" then return v % 256 end
  return 255
end

local want = {}
for _, m in ipairs(MARKS) do want[m] = true end

local function snap(n)
  local regs = {}
  -- $2000,$2001,$2005x2 first: $2002 must be read last (it clears vblank/latch)
  for _, a in ipairs({ 0x2000, 0x2001, 0x2005, 0x2005 }) do
    regs[#regs + 1] = rd(a)
  end
  regs[#regs + 1] = rd(0x2002)
  w(string.format("frame %3d ppu %02X %02X %02X %02X %02X", n,
                  regs[1], regs[2], regs[3], regs[4], regs[5]))
  for base, name in ipairs({ {0x00, "zp00"}, {0x20, "zp20"}, {0x40, "zp40"},
                             {0x60, "zp60"}, {0x80, "zp80"} }) do
    local line = {}
    for i = 0, 31 do line[#line + 1] = string.format("%02X", rd(base + i)) end
    w(string.format("  %s $%02X-%02X %s", name, base, base + 31,
                    table.concat(line, " ")))
  end
end

w(string.format("marks %s", table.concat(MARKS, ",")))
snap(0)
local guard = 0
local maxf = MARKS[#MARKS]
while emu.framecount() < maxf and guard < maxf * 20 + 1000 do
  emu.frameadvance()
  guard = guard + 1
  if want[emu.framecount()] then snap(emu.framecount()) end
end
w("final_frame " .. tostring(emu.framecount()))
f:close()
pcall(function() client.exit() end)
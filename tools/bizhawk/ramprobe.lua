-- Which memory domain actually carries live NES RAM on this core?
--
--   MAGICIAN_RAMPROBE_OUT=<txt> MAGICIAN_MARKS=2,10,30,60,120 \
--     tools/bizhawk/run.sh tools/bizhawk/ramprobe.lua <rom>
--
-- Why this file exists
-- --------------------
-- `tools/bizhawk/replay.lua` samples work RAM to decide whether a replay is in
-- sync, and its first run against the release reported, for 50 consecutive
-- frames, `jt8 = FFFFFFFFFFFFFFFF` and a WRAM hash that never moved. Every one of
-- the 8192 bytes read was non-zero and every one of them was $FF.
--
-- That has two very different explanations -- the core is not executing, or the
-- domain is not a live mirror -- and they are separated by reading the same
-- addresses three ways in one run:
--
--   WRAM domain          memory.usememorydomain("WRAM"), offset 0..8191
--   System Bus $0000     memory.read_u8(0x0000 .. ), i.e. the CPU address space
--   CIRAM (nametables)   the nametable half of video RAM
--
-- plus the "CPU registers" domain's program counter, which answers the
-- "is the core executing" half directly: a PC that never leaves the reset vector
-- is not running, whatever the RAM says.
--
-- A domain whose contents never change across 120 frames of a running game is
-- not evidence about the game. It is evidence about the domain. Naming which is
-- which is what stops replay.lua reading a dead domain and reporting "the
-- cartridge never initialised RAM".
--
-- Self-coverage
-- -------------
-- Every read is counted and the totals are printed. A probe that silently read
-- nothing and printed plausible hashes would be the same failure as everything
-- else this project has had to undo.

local OUT = os.getenv("MAGICIAN_RAMPROBE_OUT") or "/tmp/opencode/ramprobe.txt"
local MARKS = {}
for t in string.gmatch(os.getenv("MAGICIAN_MARKS") or "1,2,4,10,30,60,120", "[^,]+") do
  local n = tonumber(t)
  if n then MARKS[#MARKS + 1] = n end
end
local maxf = MARKS[#MARKS] or 120

local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local reads = 0
local function note(s) reads = reads + 1; w(s) end

local function domlist()
  local out = {}
  local ok, l = pcall(memory.getmemorydomainslist)
  if not ok or type(l) ~= "table" then
    ok, l = pcall(memory.getmemorydomainlist)
  end
  if not ok or type(l) ~= "table" then return nil end
  for _, v in ipairs(l) do out[tostring(v)] = true end
  return out
end

local HAVE = domlist()
if HAVE == nil then
  w("FATAL could not read the memory domain list")
  f:close(); pcall(function() client.exit() end); return
end
local names = {}
for n in pairs(HAVE) do names[#names + 1] = n end
table.sort(names)
note("domains " .. table.concat(names, " | "))

local function sel(name)
  if not HAVE[name] then return nil, "absent" end
  if not pcall(memory.usememorydomain, name) then return nil, "did not select" end
  local cur
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  if cur ~= name then return nil, "select left " .. tostring(cur) end
  return true
end

-- A cheap content summary: 32-bit polynomial hash, plus how many of the bytes
-- were $FF and how many were zero. The two counts are the whole point -- a buffer
-- that is 100% $FF is an uninitialised buffer, and a buffer that is 100% zero is
-- a *cleared* one, and those mean opposite things.
local function summarise(s)
  local h, ff, z, nz = 1, 0, 0, 0
  for i = 1, #s do
    local b = s:byte(i)
    h = (h * 33 + b) % 0x7FFFFFFF
    if b == 0xFF then ff = ff + 1 elseif b == 0 then z = z + 1 else nz = nz + 1 end
  end
  return string.format("hash %08X  ff %d  zero %d  other %d  len %d",
                       h, ff, z, nz, #s)
end

local function snap(n)
  -- WRAM domain
  if sel("WRAM") then
    local ok, s = pcall(memory.read_bytes_as_binary_string, 0, 8192)
    if ok and type(s) == "string" then
      local zpf = {}
      for i = 0, 7 do zpf[#zpf + 1] = string.format("%02X", s:byte(0x2E + i + 1) or 0) end
      note(string.format("f%-5d WRAM-domain  %s  jt8 %s", n, summarise(s),
                         table.concat(zpf)))
    else
      note(string.format("f%-5d WRAM-domain  read failed: %s", n, tostring(s)))
    end
  else
    note(string.format("f%-5d WRAM-domain  unavailable", n))
  end

  -- System Bus, two kilobytes of the CPU address space at $0000.
  if sel("System Bus") then
    local ok, s = pcall(memory.read_bytes_as_binary_string, 0x0000, 2048)
    if ok and type(s) == "string" then
      local zpf = {}
      for i = 0, 7 do zpf[#zpf + 1] = string.format("%02X", s:byte(0x2E + i + 1) or 0) end
      note(string.format("f%-5d Bus-$0000+2K %s  jt8 %s", n, summarise(s),
                         table.concat(zpf)))
    else
      note(string.format("f%-5d Bus-$0000+2K read failed: %s", n, tostring(s)))
    end
    -- And the PPU, which is on the bus and is known to work: if $2002's vblank bit
    -- and the frame counter move, the core is definitely running.
    local p = {}
    for _, a in ipairs({ 0x2000, 0x2001, 0x2002, 0x2003, 0x2005, 0x2006 }) do
      local okr, v = pcall(memory.read_u8, a)
      p[#p + 1] = okr and string.format("%02X", (tonumber(v) or 0) % 256) or "??"
    end
    note(string.format("f%-5d ppu $2000..$2006 %s", n, table.concat(p, " ")))
  else
    note(string.format("f%-5d System Bus unavailable", n))
  end

  -- CPU registers: dumped as RAW BYTES, not named.
  --
  -- The first version read offset 0 as a little-endian PC and offsets 1..5 as
  -- A/X/Y/SP/P. That produced "PC 08EB A 08" -- the value it printed as PC was
  -- made of the same two bytes it printed as A and X, and the layout was simply
  -- wrong. Naming registers this file cannot establish is how a probe reports a
  -- program counter that does not exist, so it prints the bytes and says which
  -- they are not.
  if sel("CPU registers") then
    local b = {}
    local got = 0
    for i = 0, 15 do
      local okr, v = pcall(memory.read_u8, i)
      if okr and type(v) == "number" then
        b[#b + 1] = string.format("%02X", v % 256); got = got + 1
      else
        b[#b + 1] = "??"
      end
    end
    note(string.format("f%-5d cpu-domain[0..15] %s (%d bytes read; the layout of this "
                       .. "domain is NOT established, so no register is named)",
                       n, table.concat(b, " "), got))
  else
    note(string.format("f%-5d CPU registers unavailable", n))
  end

  -- CIRAM: the nametables, already known to read correctly in this project.
  if sel("CIRAM (nametables)") then
    local ok, s = pcall(memory.read_bytes_as_binary_string, 0, 2048)
    if ok and type(s) == "string" then
      note(string.format("f%-5d CIRAM-2K      %s", n, summarise(s)))
    end
  end
  note("")
end

local want = {}
for _, n in ipairs(MARKS) do want[n] = true end

note(string.format("marks %s", table.concat(MARKS, ",")))
snap(0)
local guard = 0
while emu.framecount() < maxf and guard < maxf * 20 + 1000 do
  emu.frameadvance()
  guard = guard + 1
  local c = emu.framecount()
  if want[c] then snap(c) end
end
note("final_frame " .. tostring(emu.framecount()))
note(string.format("READS %d lines emitted", reads))
if reads < 20 then
  note("FATAL fewer than 20 read lines: this probe measured almost nothing and any "
       .. "conclusion drawn from it would be about nothing.")
end
f:close()
pcall(function() client.exit() end)
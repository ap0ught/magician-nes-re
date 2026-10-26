-- Dump BizHawk's memory domains to disk so the two ROMs can be diffed on VRAM,
-- CHR-RAM, OAM and the palette rather than on a picture that has to be eyeballed.
--
--   MAGICIAN_LUA_OUT=<dir> MAGICIAN_FRAMES=<n> tools/bizhawk/run.sh dump.lua <rom>
--
-- Domain names are case-sensitive and an unknown name is NOT an error: it silently
-- falls back to the default domain, so a wrong name produces a file full of $F2
-- and nothing complains. The list is therefore written out and the domain chosen
-- by size, then the choice is verified by reading a byte that must differ between
-- two regions before anything is dumped.

local DIR = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/biz/dump"
local TARGET = tonumber(os.getenv("MAGICIAN_FRAMES") or "60")
os.execute("mkdir -p " .. DIR)

local txt = io.open(DIR .. "/domains.txt", "w")
local function w(s) txt:write(s .. "\n"); txt:flush() end

local guard = 0
while emu.framecount() < TARGET and guard < TARGET * 20 + 1000 do
  emu.frameadvance()
  guard = guard + 1
end
w("frame " .. tostring(emu.framecount()))

-- Every domain, with its size. The NES core exposes separate CPU RAM, PRG-RAM,
-- CHR-RAM (CHR=0 in the header means CHR-RAM, not "no graphics"), OAM and VRAM.
local list = memory.getmemorydomainlist() or {}
for i, d in ipairs(list) do
  local name, size = "?", "?"
  pcall(function() name = d.Name end)
  pcall(function() size = d.Size end)
  w(string.format("%2d %-24s %s", i, tostring(name), tostring(size)))
end

-- Verify each candidate domain actually selects, by reading one byte from a region
-- that must differ between them.
w("--- $0100 by domain ---")
local names = {}
for i, d in ipairs(list) do
  local name, size = "?", 0
  pcall(function() name = d.Name end)
  pcall(function() size = d.Size end)
  names[i] = tostring(name)
  local ok = pcall(memory.usememorydomain, name)
  local v = -1
  pcall(function() v = memory.read_u8(0x0100) end)
  w(string.format("  %-24s size=%-8s select=%s byte0100=%02X", name, tostring(size),
                  tostring(ok), v % 256))
end

-- $2001 through mainmemory, independent of whichever domain is current.
local okm, v2001 = pcall(function() return mainmemory.read_u8(0x2001) end)
local okm2, v2002 = pcall(function() return mainmemory.read_u8(0x2002) end)
w(string.format("mainmemory $2001=%s $2002=%s", okm and string.format("%02X", v2001 % 256) or "-",
                okm2 and string.format("%02X", v2002 % 256) or "-"))

-- NES-specific display state, which is what the old "PPUMASK=$FE" claim was
-- really reaching for and is measurable here without guessing.
for _, m in ipairs({ "getdispbackground", "getdispsprites", "getallowmorethaneightsprites",
                     "getclipleftandright", "gettopscanline", "getbottomscanline" }) do
  local ok, v = pcall(function() return nes[m]() end)
  w(string.format("nes.%-28s %s %s", m, tostring(ok), ok and tostring(v) or "-"))
end

-- Bulk binary dumps. read_bytes_as_binary_string avoids 16 000 Lua numbers per
-- region; anything that fails falls back to readbyterange so a partial dump is
-- never silently short.
local function dump(domain, start, length, name)
  local path = DIR .. "/" .. name
  local sel = pcall(memory.usememorydomain, domain)
  local fh = io.open(path, "wb")
  local wrote = 0
  local ok, s = pcall(memory.read_bytes_as_binary_string, start, length)
  if ok and type(s) == "string" and #s == length then
    fh:write(s)
    wrote = #s
  else
    local okr, arr = pcall(memory.readbyterange, start, length)
    if okr and type(arr) == "table" then
      for i = 1, #arr do fh:write(string.char(arr[i] % 256)) end
      wrote = #arr
    end
  end
  fh:close()
  w(string.format("dump %-10s domain=%-12s select=%s %04X+%d -> %d bytes", name,
                  tostring(domain), tostring(sel), start, length, wrote))
end

dump("VRAM", 0x0000, 0x4020, "vram.bin")
dump("OAM", 0x0000, 0x0100, "oam.bin")
dump("RAM", 0x0000, 0x0800, "ram.bin")
dump("WRAM", 0x0000, 0x2000, "work.bin")
dump("CHR", 0x0000, 0x2000, "chr.bin")

-- Palettes are inside VRAM at $3F00, but pulled out separately so a palette diff
-- does not have to be carved out of the nametable diff.
dump("VRAM", 0x3F00, 0x0020, "pal.bin")

txt:close()
pcall(function() client.exit() end)

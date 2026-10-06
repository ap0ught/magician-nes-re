-- Find the correct memory domain for PPU/VRAM reads and report the domain names.
-- The first attempt read $2000-$2007 with a one-argument memory.read_u8 and got
-- $F2 for all eight registers, which is the PPU's pattern-table area being read
-- through the wrong window -- a plausible-looking but fabricated dump.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/dom.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local function keys(name, t)
  local ks = {}
  if type(t) == "table" then
    for k, v in pairs(t) do ks[#ks + 1] = string.format("%s(%s)", tostring(k), type(v)) end
  end
  table.sort(ks)
  w(name .. ": " .. table.concat(ks, " "))
end

keys("memory", memory)
keys("mainmemory", mainmemory)
keys("nes", nes)

-- Candidate domain names for the NES core.
local DOMAINS = { "System Bus", "cpu", "cpumem", "CPU", "ppu", "vram", "VRAM",
                  "oam", "OAM", "prg", "PRG", "chr", "CHR", "ram", "RAM",
                  "wram", "WRAM", "pal", "palette", "debug" }

w("--- $2002 (PPUSTATUS) read through each candidate domain ---")
for _, d in ipairs(DOMAINS) do
  local ok, v = pcall(memory.read_u8, 0x2002, d)
  w(string.format("  %-12s ok=%s val=%s", d, tostring(ok), ok and string.format("%02X", v % 256) or "-"))
end

w("--- $0002 read through each candidate domain ---")
for _, d in ipairs(DOMAINS) do
  local ok, v = pcall(memory.read_u8, 0x0002, d)
  w(string.format("  %-12s ok=%s val=%s", d, tostring(ok), ok and string.format("%02X", v % 256) or "-"))
end

-- mainmemory methods, if it is a domain object with read methods.
w("--- mainmemory method probes ---")
for _, m in ipairs({ "read_u8", "read_u8_byte", "readbyte", "read", "read_u8_le",
                     "get_current_frame", "SaveArray", "ReadByte", "read_u16" }) do
  local ok, v = pcall(function() return mainmemory[m] end)
  w(string.format("  mainmemory.%-18s %s %s", m, tostring(ok), ok and type(v) or tostring(v)))
end

w("--- nes library probes ---")
for _, m in ipairs({ "getppuregister", "getppu", "cpu", "apu", "getram", "chr",
                     "spritecount", "getpalette" }) do
  local ok, v = pcall(function() return nes[m] end)
  w(string.format("  nes.%-18s %s %s", m, tostring(ok), ok and type(v) or tostring(v)))
end

f:close()
pcall(function() client.exit() end)

-- Census of BizHawk's NES memory domains, done by *selecting* each one and asking
-- the core how big it is, not by reading a byte.
--
-- The earlier dump.lua reported "vram.bin 0000+16416 -> 16416 bytes" and I took
-- that as proof the VRAM domain covered $0000-$401F. It is not proof: the writer
-- falls back to readbyterange and pads, so a domain that is only $2000 long still
-- produces a 16416-byte file whose tail is a constant. Both images' "$2000-$3FFF"
-- came back as 1024 bytes of $F2, which is what a padded read looks like, and that
-- is what made "all four nametables are byte-identical" briefly look true.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/census.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

emu.frameadvance()

w("--- getmemorydomainlist() element shape ---")
local list = memory.getmemorydomainlist() or {}
w("type " .. type(list) .. " #" .. tostring(#list))
local first = list[1]
if type(first) == "table" then
  local ks = {}
  for k, v in pairs(first) do ks[#ks + 1] = string.format("%s(%s)=%s", tostring(k), type(v), tostring(v)) end
  table.sort(ks)
  w("pairs: " .. table.concat(ks, "  "))
else
  w("element type " .. type(first) .. " -> " .. tostring(first))
  -- It may already be a name string, or convertible to one.
  local ok, nm = pcall(tostring, first)
  w("tostring: " .. tostring(ok) .. " " .. tostring(nm))
end

w("--- select each candidate name, then ask the core ---")
local CANDIDATES = { "System Bus", "cpu", "CPU", "ppu", "PPU", "vram", "VRAM",
                      "oam", "OAM", "pal", "PAL", "palette", "prg", "PRG",
                      "chr", "CHR", "ram", "RAM", "wram", "WRAM", "rom", "ROM",
                      "work", "WRAM ", "sprite", "SPR" }
local seen = {}
for _, name in ipairs(CANDIDATES) do
  local ok = pcall(memory.usememorydomain, name)
  if ok then
    local nm, sz = "?", "?"
    pcall(function() nm = memory.getcurrentmemorydomain() end)
    pcall(function() sz = memory.getmemorydomainsize() end)
    if nm ~= "?" then
      local fp = {}
      for _, a in ipairs({ 0x0000, 0x0001, 0x0100, 0x1000, 0x2000, 0x2001, 0x2002, 0x3F00, 0x7FFF }) do
        local v = -1
        pcall(function() v = memory.read_u8(a) end)
        fp[#fp + 1] = string.format("%04X=%02X", a, v % 256)
      end
      w(string.format("%-12s -> name=%-12s size=%-8s %s", name, tostring(nm), tostring(sz),
                      table.concat(fp, " ")))
      seen[tostring(nm)] = true
    end
  end
end
w("--- distinct domain names that actually exist ---")
local ks = {}
for k in pairs(seen) do ks[#ks + 1] = k end
table.sort(ks)
w(table.concat(ks, " | "))

w("--- event.onframeend fires? ---")
local n = 0
local okh = pcall(event.onframeend, function() n = n + 1 end)
w("install " .. tostring(okh))
for _ = 1, 5 do emu.frameadvance() end
w("onframeend fired " .. n .. " times over 5 frames")

w("--- emu.getregister at frame end ---")
for _, r in ipairs({ "PC", "A", "X", "Y", "SP", "P" }) do
  local ok, v = pcall(emu.getregister, r)
  w(string.format("  %s %s", r, ok and string.format("%02X", v % 256) or "-"))
end
local okd, d = pcall(emu.disassemble, 0x9D6C)
if okd and type(d) == "table" then
  local parts = {}
  for k, v in pairs(d) do parts[#parts + 1] = tostring(k) .. "=" .. tostring(v) end
  table.sort(parts)
  w("disassemble $9D6C: " .. table.concat(parts, " "))
end

w("done")
f:close()
pcall(function() client.exit() end)

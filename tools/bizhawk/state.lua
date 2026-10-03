-- Dump the NES's real memory domains, using the names the core itself reports,
-- and disassemble the few addresses the tracer makes claims about.
--
-- Corrects two things measured earlier in this session:
--
--   * getmemorydomainlist() returns plain name STRINGS, not objects. The earlier
--     census read `.Name`/`.Size` off them, got nil for all nine, and so had no
--     domain list at all.
--   * usememorydomain with an unknown name does NOT fall back to the default
--     domain -- it leaves the previous selection in place. "pal" and "prg" and
--     "chr" all reported "OAM" because OAM had been selected three lines earlier.
--     Anything that reads a domain without checking getcurrentmemorydomain() is
--     reading whatever was selected last.

local DIR = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/biz/state"
local TARGET = tonumber(os.getenv("MAGICIAN_FRAMES") or "60")
os.execute("mkdir -p " .. DIR)

local txt = io.open(DIR .. "/state.txt", "w")
local function w(s) txt:write(s .. "\n"); txt:flush() end

local guard = 0
while emu.framecount() < TARGET and guard < TARGET * 20 + 1000 do
  emu.frameadvance()
  guard = guard + 1
end
w("frame " .. tostring(emu.framecount()))

-- The real list.
local list = memory.getmemorydomainlist() or {}
w("--- domains (" .. tostring(#list) .. ") ---")
for i, v in ipairs(list) do
  pcall(memory.usememorydomain, v)
  local sz = "?"
  pcall(function() sz = memory.getmemorydomainsize() end)
  local cur = "?"
  pcall(function() cur = memory.getcurrentmemorydomain() end)
  w(string.format("  %d %-14s selected=%-14s size=%s", i, tostring(v), tostring(cur), tostring(sz)))
end

local function safe(tag)
  return (string.gsub(tag, "[^A-Za-z0-9]", "_"))
end

local function dump(domain, start, length, name)
  -- Verify the selection took, or refuse: writing a file full of the previously
  -- selected domain under this domain's name is the failure this script exists
  -- to prevent.
  local ok = pcall(memory.usememorydomain, domain)
  local cur, size = "?", -1
  pcall(function() cur = memory.getcurrentmemorydomain() end)
  pcall(function() size = memory.getmemorydomainsize() end)
  if cur ~= domain then
    w(string.format("SKIP %-10s wanted %-12s but selected %-12s", name, domain, tostring(cur)))
    return
  end
  local n = length
  if start + n > size then
    w(string.format("CLIP %-10s $%04X+%d exceeds size %d", name, start, length, size))
    n = size - start
  end
  local fh = io.open(DIR .. "/" .. name, "wb")
  local got, s = pcall(memory.read_bytes_as_binary_string, start, n)
  if got and type(s) == "string" and #s == n then
    fh:write(s)
  else
    local okr, arr = pcall(memory.readbyterange, start, n)
    if okr and type(arr) == "table" then
      for i = 1, #arr do fh:write(string.char(arr[i] % 256)) end
    end
  end
  fh:close()
  w(string.format("dump  %-10s domain=%-12s $%04X+%d size=%d", name, cur, start, n, size))
end

for _, d in ipairs(list) do
  pcall(memory.usememorydomain, d)
  local size = -1
  pcall(function() size = memory.getmemorydomainsize() end)
  if d == "OAM" then dump(d, 0x0000, 0x0100, "oam.bin")
  elseif d == "RAM" then dump(d, 0x0000, 0x0800, "ram.bin")
  elseif d == "WRAM" then dump(d, 0x0000, 0x2000, "work.bin")
  elseif size == 131072 then
    -- CHR-RAM sits behind the 128 KiB CPU bus in quickerNES. Both pattern tables
    -- are wanted, and so are the nametables, which live on the bus view.
    -- Unique per domain: the earlier version reused chr.bin/nt.bin/pal.bin for
    -- every domain over size 131072, so CHR, PRG ROM and CHR VROM each overwrote
    -- the last and only the final one survived on disk.
    local t = safe(d)
    dump(d, 0x0000, 0x2000, "chr_" .. t .. ".bin")
    dump(d, 0x2000, 0x1000, "nt_" .. t .. ".bin")
    dump(d, 0x3F00, 0x0020, "pal_" .. t .. ".bin")
  else
    dump(d, 0x0000, size, "dom_" .. safe(d) .. ".bin")
  end
end

-- The addresses journal/03 makes claims about.
w("--- disassembly ---")
for _, a in ipairs({ 0x9D60, 0x9D6C, 0xF9AB, 0xF9B1, 0xF9B3, 0xF9C1, 0xFFFA, 0xFFFC, 0xFFFE }) do
  local ok, d = pcall(emu.disassemble, a)
  local parts = {}
  if ok and type(d) == "table" then
    for k, v in pairs(d) do parts[#parts + 1] = tostring(k) .. "=" .. tostring(v) end
    table.sort(parts)
  end
  w(string.format("  $%04X %s %s", a, tostring(ok), table.concat(parts, " ")))
end

w("--- registers at frame end ---")
for _, r in ipairs({ "PC", "A", "X", "Y", "SP", "P", "C", "Z", "I" }) do
  local ok, v = pcall(emu.getregister, r)
  w(string.format("  %-3s %s", r, ok and string.format("%02X", v % 256) or "-"))
end

txt:close()
pcall(function() client.exit() end)

-- Per-frame memory snapshot for the divergence bisect.
--
--   MAGICIAN_LUA_OUT=<dir> MAGICIAN_FRAMES=<n> tools/bizhawk/run.sh frames.lua <rom>
--
-- The question this answers is "at which frame does the rebuild first stop agreeing
-- with the cartridge", and that can only be answered by looking at every frame from
-- the start, not by comparing at some frame late enough to be already broken.
--
-- Four facts about this core shape the code below, all measured on this machine:
--
--   * `memory.getmemorydomainlist()` returns plain STRINGS. Reading `.Name`/`.Size`
--     off them yields nil for all nine domains, which is what made an earlier dump
--     script believe the core exposed no domains.
--   * `memory.usememorydomain` with an unknown name is NOT an error and does NOT fall
--     back to the default domain -- it leaves the previous selection in place. So a
--     read that names a domain which does not exist silently returns the previously
--     selected domain's bytes, under the new domain's filename.
--   * the selection PERSISTS across calls, so selecting in a setup loop and then
--     reading in a later loop reads whatever was selected *last*. The first version of
--     this script did exactly that: it resolved six regions up front and then read
--     every region with PALRAM still current, so all six per-frame files held the
--     palette. Every read below re-selects and re-verifies, and logs the domain it
--     actually got.
--   * the CHR domain is 131072 bytes, not 8192. Dumping "the first 8 KiB of CHR" is
--     only meaningful if the mapper's CHR bank registers point inside it; a bank
--     register with the 8 KiB/16 KiB select bit set would put the data past $1FFF and
--     a 0-8192 diff would call that "identical". So the whole 128 KiB is dumped.
--
-- Nothing here reads or writes the cartridge. Domain reads are the core's own state.

local DIR = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/biz/frames"
local LAST = tonumber(os.getenv("MAGICIAN_FRAMES") or "90")
os.execute("mkdir -p " .. DIR)

local log = io.open(DIR .. "/frames.txt", "w")
local function w(s) log:write(s .. "\n"); log:flush() end

-- Select a domain and return the size only if the selection actually took.
local function sel(name)
  pcall(memory.usememorydomain, name)
  local cur, size = "?", -1
  pcall(function() cur = memory.getcurrentmemorydomain() end)
  pcall(function() size = memory.getmemorydomainsize() end)
  return cur == name, size
end

-- Select and read in one step, so a read can never use a stale selection.
local function grab(name, start, len)
  local ok, size = sel(name)
  if not ok then return nil, "domain " .. name .. " did not select" end
  if start + len > size then return nil, string.format("%s: $%X+%d > size %d", name, start, len, size) end
  local got, s = pcall(memory.read_bytes_as_binary_string, start, len)
  if got and type(s) == "string" and #s == len then return s end
  return nil, name .. ": read_bytes_as_binary_string failed"
end

local WANTS = {
  { key = "chr",   name = "CHR",    len = 131072 },
  { key = "ciram", name = "CIRAM (nametables)", len = 4096 },
  { key = "wram",  name = "WRAM",   len = 8192 },
  { key = "ram",   name = "RAM",    len = 2048 },
  { key = "oam",   name = "OAM",    len = 256 },
  { key = "pal",   name = "PALRAM", len = 32 },
}

w(string.format("dir %s, last frame %d", DIR, LAST))
w(string.format("starting emu.framecount() = %d", emu.framecount()))

-- Every domain, with the size the core reports once it is selected. Two of them are
-- 131072 bytes (CHR, CHR VROM, PRG ROM); which is which is decided by name here and
-- sanity-checked by the fact that the first dump of each is non-empty.
w("--- domains ---")
local list = memory.getmemorydomainlist() or {}
for i, v in ipairs(list) do
  local _, size = sel(v)
  w(string.format("  %d %-24s size=%d", i, tostring(v), size))
end

-- Probe each wanted region once, before the frame loop, so a domain that does not
-- exist is reported as a failure instead of silently producing a file of $F2.
w("--- probe ---")
for _, want in ipairs(WANTS) do
  local s, err = grab(want.name, 0, want.len)
  if s then
    local nz = 0
    for b in s:gmatch(".") do if b ~= "\0" then nz = nz + 1 end end
    w(string.format("  %-6s ok  %-22s %6d bytes, %d non-zero", want.key, want.name, #s, nz))
  else
    w(string.format("  %-6s FAIL %-22s %s", want.key, want.name, tostring(err)))
  end
end

-- A cheap content digest per frame, so the log alone shows the shape of the
-- divergence; the .bin files are only opened when the digests disagree.
local function digest(s)
  if s == nil then return "unreadab" end
  local b, i = 5381, 1
  local n = #s
  while i <= n do
    b = (b * 33 + s:byte(i)) % 4294967296
    i = i + 1
  end
  return string.format("%08x%04x", b, n)
end

w("--- per frame: frame | " .. table.concat((function()
  local t = {}
  for _, x in ipairs(WANTS) do t[#t + 1] = x.key end
  return t
end)(), " | ") .. " ---")

local base = emu.framecount()
local frame = base
while true do
  local parts = {}
  for _, want in ipairs(WANTS) do
    local data = grab(want.name, 0, want.len)
    if data then
      local f = io.open(string.format("%s/%s_%04d.bin", DIR, want.key, frame), "wb")
      f:write(data)
      f:close()
    end
    parts[#parts + 1] = digest(data)
  end
  w(string.format("%5d | %s", frame, table.concat(parts, " | ")))
  if frame - base >= LAST then break end
  emu.frameadvance()
  frame = emu.framecount()
end

w(string.format("done, frames %d..%d", base, frame))
log:close()
pcall(function() client.exit() end)
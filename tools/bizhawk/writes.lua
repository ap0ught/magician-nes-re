-- Trace the CPU's writes to the PPU and the mapper.
--
--   MAGICIAN_LUA_OUT=<file> MAGICIAN_FRAMES=<n> tools/bizhawk/run.sh writes.lua <rom>
--
-- Why this exists: the rebuild and the cartridge agree on RAM until frame 1 and on the
-- nametable until frame 1, and by frame 3 the nametable is 969 bytes different. The
-- only way to tell "wrote the wrong bytes" from "wrote the right bytes to the wrong
-- address" is to see the $2006/$2007 stream itself, and per-frame snapshots cannot
-- distinguish those.
--
-- The callback convention is not documented for 2.11.1 and the notes record that
-- `event.onmemorywrite(cb)` "installs but does not fire" when cb is a bare function.
-- So every plausible shape is registered at once and the log says which one fired:
--
--   * `function(cb)` with the object read as cb.Address / cb.Value / cb.DomainName
--   * `function(cb)` with cb.Address(0) called as a method
--   * a table with read/write methods, passed as the "callback object"
--
-- A count of zero is reported as zero, not as success: an earlier probe used pcall,
-- which returns true when the function merely ran, and reported a clean bill of
-- health for names that do not exist.
--
-- SETTLED 2026-10-04 -- do not add shapes here. `buswrite.lua` read the shipped
-- API definition (`Lua/_docs_luacats/event.d.lua`), which documents
-- `onmemorywrite(luaf, address, name?, scope?)`, and registered eight forms
-- including that one and `on_bus_write` with an explicit address and scope. All
-- eight returned BizHawk's `EMPTY_UUID_STR` (`00000000-0000-0000-0000-000000000000`,
-- its sentinel for "not registered") and none fired, including for a write the
-- script made itself. `event.availableScopes()` returns `{}`. The cause is in
-- BizHawk's own IL: `QuickNES.get_MemoryCallbacks()` throws
-- `NotImplementedException` unconditionally, so there is no bounded path either,
-- and the word "wildcard " in the log message is interpolated only when no
-- address was supplied. There is no memory callback of any shape on this core.
-- The bank timeline comes from per-frame System Bus polling -- see
-- `bankprobe.lua` -- or from `tools/nestrace.py`.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/writes.txt"
local LAST = tonumber(os.getenv("MAGICIAN_FRAMES") or "3")
local MAXREC = tonumber(os.getenv("MAGICIAN_MAXREC") or "20000")

local log = io.open(OUT, "w")
local function w(s) log:write(s .. "\n"); log:flush() end

local function addr_of(cb)
  -- Every shape a memory-callback parameter can plausibly take, read defensively.
  local a
  pcall(function() a = cb.Address end)
  if type(a) == "number" then return a end
  pcall(function() a = cb.address end)
  if type(a) == "number" then return a end
  pcall(function() a = cb:Address() end)
  if type(a) == "number" then return a end
  return nil
end

local function val_of(cb, a)
  local v
  pcall(function() v = cb.Value end)
  if type(v) == "number" then return v end
  pcall(function() v = cb.value end)
  if type(v) == "number" then return v end
  if type(a) == "number" then
    pcall(function() v = cb:ReadByte(a) end)
    if type(v) == "number" then return v end
  end
  return nil
end

local counts = { ppu = 0, mapper = 0, other = 0, total = 0, unparsed = 0 }
local recorded = 0
local shape_seen = nil
local frame = 0

local function record(tag, a, v)
  if tag ~= "ppu" and tag ~= "mapper" then return end
  if recorded >= MAXREC then return end
  recorded = recorded + 1
  log:write(string.format("f%d %s %04X %02X\n", frame, tag, a, v))
end

local function classify(a)
  if a >= 0x2000 and a <= 0x2007 then return "ppu" end
  if a >= 0x8000 and a <= 0x9FFF then return "mapper" end
  return "other"
end

-- Shape 1: a bare function whose single parameter is the callback object.
local function onwrite(cb)
  local a = addr_of(cb)
  counts.total = counts.total + 1
  if a == nil then counts.unparsed = counts.unparsed + 1; return end
  local v = val_of(cb, a) or 0
  local tag = classify(a)
  if tag == "ppu" then counts.ppu = counts.ppu + 1
  elseif tag == "mapper" then counts.mapper = counts.mapper + 1
  else counts.other = counts.other + 1 end
  if shape_seen == nil then
    shape_seen = {}
    local ks = {}
    pcall(function() for k, _ in pairs(cb) do ks[#ks + 1] = tostring(k) end end)
    table.sort(ks)
    shape_seen.keys = table.concat(ks, ",")
    shape_seen.addr = a
    shape_seen.val = v
  end
  record(tag, a, v)
end

local ok, err = pcall(function() return event.onmemorywrite(onwrite) end)
w("register onmemorywrite ok=" .. tostring(ok) .. " " .. tostring(err))
if ok then pcall(function() return event.can_use_callback_params(onwrite) end) end
pcall(function() return event.can_use_callback_params(onwrite) end)

-- Also try the explicit callback-object form: a table carrying read/write methods.
local obj = {}
function obj:ReadByte(a) return memory.read_u8(a) end
local ok2, err2 = pcall(function() return event.onmemorywrite(obj) end)
w("register onmemorywrite(object) ok=" .. tostring(ok2) .. " " .. tostring(err2))

w("frame at start " .. tostring(emu.framecount()))
local guard = 0
while emu.framecount() < LAST and guard < LAST * 20 + 1000 do
  frame = emu.framecount() + 1
  emu.frameadvance()
  guard = guard + 1
end
w(string.format("ran to frame %d in %d steps", emu.framecount(), guard))
w(string.format("counts total=%d ppu=%d mapper=%d other=%d unparsed=%d recorded=%d",
                counts.total, counts.ppu, counts.mapper, counts.other,
                counts.unparsed, recorded))
if shape_seen then
  w(string.format("callback shape: keys=[%s] addr=%s val=%s",
                  tostring(shape_seen.keys), tostring(shape_seen.addr),
                  tostring(shape_seen.val)))
else
  w("callback shape: NEVER FIRED")
end
log:close()
pcall(function() client.exit() end)
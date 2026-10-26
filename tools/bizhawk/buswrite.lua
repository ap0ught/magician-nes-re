-- Does an *address-bounded* `event.on_bus_write` / `event.onmemorywrite` work on
-- this BizHawk build with the quickNES core?
--
--   MAGICIAN_LUA_OUT=<file> MAGICIAN_FRAMES=<n> tools/bizhawk/run.sh buswrite.lua <rom>
--
-- ## Why this file exists
--
-- `writes.lua` registered `event.onmemorywrite(cb)` with no address and saw the
-- core log "quickerNES does not implement wildcard memory callbacks". The word
-- *wildcard* in that sentence is the whole reason this file exists: it reads like
-- a claim that bounded callbacks are implemented, and two sessions were spent
-- assuming it. `bankprobe.lua` then tried six registration shapes and none
-- fired, which is a negative result about six guesses, not about the API.
--
-- The guess was wrong because the real signature was never read. BizHawk ships
-- its own API definitions inside the install, at `Lua/_docs_luacats/event.d.lua`,
-- and they say:
--
--     function event.on_bus_write(luaf, address, name?, scope?) end
--     function event.onmemorywrite(luaf, address, name?, scope?) end   -- deprecated
--
-- Four arguments, address *second*, scope *fourth*. None of the six shapes in
-- `bankprobe.lua` matched that. This file uses the documented signature.
--
-- ## How "did it register" is decided without trusting a fire count
--
-- `event.availableScopes()` and `on_bus_write` both call
-- `EmulatorExtensions.MemoryCallbacksAvailable(debuggable)`. Reading BizHawk's own
-- IL out of the install (`ikdasm dll/BizHawk.Client.Common.dll`) settles what that
-- does:
--
--   * `MemoryCallbacksAvailable` calls `IDebuggable.MemoryCallbacks` inside a
--     `try` that catches `NotImplementedException` and returns **false**.
--   * `AvailableScopes()` answers an **empty list** on false. It does not throw,
--     so a probe that only pcall'd it would see a successful call and a table
--     with nothing in it.
--   * `OnBusWrite` on false falls through to
--     `LogMemoryCallbacksNotImplemented(isWildcard)` and `return EMPTY_UUID_STR`.
--     The word "wildcard " is interpolated into that message *only when no address
--     was given* -- it describes the call, not the core's capability.
--   * `EMPTY_UUID_STR` is `Guid.Empty.ToString("D")`, the literal string
--     `00000000-0000-0000-0000-000000000000`.
--
-- Measured, and all three agree: `availableScopes()` is `{}`, all eight
-- registrations returned the sentinel, and the log window printed the message
-- seven times *without* the word "wildcard" -- the seven bounded calls -- and
-- once with it, for the wildcard one.
--
-- So each registration returns either a real GUID or that exact sentinel, and the
-- return value alone says whether the callback exists. That is the primary test
-- here, because unlike a fire count it cannot be fooled by a game that happens
-- not to write the address.
--
-- ## Coverage, asserted
--
--   * every registration's return value is compared against the sentinel, so
--     "installed" and "not installed" are told apart rather than inferred from
--     silence;
--   * `$2007` is registered as a **positive control**. The game writes `$2007`
--     many times a frame. If the bounded `$2007` hook fires and the bounded
--     `$8000` hook does not, the negative is about the address. If *neither*
--     fires, the mechanism is absent, and those are different findings;
--   * the script also performs one `$8000` write of its own through
--     `memory.write_u8`, so "the game never wrote $8000" cannot be offered as an
--     explanation. Supplementary only -- a Lua-side write need not take the same
--     path as a CPU write -- so it can add evidence of a fire but never
--     establishes coverage on its own;
--   * the file ends with a `done` sentinel and prints `NO ... available` rather
--     than staying silent when nothing registered.
--
-- Nothing here reads or writes the cartridge. The only write is to the MMC3 port
-- at $8000, which is write-only and so has nothing to restore; it happens after
-- the frame loop, at the end of the session.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/buswrite.txt"
local LAST = tonumber(os.getenv("MAGICIAN_FRAMES") or "8")

local log = io.open(OUT, "w")
local function w(s) log:write(s .. "\n"); log:flush() end

-- Guid.Empty.ToString("D"), from EventsLuaLibrary's .cctor. Equal to this means
-- BizHawk declined to register the callback, whether or not the log window said
-- so.
local EMPTY_UUID = "00000000-0000-0000-0000-000000000000"

w("buswrite: the documented signature is on_bus_write(luaf, address, name?, scope?)")
w(string.format("buswrite: BizHawk 2.11.1, core quickNES, %d frames requested", LAST))
w("")

-- -------------------------------------------------------------- availableScopes
-- Not decoration. `EventsLuaLibrary.AvailableScopes` calls the same
-- `MemoryCallbacksAvailable(debuggable)` that `on_bus_write` calls first, and
-- returns an **empty table** when it is false (it does not throw, so a probe that
-- only pcall'd this would see a successful call). An empty list is the signal: no
-- bus scope exists, so `HasScope("System Bus")` would fail too and there is
-- nothing for a bounded callback to bind to.
local ok, res = pcall(function() return event.availableScopes() end)
if not ok then
  w("availableScopes()    -> THREW: " .. tostring(res))
elseif type(res) == "table" then
  local t = {}
  for k, v in pairs(res) do t[#t + 1] = tostring(k) .. "=" .. tostring(v) end
  table.sort(t)
  w("availableScopes()    -> {" .. table.concat(t, ", ") .. "}")
  if #t == 0 then
    w("  EMPTY. AvailableScopes returns an empty table when")
    w("  MemoryCallbacksAvailable(core) is false, which is the same test")
    w("  on_bus_write makes before it binds anything. No scope exists to bind to.")
  end
else
  w(string.format("availableScopes()    -> returned a %s, not a table", type(res)))
end

w("")

-- --------------------------------------------------------------- registrations
-- One flat path so every shape is tried and every outcome printed. A shape that
-- throws, a shape refused by argument checking, and a shape that "installs" but
-- returns the sentinel are three different findings and are kept apart.
local FIRES = {}
local SEEN = {}

local function mk(key)
  return function(addr, val, flags)
    FIRES[key] = (FIRES[key] or 0) + 1
    if #SEEN < 200 then
      SEEN[#SEEN + 1] = string.format("%s  addr=%s val=%s flags=%s", key,
                                       tostring(addr), tostring(val),
                                       tostring(flags))
    end
  end
end

local RESULTS = {}

-- `addr` nil means the wildcard form, which is what makes the log message say
-- "wildcard"; the address is the second argument either way.
local function try_reg(label, fname, addr, name, scope)
  local f = mk(label)
  local ok2, r = pcall(function()
    local fn = (fname == "on_bus_write") and event.on_bus_write or event.onmemorywrite
    if scope ~= nil then return fn(f, addr, name, scope) end
    if name ~= nil then return fn(f, addr, name) end
    return fn(f, addr)
  end)
  if not ok2 then
    RESULTS[#RESULTS + 1] = string.format("%-32s THREW   %s", label, tostring(r))
  elseif tostring(r) == EMPTY_UUID then
    RESULTS[#RESULTS + 1] = string.format("%-32s REFUSED (Guid.Empty)", label)
  else
    RESULTS[#RESULTS + 1] = string.format("%-32s REGISTERED guid=%s", label,
                                          tostring(r))
  end
end

w("== documented bounded forms ==")
try_reg("bus_write  $8000 sysbus",   "on_bus_write",  0x8000, "bw8000", "System Bus")
try_reg("bus_write  $8001 sysbus",   "on_bus_write",  0x8001, "bw8001", "System Bus")
try_reg("bus_write  $8000 noscope",  "on_bus_write",  0x8000, "bw8000b", nil)
try_reg("memorywrite $8000 sysbus",  "onmemorywrite", 0x8000, "mw8000", "System Bus")
try_reg("memorywrite $8000 noscope", "onmemorywrite", 0x8000, "mw8000b", nil)
w("== positive control: an address the game writes many times a frame ==")
try_reg("bus_write  $2007 sysbus",   "on_bus_write",  0x2007, "pc2007", "System Bus")
try_reg("memorywrite $2007 sysbus",  "onmemorywrite", 0x2007, "pmw2007", "System Bus")
w("== the form writes.lua used, kept only for the log-message comparison ==")
try_reg("memorywrite <no address>",  "onmemorywrite", nil, nil, nil)
w("")
for i = 1, #RESULTS do w("  " .. RESULTS[i]) end

-- ------------------------------------------------------------------ frame loop
local base = emu.framecount()
local guard = 0
while emu.framecount() - base < LAST and guard < LAST * 20 + 2000 do
  emu.frameadvance()
  guard = guard + 1
end
local last = emu.framecount()

-- ------------------------------------------------------------------- self-test
local before8 = FIRES["bus_write  $8000 sysbus"] or 0
local ctrl = FIRES["bus_write  $2007 sysbus"] or 0
local wok = pcall(function() memory.write_u8(0x8000, 0x5A, "System Bus") end)
local after8 = FIRES["bus_write  $8000 sysbus"] or 0
w("")
w("self-test: memory.write_u8($8000, $5A) ok=" .. tostring(wok))
w(string.format("self-test: bounded $8000 hook fired for it: %d   (positive "
                .. "control $2007 fired %d time(s) across the run)",
                after8 - before8, ctrl))

-- -------------------------------------------------------------------- verdict
local registered, refused, threw = 0, 0, 0
for i = 1, #RESULTS do
  local r = RESULTS[i]
  if r:find("REGISTERED", 1, true) then registered = registered + 1
  elseif r:find("REFUSED", 1, true) then refused = refused + 1
  else threw = threw + 1 end
end
local total = 0
for _k, v in pairs(FIRES) do total = total + v end

w("")
w("VERDICT")
w(string.format("frames advanced     %d..%d (%d)", base, last, last - base))
w(string.format("registrations       %d attempted -- %d REGISTERED, %d REFUSED "
                .. "(Guid.Empty), %d threw", #RESULTS, registered, refused, threw))
w(string.format("callback fires      %d, across every shape", total))
if #SEEN == 0 then
  w("callback shape       NEVER OBSERVED: no callback body ever ran, so there is")
  w("                      nothing here that could be read as a working fire")
else
  for i = 1, #SEEN do w("  " .. SEEN[i]) end
end

if registered == 0 and total == 0 then
  w("")
  w("RESULT: NO address-bounded memory callback is available in this build.")
  w("Every documented form of on_bus_write / onmemorywrite returned Guid.Empty,")
  w("BizHawk's sentinel for 'not registered' (EventsLuaLibrary.EMPTY_UUID_STR),")
  w("and none fired for the frames advanced or for a write this script made.")
  w("")
  w("The log line 'quickerNES does not implement wildcard memory callbacks'")
  w("names the CALL, not the core. BizHawk interpolates the word 'wildcard ' only")
  w("when no address was supplied; with an address the same line is logged")
  w("without it, because QuickNES.get_MemoryCallbacks() in")
  w("dll/BizHawk.Emulation.Cores.dll throws NotImplementedException")
  w("unconditionally and there is no bounded path to take.")
  w("")
  w("DO NOT RETRY THE BOUNDED CALLBACK. There is no per-instruction $8000/$8001")
  w("write trace from this BizHawk build. The two instruments that do work are")
  w("per-frame polling of the System Bus domain (bankprobe.lua) and")
  w("tools/nestrace.py.")
elseif registered == 0 then
  w("")
  w("RESULT: AMBIGUOUS -- nothing returned a real GUID, yet callbacks fired.")
  w("Do not call the bounded callback working until the return values above are")
  w("explained; a fire with no registration is the shape of a bug in this file.")
else
  w("")
  w("RESULT: at least one bounded callback WAS registered. Use the guid lines and")
  w("the per-shape fire counts above.")
end
w("")
w("done, frames " .. base .. ".." .. last)
log:close()
pcall(function() client.exit() end)

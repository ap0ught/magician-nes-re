-- Pin down the exact callback signatures of event.onmemorywrite / onmemoryexecute /
-- on_bus_write, and confirm emu.getregister and emu.disassemble work.
--
-- The signature question is not pedantry. BizHawk has two calling conventions --
-- one big callback with parameters, one closure per registration -- and
-- `event.can_use_callback_params` exists precisely because a script written for
-- the other one registers successfully and then never fires. Recording how many
-- arguments actually arrive is the only way to tell the difference.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/sig.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local fired = {}

-- A probe callback that records arity and types of whatever it is handed, once.
local function probe(tag)
  return function(...)
    if fired[tag] then return end
    fired[tag] = true
    local n = select("#", ...)
    local parts = {}
    for i = 1, n do parts[#parts + 1] = tostring(select(i, ...)) end
    w(string.format("  %-22s argc=%d  [%s]", tag, n, table.concat(parts, " | ")))
  end
end

w("can_use_callback_params = " .. tostring(event.can_use_callback_params and event.can_use_callback_params() or "nil"))
w("availableScopes = " .. tostring(event.availableScopes and event.availableScopes() or "nil"))

local TRIALS = {
  { "onmemorywrite(cb)",              function() event.onmemorywrite(probe("omw_cb")) end },
  { "onmemorywrite(addr,cb)",         function() event.onmemorywrite(0x2007, probe("omw_addr_cb")) end },
  { "onmemorywrite(addr,sz,cb)",      function() event.onmemorywrite(0x2007, 1, probe("omw_3")) end },
  { "onmemorywrite(addr,sz,cb,name)", function() event.onmemorywrite(0x2007, 1, probe("omw_4"), "p7") end },
  { "onmemorywrite(addr,sz,'w',cb)",  function() event.onmemorywrite(0x2007, 1, "w", probe("omw_w")) end },
  { "onmemoryread(cb)",               function() event.onmemoryread(probe("omr_cb")) end },
  { "onmemoryexecute(cb)",            function() event.onmemoryexecute(probe("omx_cb")) end },
  { "onmemoryexecute(addr,cb)",       function() event.onmemoryexecute(0x9D6C, probe("omx_a")) end },
  { "onmemoryexecute(addr,sz,cb)",    function() event.onmemoryexecute(0x9D6C, 1, probe("omx_3")) end },
  { "onmemoryexecuteany(cb)",         function() event.onmemoryexecuteany(probe("omxa")) end },
  { "on_bus_write(cb)",               function() event.on_bus_write(probe("obw")) end },
  { "on_bus_write(addr,cb)",          function() event.on_bus_write(0x2007, probe("obw_a")) end },
  { "on_bus_exec(cb)",                function() event.on_bus_exec(probe("obe")) end },
}
for _, t in ipairs(TRIALS) do
  local ok, err = pcall(t[2])
  w(string.format("%-32s install %-6s %s", t[1], tostring(ok), ok and "" or tostring(err)))
end

-- Registers and disassembly.
for _, r in ipairs({ "A", "X", "Y", "SP", "PC", "P", "C", "Z", "I", "D", "B", "V", "N" }) do
  local ok, v = pcall(emu.getregister, r)
  w(string.format("getregister %-3s %-6s %s", r, tostring(ok), ok and tostring(v) or "-"))
end
local okr, regs = pcall(emu.getregisters)
w("getregisters " .. tostring(okr) .. " " .. tostring(regs))

for _, a in ipairs({ 0x9D6C, 0xF9B3, 0xF9C1, 0x8000, 0xFFFC }) do
  local okd, d = pcall(emu.disassemble, a)
  w(string.format("disassemble $%04X %-6s %s", a, tostring(okd), okd and tostring(d) or "-"))
end

w("--- running 8 frames ---")
for _ = 1, 8 do emu.frameadvance() end

w("--- fired ---")
local ks = {}
for k in pairs(fired) do ks[#ks + 1] = k end
table.sort(ks)
if #ks == 0 then w("  (nothing fired)") end
for _, k in ipairs(ks) do w("  " .. k) end

w("done")
f:close()
pcall(function() client.exit() end)

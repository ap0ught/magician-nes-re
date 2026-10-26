-- Establish the exact signature of BizHawk's memory/exec hooks before relying on
-- them. A hook that fails to install raises nothing visible from inside the
-- emulator, and a tracer that silently records nothing is indistinguishable from
-- a tracer that found no divergence.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/hooks.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

emu.frameadvance()

-- Candidate orderings of emu.registerafter / emu.registerbefore. Each variant is
-- tried in a pcall and its success recorded; a hook that installs is proved by
-- the callback actually firing, not by the call returning.
local fired = {}
local function cb(tag) return function(...) fired[tag] = (fired[tag] or 0) + 1 end end

local VARIANTS = {
  { "after(addr,size,type,func,name)", function()
      emu.registerafter(0x2006, 2, "w", cb("a1"), "latch") end },
  { "after(addr,size,type,func)", function()
      emu.registerafter(0x2007, 1, "w", cb("a2"), "data") end },
  { "after(addr,size,func,name,type)", function()
      emu.registerafter(0x2001, 1, cb("a3"), "mask", "w") end },
  { "after(addr,size,func,type)", function()
      emu.registerafter(0x4014, 1, cb("a4"), "w") end },
  { "after(addr,size,type,func,offset)", function()
      emu.registerafter(0x2005, 2, "w", cb("a5"), "scroll", 0) end },
  { "before(addr,size,func,name)", function()
      emu.registerbefore(0x9D6C, 1, cb("b1"), "rts9d6c") end },
  { "before(addr,size,func,name,type)", function()
      emu.registerbefore(0x9D6C, 2, cb("b2"), "rts9d6c2", "e") end },
  { "before exec only (addr,func,name)", function()
      emu.registerbefore(0x8000, 1, cb("b3"), "exec8000") end },
  { "unregisterafter(name)", function()
      emu.unregisterafter("data") end },
}

for _, v in ipairs(VARIANTS) do
  local ok, err = pcall(v[2])
  w(string.format("%-40s %s %s", v[1], tostring(ok), ok and "" or tostring(err)))
end

-- Run a few frames and see which callbacks ever fired.
for _ = 1, 5 do emu.frameadvance() end
w("--- fired ---")
local ks = {}
for k in pairs(fired) do ks[#ks + 1] = k end
table.sort(ks)
for _, k in ipairs(ks) do w(string.format("  %-10s %d", k, fired[k])) end
if #ks == 0 then w("  (nothing fired)") end

-- Is client.cpu live inside a callback?
emu.registerafter(0x2001, 1, "w", function()
  local okc, cpu = pcall(function() return client.cpu end)
  f:write("in-callback client.cpu ok=" .. tostring(okc) .. " type=" .. type(cpu) .. "\n")
  if okc and type(cpu) == "table" then
    local kk = {}
    for k2, v2 in pairs(cpu) do kk[#kk + 1] = tostring(k2) .. "=" .. tostring(v2) end
    table.sort(kk)
    f:write("  cpu: " .. table.concat(kk, " ") .. "\n")
  end
  local okp, ppu = pcall(function() return client.ppu end)
  f:write("in-callback client.ppu ok=" .. tostring(okp) .. " type=" .. type(ppu) .. "\n")
  f:flush()
  emu.unregisterafter("probe-cpu")
end, "probe-cpu")

for _ = 1, 5 do emu.frameadvance() end
w("done")
f:close()
pcall(function() client.exit() end)

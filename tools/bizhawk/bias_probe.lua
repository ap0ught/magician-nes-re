-- Enumerate the NES core's memory domains *after frames have run*, because a
-- `--lua` script is loaded before the core has stepped and the domain list may
-- not be populated until it has.
--
-- `memory.getmemorydomainlist()` comes back as NLua userdata rather than a Lua
-- table, so indexing it with ipairs silently yields nothing -- which is exactly
-- what dump.lua's own header comment warns about ("an unknown name is NOT an
-- error: it silently falls back"). So both routes are tried, and each candidate
-- name is confirmed with getmemorydomainsize() as well as with the
-- usememorydomain/getcurrentmemorydomain pair.
--
--   MAGICIAN_LUA_OUT=<report> tools/bizhawk/bias_probe.lua <rom>

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/dom.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local function ndomains()
  local n = 0
  pcall(function()
    local l = memory.getmemorydomainlist()
    if type(l) == "table" then
      for _ in pairs(l) do n = n + 1 end
    end
  end)
  return n
end

w("frames=0 domains=" .. ndomains())
for i = 1, 3 do
  if not pcall(function() emu.frameadvance() end) then w("advance " .. i .. " failed"); break end
  w("frames=" .. emu.framecount() .. " domains=" .. ndomains())
end

-- getmemorydomainsize is the cheap existence test.
w("=== getmemorydomainsize ===")
local CAND = { "VRAM", "VRAM (Nametables)", "Nametables", "PPU", "PPU Bus", "System Bus",
               "CPU Bus", "CPU", "CHR", "CHR ROM", "CHR-RAM", "PRG ROM", "PRG ROM 0",
               "PRG ROM 1", "PRG RAM", "RAM", "Work RAM", "WRAM", "OAM", "Palette",
               "Main RAM", "Null" }
for _, d in ipairs(CAND) do
  local ok, sz = pcall(memory.getmemorydomainsize, d)
  local oks = pcall(memory.usememorydomain, d)
  local cur = "<nil>"
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  w(string.format("  %-20s size_ok=%-5s size=%-10s select_ok=%-5s current=%s",
                  d, tostring(ok), tostring(ok and sz or "-"), tostring(oks), cur))
end

f:close()
pcall(function() client.exit() end)
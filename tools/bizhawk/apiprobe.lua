-- Enumerate what BizHawk 2.11.1's Lua API actually offers under Mono, and how far
-- the emulation gets, writing everything to OUT. Everything here is discovered at
-- runtime on purpose: previous sessions concluded Lua was unusable from the CLI,
-- and that conclusion has to be checked against the real surface.
--
--   EmuHawkMono.sh --gdi --config <ini> --lua tools/bizhawk/apiprobe.lua <rom>

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/lua_api.txt"

local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local function safe(label, fn, ...)
  local ok, res = pcall(fn, ...)
  return ok, res
end

w("=== globals ===")
local gnames = {}
for k, v in pairs(_G) do gnames[#gnames + 1] = tostring(k) .. " = " .. type(v) end
table.sort(gnames)
for _, l in ipairs(gnames) do w("  " .. l) end

-- pairs() may miss entries behind an __index metatable, so also probe a list of
-- names BizHawk's docs are known to use. Non-nil means it is really there.
local CANDIDATES = {
  "savescreenshot", "screenshot", "takescreenshot", "getpixel", "getframebuffer",
  "getscreen", "getvideo", "readpixel", "framebuffer", "savesprite",
  "shutdown", "exit", "quit", "getversion", "getsystemid", "getromname",
  "getscreenwidth", "screenwidth", "width", "height",
  "save", "load", "saveram", "loadram", "savestate", "loadstate",
  "log", "info", "error", "warning", "memoryareas", "cpu", "ppu", "system",
  "userdata", "audioregister", "pause", "unpause", "ismovie", "userrecordid",
}
w("=== client candidates ===")
if type(client) == "table" then
  for _, n in ipairs(CANDIDATES) do
    local ok, v = safe("probe", function() return client[n] end)
    w(string.format("  client.%-22s %s %s", n, tostring(ok), ok and type(v) or tostring(v)))
  end
  w("=== client keys (pairs) ===")
  local ks = {}
  for k in pairs(client) do ks[#ks + 1] = tostring(k) end
  table.sort(ks)
  for _, k in ipairs(ks) do w("  " .. k) end
else
  w("  client is " .. type(client))
end

w("=== emu candidates ===")
local ECAND = {
  "frameadvance", "framecount", "framecountcallback", "paused", "pause",
  "unpause", "speedfactor", "speedmode", "reset", "softreset", "hardreset",
  "client", "memory", "margin", "skipping", "execspeed", "lagged", "lags",
  "clockrate", "displaylag", "audioavail", "noactivity",
}
if type(emu) == "table" then
  for _, n in ipairs(ECAND) do
    local ok, v = safe("probe", function() return emu[n] end)
    w(string.format("  emu.%-22s %s %s", n, tostring(ok), ok and type(v) or tostring(v)))
  end
end

w("=== emulating ===")
local ok, err = safe("advance", function()
  for _ = 1, 10 do emu.frameadvance() end
end)
w("  frameadvance ok=" .. tostring(ok) .. " res=" .. tostring(err))
ok, err = safe("count", function() return emu.framecount() end)
w("  framecount ok=" .. tostring(ok) .. " val=" .. tostring(err))

-- Can we read core memory at all, and does client.cpu resolve now that frames ran?
local ok2, res2 = safe("cpu", function() return client.cpu end)
w("  client.cpu ok=" .. tostring(ok2) .. " val=" .. tostring(res2))
local ok3, res3 = safe("mem", function() return memory.read_u8(0x0200) end)
w("  memory.read_u8(0x200) ok=" .. tostring(ok3) .. " val=" .. tostring(res3))
local ok4, res4 = safe("areas", function()
  local a = {}
  for m, name in pairs(memory.map()) do a[#a + 1] = name .. "@" .. string.format("%X", m) end
  table.sort(a)
  return table.concat(a, " ")
end)
w("  memory areas: " .. tostring(ok4) .. " " .. tostring(res4))

w("=== done ===")
f:close()

-- Leave the window up so it can be screenshotted; the driver kills us.

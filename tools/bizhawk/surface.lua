-- Enumerate the emu/event/memory surfaces. `emu.registerafter` -- the function
-- BizHawk's docs and this project's own notes both assume exists -- is nil in
-- 2.11.1, so the real names have to be read off the running build rather than
-- assumed from documentation.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/surf.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local function keys(name, t)
  local ks = {}
  if type(t) == "table" then
    local mt = getmetatable(t)
    for k, v in pairs(t) do ks[#ks + 1] = string.format("%s(%s)", tostring(k), type(v)) end
    if mt then
      local mk = getmetatable(mt)
      if mk then for k in pairs(mk) do ks[#ks + 1] = string.format("__%s", tostring(k)) end end
    end
  end
  table.sort(ks)
  w(name .. ": " .. table.concat(ks, " "))
end

keys("emu", emu)
keys("event", event)
keys("client", client)
keys("comm", comm)

-- A few names that older/other BizHawk builds expose.
for _, n in ipairs({ "registerafter", "registerbefore", "registerexec", "registermem",
                     "unregisterafter", "unregisterbefore", "unregistermem",
                     "addlogCallback", "onframeend", "onexit", "onreset",
                     "onnewgame", "frameadvance", "framecount", "paused",
                     "noacceleration", "displaylag", "margins", "speedmode" }) do
  local a = pcall(function() return emu[n] end)
  local b = pcall(function() return event[n] end)
  w(string.format("  emu.%-20s %-6s   event.%-20s %-6s", n, tostring(a == true),
                  n, tostring(b == true)))
end

f:close()
pcall(function() client.exit() end)

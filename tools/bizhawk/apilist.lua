-- Ask BizHawk itself for its Lua API: client.getluafunctionslist() is the
-- authoritative list, and reading it beats guessing names from documentation
-- (emu.registerafter and emu.registerbefore, both assumed by earlier notes, do
-- not exist in 2.11.1).

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/api.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local ok, lst = pcall(client.getluafunctionslist)
w("getluafunctionslist ok=" .. tostring(ok) .. " type=" .. type(lst))
if ok and type(lst) == "string" then
  -- It is a document, not a table. Dump it whole; grep it for the names that matter.
  local d = io.open(OUT .. ".doc", "w")
  d:write(lst)
  d:close()
  w("wrote " .. OUT .. ".doc")
end
if ok and type(lst) == "table" then
  local keys = {}
  for k, v in pairs(lst) do
    if type(k) == "string" then keys[#keys + 1] = k end
  end
  table.sort(keys)
  w("count " .. #keys)
  for _, k in ipairs(keys) do
    local v = lst[k]
    local parts = {}
    if type(v) == "table" then
      for kk, vv in pairs(v) do parts[#parts + 1] = tostring(kk) .. "=" .. tostring(vv) end
      table.sort(parts)
      w(string.format("%-46s %s", k, table.concat(parts, " ")))
    else
      w(string.format("%-46s %s", k, tostring(v)))
    end
  end
end

-- Memory-callback capability flag, wherever it lives.
for _, holder in ipairs({ { "client", client }, { "emu", emu }, { "event", event } }) do
  local t = holder[2]
  for _, n in ipairs({ "MemoryCallbacksAvailable", "memorycallbacksavailable",
                       "CanUseMemoryCallbacks", "canusememorycallbacks" }) do
    local ok2, v = pcall(function() return t[n] end)
    if ok2 and v ~= nil then w(holder[1] .. "." .. n .. " = " .. tostring(v)) end
  end
end

f:close()
pcall(function() client.exit() end)

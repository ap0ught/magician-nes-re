-- The actual domain names on this core, and confirmation that a select only took
-- if getcurrentmemorydomain() agrees. Everything else here is guesswork.
--
-- getmemorydomainsize() is useless as an existence test on this build -- it
-- returns a size for every name tried, including "Null" -- so the only honest
-- test is usememorydomain() followed by getcurrentmemorydomain().

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/domlist.txt"
local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local list = memory.getmemorydomainlist() or {}
local names = {}
for _, v in ipairs(list) do names[#names + 1] = tostring(v) end
table.sort(names)
w("--- getmemorydomainlist() returns " .. #names .. " entries ---")
for _, n in ipairs(names) do
  local cur = "?"
  pcall(memory.usememorydomain, n)
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  local size = -1
  pcall(function() size = memory.getmemorydomainsize() end)
  w(string.format("  %-24s selects=%-5s size=%d", n, tostring(cur == n), size))
end

w("--- names that do NOT exist (control: these must report selects=false) ---")
for _, n in ipairs({ "VRAM", "PPU", "Nametables", "PALRAM", "CIRAM (nametables)",
                     "Definitely Not A Domain", "Null" }) do
  local cur = "?"
  pcall(memory.usememorydomain, n)
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  w(string.format("  %-24s selects=%-5s current=%s", n, tostring(cur == n), cur))
end

f:close()
pcall(function() client.exit() end)
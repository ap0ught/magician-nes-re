-- Measure which RAM byte is which controller button, by pressing one button at a
-- time and looking at the answer.
--
--   MAGICIAN_PADPROBE_OUT=<txt> tools/bizhawk/run.sh tools/bizhawk/padprobe.lua <rom>
--
-- Why this file exists
-- --------------------
-- replay.lua needs to read the *cartridge's* decoded joypad state, which lives at
-- $002E-$0035 (`jt`) and $0036-$003B (`dlr`). Working out the byte order from the
-- source is a trap: `jk0` does not store one bit per button. It shifts a single
-- accumulator left once per *unpressed* read and stops at the first press, so the
-- accumulator ends up holding one bit set -- bit (k+1), where k is the index of
-- the first pressed button -- plus bit 0, and that value is then unpacked by
-- `joykey` into eight bytes. The observed result is not any simple identity:
-- pressing one button lights two bytes, and Right -- the eighth -- lights only
-- one, because the shift has already fallen off the end of the byte.
--
-- Two derivations of the same table were available and they disagreed, and a table
-- that is wrong in this way still produces plausible non-zero bytes, so it would
-- have gone unnoticed. This settles it by asking the hardware: press exactly one
-- button, for long enough to be unambiguous, and record which bytes moved.
--
-- Self-coverage
-- -------------
-- Each button's window must be long enough and must actually produce a non-zero
-- byte; a button that lights nothing is reported as such rather than being left
-- out of the table, and the file exits non-zero if any button could not be
-- resolved. "Eight buttons, seven answers" is a failure, not a result.

local OUT = os.getenv("MAGICIAN_PADPROBE_OUT") or "/tmp/opencode/padprobe.txt"
local LEAD = tonumber(os.getenv("MAGICIAN_PADPROBE_LEAD") or "40") or 40
local HOLD = tonumber(os.getenv("MAGICIAN_PADPROBE_HOLD") or "60") or 60
local SETTLE = tonumber(os.getenv("MAGICIAN_PADPROBE_SETTLE") or "40") or 40

-- NES latch order, bit 0 upward. This is the order the 6510 shifts $4016 out in
-- and it is what tools/fm2.py's `$4016` byte is built from.
local BIT = { A = 0x01, B = 0x02, Select = 0x04, Start = 0x08,
              Up = 0x10, Down = 0x20, Left = 0x40, Right = 0x80 }
local NAMES = { "A", "B", "Select", "Start", "Up", "Down", "Left", "Right" }

local f = io.open(OUT, "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local function sel(name)
  if not pcall(memory.usememorydomain, name) then return nil end
  local cur
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  return cur == name
end

if not sel("System Bus") then
  w("FATAL no System Bus domain")
  f:close(); pcall(function() client.exit() end); return
end
-- Confirm the bus is live before attributing anything to the buttons. If RAM is
-- all $FF the whole probe would report "no button does anything".
do
  local ok, s = pcall(memory.read_bytes_as_binary_string, 0, 64)
  if not (ok and type(s) == "string") then
    w("FATAL could not read 64 bytes of RAM from the System Bus")
    f:close(); pcall(function() client.exit() end); return
  end
  w(string.format("bus is live: $0000-$003F = %s",
                  (s:gsub(".", function(c) return string.format("%02X", c:byte()) end))))
end

local JT, DLR = 0x2E, 0x36
local function snap()
  local ok, s = pcall(memory.read_bytes_as_binary_string, 0x0000, 0x80)
  if not ok or type(s) ~= "string" or #s < 0x40 then return nil end
  local jt, dl = {}, {}
  for i = 0, 7 do jt[#jt + 1] = s:byte(JT + i + 1) or -1 end
  for i = 0, 5 do dl[#dl + 1] = s:byte(DLR + i + 1) or -1 end
  return jt, dl
end

local function set(bits)
  local t = {}
  for _, n in ipairs({ "Power", "Up", "Down", "Left", "Right", "A", "B",
                       "Select", "Start", "Eject", "Stretch" }) do
    t[n] = false
  end
  for _, n in ipairs(NAMES) do
    if bits & BIT[n] ~= 0 then t[n] = true end
  end
  pcall(joypad.set, t, 1)
end

local function run(bits, n)
  local jts, dls = {}, {}
  for _ = 1, n do
    set(bits)
    emu.frameadvance()
    local jt, dl = snap()
    if jt then jts[#jts + 1] = jt; dls[#dls + 1] = dl end
  end
  return jts, dls
end

-- The value each offset held in the *last* frame of a window. "Ever non-zero"
-- is too weak a test for a baseline: the game is still settling its own direction
-- bytes a few dozen frames after a press, so "ever" credits a resting value to
-- whichever button is tested next.
local function offsets_last(jts)
  local seen = {}
  local jt = jts[#jts]
  if jt then
    for i = 1, 8 do
      if jt[i] ~= 0 then seen[i] = jt[i] end
    end
  end
  return seen
end

-- The offsets that were ever non-zero, over the whole window.
local function offsets_seen(jts)
  local seen = {}
  for _, jt in ipairs(jts) do
    for i = 1, 8 do
      if jt[i] ~= 0 then seen[i] = seen[i] or jt[i] end
    end
  end
  return seen
end

w(string.format("pad probe: lead %d idle, hold %d pressed, settle %d idle", LEAD, HOLD, SETTLE))
w(string.format("frame count at start %d", tonumber(emu.framecount()) or -1))
w("")

local results = {}
local unresolved = {}

-- Baseline: nothing pressed at all, held long enough for the game's own debounce
-- to settle, then read. Without it a byte that is resting non-zero is credited to
-- whichever button happened to be tested first -- which is exactly what a first
-- pass of this probe did.
run(0, LEAD + SETTLE)
local _, base_jts = run(0, 60)
local base_jt = offsets_last(base_jts)
local blist = {}
for k in pairs(base_jt) do blist[#blist + 1] = string.format("%d=$002X=%02X", k, JT + k - 1, base_jt[k]) end
table.sort(blist)
w("baseline, nothing pressed for " .. (LEAD + SETTLE + 60) .. " frames:")
w("  $002E-$0035 offsets resting non-zero: " .. (#blist > 0 and table.concat(blist, " ") or "none"))
if #blist > 0 then
  w("  ^ these are NOT buttons. $0030/$0031 are `lr`/`ud`, which `joykey` sets to")
  w("    $FF when either direction bit is set and which the game also uses as")
  w("    scratch. Any table written from this probe must treat them as derived.")
end
w("")

for _, name in ipairs(NAMES) do
  run(0, LEAD)
  local jts, dls = run(BIT[name], HOLD)
  run(0, SETTLE)
  -- The last 20 frames of the hold, not the whole window: the game's debounce
  -- needs a few frames to accept a press, and counting the approach would put
  -- every button at every offset.
  local tail_j, tail_d = {}, {}
  for i = math.max(1, #jts - 19), #jts do tail_j[#tail_j + 1] = jts[i]; tail_d[#tail_d + 1] = dls[i] end
  local jt, dl = offsets_last(tail_j), offsets_last(tail_d)
  local jlist, dlist = {}, {}
  for k in pairs(jt) do jlist[#jlist + 1] = string.format("%d=$%04X:%02X", k, JT + k - 1, jt[k]) end
  for k in pairs(dl) do dlist[#dlist + 1] = string.format("%d=$%04X:%02X", k, DLR + k - 1, dl[k]) end
  table.sort(jlist); table.sort(dlist)
  w(string.format("press %-7s  jt: %-38s dlr: %s",
                  name, #jlist > 0 and table.concat(jlist, " ") or "NOTHING",
                  #dlist > 0 and table.concat(dlist, " ") or "NOTHING"))
  -- An offset that is also non-zero at rest cannot be read as this button.
  local fresh = {}
  for k in pairs(jt) do if base_jt[k] == nil then fresh[#fresh + 1] = k end end
  table.sort(fresh)
  w(string.format("            offsets that MOVED for this button: %s",
                  #fresh > 0 and table.concat(fresh, " ") or "none (see baseline)"))
  results[name] = { jt = jt, dlr = dl, fresh = fresh }
  if #fresh == 0 then unresolved[#unresolved + 1] = name end
end

w("")
w("summary: which $002E-$0035 offset uniquely identifies which button")
w("(a button is 'unique' if no other button shares its lowest offset):")
for _, name in ipairs(NAMES) do
  local r = results[name]
  if r and #r.fresh > 0 then
    local shared = {}
    for _, other in ipairs(NAMES) do
      if other ~= name and results[other] then
        for _, a in ipairs(r.fresh) do
          for _, b in ipairs(results[other].fresh) do
            if a == b then shared[#shared + 1] = other end
          end
        end
      end
    end
    local u = {}
    for _, v in ipairs(r.fresh) do u[#u + 1] = tostring(v) end
    w(string.format("  %-7s -> offsets {%s}%s", name, table.concat(u, ","),
                    #shared > 0 and ("  shared with " .. table.concat(shared, ", "))
                                  or "  (no other button uses these)"))
  else
    w(string.format("  %-7s -> UNRESOLVED", name))
  end
end

if #unresolved > 0 then
  w("")
  w("FAIL: " .. table.concat(unresolved, ", ") .. " pressed with nothing moving in "
     .. "$002E-$0035. Either the inject did not land or the table is wrong; either "
     .. "way this probe did not resolve all eight buttons and must not be used to "
     .. "write one down.")
end
w("done")
f:close()
pcall(function() client.exit() end)
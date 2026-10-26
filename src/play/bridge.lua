-- bridge.lua : the BizHawk side of src/play/emu.py.
--
-- Runs INSIDE EmuHawk, connects out to the Python controller over TCP on
-- 127.0.0.1, and executes one newline-delimited command per line, replying with
-- exactly one line per command. Ported from aibeatszelda's src/bridge.lua; the
-- differences and the reasons for them are in emu.py's module docstring.
--
-- Loaded by tools/bizhawk/run.sh, which wraps it in verify_preamble.lua -- so by
-- the time this runs, the core has been proven to be NES/mmc3 running the exact
-- PRG window that was asked for.
--
--   ping                    -> pong
--   step <n> <buttons>      -> hold <buttons> for n frames -> "ok frame=<n>"
--   stepu <budget> <btns> <pred>...   -> step while every pred is false
--                                   -> "ok used=<n> hit=<0|1>"
--   ram <addr> <len>        -> hex of the CPU address space (System Bus)
--   frame                   -> "ok frame=<n>"
--   domains                 -> "dom <i> <size> <name>" lines, then "ok domains=9"
--   dom <i> <addr> <len>    -> "ok dom=<name> bytes=<n> <hex>"
--   snapshot <dir>          -> dumps all nine domains + PNG + regs -> "ok files=.."
--   screenshot <path>       -> png
--   save <path> / load <path>
--   fast / normal / reset / quit
--
-- A PRED is `<addr>:<len>:<op>:<value>` with op one of
-- eq ne lt le gt ge band bne bclr, and several are ANDed. band/bne/bclr are
-- BITWISE and their value field is a MASK, not a compare target. Predicates are
-- here, inside the core, on every frame of a `stepu`, because that is the only
-- way to walk thousands of frames without a socket round trip per frame.
--
-- WHY THIS FILE ASSERTS ITS OWN COVERAGE
-- --------------------------------------
-- Twelve instruments in this project reported plausible wrong numbers without
-- error. Three of the ways that happens are specific to a memory bridge:
--
--   * `memory.usememorydomain(name)` on an unknown name does NOT fail. It logs
--     and leaves the PREVIOUS domain selected, so the next read returns another
--     region's bytes at the right size and nothing complains. Every read here
--     therefore re-checks getcurrentmemorydomain() and aborts on a mismatch.
--   * Domains are addressed by INDEX into the core's own list, because two of
--     the nine real names contain spaces and parentheses ("CIRAM (nametables)")
--     and cannot survive a space-separated protocol. Every reply repeats the
--     domain name it actually used, so the Python side can assert it asked for
--     the one it got.
--   * `client.screenshot()` returns nothing and writes the file lazily. A
--     zero-byte PNG is not a picture of nothing, it is a screenshot that was
--     never taken, so `snapshot` checks every file it wrote afterwards and
--     refuses to answer "ok" unless all of them exist at the expected size.

local socket = require("socket.core")

-- --------------------------------------------------------------- connection
local function readport()
  local p = tonumber(os.getenv("MAGICIAN_BRIDGE_PORT") or "")
  if p and p > 0 then return p end
  local path = os.getenv("MAGICIAN_BRIDGE_PORTFILE")
  if path and path ~= "" then
    local f = io.open(path, "r")
    if f then local v = tonumber(f:read("*l")); f:close(); if v and v > 0 then return v end end
  end
  return nil
end

local PORT = readport()
assert(PORT, "bridge: no port (set MAGICIAN_BRIDGE_PORT)")

local conn = socket.tcp()
conn:settimeout(20)
local ok, err = conn:connect("127.0.0.1", PORT)
assert(ok, "bridge: connect failed: " .. tostring(err))
conn:setoption("tcp-nodelay", true)
conn:settimeout(0.05)

-- ------------------------------------------------------------------ logging
-- One file per session, beside the launch log. If this script dies mid-run the
-- console log is the only record, and the console log is interleaved with GTK
-- noise. Appended, never truncated: two sessions in one file beats none.
local LOGPATH = os.getenv("MAGICIAN_BRIDGE_LOG")
local LOG = LOGPATH and io.open(LOGPATH, "a") or nil
local function log(s)
  if LOG then LOG:write(s .. "\n"); LOG:flush() end
end

-- ------------------------------------------------------------------ domains
-- The core's own list, taken once. Index-addressed on the wire because two of
-- the names contain spaces.
local DOMAINS = {}
local DOMSIZE = {}
do
  local list = memory.getmemorydomainlist() or {}
  for i, v in ipairs(list) do
    DOMAINS[i] = tostring(v)
    local sz = -1
    if pcall(memory.usememorydomain, DOMAINS[i]) then
      pcall(function() sz = memory.getmemorydomainsize() end)
    end
    DOMSIZE[i] = sz
  end
end

-- The nine this build of quickerNES exposes. Checked, not assumed: a core that
-- lost one would make a snapshot silently smaller, which is the failure this
-- whole exercise is about.
local NEEDED = {
  ["CHR"] = true,
  ["CHR VROM"] = true,
  ["CIRAM (nametables)"] = true,
  ["CPU registers"] = true,
  ["OAM"] = true,
  ["PALRAM"] = true,
  ["PRG ROM"] = true,
  ["System Bus"] = true,
  ["WRAM"] = true,
}

local function select(i)
  local name = DOMAINS[i]
  if not name then return nil, "no domain at index " .. tostring(i) end
  if not pcall(memory.usememorydomain, name) then
    return nil, "usememorydomain failed for " .. name
  end
  local cur
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  if cur ~= name then
    return nil, string.format("wanted domain %q, core has %q selected -- an "
                               .. "unknown name does not fail, it silently keeps "
                               .. "the previous selection", name, tostring(cur))
  end
  return name
end

local function hex(s)
  local out = {}
  for i = 1, #s do out[i] = string.format("%02x", s:byte(i)) end
  return table.concat(out)
end

-- Bulk read. Returns nil rather than a short string: a caller that gets a
-- truncated buffer and hexes it gets a plausible-looking wrong answer, which is
-- the single worst failure mode available here.
local function readbin(addr, len)
  -- ALWAYS returns exactly two values: the bytes read, and either nil or the
  -- list of offsets that could not be read. A one-value return on the fast path
  -- leaves the caller with `missing == nil` where it tests `#missing`, which is
  -- an error rather than a wrong answer -- and an error inside a predicate
  -- reads as "the bridge is broken" rather than "readbin forgot a return".
  local okr, s = pcall(memory.read_bytes_as_binary_string, addr, len)
  if okr and type(s) == "string" and #s == len then return s, nil end
  local ok2, arr = pcall(memory.readbyterange, addr, len)
  if ok2 and type(arr) == "table" and #arr == len then
    local b = {}
    for i = 1, len do b[i] = string.char(arr[i] % 256) end
    return table.concat(b), nil
  end
  -- Last resort, one byte at a time. The "CPU registers" domain is 12 bytes
  -- and is not a byte array at all -- both bulk forms refuse it -- so without
  -- this a snapshot of all nine domains fails at the ninth, and the natural
  -- response to that is to drop it, which is exactly the failure this exercise
  -- is about. Offsets that cannot be read are recorded, not skipped silently:
  -- `readbin` returns nil and the caller names the offsets.
  local b, bad = {}, {}
  for i = 0, len - 1 do
    local ok3, v = pcall(memory.read_u8, addr + i)
    if ok3 and type(v) == "number" then b[#b + 1] = string.char(v % 256)
    else bad[#bad + 1] = i end
  end
  if #bad > 0 then
    -- Not an error by itself. "CPU registers" is 12 bytes of which only the
    -- first 8 are byte-addressable: the last four are the individual status
    -- bits, which this API exposes only by NAME (emu.getregister("N") and
    -- friends). Returning the readable prefix plus the list of missing offsets
    -- lets the caller record what is missing and where its values live, instead
    -- of either dropping the domain or pretending it read 12 bytes.
    return table.concat(b), bad
  end
  return table.concat(b), nil
end

-- ------------------------------------------------------------------- buttons
-- Every button named explicitly as false first. BizHawk treats an absent key as
-- "leave alone", so a bridge that only mentions the buttons it wants pressed
-- inherits whatever was set on an earlier frame -- which is how a run that
-- released a button still holds it.
local ALLBUTTONS = { "Power", "Up", "Down", "Left", "Right", "A", "B",
                     "Select", "Start", "Eject", "Stretch", "L", "R" }
local function buttonset(names)
  local t = {}
  for _, n in ipairs(ALLBUTTONS) do t[n] = false end
  for _, n in ipairs(names) do
    t[n] = true
  end
  return t
end

local function parsebuttons(s)
  local out = {}
  if s and s ~= "-" then
    for b in string.gmatch(s, "[^,]+") do out[#out + 1] = b end
  end
  return out
end

local KNOWNBUTTON = {}
for _, n in ipairs(ALLBUTTONS) do KNOWNBUTTON[n] = true end

-- ---------------------------------------------------------------- predicates
-- <addr>:<len>:<op>:<value>, value decimal, len 1..2.
local function parsepred(s)
  local a, l, op, v = string.match(s, "^(%d+):(%d+):(%a+):(-?%d+)$")
  if not a then return nil, "malformed predicate: " .. tostring(s) end
  a, l, v = tonumber(a), tonumber(l), tonumber(v)
  if l ~= 1 and l ~= 2 then return nil, "predicate length must be 1 or 2: " .. tostring(s) end
  if not (a >= 0 and a < 0x10000) then return nil, "predicate address out of range" end
  local f
  if op == "eq" then f = function(x) return x == v end
  elseif op == "ne" then f = function(x) return x ~= v end
  elseif op == "lt" then f = function(x) return x < v end
  elseif op == "le" then f = function(x) return x <= v end
  elseif op == "gt" then f = function(x) return x > v end
  elseif op == "ge" then f = function(x) return x >= v end
  -- band/bne take the MASK in the value field, not a compare target, which is
  -- why they cannot live in the table form the rest use. They are BITWISE,
  -- matching ram.py's `_OPS` and the game's own `bit tmpflag`: modulo here
  -- would make `band` with mask 1 vacuously true, so "has the player bought the
  -- goat's milk" would hold in every state. test_play_ram.py check 12 feeds
  -- both implementations the same cases, so the two cannot drift apart.
  elseif op == "band" then f = function(x) return (x & v) == v end
  elseif op == "bne" then f = function(x) return (x & v) ~= 0 end
  elseif op == "bclr" then f = function(x) return (x & v) == 0 end
  else return nil, "unknown predicate op: " .. tostring(op) end
  if (op == "band" or op == "bne" or op == "bclr") and v <= 0 then
    return nil, "mask must be > 0 for " .. op .. ": " .. tostring(v)
  end
  return { addr = a, len = l, test = f, text = s }
end

-- Read the CPU address space. System Bus is the only domain that covers
-- $0000-$07FF as RAM and $8000+ as the banked PRG, so every predicate and every
-- `ram` read selects it and then CONFIRMS the selection.
local BUS = nil
for i, n in ipairs(DOMAINS) do
  if n == "System Bus" then BUS = i break end
end
assert(BUS, "bridge: this core has no System Bus domain")

local function peek(addr, len)
  local name, err = select(BUS)
  if not name then return nil, err end
  local s, missing = readbin(addr, len)
  if not s then return nil, string.format("could not read %d bytes at $%04X", len, addr) end
  if missing and #missing > 0 then
    return nil, string.format("%d bytes at $%04X are not all readable: %s",
                               len, addr, table.concat(missing, ","))
  end
  if len == 1 then return s:byte(1) end
  return s:byte(1) + s:byte(2) * 256
end

local function predholds(p)
  local v, err = peek(p.addr, p.len)
  if v == nil then return nil, err end
  return p.test(v) and true or false
end

-- ------------------------------------------------------------------- replies
-- LuaSocket's send() returns the NUMBER OF BYTES IT WROTE and this used to
-- throw that away. A short write leaves the tail of the reply in the socket,
-- where it is read as the answer to the NEXT command -- so the reply to
-- `step 12` comes back as `ok` and the run then thinks a press did nothing.
-- A receive timeout of zero is NOT "no timeout" in this LuaSocket: `settimeout(0)`
-- means a non-blocking socket and `settimeout(nil)` restores a small default,
-- so a loop written the obvious way gets `nil, "timeout"` and tears the session
-- down while the emulator is perfectly healthy. It is set to a long finite value
-- and re-armed at the top of every iteration, because `send()` changes it.
local READ_TIMEOUT = 7200

local function send(line)
  conn:settimeout(60)
  local payload, from = line .. "\n", 1
  local total = 0
  while from <= #payload do
    local n = conn:send(payload, from)
    if not n then
      log("send FAILED after " .. total .. " bytes: " .. tostring(n))
      return
    end
    total = total + n
    from = from + n
  end
  log(string.format("-> %s", line))
end

local function safe(name)
  return (string.gsub(name, "[^A-Za-z0-9]+", "_"))
end

-- ------------------------------------------------------------------ handlers
local CMD = {}

function CMD.ping() return "pong" end

function CMD.step(rest)
  local n, btn = rest:match("^(%d+)%s*(%S*)$")
  n = tonumber(n) or 1
  local t = buttonset(parsebuttons(btn))
  for _ = 1, n do
    joypad.set(t, 1)
    emu.frameadvance()
  end
  return string.format("ok frame=%d", emu.framecount())
end

-- Drive input only while the predicate is false. The predicate is checked
-- BEFORE the first frame (so a step that is already satisfied costs zero
-- frames) and once more after the last frame (so the final frame's write is
-- not missed). `hit=0` means the budget ran out, and the caller MUST assert on
-- that rather than warn.
function CMD.stepu(rest)
  local budget, btn, tail = rest:match("^(%d+)%s+(%S*)%s*(.*)$")
  if not budget then return "err stepu needs <budget> <buttons> <pred>..." end
  budget = tonumber(budget)
  if budget < 1 or budget > 60000 then
    return "err stepu budget out of range: " .. tostring(budget)
  end
  local names = parsebuttons(btn)
  for _, n in ipairs(names) do
    if not KNOWNBUTTON[n] then return "err unknown button: " .. n end
  end
  local preds, err = {}, nil
  for p in string.gmatch(tail, "%S+") do
    local q, e = parsepred(p)
    if not q then return "err " .. tostring(e) end
    preds[#preds + 1] = q
  end
  if #preds == 0 then return "err stepu needs at least one predicate" end

  local t = buttonset(names)
  local function holds()
    for _, p in ipairs(preds) do
      local h, e = predholds(p)
      if h == nil then return nil, e end
      if not h then return false end
    end
    return true
  end

  local used = 0
  local h, herr = holds()
  if h == nil then return "err " .. tostring(herr) end
  if h then return string.format("ok used=0 hit=1 frame=%d", emu.framecount()) end
  for i = 1, budget do
    joypad.set(t, 1)
    emu.frameadvance()
    used = i
    h, herr = holds()
    if h == nil then return "err " .. tostring(herr) end
    if h then return string.format("ok used=%d hit=1 frame=%d", used, emu.framecount()) end
  end
  return string.format("ok used=%d hit=0 frame=%d", used, emu.framecount())
end

function CMD.ram(rest)
  local a, l = rest:match("^(%d+)%s+(%d+)$")
  if not a then return "err ram needs <addr> <len>" end
  a, l = tonumber(a), tonumber(l)
  if l < 1 or l > 65536 then return "err ram length out of range" end
  local name, err = select(BUS)
  if not name then return "err " .. tostring(err) end
  local s, missing = readbin(a, l)
  if not s or (missing and #missing > 0) then
    return string.format("err could not read %d bytes at $%04X%s", l, a,
                         missing and #missing > 0
                           and (" (missing " .. table.concat(missing, ",") .. ")") or "")
  end
  return string.format("ok frame=%d bytes=%d %s", emu.framecount(), l, hex(s))
end

function CMD.frame() return string.format("ok frame=%d", emu.framecount()) end

function CMD.domains()
  local out = {}
  for i, n in ipairs(DOMAINS) do
    out[#out + 1] = string.format("dom %d %d %s", i, DOMSIZE[i], n)
  end
  out[#out + 1] = string.format("ok domains=%d", #DOMAINS)
  return table.concat(out, "\n")
end

function CMD.dom(rest)
  local i, a, l = rest:match("^(%d+)%s+(%d+)%s+(%d+)$")
  if not i then return "err dom needs <index> <addr> <len>" end
  i, a, l = tonumber(i), tonumber(a), tonumber(l)
  local name, err = select(i)
  if not name then return "err " .. tostring(err) end
  if a + l > DOMSIZE[i] then
    return string.format("err $%04X+%d is past the end of %s (%d bytes)",
                         a, l, name, DOMSIZE[i])
  end
  local s, missing = readbin(a, l)
  if not s or (missing and #missing > 0) then
    return string.format("err could not read %s $%04X+%d%s", name, a, l,
                         missing and #missing > 0
                           and (" (missing " .. table.concat(missing, ",") .. ")") or "")
  end
  return string.format("ok dom=%s bytes=%d %s", name, #s, hex(s))
end

-- Every domain, whole, plus the picture, the registers and the frame number.
-- Verified after writing: the manifest lists what was written and the byte
-- count each file actually has, and a short write is a failure here, not a
-- smaller snapshot.
function CMD.snapshot(rest)
  local dir = rest:match("^(%S+)$")
  if not dir then
    return "err snapshot needs ONE whitespace-free directory token; got: " .. rest
  end
  os.execute("mkdir -p '" .. dir .. "'")
  local man = { string.format("frame=%d", emu.framecount()) }
  local wrote, bad = 0, {}
  for i, n in ipairs(DOMAINS) do
    local name, err = select(i)
    if not name then
      bad[#bad + 1] = tostring(err)
    else
      local s, missing = readbin(0, DOMSIZE[i])
      local nmissing = missing and #missing or 0
      if not s then
        bad[#bad + 1] = string.format("could not read %s (%d bytes)", name, DOMSIZE[i])
      else
        local path = string.format("%s/%02d_%s.bin", dir, i, safe(name))
        local fh = io.open(path, "wb")
        if not fh then
          bad[#bad + 1] = "could not open " .. path
        else
          fh:write(s)
          fh:close()
          -- Re-stat from the filesystem rather than trusting #s: the question is
          -- what is on disk, and io.open("wb") on a full disk writes nothing.
          local chk = io.open(path, "rb")
          local onfile = chk and chk:seek("end") or -1
          if chk then chk:close() end
          local want = DOMSIZE[i] - nmissing
          if onfile ~= want then
            bad[#bad + 1] = string.format("%s is %d bytes on disk, expected %d",
                                           path, onfile, want)
          elseif nmissing > 0 then
            -- Recorded, never silently short. The flag values are in regs.txt.
            wrote = wrote + 1
            man[#man + 1] = string.format(
              "domain %d %-22s %d of %d -- offsets %s are not byte-addressable "
              .. "in this API; their values are in regs.txt by name",
              i, name, onfile, DOMSIZE[i], table.concat(missing, ","))
          else
            wrote = wrote + 1
            man[#man + 1] = string.format("domain %d %-22s %d", i, name, onfile)
          end
        end
      end
    end
  end
  local shot = dir .. "/frame.png"
  local okr = pcall(client.screenshot, shot)
  local sf = io.open(shot, "rb")
  local ssize = sf and sf:seek("end") or -1
  if sf then sf:close() end
  if not (okr and ssize > 8) then
    bad[#bad + 1] = string.format("screenshot is %d bytes", ssize)
  else
    man[#man + 1] = string.format("png frame.png %d", ssize)
  end
  -- CPU registers, by name where this build knows the name and by raw domain
  -- dump otherwise. The raw 12 bytes are always written: a register dump that
  -- silently lost a register is the same failure as a memory dump that did.
  local regs = {}
  for _, r in ipairs({ "PC", "A", "X", "Y", "SP", "P", "C", "Z", "I" }) do
    local ok2, v = pcall(emu.getregister, r)
    regs[#regs + 1] = string.format("%-2s %s", r,
      (ok2 and type(v) == "number") and string.format("%02X", v % 256) or "?")
  end
  local rf = io.open(dir .. "/regs.txt", "w")
  if rf then
    rf:write("frame " .. tostring(emu.framecount()) .. "\n")
    rf:write(table.concat(regs, "\n") .. "\n")
    rf:close()
    man[#man + 1] = "regs regs.txt"
    wrote = wrote + 1
  else
    bad[#bad + 1] = "could not write regs.txt"
  end
  local mf = io.open(dir .. "/manifest.txt", "w")
  if mf then
    mf:write(table.concat(man, "\n") .. "\n")
    mf:close()
  end
  if #bad > 0 then
    return "err snapshot incomplete: " .. table.concat(bad, "; ")
  end
  return string.format("ok files=%d frame=%d manifest=%s", wrote,
                       emu.framecount(), dir .. "/manifest.txt")
end

function CMD.screenshot(rest)
  local path = rest:match("^(%S+)$")
  if not path then return "err screenshot needs <path>" end
  local okr = pcall(client.screenshot, path)
  local f = io.open(path, "rb")
  local sz = f and f:seek("end") or -1
  if f then f:close() end
  if not okr then return "err client.screenshot threw" end
  if sz <= 8 then return string.format("err screenshot is %d bytes at %s", sz, path) end
  return string.format("ok png=%d %s", sz, path)
end

function CMD.save(rest)
  local path = rest:match("^(%S+)$")
  if not path then return "err save needs <path>" end
  savestate.save(path, true)
  local f = io.open(path, "rb")
  local sz = f and f:seek("end") or -1
  if f then f:close() end
  if sz <= 0 then return "err savestate is 0 bytes at " .. path end
  return string.format("ok state=%d %s", sz, path)
end

function CMD.load(rest)
  local path = rest:match("^(%S+)$")
  if not path then return "err load needs <path>" end
  local r = savestate.load(path, true)
  if not r then return "err savestate load failed: " .. path end
  return string.format("ok frame=%d", emu.framecount())
end

function CMD.fast() emu.limitframerate(false); pcall(function() client.SetSoundOn(false) end); return "ok" end
function CMD.normal() emu.limitframerate(true); pcall(function() client.SetSoundOn(true) end); return "ok" end

function CMD.reset()
  client.reboot_core()
  return string.format("ok frame=%d", emu.framecount())
end

function CMD.quit()
  send("ok")
  conn:close()
  log("bridge: quit on frame " .. tostring(emu.framecount()))
  if LOG then LOG:close() end
  client.exit()
end

-- ---------------------------------------------------------------- dispatch
-- Refuse to start if the core is missing a domain the snapshot contract names.
-- A snapshot with eight of the nine domains in it is not a snapshot.
local function checkcoverage()
  local have = {}
  for _, n in ipairs(DOMAINS) do have[n] = true end
  local missing = {}
  for n in pairs(NEEDED) do
    if not have[n] then missing[#missing + 1] = n end
  end
  if #missing > 0 then
    table.sort(missing)
    return "FATAL this core is missing required memory domains: "
           .. table.concat(missing, ", ")
         .. " (it has: " .. table.concat(DOMAINS, ", ") .. ")"
  end
  return nil
end

local fatal = checkcoverage()
log("bridge: connected on port " .. PORT .. ", domains=" .. #DOMAINS)
if fatal then
  log("bridge: " .. fatal)
  send("err " .. fatal)
  conn:close()
  client.exit()
  return
end

client.unpause()
-- Hold the main thread instead of yielding: a free-running emulator advances
-- frames with no input from us, and every input log recorded here would then be
-- a lie.
while true do
  conn:settimeout(READ_TIMEOUT)
  local line, e = conn:receive("*l")
  if line then
    log(string.format("<- %s", line))
    local cmd, rest = line:match("^(%S+)%s*(.*)$")
    if cmd == nil then
      send("err empty command")
    else
      local fn = CMD[cmd]
      if not fn then
        send("err unknown command: " .. tostring(cmd))
      else
        local okh, res = pcall(fn, rest or "")
        if not okh then
          send("err " .. tostring(res))
          log("cmd " .. cmd .. " threw: " .. tostring(res))
        else
          -- A multi-line reply (domains, snapshot manifest) is one write; the
          -- Python side splits on newlines and checks the LAST line is `ok`.
          send(res)
        end
      end
    end
  elseif e == "closed" then
    log("bridge: connection closed on frame " .. tostring(emu.framecount()))
    if LOG then LOG:close() end
    client.exit()
    break
  else
    log("bridge: receive error " .. tostring(e))
    conn:close()
    if LOG then LOG:close() end
    client.exit()
    break
  end
end
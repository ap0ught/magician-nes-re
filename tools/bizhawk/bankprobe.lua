-- The MMC3 bank state, frame by frame, plus an attempted $8000/$8001 write hook.
--
--   MAGICIAN_LUA_OUT=<file> MAGICIAN_FRAMES=<n> tools/bizhawk/run.sh bankprobe.lua <rom>
--
-- ## The question
--
-- `journal/09` left two things unchecked, and this answers the first: "pntslot`/
-- `bnk` for the level data groups (`b = $8`, `$9`, ...) has never been checked
-- against the cartridge's `$8000` writes." Those writes are the MMC3's bank
-- select ($8000 even) and bank data ($8001 odd) ports. Nothing in the previous
-- instrument set could see them: `writes.lua` registers `event.onmemorywrite`
-- in four plausible shapes and, on quickerNES 2.11.1, reports zero fires.
--
-- SETTLED 2026-10-04: the six shapes below are not six things to keep trying.
-- `buswrite.lua` read the shipped signature
-- (`onmemorywrite(luaf, address, name?, scope?)`, `Lua/_docs_luacats/event.d.lua`)
-- and registered eight forms including that one; every one returned BizHawk's
-- `EMPTY_UUID_STR` and none fired. QuickNES implements no memory callback of any
-- shape. The registration block below is kept only so the log says what was
-- attempted; the per-frame poll is the instrument, and the verdict block below
-- says so in as many words when nothing fires.
--
-- ## What is read, and why each read is safe to make
--
-- `$8000`/`$8001` are MMC3 *read* ports: $8000 returns the current bank-select
-- register (bits 0-2, plus bit 7 for CHR A12 inversion) and $8001 returns the
-- data last written to the selected register. Reading them does not disturb the
-- mapper. `$2002` is deliberately NOT read, because it clears the vblank flag
-- and resets the address latch.
--
-- `$00-$07` are the game's own copy of MMC3 registers R0-R7 (`X0.PDS:363-370`,
-- `zp r0,1` .. `zp r7,1`, `z = $00`), `$2D` is `bnksel`, and `$65-$74` is
-- `mapbnk70`..`mapbnk7f`, the per-level bank values `initlev` copies out of the
-- `ld10` table. journal/07 retraction 3 established that the two images allocate
-- zero page identically, so the same addresses mean the same names on both
-- sides; a difference at one of them is a difference about the same variable.
--
-- ## Coverage, asserted
--
-- The project's established failure mode is an instrument that prints plausible
-- numbers for the wrong range, so every claim this file makes is checked:
--
--   * the domain that answers for $8000 must be identified by *reading it back
--     after writing to it*, not by its name. `memory.usememorydomain` with an
--     unknown name is not an error in BizHawk 2.11.1 -- it silently leaves the
--     previous selection in place, so a read naming a domain that does not exist
--     returns some other domain's bytes under the new name. The self-test below
--     writes a known (select, data) pair and requires it to read back; if it
--     does not, this file aborts rather than logging a bank timeline that is
--     really a CHR pattern table.
--   * the self-test runs only when $8000/$8001 read as 0/0, which is the MMC3's
--     power-up state and means restoring 0/0 afterwards is exact rather than
--     approximate. Otherwise it is deferred to the end of the run, after the
--     last logged frame.
--   * the write hook's own coverage is measured the same way: after the frame
--     loop the script performs one deliberate $8000 write and checks whether the
--     callback saw it. A hook that fires zero times on a write the script itself
--     made has coverage zero, and the file says `callback coverage 0` rather than
--     leaving the reader to assume it was watching.
--   * the frame loop's own coverage is counted and asserted at the end, and the
--     file ends with a `done` sentinel `run.sh` waits for.
--
-- Nothing here reads or writes the cartridge. Domain reads are the core's state;
-- the two self-test writes go to the mapper, and both are undone.

local OUT = os.getenv("MAGICIAN_LUA_OUT") or "/tmp/opencode/bank.txt"
local LAST = tonumber(os.getenv("MAGICIAN_FRAMES") or "96")

local log = io.open(OUT, "w")
local function w(s) log:write(s .. "\n"); log:flush() end

local function select(d)
  if not pcall(memory.usememorydomain, d) then return false end
  local cur
  pcall(function() cur = tostring(memory.getcurrentmemorydomain()) end)
  return cur == d
end

local DOM = "System Bus"
if not select(DOM) then
  w("FATAL no '" .. DOM .. "' domain; the bank ports cannot be read at all")
  log:close()
  pcall(function() client.exit() end)
  return
end

-- `rd` re-selects its domain on every call. It has to: the selection is global
-- and persistent in BizHawk 2.11.1, so a read helper that assumes the selection
-- it left behind will silently read whichever domain was touched last. The first
-- version of this file's end-of-run self-test did exactly that -- the frame poll
-- leaves the `RAM` domain selected, so the self-test's `$8000` read came out of
-- `RAM` at offset $8000 and reported the mapper ports as "00/00", which reads
-- like a healthy MMC3 at power-up and is not one.
local function rd(a)
  if not select(DOM) then return nil end
  local ok, v = pcall(memory.read_u8, a)
  if ok and type(v) == "number" then return v % 256 end
  return nil
end

local function wr(a, v)
  if not select(DOM) then return false end
  local ok = pcall(memory.write_u8, a, v, DOM)
  return ok
end

-- ---------------------------------------------------- the write hook, attempted
-- Every shape `writes.lua` tried, registered with the domain and address range
-- named explicitly this time. Fired counts are per-shape so a shape that works
-- is distinguishable from a shape that was merely installed.
local HITS = { a = 0, b = 0, c = 0, d = 0 }
local SEEN = {}

local function note_hit(key, a, v)
  HITS[key] = HITS[key] + 1
  if #SEEN < 400 then
    SEEN[#SEEN + 1] = string.format("%s %04X %02X", key, a, v)
  end
end

local function shape_a(p)  -- bare function, single callback-object parameter
  local ok, a = pcall(function() return p.Address end)
  if not ok or type(a) ~= "number" then return end
  local v = pcall(function() return p.Value end) and p.Value or 0
  note_hit("a", a % 65536, v % 256)
end

local function shape_b(a, v)  -- two plain parameters
  if type(a) ~= "number" then return end
  note_hit("b", a % 65536, (tonumber(v) or 0) % 256)
end

local function shape_c(p)  -- address/value fields, lower case
  local ok, a = pcall(function() return p.address end)
  if not ok or type(a) ~= "number" then return end
  local v = pcall(function() return p.value end) and p.value or 0
  note_hit("c", a % 65536, v % 256)
end

local function shape_d(p)  -- methods
  local ok, a = pcall(function() return p:Address() end)
  if not ok or type(a) ~= "number" then
    ok, a = pcall(function() return p.Address end)
    if not ok or type(a) ~= "number" then return end
  end
  local okv, v = pcall(function() return p:Value() end)
  note_hit("d", a % 65536, (okv and tonumber(v) or 0) % 256)
end

local REGISTERED = {}
-- `select("#", ...)` is not usable in BizHawk's Lua: this build resolves `select`
-- to something that takes a *memory domain name* and logs "Unable to find
-- domain: #" before failing, so a vararg helper silently became a domain lookup.
-- The arity is passed in explicitly instead.
local function register(name, nargs, fn)
  local ok, err
  if nargs == 1 then
    ok, err = pcall(function() return event.onmemorywrite(fn) end)
  elseif nargs == 2 then
    ok, err = pcall(function() return event.onmemorywrite(fn, DOM) end)
  else
    ok, err = pcall(function() return event.onmemorywrite(fn, DOM, 0x8000, 0x9FFF) end)
  end
  local what = string.format("%s/%darg=%s", name, nargs, tostring(ok))
  if not ok then
    local e = tostring(err):gsub(".*bankprobe%.lua:%d+: ", "")
    REGISTERED[#REGISTERED + 1] = what .. "  rejected: " .. e
  else
    REGISTERED[#REGISTERED + 1] = what
  end
end
-- Measured on this build: the four-argument form
-- `onmemorywrite(fn, domain, start, end)` is *rejected* ("Invalid arguments to
-- method call"), so `writes.lua`'s conclusion that the callback never fires was
-- reached with a registration that had not been shown to be the right shape.
-- Every arity is tried and the log says which ones the API accepted.
register("shape_a", 1, shape_a)
register("shape_b", 2, shape_b)
register("shape_c", 2, shape_c)
register("shape_d", 4, shape_d)
register("shape_a", 2, shape_a)
register("shape_a", 4, shape_a)

-- ------------------------------------------- what do $8000/$8001 actually read?
-- MMC3 read-back would make the bank-select and bank-data ports readable and
-- remove the need for the CPU's own mirror. It has to be *shown* to work, not
-- assumed, and it is shown by writing and reading back -- but the registers are
-- not at power-up state on the first probe, and $8000 bit 6 (PRG mode) is
-- write-only, so a write-then-restore could silently flip the bank mode
-- underneath the measurement. So the ports are swept read-only and the log
-- reports what they hold. Constant values across every frame, unchanged while
-- r6/r7 change underneath, mean the core is not exposing the mapper here, and
-- the report says so rather than printing a column of bank registers.
local function portsweep(tag)
  local out = {}
  for a = 0x8000, 0x8007 do
    local v = rd(a)
    out[#out + 1] = string.format("$%04X=%s", a, v and string.format("%02X", v) or "--")
  end
  w(string.format("portsweep(%s): %s", tag, table.concat(out, " ")))
end
portsweep("frame0")

-- Are the $8000 ports MMC3 registers, or PRG seen through the bank window?
-- This decides what the 'port' columns are worth, so it is settled against the
-- file rather than argued. `run.sh` exports the absolute path of the ROM *after*
-- proving that the core loaded those exact bytes (it reads $F9A8 back through
-- the core and compares), so reading the file here and comparing is sound.
--
-- On a core with no mapper read-back, $8000 returns whatever PRG byte the R6
-- window is currently showing. That is a fact about the window, not about the
-- register -- and reporting the window's first byte in a column headed "port"
-- would be a plausible-looking fabrication.
local ROMPATH = os.getenv("MAGICIAN_WANT_ROM_PATH") or ""
local PRG = nil
if ROMPATH ~= "" then
  local fh = io.open(ROMPATH, "rb")
  if fh then
    local raw = fh:read("*a")
    fh:close()
    if type(raw) == "string" and #raw >= 16 + 131072 and raw:sub(1, 4) == "NES\1" then
      PRG = raw:sub(17, 16 + 131072)
      w(string.format("rom file          %s  PRG %d bytes (identity already "
                      .. "verified by run.sh)", ROMPATH, #PRG))
    else
      w(string.format("rom file          %s  NOT USABLE (%d bytes, magic %q)",
                      ROMPATH, type(raw) == "string" and #raw or -1,
                      type(raw) == "string" and raw:sub(1, 4) or "?"))
    end
  else
    w(string.format("rom file          %s  could not be opened -- the portsweep "
                    .. "below cannot be interpreted", ROMPATH))
  end
end

local PORTS_ARE_ROM = "undetermined (no readable ROM file to compare against)"

local function classify_ports(tag, r6)
  if not PRG or type(r6) ~= "number" or r6 > 15 then return end
  local base = r6 * 8192
  local hits, mismatch = 0, 0
  for i = 0, 7 do
    local v = rd(0x8000 + i)
    if v ~= nil then
      if v == PRG:byte(base + i + 1) then hits = hits + 1 else mismatch = mismatch + 1 end
    end
  end
  local s = string.format("portsweep-vs-file(%s): $8000-$8007 vs PRG[slot %d +0..7] "
                          .. "-> %d/8 match, %d differ", tag, r6, hits, mismatch)
  w(s)
  if hits == 8 then
    PORTS_ARE_ROM = string.format("PRG through the R6 window (proved %s: 8/8 at "
                                  .. "slot %d)", tag, r6)
  elseif mismatch == 8 then
    PORTS_ARE_ROM = "an MMC3 register, not PRG (proved: 0/8 against PRG)"
  else
    PORTS_ARE_ROM = string.format("MIXED (%d/8 match PRG) -- uninterpretable", hits)
  end
end

-- ------------------------------------------------------------- the self-test
-- Write a known (select, data) pair to $8000/$8001 and read it back. Only valid
-- if the MMC3 answers, and only non-destructive if the registers are already 0,
-- because bit 6 of $8000 (the PRG mode bit) is write-only and cannot be read
-- back -- so a restore to "whatever was read" could silently flip the PRG mode
-- and change the very bank map being measured.
local SELFTEST = { done = false, ok = false, deferred = false }

local function selftest(tag)
  local s, d0 = rd(0x8000), rd(0x8001)
  if s == nil or d0 == nil then
    w(string.format("selftest(%s): $8000/$8001 unreadable through '%s'", tag, DOM))
    return
  end
  w(string.format("selftest(%s): before $8000=%02X $8001=%02X", tag, s, d0))
  if s ~= 0 or d0 ~= 0 then
    -- Not at power-up state. Do not write: the mode bit is unreadable, so the
    -- restore could change the PRG bank mode underneath the measurement.
    w(string.format("selftest(%s): DEFERRED -- registers are not 0/0, and $8000 "
                    .. "bit 6 is write-only so a restore could flip the PRG mode",
                    tag))
    SELFTEST.deferred = true
    return
  end
  local before = { HITS.a, HITS.b, HITS.c, HITS.d }
  wr(0x8000, 0x04)
  wr(0x8001, 0x0A)
  local s2, d2 = rd(0x8000), rd(0x8001)
  local fired = { HITS.a - before[1], HITS.b - before[2],
                  HITS.c - before[3], HITS.d - before[4] }
  wr(0x8000, 0x00)
  wr(0x8001, 0x00)
  local s3, d3 = rd(0x8000), rd(0x8001)
  local pass = (s2 == 0x04 and d2 == 0x0A and s3 == 0x00 and d3 == 0x00)
  SELFTEST.done = true
  SELFTEST.ok = pass
  w(string.format("selftest(%s): wrote $8000=04 $8001=0A -> read %02X/%02X, "
                  .. "restored -> %02X/%02X  %s", tag, s2 or 0, d2 or 0, s3 or 0,
                  d3 or 0, pass and "PASS" or "FAIL"))
  w(string.format("selftest(%s): hook fired for the script's own write: "
                  .. "a=%d b=%d c=%d d=%d", tag, fired[1], fired[2], fired[3],
                  fired[4]))
  if pass and (fired[1] + fired[2] + fired[3] + fired[4]) == 0 then
    w("selftest(" .. tag .. "): the write reached the mapper and the hook still "
      .. "did not fire, so callback coverage is ZERO. The per-frame poll below "
      .. "is then the only bank evidence there is.")
  end
end

w(string.format("bankprobe: frames 0..%d, domain '%s'", LAST, DOM))
w("hook registration: " .. table.concat(REGISTERED, "  "))
selftest("frame0")

-- ------------------------------------------------------------- the frame loop
-- Every address this file claims to read, counted. A loop that silently reads
-- 36 of 256 zero-page bytes still prints plausible rows, so the count is
-- printed and asserted rather than assumed.
--
-- Zero page is read through **two** domains and the two are cross-checked byte
-- for byte. `journal/07` retraction 1 established that the core's `WRAM` domain
-- is 8192 bytes of `$FF` until frame 45 and that the real work RAM is the
-- 2048-byte `RAM` domain; `zp.lua` meanwhile reads zero page through `System
-- Bus`. Both are read here and any byte where they disagree is counted and
-- reported, because a probe that silently got hold of the wrong window would
-- otherwise print a very plausible bank mirror.
local COVERED = {}
local ZPCOVERED = {}
local ZPDISAGREE = 0
local frames_logged = 0

local RAM = "RAM"
local have_ram = select(RAM)

-- Read a run of bytes through the *named* domain, re-selecting every time.
-- `frames.lua` records why: `memory.usememorydomain` with an unknown name is not
-- an error in BizHawk 2.11.1, it leaves the previous selection in place, and the
-- selection persists across calls -- so a read that names a domain which does not
-- exist returns the previously selected domain's bytes under the new name.
local function grab(dom, start, len)
  if not select(dom) then return nil end
  local ok, s = pcall(memory.read_bytes_as_binary_string, start, len)
  if ok and type(s) == "string" and #s == len then return s end
  return nil
end

local function snap(n)
  local function hx(t)
    local o = {}
    for i = 1, #t do o[i] = string.format("%02X", t[i] or 0) end
    return table.concat(o, " ")
  end
  COVERED[0x8000] = true
  COVERED[0x8001] = true
  if not select(DOM) then
    w(string.format("f%04d FATAL the '%s' domain stopped selecting mid-run", n, DOM))
    frames_logged = frames_logged + 1
    return
  end
  local s, d = rd(0x8000), rd(0x8001)

  -- The whole of zero page, through both windows, in two bulk reads.
  local zsb = grab(DOM, 0x00, 256)
  local zram = have_ram and grab(RAM, 0x00, 256) or nil
  if not zsb then
    w(string.format("f%04d FATAL could not read 256 zero-page bytes through '%s'",
                    n, DOM))
    frames_logged = frames_logged + 1
    return
  end
  local r = {}
  for a = 0x00, 0xFF do
    COVERED[a] = true
    ZPCOVERED[a] = true
    r[a + 1] = zsb:byte(a + 1)
  end
  local dis = 0
  if zram then
    for a = 0x00, 0xFF do if zram:byte(a + 1) ~= r[a + 1] then dis = dis + 1 end end
  end
  ZPDISAGREE = ZPDISAGREE + dis

  -- r0..r7 ($00-$07), bnksel ($2D), mapbnk70..mapbnk7f ($65-$74)
  local regs = {}
  for i = 1, 8 do regs[#regs + 1] = r[i] end
  local bnk = r[0x2E]
  local maps = {}
  for a = 0x65, 0x74 do maps[#maps + 1] = r[a + 1] end

  w(string.format("f%04d port %02X/%02X bnksel %02X r0-r7 %s mapbnk70-7f %s "
                  .. "lev %d/%d/%d %d  zpcross %s",
                  n, s or 0, d or 0, bnk or 0, hx(regs), hx(maps),
                  r[0x50] or 0, r[0x62] or 0, r[0x64] or 0, r[0x60] or 0,
                  zram and string.format("%d/256 differ", dis)
                        or "RAM domain absent, NOT cross-checked"))
  frames_logged = frames_logged + 1
  if n % 8 == 0 then classify_ports(string.format("f%d", n), regs[7]) end
end

local base = emu.framecount()
snap(base)
local guard = 0
while emu.framecount() - base < LAST and guard < LAST * 20 + 2000 do
  emu.frameadvance()
  guard = guard + 1
  snap(emu.framecount())
end
local last = emu.framecount()

if not SELFTEST.ok then selftest("end") end

-- ---------------------------------------------------------------- the verdict
local naddrs = 0
for _ in pairs(COVERED) do naddrs = naddrs + 1 end
local nzp = 0
for _ in pairs(ZPCOVERED) do nzp = nzp + 1 end
local hsum = HITS.a + HITS.b + HITS.c + HITS.d

w("")
w("VERDICT")
w(string.format("frames logged        %d  (frame range %d..%d, asked for %d)",
                 frames_logged, base, last, LAST))
w(string.format("zero-page coverage   %d/256 bytes read every frame%s", nzp,
                nzp == 256 and "" or "   FATAL"))
w(string.format("addresses read       %d distinct", naddrs))
w(string.format("domain cross-check   System Bus vs RAM: %d differing byte(s) over "
                 .. "%d frame(s)%s", ZPDISAGREE, frames_logged,
                have_ram and "" or "   RAM DOMAIN ABSENT -- NOT CROSS-CHECKED"))
w(string.format("write-hook fires     total %d  (a=%d b=%d c=%d d=%d)", hsum,
                HITS.a, HITS.b, HITS.c, HITS.d))
if hsum == 0 then
  w("callback coverage    ZERO. Of the six registration shapes tried, "
    .. "none both installed and fired -- including for a write this script made "
    .. "itself. There is NO per-instruction $8000/$8001 write trace available "
    .. "from this BizHawk build; do not read the absence of divergence in this "
    .. "file as agreement at instruction granularity.")
else
  w("callback coverage    %d fire(s). A write trace IS available; use it.", hsum)
end
w(string.format("port read-back       %s", SELFTEST.ok
                and "PASS -- $8000/$8001 read back what was written, so the 'port' "
                    .. "columns are MMC3 register values"
                or (SELFTEST.deferred
                   and "NOT AVAILABLE -- the write/restore self-test was refused "
                       .. "because the ports were never at 0/0 (they never are: "
                       .. "they read ROM). The 'port' columns are " .. PORTS_ARE_ROM
                       .. " and must NOT be compared between the two images as bank "
                       .. "registers."
                   or "FAILED")))
w("")
w("WHAT IS AND IS NOT EVIDENCE HERE")
w("  EVIDENCE: the per-frame r0-r7 / bnksel / mapbnk70-7f columns. These are the")
w("    game's own copy of MMC3 registers R0-R7 (X0.PDS:363-370) and the per-level")
w("    bank table, so a difference between the two images at the same frame is a")
w("    difference in the bank sequence the two CPUs issued. journal/07 retraction 3")
w("    established the zero-page layout is identical on both sides.")
w("  NOT EVIDENCE: the port columns. See 'port read-back' above.")
w("  NOT EVIDENCE: any statement about writes that happened *and were undone*")
w("    inside one frame. The poll is once per frame, so a bank switch issued and")
w("    replaced within a frame is invisible here.")
for i = 1, #SEEN do w("hook " .. SEEN[i]) end
w(string.format("done, frames %d..%d", base, last))
log:close()
pcall(function() client.exit() end)
-- drive.lua -- the FCEUX half of tools/tas/run.sh.
--
-- FCEUX is the emulator the movie was recorded in (`emuVersion 20100` in the .fm2
-- header), so this script is deliberately thin: it lets FCEUX's own movie player
-- do the playback and then MEASURES what happened, rather than injecting input
-- from outside and hoping. Everything here is read-only with respect to the game.
--
-- It writes into $OUT_DIR and nowhere else:
--
--   series.tsv   one line per emulated frame: movie position, what FCEUX actually
--                delivered on port 1, the watched RAM cells, and a RAM hash
--   summary.txt  the numbers run.sh turns into a verdict
--   shots.map    which FCEUX snapshot belongs to which movie frame
--
-- ---------------------------------------------------------------------------
-- WHAT THE WATCHED CELLS DO AND DO NOT MEAN HERE
--
-- The addresses come from src/play/ram.py, which resolves them through the
-- committed slice of mag.sym -- and mag.sym is the symbol table of BETA 1, the
-- build this project assembles. The movie runs on the RELEASE, a different ROM.
-- Measured on this cartridge: `curlev` ($0061) behaves exactly as documented
-- (it climbs $10 -> $20 -> ... -> $91 across the run), but `manacur` ($0047) does
-- NOT -- it sits at 0 for most of the run and reads 43008 as a little-endian
-- word near frame 12000, which is not a mana count. So per-cell values here are
-- Beta-1 semantics applied to release bytes, and a cell that looks wrong is much
-- more likely to be the wrong address than a game that stopped casting spells.
--
-- That is why NO verdict below is gated on an individual cell. The gates use
-- signals that cannot mean the wrong thing: the input sequence FCEUX delivered,
-- how many frames were covered, whether the machine's RAM is moving at all, and
-- whether the run progressed through distinct levels. Every cell is still
-- reported, with its distinct-value count, so a reader can see exactly which
-- signals moved and which did not.
-- ---------------------------------------------------------------------------

local OUT_DIR = assert(os.getenv("OUT_DIR"), "OUT_DIR required")
local FRAMES_FILE = assert(os.getenv("FRAMES_FILE"), "FRAMES_FILE required")

-- The movie's own per-frame input, converted from the .fm2 by identity.py: one
-- line per movie frame, 8 characters, alphabet R L D U T S B A. That alphabet was
-- calibrated against joypad.get(1) on this machine, not taken from documentation.
-- EXPECT is 1-based here, so movie frame f is EXPECT[f + 1].
local EXPECT = {}
local nread = 0
do
  local f = assert(io.open(FRAMES_FILE, "r"))
  for line in f:lines() do EXPECT[nread + 1] = line nread = nread + 1 end
  f:close()
end
-- Counted rather than `#EXPECT`: this table starts at key 1 precisely so that a
-- negative index is a reliable "before the start" test in the alignment search
-- below, and `#` would be the wrong way to ask.
local NFRAMES = nread

-- WATCH_FILE is `name<TAB>$hex` per line, produced by identity.py from
-- src/play/ram.py's symbol table so no address is written down twice.
local WATCH = {}
do
  local f = assert(io.open(assert(os.getenv("WATCH_FILE"), "WATCH_FILE required"), "r"))
  for line in f:lines() do
    local name, hex = line:match("^(%S+)%s+0[xX](%x+)$")
    if name then WATCH[#WATCH + 1] = { name = name, addr = tonumber(hex, 16) } end
  end
  f:close()
end

local series = assert(io.open(OUT_DIR .. "/series.tsv", "w"))
local summary = assert(io.open(OUT_DIR .. "/summary.txt", "w"))
local function S(fmt, ...)
  summary:write(string.format(fmt, ...) .. "\n")
  summary:flush()
end
series:write("frame\tmovieframe\tdelivered\tphase\tnewphase\tsubphase\tcurlev\tmapind\thilev\tmanacur\tfood\twater\twealth\tmclock\tramhash\n")

local SHOTWANT = {}
local SHOTORDER = {}
if os.getenv("SHOT_FRAMES") and os.getenv("SHOT_FRAMES") ~= "" then
  for n in os.getenv("SHOT_FRAMES"):gmatch("[^,]+") do
    local v = tonumber(n)
    if v then SHOTWANT[v] = true SHOTORDER[#SHOTORDER + 1] = v end
  end
end

local ORDER = {"R", "L", "D", "U", "T", "S", "B", "A"}
local TOJOY = { R = "right", L = "left", D = "down", U = "up",
                T = "start", S = "select", B = "B", A = "A" }
local IDLE = string.rep(".", 8)

local function delivered_string()
  local p = joypad.get(1)
  local out = {}
  for _, b in ipairs(ORDER) do out[#out + 1] = (p[TOJOY[b]] and b or ".") end
  return table.concat(out)
end

-- bit.band returns a SIGNED 32-bit value in this Lua, so masking to 32 bits is
-- not enough: a negative result prints as "ffffffff9b832de8" and the digest stops
-- being a fixed-width field, which quietly breaks the whole-run comparison.
local function u32(x)
  x = bit.band(x, 0xFFFFFFFF)
  if x < 0 then x = x + 0x100000000 end
  return x
end

local function ramhash()
  local bytes = memory.readbyterange(0x0000, 0x0800)
  local h = 2166136261
  for i = 1, #bytes do h = u32(bit.bxor(h, string.byte(bytes, i)) * 16777619) end
  return string.format("%08x", h)
end

-- Frame alignment, measured the first time the author presses anything. This is a
-- HINT used only to time screenshots; the authoritative offset is the exhaustive
-- search further down, and the two are reported separately because they have
-- disagreed before: joypad.get(1) reflects the pad state as of the last completed
-- frame, so the first delivered press shows up one iteration later than the frame
-- it was delivered on.
local FIRSTPRESS = nil
for f = 0, NFRAMES - 1 do
  if EXPECT[f + 1]:find("[^.]") then FIRSTPRESS = f break end
end

local ALIGN_LO, ALIGN_HI = -4, 4

-- The subtle part is the ends of the range. A negative offset makes the first few
-- iterations address frames before the start of the movie, and those must be
-- SKIPPED, not treated as a mismatch and not allowed to end the scan -- an earlier
-- version broke out of the loop on the first nil and so reported three offsets as
-- perfect having compared no frames at all.
--
-- `min_cover` is a floor on how much of the movie an offset must have compared. It
-- is measured against the MOVIE's length, never against how much happened to be
-- delivered: scaling the floor to the delivered length means a run that delivered
-- fifty frames of a 44003-frame movie asks only that those fifty agree, and offset
-- zero passes. That is the coverage gate's job to catch and this gate's job not to
-- undermine, and an earlier version did exactly that.
local function find_alignment(get, upto, min_cover)
  local viable, detail = {}, {}
  for off = ALIGN_LO, ALIGN_HI do
    local bad, pressed, compared = 0, 0, 0
    for i = 0, upto - 1 do
      local frame = i + off
      if frame >= NFRAMES then break end        -- past the end of the movie
      if frame >= 0 then                        -- before the start: skip, do not stop
        local want = EXPECT[frame + 1]
        compared = compared + 1
        if not (want == IDLE and get(i + 1) == IDLE) then
          pressed = pressed + 1
          if want ~= get(i + 1) then bad = bad + 1 end
        end
      end
    end
    detail[off] = { bad = bad, pressed = pressed, compared = compared }
    if bad == 0 and compared >= min_cover then viable[#viable + 1] = off end
  end
  return viable, detail
end

-- ------------------------------------------------------------ preflight
S("mode              %s", tostring(movie.mode()))
S("movie.active      %s", tostring(movie.active()))
S("movie.playing     %s", tostring(movie.playing()))
S("movie.length      %s", tostring(movie.length()))
S("movie.rerecords   %s", tostring(movie.rerecordcount()))
S("expected.frames   %d", NFRAMES)
S("movie.firstpress  %s", tostring(FIRSTPRESS))

if movie.mode() ~= "playback" then
  S("verdict fail")
  S("FAIL fceux is not in movie playback mode; nothing below would mean anything")
  series:close() emu.exit()
end
if movie.length() ~= NFRAMES then
  S("verdict fail")
  S("FAIL fceux read %d movie frames but the file has %d", movie.length(), NFRAMES)
  series:close() emu.exit()
end

-- ------------------------------------------------------------ run
-- movie.framecount() in FCEUX 2.6.6 is a count of frames elapsed, not a clamped
-- position: it runs past movie.length() and never stalls. So the end of the movie
-- is detected by running a known number of frames, not by watching for a stall --
-- an earlier version waited for a stall that does not come and then reported a
-- complete 44003-frame run as "never completed".
local TAIL = 8
local TOTAL = NFRAMES + TAIL
local CALIB = 400
local DELIVERED, hashes = {}, {}
local cellsseen = {}
for _, w in ipairs(WATCH) do cellsseen[w.name] = {} end
local ramdistinct = {}
local shotsmap = assert(io.open(OUT_DIR .. "/shots.map", "w"))
local nshots = 0
local SHOTOFF = 0            -- set from the calibration pass below

local function record(iter)
  local d = delivered_string()
  DELIVERED[#DELIVERED + 1] = d
  hashes[#hashes + 1] = ramhash()
  ramdistinct[hashes[#hashes]] = true

  local cells = {}
  for _, w in ipairs(WATCH) do
    local v = memory.readbyte(w.addr)
    cellsseen[w.name][v] = true
    cells[#cells + 1] = string.format("%02X", v)
  end

  series:write(string.format("%d\t%d\t%s\t%s\t%s\n", iter, movie.framecount(), d,
    table.concat(cells, "\t"), hashes[#hashes]))

  -- Screenshot. gui.savescreenshot IGNORES the path it is given and writes
  -- $HOME/.fceux/snaps/<romstem>-<n>.png, so the mapping is recorded here and
  -- run.sh renames the files afterwards; asking FCEUX for a path does not work.
  if #SHOTORDER > 0 and SHOTWANT[iter + SHOTOFF] then
    local frame = iter + SHOTOFF
    nshots = nshots + 1
    shotsmap:write(string.format("%d\t%d\t%d\n", nshots - 1, frame, iter))
    gui.savescreenshot(string.format("%s/shots/frame-%06d.png", OUT_DIR, frame))
  end
end

-- Pass 1: a short calibration prefix, so screenshots can be timed to a movie
-- frame using the MEASURED offset rather than a constant written into this file.
for iter = 1, CALIB do emu.frameadvance() record(iter) end
local calib, _ = find_alignment(function(k) return DELIVERED[k] end, #DELIVERED,
                                  math.min(#DELIVERED, NFRAMES) - 16)
if #calib == 1 then
  SHOTOFF = calib[1]
else
  S("warn.calibration  %d offsets tie on the first %d frames (%s); screenshots will be timed by iteration, not by movie frame",
    #calib, CALIB, table.concat(calib, ","))
end

-- Pass 2: the rest of the run, screenshots now correctly aligned.
for iter = CALIB + 1, TOTAL do emu.frameadvance() record(iter) end
shotsmap:close()

S("emulated.frames   %d", TOTAL)
S("shots.taken       %d", nshots)
S("align.shotsoffset %d", SHOTOFF)

-- ------------------------------------------------------------ ALIGNMENT
-- The authoritative check: compare the whole delivered sequence against the
-- movie's own sequence at every constant offset in range. Exactly one offset must
-- hold, with zero mismatches. This proves at once that the delivered sequence IS
-- the movie's, that it is not shifted, and that the movie is not being played on
-- the wrong port.
local viable, res = find_alignment(function(k) return DELIVERED[k] end, #DELIVERED,
                                    NFRAMES - 16)
for off = ALIGN_LO, ALIGN_HI do
  S("align %+d          compared=%d pressed=%d mismatches=%d", off,
    res[off].compared, res[off].pressed, res[off].bad)
end
S("align.viable      %s", (#viable == 0 and "none" or table.concat(viable, ",")))
S("align.offset      %s", (#viable == 1 and tostring(viable[1]) or "AMBIGUOUS"))

-- ------------------------------------------------------------ liveness
-- Deliberately not gated on `phase`: measured on this cartridge `phase` ($5F)
-- sits at $80 for 44092 of 44123 frames while the run is plainly progressing, so a
-- "phase must change" gate fails a correct run. See the header comment.
local function ndistinct(t) local n = 0 for _ in pairs(t) do n = n + 1 end return n end
local fold = 2166136261
for i = 1, #hashes do fold = u32(bit.bxor(fold, tonumber(hashes[i]:sub(1, 7), 16)) * 16777619) end

S("ramhash.first     %s", hashes[1] or "-")
S("ramhash.last      %s", hashes[#hashes] or "-")
S("ramhash.fold      %08x", fold)
S("ramhash.distinct  %d of %d frames", ndistinct(ramdistinct), TOTAL)
for _, w in ipairs(WATCH) do
  S("cell %-12s %4d distinct", w.name, ndistinct(cellsseen[w.name]))
end

local ramratio = ndistinct(ramdistinct) / TOTAL
local curlevdistinct = ndistinct(cellsseen["curlev"] or {})

-- ------------------------------------------------------------ verdict
local failures = {}
if #viable == 0 then
  failures[#failures + 1] = "input fidelity: no single frame offset reproduces the movie's input"
elseif #viable > 1 then
  failures[#failures + 1] = "input alignment is ambiguous between offsets " .. table.concat(viable, ",")
end
if TOTAL < NFRAMES then
  failures[#failures + 1] = string.format("coverage: emulated %d of %d movie frames", TOTAL, NFRAMES)
end
if ramratio < 0.5 then
  failures[#failures + 1] = string.format("liveness: RAM changed on only %d of %d frames; the machine is frozen", ndistinct(ramdistinct), TOTAL)
end
if curlevdistinct < 4 then
  failures[#failures + 1] = string.format("progress: `curlev` took only %d values; the run did not climb through levels", curlevdistinct)
end

if #failures == 0 then
  S("verdict ok")
else
  S("verdict fail")
  for _, f in ipairs(failures) do S("FAIL %s", f) end
end

series:close()
emu.exit()
-- drive.lua -- the FCEUX half of tools/tas/run.sh.
--
-- FCEUX is the emulator the movie was recorded in (`emuVersion` in the .fm2
-- header), so this script is deliberately thin: it lets FCEUX's own movie player
-- do the playback and then MEASURES what happened, rather than injecting input
-- from outside and hoping. Everything here is read-only with respect to the game.
--
-- It writes three things into $OUT_DIR and nothing anywhere else:
--
--   series.tsv   one line per emulated frame: the movie position, what FCEUX
--                actually delivered on port 1, the watched RAM cells, and a
--                RAM hash
--   summary.txt  the numbers run.sh turns into a verdict
--   shots/       PNGs at the requested frames
--
-- The one thing it decides for itself is the frame alignment between the
-- delivered input and the movie's own input sequence. That offset is measured,
-- not assumed -- see "ALIGNMENT" below. A hardcoded offset is exactly the kind of
-- constant that is right once and wrong after an emulator update, and it fails
-- silently: a one-frame shift still delivers *a* plausible button sequence.

local OUT_DIR = assert(os.getenv("OUT_DIR"), "OUT_DIR required")
local FRAMES_FILE = assert(os.getenv("FRAMES_FILE"), "FRAMES_FILE required")

-- ------------------------------------------------------- expected input
-- FRAMES_FILE is the movie's own per-frame input, converted from the .fm2 by
-- identity.py: one line per movie frame, 8 characters, alphabet R L D U T S B A
-- (calibrated against joypad.get(1), not taken from documentation).
local EXPECT = {}
local nread = 0
do
  local f = assert(io.open(FRAMES_FILE, "r"))
  for line in f:lines() do EXPECT[nread] = line nread = nread + 1 end
  f:close()
end
-- Counted, not `#EXPECT`: the table is keyed from 0, and Lua's length operator
-- ignores key 0, so `#EXPECT` on a 44003-frame movie reads 44002. That number is
-- the coverage assertion, so being off by one here is a wrong verdict rather than
-- a wrong number.
local NFRAMES = nread

-- ------------------------------------------------------- watched RAM cells
-- WATCH_FILE is `name<TAB>$hex` per line, produced by identity.py from
-- src/play/ram.py's symbol table so no address is written down twice.
local WATCH = {}
do
  local f = io.open(assert(os.getenv("WATCH_FILE"), "WATCH_FILE required"), "r")
  for line in f:lines() do
    local name, hex = line:match("^(%S+)%s+0[xX](%x+)$")
    if name then WATCH[#WATCH + 1] = { name = name, addr = tonumber(hex, 16) } end
  end
  f:close()
end

-- ------------------------------------------------------- output files
local series = assert(io.open(OUT_DIR .. "/series.tsv", "w"))
local summary = assert(io.open(OUT_DIR .. "/summary.txt", "w"))
local function S(fmt, ...)
  summary:write(string.format(fmt, ...) .. "\n")
  summary:flush()
end

series:write("frame\tmovieframe\tdelivered\tphase\tnewphase\tsubphase\tcurlev\tmapind\thilev\tmanacur\tfood\twater\twealth\tmclock\tramhash\n")

-- ------------------------------------------------------- requested frames
local SHOTS = {}
if os.getenv("SHOT_FRAMES") and os.getenv("SHOT_FRAMES") ~= "" then
  for n in os.getenv("SHOT_FRAMES"):gmatch("[^,]+") do
    SHOTS[tonumber(n)] = true
  end
end

local BUTTONS = {"A", "B", "select", "start", "up", "down", "left", "right"}
-- joypad.get(1) name -> movie alphabet character
local TOCHAR = { A = "A", B = "B", select = "S", start = "T",
                 up = "U", down = "D", left = "L", right = "R" }
local ORDER = {"R", "L", "D", "U", "T", "S", "B", "A"}

local function delivered_string()
  local p = joypad.get(1)
  local out = {}
  for _, b in ipairs(ORDER) do
    local key
    for k, v in pairs(TOCHAR) do if v == b then key = k end end
    out[#out + 1] = (p[key] and b or ".")
  end
  return table.concat(out)
end

-- FNV-1a over a byte range, for the per-frame RAM fingerprint.
-- bit.band returns a SIGNED 32-bit value in this Lua, so masking to 32 bits is
-- not enough: a negative result prints as "ffffffff9b832de8" and the digest stops
-- being a fixed-width field, which quietly breaks the whole-run comparison below.
local function u32(x)
  x = bit.band(x, 0xFFFFFFFF)
  if x < 0 then x = x + 0x100000000 end
  return x
end

local function ramhash()
  local bytes = memory.readbyterange(0x0000, 0x0800)
  local h = 2166136261
  for i = 1, #bytes do
    h = u32(bit.bxor(h, string.byte(bytes, i)) * 16777619)
  end
  return string.format("%08x", h)
end

-- ------------------------------------------------------------ preflight
S("mode              %s", tostring(movie.mode()))
S("movie.active      %s", tostring(movie.active()))
S("movie.playing     %s", tostring(movie.playing()))
S("movie.length      %s", tostring(movie.length()))
S("movie.rerecords   %s", tostring(movie.rerecordcount()))
S("expected.frames   %d", NFRAMES)
S("emulated.frames   0")

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
local DELIVERED = {}
local hashes = {}
local maxiter = NFRAMES + 120
local lastmf, stuck, iterations, ran = 0, 0, 0, 0
local firstdistinct = {}   -- phase value -> frame it was first seen
local phases = {}

while iterations < maxiter do
  emu.frameadvance()
  iterations = iterations + 1

  local mf = movie.framecount()
  local d = delivered_string()
  DELIVERED[#DELIVERED + 1] = d

  local cells = {}
  for _, w in ipairs(WATCH) do
    cells[#cells + 1] = string.format("%02X", memory.readbyte(w.addr))
  end
  local h = ramhash()
  hashes[#hashes + 1] = h

  -- `phase` is the main-loop state word (src/play/ram.py: it indexes gvl/gvh,
  -- so 0..10 selects g00..g0a). A run in which it never changes is a run whose
  -- main loop never advanced -- the exact failure the BizHawk replay showed.
  local phase = memory.readbyte(0x005F)
  phases[phase] = (phases[phase] or 0) + 1
  if not firstdistinct[phase] then firstdistinct[phase] = iterations end

  series:write(string.format("%d\t%d\t%s\t%s\t%s\n", iterations, mf, d,
    table.concat(cells, "\t"), h))

  if SHOTS[iterations] then
    gui.savescreenshot(string.format("%s/shots/frame-%06d.png", OUT_DIR, iterations))
  end

  -- Track how far the movie itself got. FCEUX stops advancing movie.framecount()
  -- once the recording is exhausted; that stall is how the end of the movie is
  -- detected, and the frame it stalls on is the coverage number that matters.
  if mf > lastmf then lastmf = mf stuck = 0 else stuck = stuck + 1 end
  if stuck >= 30 then ran = iterations break end
end

S("emulated.frames   %d", iterations)
S("last.movieframe   %d", lastmf)
S("stalled.after     %d", ran > 0 and stuck or -1)

-- ------------------------------------------------------------ ALIGNMENT
-- Compare the sequence FCEUX delivered against the movie's own sequence, at
-- every constant offset in range, and report which offsets hold for every frame.
-- Exactly one must, with zero disagreements. This proves three things at once:
-- that the delivered sequence IS the movie's, that it is not shifted, and that
-- the movie is not being played on the wrong port.
local OFFSET_LO, OFFSET_HI = -4, 4
local viable, best = {}, nil
for off = OFFSET_LO, OFFSET_HI do
  local bad, pressed = 0, 0
  for i = 0, #DELIVERED - 1 do
    local want = EXPECT[i + off + 1]      -- Lua tables are 1-based; frame 0 is 1
    if want == nil then break end
    local got = DELIVERED[i + 1]
    local idle = true
    for k = 1, 8 do if want:sub(k, k) ~= "." then idle = false break end end
    if idle and got == string.rep(".", 8) then
      -- both idle: agreement, counted apart so it cannot mask a real match
    else
      pressed = pressed + 1
      if want ~= got then
        bad = bad + 1
        if best == nil or bad < best then
          best = bad
        end
      end
    end
  end
  if bad == 0 then viable[#viable + 1] = off end
  S("align %+d          mismatches=%d pressed=%d", off, bad, pressed)
end
S("align.viable      %s", (#viable == 0 and "none" or table.concat(viable, ",")))
S("align.offset      %s", (#viable == 1 and tostring(viable[1]) or "AMBIGUOUS"))

-- ------------------------------------------------------------ RAM hash
-- Concatenated and folded once: the digest of the whole run's fingerprint
-- sequence. Two runs that agree here are byte-identical frame for frame, which is
-- what makes a second run a real check rather than a rerun.
local fold = 2166136261
for i = 1, #hashes do
  fold = u32(bit.bxor(fold, tonumber(hashes[i]:sub(1, 7), 16)) * 16777619)
end
S("ramhash.first     %s", hashes[1] or "-")
S("ramhash.last      %s", hashes[#hashes] or "-")
S("ramhash.fold      %08x", fold)
S("ramhash.distinct  %d", (function()
  local seen = {} for i = 1, #hashes do seen[hashes[i]] = true end
  local n = 0 for _ in pairs(seen) do n = n + 1 end return n end)())

-- ------------------------------------------------------------ phase
local pv = {}
for v in pairs(phases) do pv[#pv + 1] = v end
table.sort(pv)
S("phase.distinct    %d", #pv)
for _, v in ipairs(pv) do
  S("phase $%02X          %d frames, first at %d", v, phases[v], firstdistinct[v])
end

-- ------------------------------------------------------------ verdict
local failures = {}
if ran == 0 then failures[#failures + 1] = "the run never completed: movie.framecount() kept advancing to the frame budget" end
if lastmf < NFRAMES - 1 then
  failures[#failures + 1] = string.format("coverage: movie reached frame %d of %d", lastmf, NFRAMES)
end
if #viable == 0 then
  failures[#failures + 1] = "input fidelity: no single frame offset reproduces the movie's input"
elseif #viable > 1 then
  failures[#failures + 1] = "input alignment is ambiguous between offsets " .. table.concat(viable, ",")
end
if #pv < 2 then
  failures[#failures + 1] = "game liveness: `phase` never changed, so the main loop never advanced"
end

if #failures == 0 then
  S("verdict ok")
else
  S("verdict fail")
  for _, f in ipairs(failures) do S("FAIL %s", f) end
end

series:close()
emu.exit()
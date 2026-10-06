-- Dump the PPU state a title screen is made of, at an exact frame.
--
--   MAGICIAN_TITLE_OUT=<dir> MAGICIAN_FRAMES=<n> MAGICIAN_INPUT=<spec> \
--     tools/bizhawk/run.sh tools/bizhawk/title.lua <rom>
--
-- Writes <dir>/nt0.bin (1024 tile bytes), <dir>/nt0attr.bin (64 attribute
-- bytes), <dir>/pal.bin (32 palette bytes), <dir>/chr.bin (8192 bytes of the
-- pattern table the screen is drawing from) and <dir>/title.txt.
--
-- WHY DOMAINS AND NOT $2006/$2007. tools/bizhawk/frame.lua reads VRAM by poking
-- $2006 and then $2007, on the reasoning that "poke registers is how VRAM gets
-- read". On this core that silently returns $00 for everything: a run against the
-- cartridge at frame 60 reported `palette_nonzero 0`, `nametable_2000_nonzero 0
-- of 1024` and `chr_0000_nonzero 0 of 2048`, and `reg_PPUCTRL_2000=00` on a
-- screen that is demonstrably drawing. Zero non-zero bytes out of 1024 is not a
-- plausible measurement of a title screen; it is the signature of a read that
-- never happened, and it looks exactly like data.
--
-- The memory *domains* are the supported route and they work. BizHawk 2.11.1's
-- quickerNES exposes: CHR, CHR VROM, CIRAM (nametables), CPU registers, OAM,
-- PALRAM, PRG ROM, System Bus, WRAM. There is no VRAM domain -- the nametables
-- are CIRAM and the palette is PALRAM -- so a script that reads "VRAM" gets
-- nothing. This script selects each domain by name, reads it, and *fails loudly*
-- if the read did not happen: every dump is checked for content before the run
-- is allowed to report success.
--
-- Why the cartridge and the rebuild must be dumped the same way: the only
-- question this answers is "do these two images agree on the title screen", and
-- that is only meaningful if both were read through the same core.

local OUT = os.getenv("MAGICIAN_TITLE_OUT") or "/tmp/opencode/title"
local TARGET = tonumber(os.getenv("MAGICIAN_FRAMES") or "60")

local f = io.open(OUT .. "/title.txt", "w")
local function w(s) f:write(s .. "\n"); f:flush() end

local function sel(name)
  local ok = pcall(memory.usememorydomain, name)
  if not ok then return nil, "domain " .. name .. " did not select" end
  local _, size = pcall(memory.getmemorydomainsize, name)
  return size or 0
end

local function grab(name, start, len, why)
  local size = sel(name)
  if not size then return nil, "no such domain: " .. name end
  if start + len > size then
    return nil, string.format("%s: $%X+%d past the domain's %d bytes", name, start, len, size)
  end
  local got, s = pcall(memory.read_bytes_as_binary_string, start, len)
  if not (got and type(s) == "string" and #s == len) then
    return nil, name .. ": read_bytes_as_binary_string did not return " .. len .. " bytes"
  end
  -- The check that matters. A dump of the right length full of $00 is what a
  -- failed read looks like, and it is indistinguishable from real data if
  -- nothing counts it. So every read is counted before it is written, and the
  -- count goes in the log where a reader will see it.
  local nz = 0
  for i = 1, #s do if s:byte(i) ~= 0 then nz = nz + 1 end end
  if why and nz == 0 then
    return nil, string.format("%s: %d bytes, ALL ZERO -- the read did not happen "
                                  .. "(this is the failure that looks like data)", name)
  end
  return s, nz
end

local function wr(name, s)
  local h = io.open(OUT .. "/" .. name, "wb")
  h:write(s)
  h:close()
end

w(string.format("target_frames %d", TARGET))
w(string.format("domains %s", table.concat(memory.getmemorydomainlist() or {}, ", ")))

-- emu.frameadvance() is exact: framecount() returned 10 after 10 calls.
local guard = 0
while emu.framecount() < TARGET and guard < TARGET * 20 + 1000 do
  emu.frameadvance()
  guard = guard + 1
end
w(string.format("reached_frame %d steps %d", emu.framecount(), guard))
if emu.framecount() < TARGET then
  w("FAIL could not reach the target frame")
  f:close()
  pcall(function() client.exit() end)
  return
end

-- Screenshot before touching a domain, for the same reason frame.lua does: the
-- domains are read-only selections and do not disturb the PPU, but the shot has
-- to be of the frame we advanced to, not of one a probe disturbed.
local png = OUT .. "/frame.png"
pcall(function() client.screenshot(png) end)

local failures = 0

-- CIRAM is 4096 bytes: four 1 KiB nametables laid out consecutively, so nt0 is
-- $0000-$03FF. The 64 bytes after that are nt0's attributes.
local ciram, nzc = grab("CIRAM (nametables)", 0, 4096, true)
if ciram then
  wr("nt0.bin", ciram:sub(1, 1024))
  wr("nt0attr.bin", ciram:sub(1025, 1088))
  wr("ciram.bin", ciram)
  w(string.format("ciram 4096 bytes, %d non-zero", nzc))
else
  w("FAIL ciram: " .. tostring(nzc)); failures = failures + 1
end

-- PALRAM is 32 bytes: $3F00-$3F0F background, $3F10-$3F1F sprite.
local pal, nzp = grab("PALRAM", 0, 32, true)
if pal then
  wr("pal.bin", pal)
  local t = {}
  for i = 1, 32 do t[#t + 1] = string.format("%02X", pal:byte(i)) end
  w("palram " .. table.concat(t, " "))
  w(string.format("palram_nonzero %d", nzp))
else
  w("FAIL palram: " .. tostring(nzp)); failures = failures + 1
end

-- 8 KiB of pattern table: which 8 KiB is a question, not a guess. The title
-- screen selects its set with `lda #s0e / sta mapbnk0` (X0.PDS:629), and the
-- three title CHR banks sit at 4 KiB banks 14/15/16, so the background tiles
-- for a title screen are the 8 KiB at CHR $8000-$9FFF. Dumped, not assumed:
-- the nonzero count says whether art is there.
local chr, nzt = grab("CHR", 0x8000, 0x2000, true)
if chr then
  wr("chr.bin", chr)
  w(string.format("chr_8000 8192 bytes, %d non-zero", nzt))
else
  w("FAIL chr_8000: " .. tostring(nzt)); failures = failures + 1
end

-- Also dump CHR $0000-$7FFF in one go, because if $8000 turns out to be the
-- wrong 8 KiB the answer is in here and not worth a second run.
local chrfull, nzf = grab("CHR", 0, 0x10000, false)
if chrfull then
  wr("chrfull.bin", chrfull)
  w(string.format("chr_full 65536 bytes, %d non-zero", nzf))
end

w(failures == 0 and "title_dump ok" or ("title_dump FAIL " .. failures))
w("final_frame " .. tostring(emu.framecount()))
f:close()
pcall(function() client.sleep(500) end)
pcall(function() client.exit() end)
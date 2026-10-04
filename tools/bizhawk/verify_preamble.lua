-- Assert, before any measuring script runs, that BizHawk is running the core we
-- asked for on the ROM we asked for. Loaded by tools/bizhawk/run.sh as BizHawk's
-- single --lua script; it checks, writes a verdict, and only then runs the script
-- that was actually asked for.
--
-- This exists because the harness was capable of reporting success while
-- measuring nothing. `run.sh` once passed a *relative* ROM path; BizHawk failed
-- to load the file, fell back to NullHawk, rendered nothing, and the run still
-- looked green. A missing ROM and a stale NES/SaveRAM/ do the same thing.
--
-- ## What this build actually exposes, and why the check is built this way
--
-- `client.getsystemid()`, `client.getromname()` and `client.getromhash()` do not
-- exist on BizHawk 2.11.1 here. Measured with bias_probe.lua: pcall succeeds and
-- the value is nil, at script load and after four frameadvances, while the
-- launch log plainly shows quickerNES booting. Reading them as "no core" would
-- have failed every run; not reading them at all would have left the original bug
-- in place. The identity API on this build is:
--
--     emu.getsystemid()     "NES"       (NullHawk reports "Null")
--     emu.getboardname()    "mmc3"      (a null core has no board)
--     memory domain "System Bus", which reads the *banked* PRG at CPU addresses
--
-- and that last one is the strong one. Reading the interrupt vectors and the
-- reset routine out of System Bus compares the emulator's loaded cartridge
-- against the file on disk byte for byte. It cannot be satisfied by a fallback
-- core, by a missing file, or by a different ROM that happens to share a
-- filename -- and it is exactly the thing a "which ROM am I running" question
-- should be answered with.
--
-- run.sh passes the expected bytes as MAGICIAN_WANT_WINDOW_HEX, taken from the
-- requested file, so the expectation cannot drift from the request.

local VERIFY = (os.getenv("MAGICIAN_VERIFY_DIR") or "/tmp/opencode") .. "/verify.txt"

local WANT_SYSTEM = os.getenv("MAGICIAN_WANT_SYSTEM") or "NES"
local WANT_BOARD = os.getenv("MAGICIAN_WANT_BOARD") or ""
local WANT_ROM = os.getenv("MAGICIAN_WANT_ROM") or ""
local WANT_WINDOW_AT = tonumber(os.getenv("MAGICIAN_WANT_WINDOW_AT") or "") or 0
local WANT_WINDOW_HEX = os.getenv("MAGICIAN_WANT_WINDOW_HEX") or ""

local fails, lines = {}, {}
local function note(s) lines[#lines + 1] = s end
local function fail(s) fails[#fails + 1] = s end

-- A member that does not exist reads nil, and pcall succeeds. Both outcomes have
-- to be reported as "unknown", never quietly as "fine".
--
-- NLua hands back .NET methods as *userdata*, not as Lua functions, so
-- `type(v) == "function"` is the wrong test and tostring(v) yields
-- "luaNet_function: 0x...". Calling has to be attempted on anything non-nil.
local function str(holder, name)
  local ok, v = pcall(function() return holder[name] end)
  if not ok then return nil, tostring(v) end
  if v == nil then return nil, "absent" end
  if type(v) == "table" then return nil, "table, not callable" end
  local ok2, r = pcall(v)
  if not ok2 then
    -- Not callable: report its own text, which is at least honest about what the
    -- build actually gave us.
    return nil, "not callable (" .. type(v) .. " " .. tostring(v) .. ")"
  end
  if r == nil then return nil, "returned nil" end
  return tostring(r), nil
end

note(string.format("want_system  = %s", WANT_SYSTEM))
note(string.format("want_board   = %s", WANT_BOARD))
note(string.format("want_rom     = %s", WANT_ROM))

-- ------------------------------------------------------- 1. which core
local sysid, syserr = str(emu, "getsystemid")
note("systemid     = " .. tostring(sysid or ("<" .. tostring(syserr) .. ">")))
if sysid == nil then
  fail("emu.getsystemid() is unavailable, so the core cannot be identified")
elseif WANT_SYSTEM ~= "" and sysid ~= WANT_SYSTEM then
  fail(string.format(
    "core is %q, not %q -- BizHawk fell back to a different core. NullHawk is the "
    .. "usual one and it renders nothing, which is how a broken run looks like a "
    .. "good one", sysid, WANT_SYSTEM))
end

local board, boarderr = str(emu, "getboardname")
note("boardname    = " .. tostring(board or ("<" .. tostring(boarderr) .. ">")))
if WANT_BOARD ~= "" then
  if board == nil then
    fail("emu.getboardname() is unavailable, so the mapper cannot be checked")
  elseif board ~= WANT_BOARD then
    fail(string.format("core reports board %q, not %q", board, WANT_BOARD))
  end
end

local function select(d)
  if not pcall(memory.usememorydomain, d) then return false end
  local cur = str(memory, "getcurrentmemorydomain")
  return cur == d
end

-- ------------------------------------------------------- 2. which ROM
-- The loaded cartridge, read out of the core through System Bus, compared with
-- the bytes of the file that was asked for. This is the check that a wrong path,
-- a NullHawk fallback, or a same-named different file cannot pass.
if WANT_WINDOW_HEX ~= "" and WANT_WINDOW_AT ~= 0 then
  if not select("System Bus") then
    fail("the core has no \"System Bus\" memory domain, so the loaded PRG "
         .. "cannot be read back and the ROM cannot be identified")
  else
    local got = {}
    local unreadable = 0
    for i = 1, #WANT_WINDOW_HEX, 2 do
      local a = WANT_WINDOW_AT + ((i - 1) // 2)
      local okr, v = pcall(memory.read_u8, a)
      if okr and type(v) == "number" then
        got[#got + 1] = string.format("%02X", v % 256)
      else
        got[#got + 1] = "??"
        unreadable = unreadable + 1
      end
    end
    local hex = table.concat(got)
    note(string.format("window_at    = $%04X", WANT_WINDOW_AT))
    note(string.format("window_want  = %s", WANT_WINDOW_HEX))
    note(string.format("window_got   = %s", hex))
    if unreadable > 0 then
      fail(string.format("%d of %d bytes at $%04X could not be read from the core",
                         unreadable, #got, WANT_WINDOW_AT))
    elseif hex ~= WANT_WINDOW_HEX:upper() then
      -- Name the first differing byte: "the ROM is wrong" is much less useful
      -- than "byte 5 of the reset routine differs".
      local first = nil
      for i = 1, #WANT_WINDOW_HEX, 2 do
        if hex:sub(i, i + 1):upper() ~= WANT_WINDOW_HEX:sub(i, i + 1):upper() then
          first = (i - 1) // 2
          break
        end
      end
      fail(string.format(
        "the PRG the core has loaded is not the ROM that was asked for: first "
        .. "difference at $%04X (byte %d of %d, wanted %s got %s). A NullHawk "
        .. "fallback, a wrong path, or a different file with the same name all "
        .. "look like this",
        WANT_WINDOW_AT + (first or 0), (first or 0), #WANT_WINDOW_HEX,
        WANT_WINDOW_HEX:sub((first or 0) * 2 + 1, (first or 0) * 2 + 2),
        hex:sub((first or 0) * 2 + 1, (first or 0) * 2 + 2)))
    end
  end
else
  fail("run.sh did not pass an expected ROM window, so the ROM was never checked")
end

-- --------------------------------------- 3. can the core be asked for memory
-- This check used to be implicit: NullHawk "does not implement memory domains",
-- a Lua script then dies on its first domain call, and that reads as a property
-- of the ROM rather than of the emulator.
--
-- ## The trap this had to be written around
--
-- On this build `memory.usememorydomain(name)` and `memory.getmemorydomainsize(name)`
-- **never fail**. Measured with bias_probe.lua: all 21 candidate names returned
-- size_ok=true and select_ok=true, including "VRAM", "PPU", "Nametables" and
-- "Null". An unknown name logs "Unable to find domain: <name>" and then leaves
-- the *previous* domain selected. A subsequent read therefore returns another
-- region's bytes, at the right size, with no error -- which is a nametable dump
-- full of cartridge code.
--
-- So a select is only believed when `getcurrentmemorydomain()` confirms it, and
-- the domains required below are the ones that actually exist on quickerNES:
-- System Bus (which is where the nametables and the palette live -- there is no
-- VRAM domain), CHR, RAM, WRAM and OAM.
for _, d in ipairs({ "System Bus", "CHR", "RAM" }) do
  if not select(d) then
    fail(string.format(
      "the %q memory domain is not selectable on this core. On quickerNES the "
      .. "real domains are System Bus, CHR, PRG ROM, RAM, WRAM and OAM; asking "
      .. "for any other name does not fail, it silently keeps the previous "
      .. "domain, so a read afterwards returns the wrong region's bytes",
      d))
  else
    local okr, v = pcall(memory.read_u8, 0)
    if okr and type(v) == "number" then
      note(string.format("domain %-12s selected and readable, size %s, first byte %02X",
                         d, tostring((function()
                           local ok, s = pcall(memory.getmemorydomainsize, d)
                           return ok and s or "?"
                         end)()), v % 256))
    else
      fail(string.format("the %q memory domain could not be read", d))
    end
  end
end

-- ------------------------------------------------------------- the verdict
if #fails == 0 then
  note("verdict ok")
else
  for _, f in ipairs(fails) do note("verdict FAIL " .. f) end
end

-- Write and flush before the real script runs, so a script that dies on its
-- first line still leaves a verdict behind. That is the whole point: the failure
-- being guarded against is a run that gets no further than this.
local fh = io.open(VERIFY, "w")
if fh then
  fh:write(table.concat(lines, "\n") .. "\n")
  fh:close()
end

-- Hand over to the script run.sh was actually asked to run.
local main = os.getenv("MAGICIAN_MAIN_LUA")
if main and main ~= "" then
  local chunk, err = loadfile(main)
  if not chunk then
    local eh = io.open(VERIFY, "a")
    if eh then
      eh:write("verdict FAIL could not load " .. main .. ": " .. tostring(err) .. "\n")
      eh:close()
    end
  else
    chunk()
  end
end
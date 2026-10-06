"""Playing The Magician (NES) with Python instead of a TAS.

Layout
------
    emu.py     the only module that talks to BizHawk
    ram.py     named RAM decode -- the single source of truth for addresses
    actions/   walk.py shop.py fight.py spells.py talk.py
    route.py   an ordered list of segments, with a snapshot before each
    milestones/  one script per proven milestone

Nothing outside emu.py opens a socket, launches a process or names a Lua file.
Nothing outside ram.py contains a RAM address. See ram.py's module docstring for
why the second half of that sentence is a rule and not a preference.
"""
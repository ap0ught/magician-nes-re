#!/usr/bin/env python3
"""A PDS 1.26 compatible assembler for the Eurocom Magician source.

PDS ("Program Development System", P.D.Systems Ltd 1985-88) was the Atari ST
toolchain Eurocom assembled Magician with. It is not available, so this
reimplements the dialect the eight `X?.PDS` banks actually use.

The dialect, as measured from the source
----------------------------------------

* **Lines.** The container stores lines as `CR NUL` and uses a lone `CR` as the
  editor's soft wrap *inside* one logical line. A logical line can hold several
  instructions and several directives, which is why the wrap cannot simply
  become a newline: `db "MAGIC1+"` followed by `hex 25 02 90` is one line whose
  two directives emit ten bytes.
* **Labels.** A name in column 0 defines a label at the current address.
  `name equ expr` and `name = expr` define symbols (`=` is redefinable, and the
  source uses it for the running counters `z`, `b`, `li`, `lo`). A name
  beginning `!` is a *local* label scoped to the most recent global label:
  `!a` is reused in `partime`, `tstpw`, `moveb` and `eorpw` alike.
* **Macros.** `name macro` .. `endm`, with positional parameters `@1`..`@9`
  substituted *textually*, which is what lets `rvar ptime` expand to
  `ptime = *-asb` and `load 21.blk,$8000,$a000,,l21b` expand to a dozen lines.
  `ifs [@2] []` tests whether the *text* of a parameter is empty, so textual
  substitution is a requirement, not an implementation detail.
* **Conditionals.** `if <expr>` / `ifs <string> <string>` / `else` / `endif`.
  Note the toolchain's own spelling of `ifs`.
* **Addresses.** `*` is the current address. `org a` sets it; `org a,b` sets the
  assembler's physical address `a` and the *logical* address `b` that symbols are
  defined at. They differ because the game's MMC3 PRG mode presents a bank at an
  address other than the one it is assembled at: `load 21.blk,$8000,$a000`
  assembles at $8000 and is addressed by the game at $A000.
* **Banks.** `bank n` selects 8 KiB slot `n` of the 128 KiB PRG image (slots
  0-15). The source's `b = $6` … `b = $f` are 8 KiB slots, not 16 KiB banks;
  `memchk c000,b` then lets a group fill both slots of one 16 KiB bank. A
  module's *initial* slot is not written down anywhere, so `build.py` determines
  it by matching the module's output against the cartridge.
* **Numbers.** `radix 10` is in effect: bare digits are decimal, `$` is hex, `%`
  is binary, and a bare token carrying hex letters is read as hex
  (`memchk c000,b`). `"A"` is a character constant, `"MAGIC1+"` a string.
* **Byte selectors.** `<expr` and `>expr` are the low and high byte, as
  `option 0,0` documents. Infix `<` and `>` are comparisons, as in `if *>$7fff`.
* **Directives.** `hex` (raw hex digit pairs, ignoring `radix`), `db`,
  `dw`, `dl`/`dh` (the LOW and HIGH halves of a pointer list: ONE byte each,
  not the Atari MACRO "define long" of 4 and 2 -- see `directive` and
  `src/testing/test_pointer_widths.py`), `ds n,f`, `incbin`, `include`,
  `error`, `end`, and the no-ops `send`, `option`, `radix`.

`if 0=1` in x7 (lines 1005-1147) was long assumed to be dead -- an obsolete
hand-written address table, with the live branch packing the data files
sequentially. **That assumption is wrong, and measuring it is what showed it.**
Forcing the branch true takes the rebuilt PRG from 32 719 non-zero bytes to
60 359 -- 27 640 bytes of real level data that was being skipped entirely. The
byte *match* falls (9.7% to 6.4%) only because those bytes land in the wrong
banks while slot placement is still unproven, so a worse score here does not
mean the branch is dead.

So `0=1` is an author-level switch, not dead code, and the cartridge was built
with it enabled. Which branch is live is now a build-time option
(`asm/build.py --x7-level-table {on,off}`), on by default, rather than a belief
baked into the parser.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# ---------------------------------------------------------------- 6502 tables

OPCODES: dict[str, list[tuple[int, str]]] = {
    "adc": [(0x69, "imm"), (0x65, "zp"), (0x75, "zpx"), (0x6D, "abs"), (0x7D, "absx"), (0x79, "absy"), (0x61, "indx"), (0x71, "indy")],
    "and": [(0x29, "imm"), (0x25, "zp"), (0x35, "zpx"), (0x2D, "abs"), (0x3D, "absx"), (0x39, "absy"), (0x21, "indx"), (0x31, "indy")],
    "asl": [(0x0A, "acc"), (0x06, "zp"), (0x16, "zpx"), (0x0E, "abs"), (0x1E, "absx")],
    "bcc": [(0x90, "rel")], "bcs": [(0xB0, "rel")], "beq": [(0xF0, "rel")], "bmi": [(0x30, "rel")],
    "bne": [(0xD0, "rel")], "bpl": [(0x10, "rel")], "bvc": [(0x50, "rel")], "bvs": [(0x70, "rel")],
    "bit": [(0x24, "zp"), (0x2C, "abs")],
    "brk": [(0x00, "imp")],
    "clc": [(0x18, "imp")], "cld": [(0xD8, "imp")], "cli": [(0x58, "imp")], "clv": [(0xB8, "imp")],
    "cmp": [(0xC9, "imm"), (0xC5, "zp"), (0xD5, "zpx"), (0xCD, "abs"), (0xDD, "absx"), (0xD9, "absy"), (0xC1, "indx"), (0xD1, "indy")],
    "cpx": [(0xE0, "imm"), (0xE4, "zp"), (0xEC, "abs")],
    "cpy": [(0xC0, "imm"), (0xC4, "zp"), (0xCC, "abs")],
    "dec": [(0xC6, "zp"), (0xD6, "zpx"), (0xCE, "abs"), (0xDE, "absx")],
    "dex": [(0xCA, "imp")], "dey": [(0x88, "imp")],
    "eor": [(0x49, "imm"), (0x45, "zp"), (0x55, "zpx"), (0x4D, "abs"), (0x5D, "absx"), (0x59, "absy"), (0x41, "indx"), (0x51, "indy")],
    "inc": [(0xE6, "zp"), (0xF6, "zpx"), (0xEE, "abs"), (0xFE, "absx")],
    "inx": [(0xE8, "imp")], "iny": [(0xC8, "imp")],
    "jmp": [(0x4C, "abs"), (0x6C, "ind")],
    "jsr": [(0x20, "abs")],
    "lda": [(0xA9, "imm"), (0xA5, "zp"), (0xB5, "zpx"), (0xAD, "abs"), (0xBD, "absx"), (0xB9, "absy"), (0xA1, "indx"), (0xB1, "indy")],
    "ldx": [(0xA2, "imm"), (0xA6, "zp"), (0xB6, "zpy"), (0xAE, "abs"), (0xBE, "absy")],
    "ldy": [(0xA0, "imm"), (0xA4, "zp"), (0xB4, "zpx"), (0xAC, "abs"), (0xBC, "absx")],
    "lsr": [(0x4A, "acc"), (0x46, "zp"), (0x56, "zpx"), (0x4E, "abs"), (0x5E, "absx")],
    "nop": [(0xEA, "imp")],
    "ora": [(0x09, "imm"), (0x05, "zp"), (0x15, "zpx"), (0x0D, "abs"), (0x1D, "absx"), (0x19, "absy"), (0x01, "indx"), (0x11, "indy")],
    "pha": [(0x48, "imp")], "php": [(0x08, "imp")], "pla": [(0x68, "imp")], "plp": [(0x28, "imp")],
    "rol": [(0x2A, "acc"), (0x26, "zp"), (0x36, "zpx"), (0x2E, "abs"), (0x3E, "absx")],
    "ror": [(0x6A, "acc"), (0x66, "zp"), (0x76, "zpx"), (0x6E, "abs"), (0x7E, "absx")],
    "rti": [(0x40, "imp")], "rts": [(0x60, "imp")],
    "sbc": [(0xE9, "imm"), (0xE5, "zp"), (0xF5, "zpx"), (0xED, "abs"), (0xFD, "absx"), (0xF9, "absy"), (0xE1, "indx"), (0xF1, "indy")],
    "sec": [(0x38, "imp")], "sed": [(0xF8, "imp")], "sei": [(0x78, "imp")],
    "sta": [(0x85, "zp"), (0x95, "zpx"), (0x8D, "abs"), (0x9D, "absx"), (0x99, "absy"), (0x81, "indx"), (0x91, "indy")],
    "stx": [(0x86, "zp"), (0x96, "zpy"), (0x8E, "abs")],
    "sty": [(0x84, "zp"), (0x94, "zpx"), (0x8C, "abs")],
    "tax": [(0xAA, "imp")], "tay": [(0xA8, "imp")], "tsx": [(0xBA, "imp")], "txa": [(0x8A, "imp")],
    "txs": [(0x9A, "imp")], "tya": [(0x98, "imp")],
}

SIZE = {"imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2, "rel": 2,
        "abs": 3, "absx": 3, "absy": 3, "ind": 3, "indx": 2, "indy": 2}

BRANCHES = {"bcc", "bcs", "beq", "bmi", "bne", "bpl", "bvc", "bvs"}
FORCE_ABS = {"jmp", "jsr"}

DIRECTIVES = {"if", "ifs", "else", "endif", "org", "bank", "hex", "db", "dw", "dl", "dh",
              "ds", "incbin", "include", "error", "end", "send", "option", "radix",
              "macro", "endm", "name", "page", "space", "equ", "=", "dc"}
# `set` is deliberately absent: shopdat.src defines `set equ $07`, and a keyword
# list is what the statement scanner uses to tell a label from a directive, so
# listing it cut `set equ $07` into a `set` directive and lost the label.

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_!]*")
# A label may be global, `!local`, or a macro parameter (`@1 equ z`). A parameter
# may also be *part* of a name: x0's `pti` expands to `pi@1 equ li`, and without
# the `@` allowed inside the name that statement did not parse as a label at all
# and assembled as the instruction `pi10`. The `load` macro likewise defines its
# file name as a label when asked (`tit.dat equ *`).
LABEL_RE = re.compile(r"(?:[!@][A-Za-z0-9_!]*|[A-Za-z_][A-Za-z0-9_.!@]*)")

# Directives whose next token is their operand, so a name there is not a label.
NAME_TAKES_OPERAND = {"equ", "=", "set", "db", "dw", "dl", "dh", "hex", "load",
                      "org", "bank", "macro", "if", "ifs", "ds", "include", "name"}
EXPR_RE = re.compile(
    r"""\s*(?:
      (?P<str>"[^"]*")
    | (?P<hex>\$[0-9a-fA-F]+)
    | (?P<bin>%[01]+)
    | (?P<lbl>![A-Za-z0-9_][A-Za-z0-9_!]*|@[A-Za-z0-9_]+)
    | (?P<op><>|<<|>>|[-+*/&|^()<>=,!~])
    | (?P<name>[A-Za-z_][A-Za-z0-9_!]*)
    | (?P<num>[0-9][0-9a-fA-F]*)
    | (?P<other>\S)
    )""",
    re.VERBOSE,
)


class AsmError(Exception):
    def __init__(self, msg: str, where: str = ""):
        super().__init__(f"{where}: {msg}" if where else msg)
        self.raw = msg
        self.where = where


# ------------------------------------------------------------------ lexing

def strip_comment(line: str) -> str:
    """Drop a `;` comment, respecting `"` strings and character constants."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch == ";":
            break
        if ch == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 1
            out.append(line[i:min(j + 1, n)])
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_operands(text: str) -> list[str]:
    """Split an operand list on commas outside brackets and strings."""
    parts, cur, depth, i = [], [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 1
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    tail = "".join(cur)
    if tail.strip() or parts:
        parts.append(tail)
    return [p.strip() for p in parts]


def tokenize_expr(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = EXPR_RE.match(text, pos)
        if not m or m.end() == pos:
            raise AsmError(f"cannot lex expression {text!r} at offset {pos}")
        pos = m.end()
        for kind in ("str", "hex", "bin", "lbl", "op", "name", "num", "other"):
            v = m.group(kind)
            if v is not None:
                out.append((kind, v))
                break
    return out


class Expr:
    """Recursive-descent evaluator over tokenize_expr() output."""

    def __init__(self, toks: list[tuple[str, str]], asm: "Assembler"):
        self.t, self.i, self.asm = toks, 0, asm

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect_op(self, op):
        _, val = self.next()
        if val != op:
            raise AsmError(f"expected {op!r}, got {val!r}")

    def parse(self):
        v = self.compare()
        if self.i != len(self.t):
            raise AsmError(f"trailing tokens in expression: {self.t[self.i:]}")
        return v

    def compare(self):
        """`if *>$7fff`, `if 0=1`, `if @2=st_che`.

        Comparisons bind looser than arithmetic and are not chained here: the
        source never chains them, and `<`/`>` are also the low/high byte
        selectors when they start an operand.
        """
        v = self.or_expr()
        while self.peek()[1] in ("=", "<>", "<", ">"):
            op = self.next()[1]
            r = self.or_expr()
            v = {"=": v == r, "<>": v != r, "<": v < r, ">": v > r}[op]
        return v

    def or_expr(self):
        v = self.xor_expr()
        while self.peek()[1] == "|":
            self.next()
            v |= self.xor_expr()
        return v

    def xor_expr(self):
        v = self.and_expr()
        while self.peek()[1] == "^":
            self.next()
            v ^= self.and_expr()
        return v

    def and_expr(self):
        v = self.shift_expr()
        while self.peek()[1] == "&":
            self.next()
            v &= self.shift_expr()
        return v

    def shift_expr(self):
        v = self.add_expr()
        while self.peek()[1] in ("<<", ">>"):
            op = self.next()[1]
            r = self.add_expr()
            v = (v << r) if op == "<<" else (v >> r)
        return v

    def add_expr(self):
        v = self.mul_expr()
        while self.peek()[1] in ("+", "-"):
            op = self.next()[1]
            r = self.mul_expr()
            v = v + r if op == "+" else v - r
        return v

    def mul_expr(self):
        v = self.unary()
        while self.peek()[1] in ("*", "/"):
            op = self.next()[1]
            r = self.unary()
            v = v * r if op == "*" else v // r
        return v

    def unary(self):
        _, val = self.peek()
        if val == "-":
            self.next()
            return -self.unary()
        if val == "+":
            self.next()
            return self.unary()
        return self.primary()

    def primary(self):
        kind, val = self.next()
        if val is None:
            raise AsmError("expression ended early")
        if val == "(":
            v = self.or_expr()
            self.expect_op(")")
            return v
        if val == '"':
            # The container eats a trailing space inside a string, so a lone
            # `"` here is a `" "` that lost its space (`db hyp,hyp,"`).
            return 0x20
        if kind == "str":
            return self.string_value(val)
        if kind == "hex":
            return int(val[1:], 16)
        if kind == "bin":
            return int(val[1:], 2)
        if kind == "num":
            return self.asm.number(val)
        if val == "*":
            return self.asm.star
        if kind in ("name", "lbl"):
            return self.asm.lookup(val)
        if val in ("<", ">"):
            v = self.or_expr()
            return (v & 0xFF) if val == "<" else ((v >> 8) & 0xFF)
        raise AsmError(f"unexpected token {val!r} in expression")

    @staticmethod
    def string_value(tok: str) -> int:
        if tok == '""':
            return 0
        body = tok[1:-1]
        if len(body) == 1:
            return ord(body)
        return int.from_bytes(body.encode("latin-1"), "little")


# ------------------------------------------------------------------- lines

class Line:
    __slots__ = ("where", "raw", "label", "op", "operands")

    def __init__(self, where: str, raw: str):
        self.where = where
        self.raw = raw
        self.label = None
        self.op = None
        self.operands = ""
        body = strip_comment(raw).strip()
        if not body:
            return
        head = body.split(None, 1)
        word = head[0]
        rest = head[1] if len(head) > 1 else ""
        # `mus macro` is a macro definition even though the name is indented,
        # so a second word of `macro` always promotes the first to a label.
        p2 = rest.split(None, 1)
        if p2 and p2[0].lower() == "macro":
            self.label = word
            self.op = "macro"
            self.operands = p2[1] if len(p2) > 1 else ""
            return
        # Column 0 means a label; anything indented is a bare directive or
        # instruction. The indent has to be read from the raw line, because the
        # body above has been stripped.
        if raw[:1] not in (" ", "\t"):
            if LABEL_RE.fullmatch(word):
                self.label = word
                if rest:
                    p2 = rest.split(None, 1)
                    self.op = p2[0].lower()
                    self.operands = p2[1] if len(p2) > 1 else ""
                return
        self.op = word.lower()
        self.operands = rest


# Keywords that take no operand.
NO_OPERAND = {"else", "endif", "endm", "end", "macro", "page", "space"}
# Keywords that take exactly one operand.
ONE_OPERAND = {"bank", "equ", "=", "incbin", "include", "name", "if",
               "radix", "option", "send"}
# Keywords that take up to N comma-separated arguments.
N_OPERANDS = {"org": 2, "ds": 2, "ifs": 2, "load": 5, "error": 1}
# Keywords whose operand list continues while the next token starts with a comma.
COMMA_LIST = {"db", "dc", "dw", "dl", "dh"}
# Instructions with no operand form at all (`cli` never takes one), so the next
# token after them starts the next statement.
NO_OPERAND_INSN = {op for op, modes in OPCODES.items()
                   if all(m in ("imp", "acc") for _, m in modes)}

HEX_RUN_RE = re.compile(r"[0-9A-Fa-f]+")
# A macro parameter reference. `@1`..`@9` are substituted textually, so before
# substitution these are still operands as far as the scanner is concerned.
MACRO_PARAM_RE = re.compile(r"@[1-9]")


class Word:
    """One token, with the column it started in and the text of its statement."""

    __slots__ = ("text", "col0", "line")

    def __init__(self, text: str, col0: bool, line: int):
        self.text = text
        self.col0 = col0
        self.line = line


# One token: a maximal run of non-space characters inside which a quoted string
# is atomic, so a string that *contains* spaces does not tear the token in two.
# `db hyp,hyp," QUIT GAME ",hyp,hyp+$80` is a keyword plus one operand, and
# `db "WHO",app,"S WHO",qu+$80` is too -- both are one `db`, and cutting either
# in two silently shifts every address after it. The first branch is what makes
# the string win over `\S+`, which would otherwise swallow the opening quote
# and leave the rest of the string to be torn apart.
WORD_RE = re.compile(r'(?:"[^"]*"|[^\s"])+|\S+')


def words_of(line: str, lineno: int = 0) -> list[Word]:
    """Tokenise one *logical* line, remembering which tokens were in column 0.

    A lone `CR` is the editor's soft wrap and does not break a logical line, but
    the wrap is a physical line break and a physical line can start in column 0
    with a label. So the column has to survive the rejoin: x5's `sql` table and
    the `sqh` and `cos` labels that follow it are all one logical line, and only
    the column marks where one statement ends and the next begins.

    A segment with an *odd* number of `"` ends inside a string, and the rest of
    that segment is string text. Two places in this source do this -- `memchk`'s
    `error "** exceeded $@1` and x7's `error ">$FB80!` -- and in both the rest of
    the segment is the message. Treating it as words is not cosmetic: it left
    `$@1` bare, the scanner read it as an instruction and took the next token,
    `endif`, as its operand, so `memchk` never closed its conditional and the
    whole rest of x7 assembled inside a false `if`. That is how `btit` and `bpw`
    came out undefined. Judged per segment, which is right because the editor's
    soft wrap already ends the logical line there.
    """
    out: list[Word] = []
    for seg in line.split("\r"):
        body = strip_comment(seg).strip()
        if not body:
            continue
        head, tail = body, ""
        if body.count('"') % 2:
            cut = body.rfind('"')
            head, tail = body[:cut], body[cut:]
        # Column 0 means *this token* is the first on a physical line that starts
        # in column 0 -- not merely that its segment did. x7's
        # `onechr<tab>swapstk 0,1` is one segment beginning in column 0, and `0,1`
        # is `swapstk`'s operand; x4's `pca0 hex f7 / done / pca1 hex 0f` puts
        # `pca1` at the head of a later segment, and it is a label. Only the first
        # token of a column-0 segment can begin a statement.
        first = True
        for m in WORD_RE.finditer(head):
            out.append(Word(m.group(), first and seg[:1] not in (" ", "\t"), lineno))
            first = False
        if tail:
            out.append(Word(tail, first and seg[:1] not in (" ", "\t"), lineno))
    return out


def scan_statements(words: list[Word], keywords: set[str], where: str,
                    report: list[str] | None, macro_names: set[str] | None = None) -> list[Line]:
    """Cut one PDS logical line into its statements.

    A logical line is not one statement. x5's `sql` table is a single line of
    eight `hex` directives; x7's data section puts `load`, `include`, `memchk`,
    `org`, `error` and whole new labels on one line each. The cut has to know
    each keyword's shape: `hex` swallows runs of hex digits and nothing else, so
    a bare name after one is the next label; `db`/`dw`/`org`/`ds` swallow
    comma-separated lists; `load` takes up to five arguments, the fifth of which
    is a bare label that may collide with a keyword name; and an instruction
    takes one operand, which may itself be glued to a following comma
    (`sta (ptr),y`).

    Getting this wrong is silent -- two statements assembled as one shifts every
    address after them -- so every cut that is not the trivially expected one is
    recorded in `report` for review.
    """
    n = len(words)
    if not n:
        return []
    starts: list[int] = []
    i = 0
    # Whatever the scanner does not consume as an operand starts the next
    # statement, which is what cuts `sql hex .. hex ..` and `.. hex .. sqh hex`
    # without needing to know where the editor's soft wrap fell. A statement may
    # open with a label -- `numch equ *-chevn-1`, `sqh hex ..` -- and the label
    # belongs to the statement that follows it.
    while i < n:
        low = words[i].text.lower()
        if low == "macro" and starts and starts[-1] == i - 1:
            # `zp macro`: the name is a label and `macro` is the marker, so the
            # two are one statement. The name is also a macro name used
            # elsewhere in the file, so the name alone cannot be recognised as a
            # label -- only its position, right before `macro`, gives it away.
            i += 1
            continue
        starts.append(i)
        if is_keyword(words[i].text, keywords):
            low = words[i].text.lower()
            i += 1
        else:
            low = None
            i += 1
            if i < n and is_keyword(words[i].text, keywords):
                low = words[i].text.lower()
                i += 1
            else:
                continue
        if low in NO_OPERAND:
            continue
        if low == "hex":
            # `hex` takes runs of hex digits and nothing else, so a bare name
            # after one is the next label rather than an operand. A macro
            # parameter counts as a hex run: substitution is textual and has
            # not happened yet, but `hex @5` in x0's `l0` macro *is* an operand.
            # Cutting it off instead makes the parameter the next statement's
            # label, and `@5` matches LABEL_RE -- so the parameter is defined as
            # a label and redefined by every call, for no good reason.
            while i < n and (HEX_RUN_RE.fullmatch(words[i].text)
                             or MACRO_PARAM_RE.fullmatch(words[i].text)):
                i += 1
            continue
        if low in COMMA_LIST:
            i += 1
            while i < n and words[i].text.startswith(","):
                i += 1
            continue
        if low in ONE_OPERAND:
            i += 1
            continue
        if low in N_OPERANDS:
            # `ifs [@2] []` and `error "..."` take a fixed count; `org a`,
            # `org a,b`, `ds n`, `ds n,f` and `load f,p,l,b,lbl` take one plus
            # any comma-glued extras.
            exact = low in ("ifs", "error")
            limit = N_OPERANDS[low]
            taken = 0
            while i < n and taken < limit:
                if taken and not (exact or words[i].text.startswith(",")):
                    break
                i += 1
                taken += 1
                if exact and taken >= limit:
                    break
            continue
        if low in NO_OPERAND_INSN:
            continue
        if macro_names is not None and low in macro_names:
            # A macro may take no operand at all -- `done` is written as a bare
            # word next to a `hex` directive -- so one is only taken when the next
            # token is not itself the start of a statement. Instructions always
            # take exactly one.
            #
            # "Start of a statement" is decided by *column*, not by the keyword
            # list. x4's `pca0 hex f7 / done / pca1 hex 0f` put the next label at
            # column 0 after the soft wrap, and `pca1` is in no keyword list, so
            # the old test read it as an argument to `done`, which then emitted
            # `db a_done` and left `pca1` and `pca2` undefined.
            if i < n and not is_keyword(words[i].text, keywords) \
                    and not words[i].col0:
                i += 1
                while i < n and words[i].text.startswith(","):
                    i += 1
            continue
        # An instruction: one operand, possibly glued to a following comma.
        if i < n:
            i += 1
            while i < n and words[i].text.startswith(","):
                i += 1

    if len(starts) == 1:
        text = words[0].text if n == 1 else statement_text(words, 0, n)
        return [Line(stmt_where(where, words, 0),
                     text if words[0].col0 else "\t" + text)]

    out: list[Line] = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else n
        text = statement_text(words, start, end)
        # A fragment that is only a name is a label definition, and a label is
        # in column 0 even when the logical line began with code.
        at = stmt_where(where, words, start)
        if end - start == 1 and LABEL_RE.fullmatch(words[start].text) \
                and not is_keyword(words[start].text, keywords):
            out.append(Line(at, text))
        else:
            # A fragment that opens with a name opens with a label, and a
            # label is in column 0 (`sqh` half way along x5's `sql` line).
            lead = "" if not is_keyword(words[start].text, keywords) else "\t"
            out.append(Line(at, lead + text))
    return out


def is_keyword(text: str, keywords: set[str]) -> bool:
    return text.lower() in keywords


def statement_text(words: list[Word], start: int, end: int) -> str:
    return " ".join(w.text for w in words[start:end])


def stmt_where(where: str, words: list[Word], start: int) -> str:
    return where if words[start].line == 0 else f"{where}:{words[start].line}"


def logical_lines(text: str, where: str, keywords: set[str] | None = None,
                  report: list[str] | None = None,
                  macro_names: set[str] | None = None) -> list[Line]:
    """PDS lines: `CR NUL` ends one, a lone `CR` is a soft wrap inside one.

    The wrap is a property of the editor's screen, not of the source, and it
    does not respect token boundaries -- in x5 a wrap lands between a `hex`
    digit and the label that follows it (`...0401` + `sqh`). Segments are
    therefore rejoined with a space, which the tokeniser treats as ordinary
    whitespace, and comments are stripped per segment so that a comment on a
    continuation line cannot swallow the code after it.
    """
    out: list[Line] = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        if keywords is None:
            indented = raw[:1] in (" ", "\t")
            joined = " ".join(s for s in (strip_comment(x).strip()
                                          for x in raw.split("\r")) if s)
            if joined:
                at = f"{where}:{lineno}"
                out.append(Line(at, ("\t" + joined) if indented else joined))
            continue
        out.extend(scan_statements(words_of(raw, lineno), keywords,
                                   f"{where}:{lineno}", report, macro_names))
    return out


# ----------------------------------------------------------------- assembler

class Macro:
    __slots__ = ("name", "lines")

    def __init__(self, name: str):
        self.name = name
        self.lines: list[Line] = []


class Fixup:
    """A branch whose displacement is filled in once every label is known.

    ``offset`` is where the byte goes in the PRG image; ``after`` is the
    *logical* address the displacement is measured from, which is not the same
    space -- a bank assembled at $8000 can be addressed by the game at $A000.
    """

    __slots__ = ("offset", "after", "expr", "where", "scope")

    def __init__(self, offset: int, after: int, expr: str, where: str, scope: str):
        self.offset, self.after = offset, after
        self.expr, self.where, self.scope = expr, where, scope


# Tables the banks read but never define -- and *why* they are undefined.
#
# ANIM.SRC:289-312 indexes eight of them (`lda cpltab,x` and friends) to build
# the sprite frame pointers, and DISP.SRC:422-445 has the same eight in the same
# order. No name here is defined in any of the eight `X?.PDS` banks or in any
# `.SRC` the banks include, which is why they assemble as zero.
#
# CORRECTION 2026-10-02. What this comment used to assert -- "a gap in the 2012
# release", "defined nowhere in the release", "no amount of reading the source
# will produce the values" -- **was false**. `vendor/Magician-NES/SEQ.SRC` defines
# all eight, and it ships in the release:
#
#     SEQ.SRC:784  XPLTAB   SEQ.SRC:801  YPLTAB
#     SEQ.SRC:816  DPLTAB   SEQ.SRC:839  CPLTAB
#     SEQ.SRC:847  XPHTAB   SEQ.SRC:864  YPHTAB
#     SEQ.SRC:879  DPHTAB   SEQ.SRC:902  CPHTAB
#
# SEQ.SRC is 907 lines of the game's animation tables (`ANIMTAB` and the per-axis
# pointer lists) and reaches nothing, because its only `include` edge is
# `DISP.SRC:130`, spelled `include \zdev\seq.src` -- a DOS path from the Atari ST
# development machine that cannot resolve on a case-sensitive POSIX filesystem.
# `DISP.SRC` itself is included by no bank, so the edge is dead at both ends.
#
# So the values were never missing; they were unreachable. `build.py` now
# assembles `SEQ.SRC` at `org $a000` -- the address `DISP.SRC:129` gives it, and
# the one both `frame` routines agree with (`ora #>$a000`) -- which defines all
# eight here. Assembling it that way defines 497 symbols and collides with
# **zero** of the 3 125 the eight banks define, so nothing already resolved moves.
#
# The old fallback stays as a guard, not as a diagnosis: if a build ever assembles
# without SEQ.SRC, these names must still resolve to something rather than raise,
# and the use must be reported so the difference is never counted as a match.
SOURCE_GAPS = frozenset({
    "cphtab", "cpltab", "dphtab", "dpltab",
    "xphtab", "xpltab", "yphtab", "ypltab",
})


class Assembler:
    SLOT = 0x2000
    MAX_PASSES = 6

    def __init__(self, prg: bytearray, root: pathlib.Path, include_dirs: list[pathlib.Path],
                 verbose: bool = False):
        self.prg = prg
        self.root = root
        self.include_dirs = include_dirs
        self.verbose = verbose

        self.sym: dict[str, int] = {}
        self.sym_where: dict[str, str] = {}
        self.redefinable: set[str] = set()
        self.constants: set[str] = set()
        # Symbols already defined when the current pass began; see define().
        self.preexisting: set[str] = set()
        self.scope = ""

        self.macros: dict[str, Macro] = {}
        self.defining: str | None = None
        # Set only during collect_macros(); see line().
        self.collecting = False

        self.slot = 0
        self.phys = 0x8000
        self.log = 0x8000
        self.star = 0x8000            # what `*` means: the logical address

        self.fixups: list[Fixup] = []
        self.emitted: dict[int, int] = {}
        # PRG offsets filled from a `DAT` file, not assembled. See do_incbin.
        self.data_offsets: set[int] = set()
        self.unresolved: dict[str, str] = {}
        # Symbols in SOURCE_GAPS that were actually referenced; see value_or_defer.
        self.gaps_used: dict[str, str] = {}
        # x7's `if 0=1` level-table branch. See the module docstring: it holds
        # 27 640 bytes of real data and the cartridge was built with it on.
        self.force_conditions: dict[str, bool] = {}
        # Symbols whose assignment also switches the output slot. See
        # maybe_prebank(): x7's `memchk c000,b` banks the *next* group.
        self.prebank_symbols: frozenset[str] = frozenset()
        # Whether a bank-counter assignment also names the slot in the *next*
        # window. See maybe_prebank().
        self.prebank_split = False
        self.prebank_log: list[tuple[str, int, int]] = []
        # Bytes a module wrote past its slot window where the next window is a
        # different slot, so no file offset can be chosen for them. See
        # prg_offset(): folding them back over the module's own start is what
        # silently destroyed X5's `sql` table.
        self.overflow: list[tuple[str, int, int]] = []
        # PRG offsets written, attributed to the file that wrote them.
        #
        # `emitted` alone cannot answer "what did this module contribute",
        # because a module that overlays another -- which is exactly what
        # src/magician/TITLE.SRC does to `titdat` -- writes offsets that are
        # already in `emitted` with a different value. The count that matters
        # for an overlay is also not `len()`: it is the number of bytes that
        # ended up holding *this* file's value, so the attribution has to happen
        # at the write. A set of offsets, not a list, because `run_all` runs each
        # module up to MAX_PASSES times before the symbols settle and the second
        # pass overwrites the first: a list reports each address once per pass.
        self.writes_by_file: dict[str, set[int]] = {}
        # Bytes dropped because the module ran past `addr_ceiling`. Kept apart
        # from `overflow` because the two mean different things -- a ceiling is a
        # decision, an overflow is a hole -- and because `overflow` is cleared
        # between files so that its report names one module.
        #
        # A *set* of (file, address, slot), because the question the build asks
        # is "how many distinct bytes did this module assemble and not emit", and
        # `run_all` runs each module several times before the symbols settle. As
        # a list the same address is counted once per attempt, which reported
        # 1096 dropped bytes for X5 where there are 517.
        self.ceiling_drops: set[tuple[str, int, int]] = set()
        self.report: list[str] = []
        # Window base address -> 8 KiB slot, for a module whose logical span
        # crosses a window boundary and so needs a *different* slot in each. The
        # only module that does is `SEQ.SRC`; see prg_offset() and SEQ_MODULES
        # in build.py. A key mapped to None means "this window is real but the
        # tree does not say which slot is in it", and its bytes are recorded in
        # `overflow` rather than guessed at.
        self.window_slots: dict[int, int | None] = {}
        # Highest physical address this run may write, or None. A module with
        # no `org` that runs out of room in a window would otherwise land on
        # top of whatever the next module puts there, and the loss is invisible:
        # the later module simply wins. See X6_LIMIT in build.py.
        self.addr_ceiling: int | None = None
        # `error` directives to record instead of raising, and the ones recorded.
        # See the `error` case in `directive`.
        self.demo_errors: tuple[str, ...] = ()
        self.demo_seen: list[tuple[str, str]] = []

        self.cond: list[bool] = []
        self.cond_taken: list[bool] = []

        self.here = root
        self.allow_redefine = False
        # Set while collecting symbols for a project: a reference to a routine
        # that a later bank defines is normal here, and is reported once the
        # whole project has been assembled.
        self.tolerate = False
        self.keywords = set(DIRECTIVES) | set(OPCODES)
        self.macro_names: set[str] = set()

    # -- symbols ---------------------------------------------------------

    @staticmethod
    def number(tok: str) -> int:
        # `radix 10` is in effect, so bare digits are decimal unless the token
        # carries hex letters, which PDS reads as hex.
        return int(tok, 16) if any(c in "abcdefABCDEF" for c in tok) else int(tok, 10)

    def scoped(self, name: str) -> str:
        k = name.lower()
        return f"{self.scope}|{k}" if k.startswith("!") else k

    def define(self, name: str, value: int, where: str, redefinable: bool = False,
                constant: bool = False):
        k = self.scoped(name)
        # Passes after the first re-assemble the same module, so their label
        # definitions are allowed to land on top of their own.
        if k in self.sym and not (redefinable or self.allow_redefine) \
                and k not in self.redefinable and k not in self.constants:
            # A source label may legitimately be defined twice at two *different*
            # addresses: x5's `sql` table is emitted twice, at $C000 and $C080, and
            # both copies are really in the cartridge. Twice at the *same* address
            # is never that -- it is this scanner having cut one logical line into
            # two statements, which would silently shift every address after it.
            #
            # `preexisting` is what separates the two cases. It holds the symbols
            # that were already defined when this pass began, so a name that was
            # defined *during* this pass (`k not in preexisting`) appearing again at
            # its own address is a mis-cut, while the same definition arriving on a
            # later pass -- which run_all does to the whole project and run_file
            # does to one module -- is just a pass landing on top of itself.
            fresh = k not in self.preexisting
            if self.sym[k] != value:
                # A `!local` label duplicating is what `memchk` does -- it emits
                # a group into both slots of a 16 KiB bank, so every local label
                # in the group is defined once per slot. That is 1400-odd entries
                # of pure noise, so only global names are recorded. A global one
                # is worth a reader's attention: x5's `sql` table is emitted twice
                # on purpose, and `exitm` is called from the `st` macro and
                # defined nowhere.
                #
                # No addresses: they move on every pass, so including them made
                # 1448 "distinct" entries out of one site.
                if not name.startswith("!"):
                    self.report.append(f"redefined {name!r} at {where}")
            elif fresh:
                raise AsmError(f"symbol {name!r} already = ${self.sym[k]:04X} "
                               f"(defined at {self.sym_where.get(k, '?')})", where)
        elif k in self.constants and self.sym[k] != value:
            # An `equ` is a constant, so re-evaluating it on a later pass is
            # normal and lands on the same value. A *different* value means a
            # forward reference resolved between passes -- interesting, because it
            # is how a build that moves shows up, but never an error.
            self.report.append(f"constant {name!r} moved at {where}")
        self.sym[k] = value
        self.sym_where[k] = where
        if redefinable:
            self.redefinable.add(k)
        if constant:
            self.constants.add(k)

    def lookup(self, name: str) -> int:
        k = self.scoped(name)
        if k in self.sym:
            return self.sym[k]
        if k.startswith("!"):
            # `!a` is scoped to the enclosing global label. A reference from
            # outside that scope is only accepted when it is unambiguous.
            hits = {kk: vv for kk, vv in self.sym.items() if kk.endswith("|" + k)}
            if len(hits) == 1:
                return next(iter(hits.values()))
        raise AsmError(f"undefined symbol {name!r}")

    # -- output ----------------------------------------------------------

    @staticmethod
    def slot_origin(slot: int) -> int:
        """Where MMC3 presents 8 KiB slot ``slot``, as a CPU address.

        The game's own MMC3 documentation (X5.PDS:10-21) is the whole story,
        and it is a MMC3 not a MMC1:

            $8000-$9FFF : register 6 -- any of slots $00..$0D
            $A000-$BFFF : register 7 -- any of slots $00..$0D
            $C000-$DFFF : slot $0E, whatever the registers say
            $E000-$FFFF : slot $0F, whatever the registers say

        So 14 and 15 are *fixed* to $C000 and $E000 and everything else is
        switchable, presented at $8000 unless the source puts it in the
        register-7 window.

        This used to be `0x8000 + (slot & 3) * 0x2000`, which is the **MMC1**
        rule -- 16 KiB banks, two of them, only four possible combinations. It
        is wrong here for 14 of the 16 slots, and the two that were visibly
        wrong were the two that did the most damage: slot 2 mapped to $C000
        (where MMC3 puts slot 14) and slot 3 to $E000 (where MMC3 puts slot 15).
        X6 has no `org` of its own, so `slot_origin` alone decided that it
        assembled at $E000 while its bytes were filed in slot 3, and `reset`'s
        `jsr initcols` -- a jump into the fixed $E000 window from inside that
        same window -- read slot 15 at $E9E9, which is a hole. The ROM reached
        `$E9E9`, executed a `brk`, and went round the reset vector until the
        frame budget ran out. Max luminance over the picture area: 0.
        """
        if slot == 14:
            return 0xC000
        if slot == 15:
            return 0xE000
        return 0x8000

    def prg_offset(self, phys: int | None = None) -> int:
        """Where a byte at physical address `phys` goes in the PRG image.

        A byte belongs to the 8 KiB *window* its address falls in -- `$8000`,
        `$A000`, `$C000` or `$E000` -- and takes the offset within that window.
        Which slot supplies the window is `self.slot`, set by `bank`.

        Two things about this source make the obvious implementation wrong, and
        both were measured against the cartridge rather than reasoned about:

        * **`$8000` means "the start of the current bank", not "the `$8000`
          window".** Every level-data group opens with `load 10.blk,$8000,$a000`
          and its bank counter `b = $8` / `$9` / … `$d` names the *slot*, so
          physical `$8000` has to land at offset 0 of slot 8, 9, … `$d`. A window
          table taken from `slot_origin` would put `$8000` at offset 0 of slot 8
          but at offset `-0x2000` of slot `$d`, dropping the whole group. This is
          also what the source itself means: `load mus.mus,$8000` is commented
          "org must = $8000!".

        * **The MMC3 fixed window is contiguous, and a module may run past it.**
          X5 is `org $c000` in slot 14 and runs to `$E4DB` -- 9 227 bytes into an
          8 KiB window. Subtracting a hardcoded `$8000` and masking with `& 0x1FFF`
          folded those 1 035 bytes back onto `0x1C000`, overwriting the
          `sql`/`sqh`/`cos` tables X5 had emitted there ten lines earlier, so the
          `sql` table -- which the cartridge carries byte-identically at file
          `0x1C000` *and* `0x1C080` -- was missing from all 131 072 bytes of the
          rebuild. `$E000` in slot 14 is genuinely slot 15, and that is where the
          cartridge has it.

        So the window is `$8000 + ((p - $8000) & $6000)`, the offset is `p`
        within it, and slot 14 hands `$E000`-`$FFFF` to slot 15. Beyond that the
        address cannot be placed without a `bank` directive saying which slot is
        there, so the byte is dropped and recorded in `overflow` rather than
        folded.

        `window_slots` overrides all of that for a module that genuinely spans
        two windows. `SEQ.SRC` is the case that forced it, and it is worth
        writing out because the naive reading loses data silently:

        Assembled at `org $a000` -- the only address the tree gives it
        (`DISP.SRC:129`) and the one both `frame` routines agree with
        (`ANIM.SRC:283`, `ora #>$a000`) -- it runs `$A000`-`$C676`: 9 827 bytes,
        the whole `$A000` window and then 1 654 bytes past it. With one
        `self.slot` for the whole module, `$A000` and `$C000` compute the *same*
        file offset, so the tail overwrites the head: 9 827 bytes assembled into
        8 192 distinct offsets, and `ANIMTAB` -- which the cartridge carries
        byte-identically at file `$0A000` -- destroyed by its own tail. So the
        module is told which slot is in each window it reaches into; see
        `SEQ_WINDOW_SLOTS` in `build.py`. A window the map does not mention, or
        maps to `None`, is recorded in `overflow` rather than guessed at.
        """
        p = self.phys if phys is None else phys
        if not 0x8000 <= p < 0x10000:
            self.overflow.append((self.here.name, p & 0xFFFF, self.slot))
            return -1
        if self.addr_ceiling is not None and p >= self.addr_ceiling:
            # Counted separately from `overflow`. `overflow` is reset per file,
            # so the ceiling drops of every module but the last one never reach
            # the build log -- which is why the byte totals for the ceilings in
            # build.py were prose rather than a measurement.
            self.ceiling_drops.add((self.here.name, p & 0xFFFF, self.slot))
            return -1
        win = 0x8000 + ((p - 0x8000) & 0x6000)
        slot = self.slot
        # The two fixed windows, and they are fixed *absolutely*. In MMC3's 8 KiB
        # PRG mode register 1 is the second-to-last bank and register 7 is the
        # last one, so $C000-$DFFF is slot 14 and $E000-$FFFF is slot 15 whatever
        # `self.slot` says. This used to be `if win >= 0xE000 and slot == 14`,
        # which only got the $E000 half right and only for a module that
        # happened to be assembled in slot 14. X2 assembles at $A000 in slot 1
        # and runs on to $CC11; with `self.slot` still 1, its $C000-$CC11 tail
        # computed the same file offsets as its own $A000-$BBFF head and
        # overwrote 7 554 bytes of it -- 8192 bytes of X2 assembled into 8192
        # distinct offsets out of the 15 746 it emitted, silently.
        if win == 0xC000:
            slot = 14
        elif win == 0xE000:
            slot = 15
        if self.window_slots:
            want = self.window_slots.get(win)
            if want is None:
                self.overflow.append((self.here.name, p & 0xFFFF, self.slot))
                return -1
            slot = want
        return slot * self.SLOT + (p - win)

    def emit(self, byte: int):
        off = self.prg_offset()
        if 0 <= off < len(self.prg):
            self.prg[off] = byte & 0xFF
            self.emitted[off] = byte & 0xFF
            if self.here is not None:
                self.writes_by_file.setdefault(self.here.name, set()).add(off)
        self.phys += 1
        self.log += 1
        self.star = self.log

    def emit_word(self, value: int):
        self.emit(value & 0xFF)
        self.emit((value >> 8) & 0xFF)

    # -- expressions -----------------------------------------------------

    def value(self, text: str) -> int:
        return Expr(tokenize_expr(text), self).parse()

    def value_or_defer(self, text: str, where: str) -> int:
        try:
            return self.value(text)
        except AsmError as e:
            if "undefined symbol" not in e.raw:
                raise
            name = e.raw.split("'")[1]
            if name.lower() in SOURCE_GAPS:
                # See SOURCE_GAPS: defined nowhere in the released source.
                self.gaps_used.setdefault(name.lower(), where)
                return 0
            self.unresolved.setdefault(name, where)
            return 0

    # -- driving a module ------------------------------------------------

    def read_source(self, path: pathlib.Path) -> str:
        data = path.read_bytes()
        if len(data) > 0x200 and data[:4] == b"\x01\x00\x05\x02":
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
            from pds_extract import decode  # noqa: E402
            return decode(data)
        return data.decode("latin-1")

    def snapshot(self) -> dict:
        # Everything a run mutates. Leaving the slot and the address counters out
        # of this was not a shortcut: the 16-slot search in build.py ends on slot
        # 15, and a restore that did not put the slot back meant every module
        # whose slot the search could not determine was then assembled at slot
        # 15 -- six modules stacked into one 8 KiB window, overwriting each
        # other, and a ROM that looked plausible and booted to a black screen.
        return {"sym": dict(self.sym), "where": dict(self.sym_where),
                "redef": set(self.redefinable), "const": set(self.constants),
                "pre": set(self.preexisting), "data": set(self.data_offsets),
                "slot": self.slot, "phys": self.phys, "log": self.log,
                "star": self.star, "prebank": list(self.prebank_log),
                "wslots": dict(self.window_slots),
                "ceiling": self.addr_ceiling}

    def restore(self, snap: dict):
        self.sym = dict(snap["sym"])
        self.sym_where = dict(snap["where"])
        self.redefinable = set(snap["redef"])
        self.constants = set(snap["const"])
        self.preexisting = set(snap["pre"])
        self.data_offsets = set(snap["data"])
        self.slot = snap["slot"]
        self.phys, self.log, self.star = snap["phys"], snap["log"], snap["star"]
        self.prebank_log = list(snap["prebank"])
        self.window_slots = dict(snap["wslots"])
        self.addr_ceiling = snap["ceiling"]

    def prescan(self, paths: list[pathlib.Path]):
        """Collect macro names up front: x1-x7 use macros that x0 defines, and a
        keyword has to be known before a line can be split into statements."""
        for path in paths:
            text = self.read_source(path)
            # The soft wrap has to become a space, not disappear: a lone CR is
            # ordinary whitespace inside one logical line, and deleting it fused
            # `pti<CR>macro` into `ptimacro`, so the regex missed the macro and
            # `pti` was left unrecognised -- its body then assembled as the bare
            # instructions `pti`, `10`.
            for m in re.finditer(r"(?m)^[ \t]*([A-Za-z_][A-Za-z0-9_!]*)[ \t]+macro\b",
                                 text.replace("\r", " ")):
                self.keywords.add(m.group(1).lower())
                self.macro_names.add(m.group(1).lower())

    def collect_macros(self, paths: list[pathlib.Path]):
        """Register every macro *body* up front, not just its name.

        All 40 macros are defined in X0.PDS, so without this every other module
        could only be assembled after X0 had run -- which makes a module
        impossible to score on its own, and scoring modules one at a time is how
        the slot search works. This pass reads the definitions and nothing else,
        following `include` so a macro defined in an include is found too.
        """
        self.collecting = True
        try:
            for path in paths:
                self.process(logical_lines(self.read_source(path), str(path),
                                           self.keywords, self.report,
                                           self.macro_names))
        finally:
            self.collecting = False
            self.defining = None
            self.cond, self.cond_taken = [], []
            self.scope = ""

    def run_all(self, paths: list[pathlib.Path], slots: list[int | None],
                origins: dict[str, int] | None = None,
                window_slots: dict[int, dict[int, int | None]] | None = None,
                ceilings: dict[str, int] | None = None):
        """Assemble every module in order, repeating until nothing moves.

        The eight banks are one program: x0's reset code calls routines that
        x1-x7 define, so a single pass over the project cannot resolve
        everything. Pass two can, because the symbol table carries over.

        A `slots` entry of `None` means "continue from wherever the previous
        module stopped", which is what a module with no `org` of its own does --
        see `CHAINED` in build.py for the one place that is used, and why.

        `origins` supplies a starting address for a module that has no `org` of
        its own either, and `window_slots` the slot in each window it reaches
        into; both are keyed by file name. See `run_file`, `prg_offset`, and
        `SEQ_MODULES` / `SEQ_WINDOW_SLOTS` in build.py for the module that needs
        them.
        """
        origins = origins or {}
        window_slots = window_slots or {}
        ceilings = ceilings or {}
        # Attribution is per-project-pass. Pass 1 in build.py runs modules through
        # `run_file` sixteen times each to search for a slot, and those writes
        # name files that were only ever tried at a slot and rejected; carrying
        # them into the report would credit a module with bytes a later module
        # overwrote.
        self.writes_by_file = {}
        prev: dict[str, int] | None = None
        for attempt in range(self.MAX_PASSES):
            self.tolerate = True
            for path, slot in zip(paths, slots):
                self.run_file(path, slot=slot, origin=origins.get(path.name),
                              window_slots=window_slots.get(path.name),
                              addr_ceiling=ceilings.get(path.name))
            current = dict(self.sym)
            if current == prev:
                break
            prev = current
        self.tolerate = False
        # One last clean pass, with the now-known symbols, so the emitted bytes
        # come from a run in which every reference resolved.
        #
        for path, slot in zip(paths, slots):
            self.run_file(path, slot=slot, origin=origins.get(path.name),
                          window_slots=window_slots.get(path.name),
                          addr_ceiling=ceilings.get(path.name))
        return self

    def run_file(self, path: pathlib.Path, slot: int | None = None,
                 origin: int | None = None,
                 window_slots: dict[int, int | None] | None = None,
                 addr_ceiling: int | None = None):
        """Assemble one module, iterating until its symbols stop moving.

        Zero-page versus absolute is chosen from the *value* of the operand, so
        a forward reference in the first pass is assumed absolute and may make
        every later address move. Re-running the module with the symbols now
        known settles it; the loop stops when a pass changes no symbol.

        `origin` sets the starting address, as an `org` would. It exists for
        `SEQ.SRC`, which has no `org` of its own because the one that places it
        lives in its includer: `DISP.SRC:129-130` is literally

            org $a000
            include \\zdev\\seq.src

        and nothing else in the tree names `$A000`. Supplying the origin here is
        the honest way to honour that line without pulling in the rest of a file
        that is a superseded 797-byte prototype (see `MODULE_NOTES` in
        build.py).

        `window_slots` says which 8 KiB slot is in each window the module's
        logical span reaches, and is only needed by the same module; see
        `prg_offset`.
        """
        saved_wslots, saved_ceiling = self.window_slots, self.addr_ceiling
        self.window_slots = dict(window_slots or {})
        self.addr_ceiling = addr_ceiling
        try:
            self._run_file(path, slot=slot, origin=origin,
                           base_wslots=dict(self.window_slots))
        finally:
            self.window_slots, self.addr_ceiling = saved_wslots, saved_ceiling

    def _run_file(self, path: pathlib.Path, slot: int | None = None,
                  origin: int | None = None, base_wslots=None):
        lines = logical_lines(self.read_source(path), str(path), self.keywords,
                              self.report, self.macro_names)
        # `self.here` is only re-pointed by `include`, so without this the
        # overflow and prebank reports name the directory instead of the module
        # -- and `overflow` is the report that says which module lost bytes.
        saved_here = self.here
        self.here = path
        try:
            self._assemble(path, lines, slot=slot, origin=origin,
                           base_wslots=dict(self.window_slots))
        finally:
            self.here = saved_here

    def _assemble(self, path: pathlib.Path, lines, slot: int | None = None,
                  origin: int | None = None, base_wslots=None):
        if slot is not None:
            self.slot = slot
            base_addr = (self.slot_origin(slot),) * 3
        else:
            base_addr = (self.phys, self.log, self.star)
        if origin is not None:
            base_addr = (origin,) * 3
        base_slot = self.slot
        # The address counters are restored per attempt. They are only ever moved
        # by `org`, and x7 has no leading `org` -- so a run that raised halfway
        # through left its address behind and the next run picked it up as its
        # own starting point. That is how an `error ">$FB80!` in x7 turned out to
        # be a leak from the *previous* module rather than a real overrun, and it
        # is why every one of x7's sixteen slots looked broken.
        # The symbol table is *not* reset between passes: pass two is what makes
        # a forward reference resolvable, which is the whole reason for more
        # than one pass. Redefinition is only allowed from the second pass on,
        # so a genuine duplicate label is still caught on the first.
        prev: dict[str, int] | None = None
        for attempt in range(self.MAX_PASSES):
            self.allow_redefine = attempt > 0
            self.slot = base_slot
            self.phys, self.log, self.star = base_addr
            self.preexisting = set(self.sym)
            self.fixups = []
            self.emitted = {}
            self.data_offsets = set()
            self.unresolved = {}
            self.overflow = []
            # Not cumulative: the point is which tables are still unresolved in
            # the run whose bytes are kept, not which were unresolved in some
            # earlier pass of the same module.
            self.gaps_used = {}
            self.cond = []
            self.cond_taken = []
            self.scope = ""
            # `window_slots` is installed by maybe_prebank() mid-module, so it
            # has to be cleared per attempt like the other per-run state.
            self.window_slots = dict(base_wslots or {})
            self.process(lines)
            current = dict(self.sym)
            if current == prev:
                break
            prev = current
        self.allow_redefine = False
        self.resolve()
        if self.unresolved and not self.tolerate:
            where = next(iter(self.unresolved.values()))
            names = ", ".join(sorted(self.unresolved))
            raise AsmError(f"undefined symbol(s): {names}", where)

    def find_file(self, name: str) -> pathlib.Path:
        raw = name.replace("\\", "/").strip('"')
        base = self.here.parent
        for d in [base, self.root] + self.include_dirs:
            for variant in (raw, raw.lower(), raw.upper(), pathlib.Path(raw).name):
                p = d / variant
                if p.is_file():
                    return p
        target = raw.split("/")[-1].lower()
        for d in [base, self.root]:
            if d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file() and f.name.lower() == target:
                        return f
        raise AsmError(f"file not found: {name}")

    def process(self, lines: list[Line]):
        for ln in lines:
            if ln.op is None and ln.label is None:
                continue
            try:
                self.line(ln)
            except AsmError as e:
                if e.where:
                    raise
                raise AsmError(f"{e.raw}  [{ln.raw.strip()[:60]}]", ln.where) from None
            if ln.op == "end":
                return

    def active(self) -> bool:
        return all(self.cond)

    # -- one source line -------------------------------------------------

    def line(self, ln: Line):
        if ln.op == "macro":
            self.defining = ln.label.lower() if ln.label else None
            self.macros[self.defining] = Macro(self.defining or "?")
            return
        if ln.op == "endm":
            self.defining = None
            return
        if self.defining is not None:
            for part in scan_statements(words_of(ln.raw), self.keywords,
                                         ln.where, self.report, self.macro_names):
                self.macros[self.defining].lines.append(part)
            return

        # Macro-collection pass: `macro`/`endm` were handled above, so everything
        # else here is a statement we only want the *names* of.
        if self.collecting:
            if ln.op == "include":
                self.do_include(ln, ln.operands)
            return

        if not self.active():
            # Keep the conditional stack balanced across a skipped block.
            if ln.op in ("if", "ifs"):
                forced = self.forced_condition(ln)
                self.cond.append(bool(forced))
                self.cond_taken.append(bool(forced))
            elif ln.op == "else":
                if self.cond:
                    self.cond_taken[-1] = not self.cond_taken[-1]
                    self.cond[-1] = self.cond_taken[-1] and all(self.cond[:-1])
            elif ln.op == "endif":
                if self.cond:
                    self.cond.pop()
                    self.cond_taken.pop()
            return

        if ln.label:
            if ln.op == "equ":
                self.define(ln.label, self.value(ln.operands), ln.where,
                            constant=True)
                return
            if ln.op == "=":
                value = self.value(ln.operands)
                self.define(ln.label, value, ln.where, redefinable=True)
                self.maybe_prebank(ln.label, value)
                return
            if ln.op == "macro":
                return
            # A global label opens a new `!local` scope; a local label is
            # itself scoped and does not change it.
            self.define(ln.label, self.log, ln.where)
            if not ln.label.startswith("!"):
                self.scope = ln.label.lower()

        if ln.op is None:
            return
        if ln.op in DIRECTIVES:
            self.exec_directive(ln, ln.operands)
            return
        if ln.op in self.macros:
            self.expand_macro(self.macros[ln.op], split_operands(ln.operands), ln)
            return
        self.instruction(ln)

    def split_macro_args(self, operands: str) -> list[str]:
        return split_operands(operands) if operands.strip() else []

    def maybe_prebank(self, name: str, value: int):
        """Bank-switch when a *bank counter* symbol is assigned.

        X7's data section is laid out as a run of groups, each opened by a
        counter and closed by a check:

            b  = $8              ;current bank
            load 10.blk,$8000,$a000,,l10b
            ...
            memchk c000,b

        `memchk` is `if *>$@1 error` then `bank @2` -- so it issues `bank b`
        *after* the group it belongs to. Assembled literally, every group
        therefore lands one 8 KiB slot below the bank its own `b` names, and the
        run is off by one from end to end. Measured against the cartridge, that
        is exactly what happens: `10.blk`, which `b = $8` says belongs in slot 8,
        is found at file `0x0C000` = slot 6, `50.blk` (`b = $9`) at `0x10000` =
        slot 8, and so on to `70.blk` (`b = $d`) at `0x18000` = slot 12.

        The cartridge has them where `b` says: `10.blk`'s block data at
        `0x1027C`-relative `l10m` = `$A27C` in slot 8, `l50m` = `$A26C` in slot 9,
        `l70m` = `$A270` in slot 13 -- every level-data label's logical address
        already agrees with the cartridge, so only the slot was wrong.

        Banking on the counter assignment instead puts every group in the slot
        its own `b` names, and takes the rebuilt PRG from 6.40% of the cartridge
        to 28.83%: banks 4, 5 and 6 (the level data) go from 4.34/6.15/3.31% to
        58.99/79.93/51.15%. See PROVENANCE.md.

        Only symbols named in `prebank_symbols` are affected, and only when the
        new value is a valid slot -- so this cannot fire on an ordinary counter.
        Set `prebank_symbols = ()` to switch it off and get the literal reading
        of the released source.

        With `prebank_split` on, the assignment also says which slot is in the
        *next* window. X7's own bank constants demand it:

            b    = $6
            load mus\\mus.mus,$8000
            ...
            org *,*&$dfff        ;(used at $8000..$9FFF)
            include probdat.src
            memchk c000,b
            bmus  equ b           ;music data bank (16k)
            bshop equ b+1         ;shop data bank
            btit  equ b+1 ... bpan equ b+1 ... bev equ b+1

        `bmus` is `b` and everything else is `b+1`, and the code agrees:
        `X0.PDS:624` `bnk 7,#btit`, `X1.PDS:743` `bnk 7,#bpan` -- register 7 is
        the `$A000` window (`X5.PDS:11-21`) -- and those routines read `titdat`
        and `pandat`, whose logical addresses `org *,*&$dfff` has folded back
        into `$8000-$9FFF`. So one group occupies two slots: `b` for its
        `$8000` half and `b+1` for its `$A000` half, and the logical wrap is what
        lets code at `$A0xx` be reached as `$80xx`.

        Without this the whole run collapses onto one slot: `$A000` and `$C000`
        both compute the same file offset for a module with a single `self.slot`,
        so the group overwrites itself from `$A000` on. Measured, that is what
        happens -- slot 6 scores 1.7% against the cartridge and **slot 7 scores
        0 non-zero bytes out of the 6 756 the cartridge has there.**
        """
        if not self.prebank_symbols:
            return
        if name.lower() not in self.prebank_symbols:
            return
        slot = value & 0x0F
        if slot == self.slot:
            return
        self.slot = slot
        if self.prebank_split:
            # `$C000`/`$E000` are the fixed windows (banks $0E/$0F), so they are
            # named here too: SAM.SAM is loaded at $FB80 inside this same run of
            # assignments and would otherwise fall out of the map.
            self.window_slots = {0x8000: slot, 0xA000: slot + 1,
                                 0xC000: 14, 0xE000: 15}
        self.prebank_log.append((self.here.name, self.star, slot))

    def expand_macro(self, macro: Macro, args: list[str], at: Line):
        binding = (args + [""] * 10)[:10]
        saved_scope = self.scope
        try:
            for body in macro.lines:
                self.line(self.substitute(body, binding))
        finally:
            self.scope = saved_scope

    def substitute(self, ln: Line, binding: list[str]) -> Line:
        def sub(text: str) -> str:
            out, i = [], 0
            while i < len(text):
                if text[i] == "@" and i + 1 < len(text) and text[i + 1].isdigit():
                    n = int(text[i + 1])
                    if 1 <= n <= 9:
                        out.append(binding[n - 1])
                        i += 2
                        continue
                out.append(text[i])
                i += 1
            return "".join(out)

        new = Line.__new__(Line)
        new.where = ln.where
        new.raw = sub(ln.raw)
        new.label = sub(ln.label) if ln.label else None
        # `op` has to be substituted too: substitution is textual, so a macro
        # invoked through a parameter (`@1 partime`) has to dispatch on the name
        # that was passed, not on the literal `@1`.
        new.op = sub(ln.op) if ln.op else None
        new.operands = sub(ln.operands)
        return new

    # -- directives ------------------------------------------------------

    def exec_directive(self, ln: Line, operands: str):
        op = ln.op
        if op == "if":
            forced = self.forced_condition(ln)
            state = self.truth(operands) if forced is None else forced
            self.cond.append(state)
            self.cond_taken.append(state)
        elif op == "ifs":
            a, b = self.ifs_args(operands)
            state = a == b
            self.cond.append(state)
            self.cond_taken.append(state)
        elif op == "else":
            if not self.cond:
                raise AsmError("else without if", ln.where)
            self.cond_taken[-1] = not self.cond_taken[-1]
            self.cond[-1] = self.cond_taken[-1] and all(self.cond[:-1])
        elif op == "endif":
            if not self.cond:
                raise AsmError("endif without if", ln.where)
            self.cond.pop()
            self.cond_taken.pop()
        elif op == "org":
            self.do_org(ln, operands)
        elif op == "bank":
            self.slot = self.value(operands) & 0x0F
        elif op == "hex":
            digits = "".join(operands.split())
            if len(digits) % 2:
                raise AsmError(f"hex with an odd digit count: {operands!r}", ln.where)
            for i in range(0, len(digits), 2):
                self.emit(int(digits[i:i + 2], 16))
        elif op in ("db", "dc", "dw", "dl", "dh"):
            # `dc` is `db` under its own name: "define character".
            #
            # `dl` and `dh` are the LOW and HIGH halves of a pointer list and each
            # emits exactly ONE byte. They were `dl`=4 and `dh`=2 here, which is
            # the Atari MACRO reading of `dl` ("define long") applied to a source
            # that does not use it that way -- and it was wrong for every use in
            # this tree. Three independent proofs, none of them a guess:
            #
            #   * `SHOPDAT.SRC:451-456` writes `ijvl dl <18 pointers>` followed by
            #     `ijvh dh <the same 18>`, and `x5.PDS:1405-1409` reads them as
            #     `lda ijvl,x / sta t2 / lda ijvh,x / sta t3 / jmp (t2)`. So the
            #     pair is one byte each per entry and the pair is 36 bytes. Beta 1
            #     has 36 bytes there; this assembler emitted 72 per table.
            #   * `MISC.SRC:866-869` writes `dl 35,0,0,40,45,50,55,60` -- a list of
            #     values in 0..180, indexed as bytes.
            #   * `ANIM.SRC:595-601` writes `dl l20m+$100*$06+$0a8,...` and then
            #     `dh` of the *same* expressions. The `+$100*$06` term only moves
            #     the high byte, so `dh` must be the high-byte emitter; with width 2
            #     it duplicated the whole pointer instead.
            #
            # This is a class-`c` finding -- an assembler bug, not a source/cart
            # difference -- so it is fixed here and no bytes are taken from a
            # cartridge to paper over it.
            width, base = {"db": (1, 0), "dc": (1, 0), "dw": (2, 0),
                           "dl": (1, 0), "dh": (1, 1)}[op]
            for item in split_operands(operands):
                # `db "ABC"` emits one byte per character, not one byte per
                # string. `Expr.string_value` folds a string into a little-endian
                # integer and the `& 0xFF` below kept only the low byte, so
                # `db "MAGIC1+"` emitted `4D` -- a single `M` -- and the cartridge's
                # `4D 41 47 49 43 2D 2A 20 10 90` at file `0x1FFF0` became
                # `4D 25 02 90`. It also collapses tables that are *indexed*:
                # `x6.PDS:186`'s `slet1 db "    AIIAIXLUATAA ",qu` is read by
                # `ldx slet1,y` and `sta slet1,x` (`x6.PDS:234`, `:467`), so the
                # two bytes this emitted instead of eighteen silently moved every
                # following address.
                #
                # Only `db`/`dc` are byte-per-character. A string is not a number,
                # so `dw "AB"` has no defined width here; the source never writes
                # one, and it still folds as before rather than inventing a width.
                # Only `db`/`dc` are byte-per-character. `dl` also has width 1 now,
                # but that is a coincidence of arithmetic, not a licence: the source
                # writes no `dl "..."`, and treating a string as a list of low bytes
                # would be inventing a rule.
                if op in ("db", "dc") and item[:1] == '"' and len(item) >= 2 \
                        and item[-1:] == '"':
                    for ch in item[1:-1]:
                        self.emit(ord(ch) & 0xFF)
                    continue
                v = self.value_or_defer(item, ln.where)
                for k in range(base, base + width):
                    self.emit((v >> (8 * k)) & 0xFF)
        elif op == "ds":
            args = split_operands(operands)
            count = self.value(args[0])
            fill = self.value(args[1]) if len(args) > 1 else 0
            for _ in range(count):
                self.emit(fill)
        elif op == "incbin":
            self.do_incbin(ln, operands)
        elif op == "include":
            self.do_include(ln, operands)
        elif op == "error":
            msg = operands.strip()
            if len(msg) >= 2 and msg[0] == '"' and msg[-1] == '"':
                msg = msg[1:-1]
            if any(pat in msg for pat in self.demo_errors):
                # A source-level `if`/`error` that measurement has shown to be
                # wrong for this build. Swallowing it is a decision, not a fix,
                # so it is recorded and printed rather than passed over: the
                # `else` arm of the guard is skipped, which is exactly what
                # makes the thing the guard was protecting get overwritten.
                self.demo_seen.append((msg, ln.where))
                return
            raise AsmError(f"error directive: {msg}", ln.where)
        elif op in ("send", "option", "radix", "name", "page", "space", "end"):
            pass
        else:
            raise AsmError(f"unknown directive {op!r}", ln.where)

    def truth(self, text: str) -> bool:
        return self.value(text) != 0

    def forced_condition(self, ln: Line) -> bool | None:
        """An override for this statement's conditional, or None if there is none.

        `force_conditions` is keyed by the *condition text* -- `{"0=1": True}` for
        x7's level-table branch -- so the key has to be built from the statement's
        operands, not from its raw text. It used to be
        `ln.raw.strip().replace(" ", "")`, which keeps the keyword: the statement
        is `\tif 0=1`, so the key that was looked up was `if0=1` and never matched
        `0=1`. The override therefore never fired, `truth("0=1")` decided the
        branch instead, and the branch was false -- so `--x7-level-table on` and
        `off` produced byte-identical PRGs and 27 640 bytes of real level data were
        skipped. `if 0=1` also carries a leading tab, so stripping spaces is not
        enough on its own.
        """
        if not self.force_conditions:
            return None
        text = ln.raw.strip()
        for kw in ("if ", "ifs ", "if\t", "ifs\t"):
            if text.lower().startswith(kw):
                text = text[len(kw):]
                break
        else:
            return None
        return self.force_conditions.get("".join(text.split()).lower())

    def ifs_args(self, operands: str):
        """`ifs [@2] []` -- compare a parameter's *text* with a literal."""
        parts, i = [], 0
        text = operands.strip()
        while i < len(text):
            if text[i] == "[":
                j = text.index("]", i)
                parts.append(text[i + 1:j].strip())
                i = j + 1
                continue
            if text[i].isspace():
                i += 1
                continue
            j = i
            while j < len(text) and text[j] not in " \t[":
                j += 1
            parts.append(text[i:j])
            i = j
        if len(parts) < 2:
            raise AsmError(f"ifs needs two operands, got {operands!r}")
        return parts[0], parts[1]

    def do_org(self, ln: Line, operands: str):
        args = split_operands(operands)
        if not args or not args[0]:
            raise AsmError("org with no operand", ln.where)
        self.phys = self.org_value(args[0], self.phys)
        if len(args) >= 2:
            self.log = self.org_value(args[1], self.log)
        else:
            self.log = self.phys
        self.star = self.log

    def org_value(self, text: str, star: int) -> int:
        """Evaluate an `org` argument, where `*` is the address being set."""
        text = text.strip()
        if text == "*":
            return star
        if "*" in text:
            head, _, tail = text.partition("*")
            lhs = self.org_value(head, star) if head.strip() else star
            return self.org_apply(lhs, tail.strip())
        return self.value(text)

    def org_apply(self, lhs: int, rhs: str) -> int:
        """The tail of an `org` argument: operators then a value.

        x7 pads its event data with `org *,*&$dfff`, so the tail is not just a
        number -- it is an operator followed by one. Only `$`-prefixed digits were
        handled, which handed `&$dfff` to `int()` and raised.
        """
        ops = ""
        while rhs[:1] in ("&", "|", "+", "-", "^"):
            ops += rhs[0]
            rhs = rhs[1:].strip()
        if not ops:
            return self.value(rhs)
        value = self.value(rhs)
        for op in reversed(ops):
            if op == "&":
                lhs &= value
            elif op == "|":
                lhs |= value
            elif op == "^":
                lhs ^= value
            elif op == "+":
                lhs += value
            else:
                lhs -= value
        return lhs

    def do_incbin(self, ln: Line, operands: str):
        path = self.find_file(operands)
        data = path.read_bytes()
        start_log, start_slot = self.log, self.slot
        first = self.prg_offset()
        for b in data:
            self.emit(b)
        # Remember which offsets came from a data file. These bytes are
        # byte-identical to the cartridge's whatever the code does, so they are
        # the only reliable evidence for *which* slot a module belongs in --
        # build.py measures over this set, not over the whole output.
        self.data_offsets.update(range(first, first + len(data)))
        if self.verbose:
            print(f"  incbin {path.name:16s} {len(data):5d}B  slot {start_slot:2d} "
                  f"log ${start_log:04X}-${self.log - 1:04X}")

    def do_include(self, ln: Line, operands: str):
        path = self.find_file(operands)
        saved_here, saved_scope = self.here, self.scope
        self.here = path
        try:
            self.process(logical_lines(self.read_source(path), str(path),
                                       self.keywords, self.report, self.macro_names))
        finally:
            self.here, self.scope = saved_here, saved_scope

    # -- instructions ----------------------------------------------------

    def instruction(self, ln: Line):
        mnemonic = ln.op
        modes = OPCODES.get(mnemonic)
        if modes is None:
            raise AsmError(f"unknown instruction {mnemonic!r}", ln.where)
        operand = ln.operands.strip()
        code, mode = self.select_mode(mnemonic, modes, operand, ln)

        if mode == "rel":
            self.emit(code)
            # A 6502 measures a branch displacement from the address of the
            # *next* instruction, which is the byte after the displacement byte.
            # At this point `self.log` is the displacement byte's own address --
            # `emit(code)` above has already advanced past the opcode -- so the
            # base has to be advanced once more.
            #
            # Getting this wrong is invisible in a listing and fatal in a ROM: it
            # makes every branch land one byte late, and it is one byte *late*
            # rather than one byte early, so a routine's loop and its backward
            # branch agree with each other and the routine still runs. Measured
            # against the cartridge, `X6.PDS:791 initcols` emitted `D0 F8` where
            # the release has `D0 F7`, and `X7.PDS:927`'s `bpl !a` pointed one
            # byte into `lda $2002`'s opcode instead of at its first byte.
            self.fixups.append(Fixup(self.prg_offset(), self.log + 1,
                                     operand.lstrip(), ln.where, self.scope))
            self.emit(0)
            return
        if mode in ("imp", "acc"):
            self.emit(code)
            return

        self.emit(code)
        expr_text = operand.lstrip()
        if expr_text.startswith("#"):
            expr_text = expr_text[1:]
        if mode in ("indx", "indy"):
            close = expr_text.find(")")
            expr_text = expr_text[1:close] if close > 0 else expr_text.strip("()")
        # `sta $00,x` / `lda (ptr),y`: the index is part of the mode, not of
        # the expression.
        expr_text = expr_text.split(",")[0].strip()
        value = self.value_or_defer(expr_text, ln.where)
        if mode in ("zp", "zpx", "zpy", "imm", "indx", "indy"):
            self.emit(value & 0xFF)
        else:
            self.emit_word(value & 0xFFFF)

    def select_mode(self, mnemonic: str, modes: list[tuple[int, str]], operand: str,
                    ln: Line):
        if operand == "":
            for code, mode in modes:
                if mode in ("imp", "acc"):
                    return code, mode
            raise AsmError(f"{mnemonic} needs an operand", ln.where)
        if operand.strip().lower() in ("a", "acc") and any(m == "acc" for _, m in modes):
            # `asl a` is the accumulator, written as the pseudo-register `a`. It
            # is not a symbol: without this, `asl a` at X0.PDS:659 was resolved as
            # a reference and reported `undefined symbol 'a'`, which stopped the
            # whole project pass over a spelling of the accumulator.
            return next((c, m) for c, m in modes if m == "acc")
        if mnemonic in BRANCHES:
            return modes[0]
        imm = operand.startswith("#")
        # `(zp,x` and `(zp),y` are one operand each; which one depends on the
        # closing bracket, not on the leading one.
        #
        # `operand[close:]` starts *at* the ')' and so reads "),y", which is
        # not an index at all: every `(zp),y` in the source therefore
        # selected `indx` and assembled to $A1/$81/$91's siblings -- `lda (t0),y`
        # became `lda (t0,x)`. That is `$B1` vs `$A1` on 54 statements across
        # all eight modules, including x0's `moveb2` level decompressor and
        # x7's `movepal`, and it is invisible in a listing: both forms are a
        # legal operand spelling. Whitespace is removed rather than trimmed,
        # because the statement splitter rejoins tokens with spaces and
        # `(t0) , y` has to read the same as `(t0),y`.
        close = operand.find(")")
        indirect = operand.lstrip().startswith("(") and close > 0
        tail = operand[close + 1:].lower().replace(" ", "") if indirect else ""
        indexed_y = tail in (",y", ",w")
        if mnemonic in FORCE_ABS:
            if indirect and any(m == "ind" for _, m in modes):
                return next((c, m) for c, m in modes if m == "ind")
            return next((c, m) for c, m in modes if m == "abs")
        if imm:
            for code, mode in modes:
                if mode == "imm":
                    return code, mode
            raise AsmError(f"{mnemonic} has no immediate mode", ln.where)
        if indirect:
            want = "indy" if indexed_y else "indx"
            for code, mode in modes:
                if mode == want:
                    return code, mode
            raise AsmError(f"{mnemonic} has no {want} mode", ln.where)
        if any(m == "indy" for _, m in modes):
            # (zp),Y is written without brackets in this source only for the
            # indirect forms; a bare symbol is never indirect.
            pass
        # `sta $00,x` is one operand with an index, so the zero-page test looks
        # at the base only -- but the *choice* of mode has to look at the index,
        # and preferring plain `zp` first does not.
        #
        # `X5.PDS:112` is `!a lda (t0),y / sta t2,y / dey / bpl !a`: copy the
        # three bytes at (t0)+2..(t0)+0 to t2+2..t2+0. There is no `sta $zp,y` on
        # a 6502 -- `sta` has `zp`, `zp,x`, `abs`, `abs,x` and `abs,y` and no
        # `zp,y` -- so this has to assemble to the *absolute* indexed form
        # `$99 t2 00`, which is what the original assembler did. Preferring `zp`
        # emitted `$85 t2`: two bytes, no index, and the three bytes all landed
        # on t2. It is silent, because `sta t2,y` is a perfectly good looking
        # operand; and it is fatal, because that loop is how a scene gets
        # uncompressed. Measured: `unrunscn` copied one token's five bytes to
        # the PPU and returned, leaving the nametable with 4 non-zero bytes in
        # 2 048 and the screen black.
        base = operand.strip("()").split(",")[0].strip()
        value = self.value_or_defer(base, ln.where)
        zp_ok = value < 0x100
        index = None
        if "," in operand:
            tail = operand.rsplit(",", 1)[1].strip().lower()
            if tail in ("x", "w"):
                index = "x"
            elif tail == "y":
                index = "y"
        # When the mnemonic has no zero-page form for the index that was written,
        # fall through to the absolute one, which every 6502 mnemonic has.
        pref = {"x": ("zpx", "absx", "zp", "abs"),
                "y": ("zpy", "absy", "zp", "abs"),
                None: ("zp", "abs")}[index]
        for want in pref:
            for code, mode in modes:
                if mode != want:
                    continue
                if mode in ("zp", "zpx", "zpy") and not zp_ok:
                    continue
                return code, mode
        for code, mode in modes:
            if mode == "indy":
                return code, mode
        raise AsmError(f"no addressing mode for {mnemonic} {operand!r}", ln.where)

    # -- branch fixups ---------------------------------------------------

    def resolve(self):
        saved = self.scope
        for f in self.fixups:
            # A `!a` target is scoped to the routine it was written in, so the
            # scope has to be restored before the name is looked up.
            self.scope = f.scope
            try:
                target = self.value(f.expr)
            except AsmError as e:
                raise AsmError(f"{e.raw} in branch to {f.expr!r}", f.where) from None
            delta = (target - f.after) & 0xFFFF
            signed = delta - 0x10000 if delta > 0x7F else delta
            if not -128 <= signed <= 127:
                raise AsmError(f"branch out of range to ${target:04X}", f.where)
            # The same bounds test `emit` makes. `prg_offset` answers -1 for a
            # byte at or above `addr_ceiling`, which is how a module that runs
            # past its slot's share of the PRG is stopped: `emit` drops those
            # bytes, and every displacement byte among them lands on offset -1.
            # Writing it unguarded does not fail -- `self.prg[-1]` is a valid
            # index -- it overwrites the IRQ vector's high byte at file offset
            # $1FFFF, and because fixups resolve in list order the last dropped
            # branch to resolve is the one that wins, so which branch corrupts
            # the vector depends on how many modules ran before it.
            if not 0 <= f.offset < len(self.prg):
                self.overflow.append(
                    (self.here.name, f.after & 0xFFFF, self.slot))
                continue
            self.prg[f.offset] = signed & 0xFF
            self.emitted[f.offset] = signed & 0xFF
        self.scope = saved


def main() -> int:
    ap = argparse.ArgumentParser(description="PDS 1.26 compatible assembler")
    ap.add_argument("source", type=pathlib.Path)
    ap.add_argument("--prg", type=pathlib.Path, required=True, help="128 KiB PRG image")
    ap.add_argument("--slot", type=lambda s: int(s, 0), default=0,
                    help="initial 8 KiB slot (0-15)")
    ap.add_argument("--sym", type=pathlib.Path)
    ap.add_argument("--append-sym", type=pathlib.Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    prg = bytearray(args.prg.read_bytes()) if args.prg.exists() else bytearray(128 * 1024)
    asm = Assembler(prg, args.source.parent, [args.source.parent], verbose=args.verbose)
    asm.run_file(args.source, slot=args.slot)
    args.prg.write_bytes(bytes(prg))
    if args.sym or args.append_sym:
        target = args.sym or args.append_sym
        with target.open("a" if args.append_sym else "w") as fh:
            for k in sorted(asm.sym):
                fh.write(f"{k} = ${asm.sym[k]:04X}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
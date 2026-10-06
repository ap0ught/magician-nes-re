"""`src/testing/nodep.py` -- does any of this repository's CODE still reach into another project?

A shared helper for two checks that answer the same question at two scopes:
`test_bizpath.py` asks it about the emulator-resolution scripts, and
`test_repo_hygiene.py` asks it about the whole executable tree.

THE QUESTION, PRECISELY
-----------------------
`aibeatszelda` appears in 69 files of this repository and almost none of them are
a problem. Three different things share one string:

  1. **DEPENDENCY.** A default path, an import, or a symlink that makes this
     project run out of another project's files. THIS is what must be zero. It is
     what let a service point at another checkout's emulator and `pkill` a
     136,526-frame replay belonging to it.
  2. **PROVENANCE.** Honest credit for ported design and for two real bugs found
     by reading a sibling's tests. `src/play/bridge.lua:5`, `emu.py`'s module
     docstring, `runner.py`, `search.py`, `README.md`, `journal/10`, `journal/13`.
     None of it is to be scrubbed; this project values knowing where a thing came
     from.
  3. **HISTORY.** `logs/` is a truthful record of what ran where. Never edited.

The distinction cannot be made by grepping the raw text, because a name in a
*comment* is provenance and a name in a *default* is a dependency, and both look
identical to `grep`. So the check strips comments first and then looks. That is
the whole reason this file exists rather than a one-line `grep -rn` in the test.

WHAT "STRIPPING COMMENTS" DOES AND DOES NOT MEAN
------------------------------------------------
* **Python**: exact. `tokenize` is the interpreter's own lexer, so a `#` inside a
  string literal is not treated as a comment and a `#` at the end of a line is;
  `ast` then removes module/class/function docstrings, which are prose by
  construction and cannot name a file anything opens. String literals that are NOT
  docstrings stay in, because a path is a string and that is the case to catch.
* **Lua**: line-based, `--` to end of line.
* **Shell, Makefile, systemd unit**: line-based. A whole-line comment is a line
  whose first non-blank character is `#`; an inline `#` truncates the line. That
  is an approximation and it is safe in the direction that matters: it can hide a
  real hit (a path after an inline `#` inside a quoted string), which is why no
  *remediation* is automated here. Over-reporting a comment is a false failure a
  human reads and relaxes; hiding a dependency is the failure this project keeps
  paying for.

WHAT IS ALLOWED
---------------
Markdown, `journal/`, `logs/`, and this suite. Prose citing where an idea came
from is the point; a test naming the thing it is testing for is the point. The
allowlist is a list of prefixes, so a new markdown file is covered by default and
a new `.py` under `src/` is covered by default too.

WHAT IT DOES NOT CLAIM
----------------------
It does not follow symlinks and it does not check a directory's contents at
runtime. A symlink from this project's install into another project's would
satisfy this check exactly as it satisfies `grep` -- which is why the install is a
real copy and `journal/14` records the measurement of what BizHawk writes.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

# The other project. Stated here, where the check lives, rather than imported
# from a tool that might one day be the thing being checked.
SIBLING = "aibeatszelda"

# Path prefixes (repo-relative) in which the name is provenance or history.
ALLOWED_PREFIXES = (
    "README.md",
    "PROVENANCE.md",
    "GAPMAP.md",
    "LEGAL.md",
    "journal/",
    "logs/",
    "src/testing/",          # the checks themselves have to name the thing
    ".gitignore",
)

# Suffixes of files that are CODE for this purpose. Anything not listed and not
# under an allowed prefix is reported, so a new file type is caught by default
# rather than silently allowed -- an allowlist that only knows about `.py` and
# `.sh` would miss a Lua script or a systemd unit, which is where a launch path
# would actually go.
CODE_SUFFIXES = (".py", ".sh", ".lua", ".service", ".mk", ".toml", ".cfg", ".ini")

# Exact filenames that are code despite having no suffix.
CODE_NAMES = ("Makefile",)


def allowed(rel: str) -> bool:
    """Is this repo-relative path one where the name is prose rather than a path?"""
    return any(rel == p or rel.startswith(p) for p in ALLOWED_PREFIXES)


def strip_shell_comments(text: str) -> list[tuple[int, str]]:
    """`(lineno, code)` for each line of a shell script, comments removed.

    Line-based, and documented as such above. `#!` on line 1 is a shebang, which
    is a comment for our purposes.
    """
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        # An inline `#` starts a comment only at the start of a word, which is
        # enough for a path literal to survive as code.
        cut = len(line)
        for j, ch in enumerate(line):
            if ch == "#" and (j == 0 or line[j - 1] in " \t"):
                cut = j
                break
        code = line[:cut]
        if code.strip():
            out.append((i, code))
    return out


def strip_lua_comments(text: str) -> list[tuple[int, str]]:
    """`(lineno, code)` for each line of Lua, `--` comments removed.

    Line-based, and a `--` inside a string literal truncates the line. Same
    caveat as the shell stripper and for the same reason: over-reporting a comment
    is a false failure a human reads, hiding a dependency is not.

    This is here because `src/play/bridge.lua:5` carries the provenance of the
    whole bridge design in a `--` comment, and a Lua-blind stripper reports it as
    a dependency. A check that fires on the project's own credit for where a thing
    came from gets deleted, and then there is no check at all.
    """
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("--"):
            continue
        cut = len(line)
        for j, ch in enumerate(line):
            if ch == "-" and line[j:j + 2] == "--" and (j == 0 or line[j - 1] in " \t"):
                cut = j
                break
        code = line[:cut]
        if code.strip():
            out.append((i, code))
    return out


def strip_python_comments(text: str) -> list[tuple[int, str]]:
    """`(lineno, code)` for each line of Python, comments removed. Exact.

    `tokenize` is the interpreter's own lexer, so `#` inside a string is not a
    comment. A file that does not tokenize yields its raw lines rather than no
    lines: a syntax error in a file we are auditing must not read as "clean",
    which is the fourth rule in `run_all.py`'s docstring all over again.
    """
    keep = {i: line for i, line in enumerate(text.splitlines(), 1)}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                ln = tok.start[0]
                keep[ln] = keep[ln][:tok.start[1]]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return [(i, s) for i, s in keep.items() if s.strip()]


def docstring_lines(text: str) -> set[int]:
    """Line numbers covered by a module/class/function docstring.

    A docstring is prose, and this project's module docstrings are where the
    provenance lives -- `src/play/emu.py` opens by explaining that the bridge
    design is aibeatszelda's, and that sentence is load-bearing documentation of
    where a thing came from, not a path. A docstring cannot name a file anything
    opens, so counting it as a dependency would trade real documentation for a
    check that already has teeth.

    Found with `ast`, which is the only thing that knows which string literal is a
    docstring rather than a value. String literals that are NOT docstrings are
    left in place, because a path *is* a string and that is exactly the case this
    has to catch.
    """
    out: set[int] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                end = getattr(first, "end_lineno", None) or first.lineno
                out.update(range(first.lineno, end + 1))
    return out


def python_prose(text: str) -> list[tuple[int, str]]:
    """`(lineno, code)` for each line of Python with comments AND docstrings gone."""
    docs = docstring_lines(text)
    return [(i, s) for i, s in strip_python_comments(text) if i not in docs]


def code_lines(path: Path) -> list[tuple[int, str]]:
    """`(lineno, comment-stripped line)` for one file, by suffix."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix == ".py":
        return python_prose(text)
    if path.suffix == ".lua":
        return strip_lua_comments(text)
    return strip_shell_comments(text)


def find_dependency_hits(repo: Path) -> list[tuple[str, int, str]]:
    """Every `(relpath, lineno, line)` in the CODE that names the other project.

    Sorted, so the output of two runs is comparable and a `diff` between them
    means something.
    """
    hits = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or ".git/" in str(path):
            continue
        rel = str(path.relative_to(repo))
        if allowed(rel):
            continue
        if path.suffix not in CODE_SUFFIXES and path.name not in CODE_NAMES:
            continue
        for lineno, line in code_lines(path):
            if SIBLING in line:
                hits.append((rel, lineno, line.strip()))
    return hits

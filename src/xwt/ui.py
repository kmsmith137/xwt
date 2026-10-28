"""Output: `xwt:` messages, warnings, errors, echoed commands and headers.

Everything is flushed as it is printed, because xwt interleaves its own output
with that of the git and pip processes it runs.
"""

from __future__ import annotations

import shlex
import shutil
import sys
import textwrap

PREFIX = "xwt: "
INDENT = " " * len(PREFIX)


class XwtError(Exception):
    """A problem reported as `xwt: error: ...` (exit status 1), without a traceback."""


def _width() -> int:
    return max(40, shutil.get_terminal_size((80, 24)).columns - 1)


def format_message(text: str, lead: str = PREFIX) -> str:
    """Wrap `text` to the terminal width, continuation lines indented under
    the text after `xwt: `. A line of `text` starting with "  - " is a list
    item, wrapped with a hanging indent; other lines starting with a space
    (commands, paths) are kept as they are."""
    out = []
    first = True
    for para in text.split("\n"):
        start = lead if first else INDENT
        first = False
        if para.startswith("  - "):
            out.extend(textwrap.wrap(para[4:], width=_width(), initial_indent=start + "  - ",
                                     subsequent_indent=INDENT + "    ",
                                     break_long_words=False, break_on_hyphens=False))
        elif not para or para.startswith(" "):
            out.append((start + para).rstrip())
        else:
            out.extend(textwrap.wrap(para, width=_width(), initial_indent=start,
                                     subsequent_indent=INDENT, break_long_words=False,
                                     break_on_hyphens=False) or [start.rstrip()])
    return "\n".join(out)


def out(text: str = "") -> None:
    """Plain output on stdout."""
    print(text, flush=True)


def info(text: str) -> None:
    print(format_message(text), flush=True)


def warn(text: str) -> None:
    sys.stdout.flush()
    print(format_message(text, PREFIX + "warning: "), file=sys.stderr, flush=True)


def error(text: str) -> None:
    sys.stdout.flush()
    print(format_message(text, PREFIX + "error: "), file=sys.stderr, flush=True)


def cmdline(cmd) -> str:
    return shlex.join(str(c) for c in cmd)


def echo(cmd, cwd=None) -> None:
    """Announce a command that changes something, before running it."""
    where = f"  (in {cwd})" if cwd is not None else ""
    print(f"{PREFIX}$ {cmdline(cmd)}{where}", flush=True)


def header(label: str) -> None:
    print(f"==================== {label} ====================", flush=True)


def stdout_isatty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def stdin_isatty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def bullet_list(items) -> str:
    return "\n".join(f"  - {i}" for i in items)

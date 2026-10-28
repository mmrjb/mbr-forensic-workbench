"""Windows command-line tokenisation for *display* and for parsing operator-entered extra
arguments. Execution always uses an argument list; these helpers never produce something
that is handed to a shell."""
from __future__ import annotations

import subprocess


def split_windows_args(text: str) -> list[str]:
    """Split like ``CommandLineToArgvW`` / the MS C runtime (post-2008 rules).

    * whitespace separates arguments outside quotes;
    * ``2n`` backslashes before a quote -> ``n`` backslashes and the quote toggles quoting;
    * ``2n+1`` backslashes before a quote -> ``n`` backslashes and a literal quote;
    * backslashes not followed by a quote are literal;
    * inside quotes, ``""`` yields a literal quote.
    No variable expansion, globbing, ``&``, ``|``, ``^`` or ``%`` handling happens here because
    no shell is involved.
    """
    args: list[str] = []
    cur: list[str] = []
    in_quotes = False
    have_arg = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            j = i
            while j < n and text[j] == "\\":
                j += 1
            count = j - i
            if j < n and text[j] == '"':
                cur.append("\\" * (count // 2))
                if count % 2:
                    cur.append('"')
                    i = j + 1
                else:
                    i = j  # quote handled on next loop iteration
                have_arg = True
                continue
            cur.append("\\" * count)
            have_arg = True
            i = j
            continue
        if ch == '"':
            if in_quotes and i + 1 < n and text[i + 1] == '"':
                cur.append('"')
                i += 2
                have_arg = True
                continue
            in_quotes = not in_quotes
            have_arg = True
            i += 1
            continue
        if ch in " \t\r\n" and not in_quotes:
            if have_arg:
                args.append("".join(cur))
                cur, have_arg = [], False
            i += 1
            continue
        cur.append(ch)
        have_arg = True
        i += 1
    if have_arg:
        args.append("".join(cur))
    return args


def display_command(argv: list[str]) -> str:
    """Human-readable command line using MS C runtime quoting. For display/copy only."""
    return subprocess.list2cmdline([str(a) for a in argv])

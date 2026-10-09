# -*- coding: utf-8 -*-

"""
Small helpers with no project dependencies.

Only what a second module needs. A helper with one caller belongs next to that
caller, where it can be read without a jump.
"""

import re
import sys
from functools import lru_cache


def debug(s: str):
    """
    Print a debug message to stderr.

    stderr rather than stdout on purpose: on Vercel stdout is part of the
    response stream, so anything printed there can reach the browser. stderr
    only ever reaches the server log.
    """
    print(s, file=sys.stderr)


#: Characters that make a pattern a regex rather than a shell-style wildcard.
#: ``*`` is excluded because it is legal in both and means the same thing.
_REGEX_METACHARACTERS = r"[.+?^${}()|[\]\\]"


@lru_cache(maxsize=256)
def _compile_pattern(pattern: str) -> re.Pattern:
    """
    Compile one include/exclude pattern.

    Cached because schema reflection matches the same handful of patterns once
    per table, and compiling is the expensive half of :func:`match`.
    """
    if re.search(_REGEX_METACHARACTERS, pattern.replace("*", "")):
        # Already a regex, use it as written.
        regex = pattern
    else:
        # A wildcard pattern: escape everything, then put `*` back as `.*`.
        regex = re.escape(pattern).replace(r"\*", ".*")
    return re.compile(regex, re.IGNORECASE)


def match(
    name: str,
    include: list[str],
    exclude: list[str],
) -> bool:
    """
    Decide whether a name survives an include/exclude filter.

    Used to keep a table out of the schema the agent is shown without dropping
    it from the database.

    Patterns are shell-style wildcards (``"widget*"``, ``"*_tmp"``) unless they
    contain regex metacharacters, in which case they are treated as regexes
    (``"^part_\\d{4}$"``). Matching is case-insensitive and must cover the whole
    name.

    The rules, in the order they are applied:

    1. Matching any ``exclude`` pattern rejects the name outright.
    2. An empty ``include`` list accepts everything else.
    3. Otherwise the name must match at least one ``include`` pattern.

    :param name: The name to test, e.g. a table name.
    :param include: Patterns to keep. Empty means keep all.
    :param exclude: Patterns to drop. Takes precedence over ``include``.

    Examples:
        >>> match("widget", ["widget*"], [])
        True
        >>> match("widget_tmp", [], ["*_tmp"])
        False
        >>> match("part_2023", [r"^part_\\d{4}$"], [])
        True
    """
    for pattern in exclude:
        if _compile_pattern(pattern).fullmatch(name):
            return False

    if not include:
        return True

    for pattern in include:
        if _compile_pattern(pattern).fullmatch(name):
            return True

    return False

# -*- coding: utf-8 -*-

"""
Where am I running?

One question, one place, so that no other module reads the environment to find
out. Detection lives here, the consequences live in ``config.py``: adding a
deployment target means an ``is_*`` method here and a factory there, and nothing
else in the codebase learns a new target exists.
"""

import os

from functools import cached_property


class Runtime:
    """Singleton-style runtime detector. Use the ``runtime`` instance, not this class."""

    @cached_property
    def _is_vercel(self) -> bool:
        # See https://vercel.com/docs/environment-variables/system-environment-variables
        return os.environ.get("VERCEL") == "1"

    def is_local(self) -> bool:
        return not self._is_vercel

    def is_vercel(self) -> bool:
        return self._is_vercel


runtime = Runtime()

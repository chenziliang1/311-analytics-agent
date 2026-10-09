# -*- coding: utf-8 -*-

"""
``python -m agent_app.mcp_server``: the server over stdio.

How the agent starts it as a child process. A module path rather than the
``agent-app`` console script, because the interpreter that is running the agent
is always there, while a script's location depends on how the package was
installed (a Vercel build has no ``.venv/bin``).
"""

from .server import build_server, warm_up

if __name__ == "__main__":
    # The agent starts this on its first question; get the warehouse ready
    # while that question's first model call runs.
    warm_up()
    build_server().run(transport="stdio")

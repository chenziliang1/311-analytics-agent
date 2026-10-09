# -*- coding: utf-8 -*-

"""
The one exception to "no test opens a network connection": tests marked
``warehouse`` query the real warehouse. They run wherever this machine has its
credentials and are skipped, with the reason, where it does not -- CI's test
job among them, which holds no database credentials on purpose.
"""

from pathlib import Path

import pytest


def warehouse_configured() -> bool:
    from agent_app.api import one

    c = one.config
    if c.warehouse == "snowflake":
        key_file = c.snowflake_private_key_path
        return bool(c.snowflake_private_key) or bool(key_file and Path(key_file).expanduser().exists())
    return bool(c.db_host)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "warehouse: queries the real warehouse; skipped without its credentials"
    )


def pytest_collection_modifyitems(config, items):
    if warehouse_configured():
        return
    skip = pytest.mark.skip(reason="no warehouse credentials on this machine")
    for item in items:
        if "warehouse" in item.keywords:
            item.add_marker(skip)

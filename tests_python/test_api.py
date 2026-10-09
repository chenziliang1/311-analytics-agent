# -*- coding: utf-8 -*-

from agent_app import api


def test_api():
    _ = api.one


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.api",
        preview=False,
    )

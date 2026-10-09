# -*- coding: utf-8 -*-

from agent_app.one.one_00_main import one


def test_one():
    _ = one


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.one.one_00_main",
        preview=False,
    )
